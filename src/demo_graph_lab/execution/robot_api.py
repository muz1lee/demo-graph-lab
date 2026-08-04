"""Task-independent motion planning and trajectory execution.

``plan_joint_path`` 调用 ``reasoning:motion_planning_stereo``；
``execute_path`` 流式下发关节航点，并用 ``info:get_qpos`` 核对终点。

规划服务接收 ``mp.*`` 控制 token、当前双臂关节角和七维目标位姿。
它返回 ``(text_out, flat_trajectory)``，因此必须使用 ``PipelineClient.call``，
不能使用要求 Mapping 返回值的 ``PipelineClient.reasoning``。所有 helper 只接收
任务无关的几何量；规划失败由 OracleRuntime 显式转入限幅伺服。
"""

from __future__ import annotations

import math
import time
from dataclasses import dataclass, field

JOINTS_PER_WAYPOINT = 7

# mp.* 控制 token 常量(派生自服务枚举 MP_PROTOCOL_VERSION=1 与 MP_*_ 集合,不新增魔数)。
MP_VERSION = "1"
MP_SCENE_CAMERA_DEFAULT = "head"  # 非特权 head 立体相机；hand 模式另需 ee_xquat

# 执行期收敛核对常数沿用 runtime 的伺服容差:
#   QPOS_CONVERGE_TOL 取 MotorNode qpos_check_tolerance=0.05 rad,
#   即「单个关节判到位」的既有阈值;每航点最多等 EXEC_SETTLE_TIMEOUT_S。
QPOS_CONVERGE_TOL = 0.05          # rad/关节
EXEC_SETTLE_TIMEOUT_S = 8.0       # 单航点收敛墙钟上限
EXEC_POLL_S = 0.2                 # get_qpos 轮询间隔
# 中间航点按固定间隔流式发送，只对终点做收敛确认。
EXEC_STREAM_DT_S = 0.2            # 中间航点连发间隔（0.15~0.25 s 经验区间的中值）
EXEC_MAX_WAYPOINTS = 20           # 超过则均匀抽稀,抽稀后 ≤ 此值且必含终点
EXEC_ENDPOINT_RETRY = 3           # 终点不收敛时重发末尾几点的次数


class PlanFailed(Exception):
    """规划失败，包括后端、IK、输入和传输错误。"""

    layer = "planning"

    def __init__(self, message: str, *, reason=None):
        super().__init__(message)
        self.reason = reason


@dataclass(frozen=True)
class PlanResult:
    """一次规划的产物。waypoints 是 N 个 7-DoF 关节航点(已 reshape),元数据供审计。"""

    waypoints: list[list[float]]
    planning_mode: str
    scene_input: str
    text_out: list[str]
    n_waypoints: int


@dataclass
class ExecResult:
    """一次逐航点执行的产物:到位与否 + 沿途每航点的关节收敛残差。"""

    reached: bool
    n_sent: int
    n_converged: int
    per_waypoint_maxdev: list[float] = field(default_factory=list)  # 每航点各关节最大残差(rad)


def _pipe(rt):
    """从 runtime 取 PipelineClient 句柄(OracleRuntime 暴露 .pipe)。"""
    return rt.pipe


def _log(rt, op, **kw):
    log = getattr(rt, "_log", None)
    if callable(log):
        log(op, **kw)


def _as_floats(seq, n=None, name="value"):
    vals = [float(v) for v in seq]
    if n is not None and len(vals) != n:
        raise PlanFailed(f"{name} 长度必须为 {n},got {len(vals)}", reason="bad_shape")
    return vals


def _reshape_flat(flat, joints=JOINTS_PER_WAYPOINT):
    """扁平 list → N×7 航点。长度非 7 的整数倍则拒绝(契约不符)。"""
    flat = [float(v) for v in flat]
    if len(flat) == 0 or len(flat) % joints != 0:
        raise PlanFailed(
            f"轨迹扁平长度 {len(flat)} 不是 {joints} 的整数倍,无法 reshape",
            reason="bad_trajectory_len",
        )
    return [flat[i:i + joints] for i in range(0, len(flat), joints)]


def plan_joint_path(rt, arm, q_goal_or_pose, *, q_current=None, q_other_arm=None,
                    planning_mode=None, scene_input="live", scene_camera=None,
                    data=None, planner_config=None, timeout_s=180.0):
    """规划一条到目标的关节空间轨迹,返回 PlanResult(N×7 航点)。

    以 motion planning 取代 raw IK：底层调 reasoning:motion_planning_stereo。

    参数(全部几何量,无任务/物体名):
      arm            : 0=左臂 / 1=右臂(=arm_id)。
      q_goal_or_pose : 目标。7 元 → joint_goal 的 q_goal;或显式传 planning_mode。
                       cartesian_goal 模式下目标位姿从 `data`([pos3,quat4])给,q_goal_or_pose
                       可为 None。
      q_current      : 当前 7-DoF 关节角。None 时从 info:get_qpos 读。
      q_other_arm    : 闲臂 7-DoF 关节角(避碰用)。None 时从 info:get_qpos 读另一臂。
      planning_mode  : joint_goal / cartesian_goal / delta_move。None 时按 q_goal_or_pose 形状推断
                       (7 元 → joint_goal)。
      scene_input    : live(抓图内联建 TSDF)/ cache(需先 build_map)。默认 live。
      scene_camera   : live 模式相机(head/hand);None → head。
      data           : [pos3,quat4] 名义目标位姿;intent=plan 的 _parse_arm_data 必需。None 时
                       从 info:get_xquat 读当前 EE 位姿作名义值(joint_goal 只作占位,真目标是 q_goal)。

    后端、IK、输入或传输失败时抛出 PlanFailed。
    """
    pipe = _pipe(rt)
    arm_id = int(arm)

    # 关节现状:未给则读。
    if q_current is None:
        q_current = _as_floats(pipe.call("info", "get_qpos", {"arm_id": arm_id}),
                               JOINTS_PER_WAYPOINT, "q_current")
    else:
        q_current = _as_floats(q_current, JOINTS_PER_WAYPOINT, "q_current")
    if q_other_arm is None:
        q_other_arm = _as_floats(pipe.call("info", "get_qpos", {"arm_id": 1 - arm_id}),
                                 JOINTS_PER_WAYPOINT, "q_other_arm")
    else:
        q_other_arm = _as_floats(q_other_arm, JOINTS_PER_WAYPOINT, "q_other_arm")

    # planning_mode 推断:7 元目标 → joint_goal。
    q_goal = None
    if planning_mode is None:
        if q_goal_or_pose is not None and len(list(q_goal_or_pose)) == JOINTS_PER_WAYPOINT:
            planning_mode = "joint_goal"
        else:
            planning_mode = "cartesian_goal"
    if planning_mode == "joint_goal":
        q_goal = _as_floats(q_goal_or_pose, JOINTS_PER_WAYPOINT, "q_goal")

    # data = 名义目标位姿([pos3,quat4]);cartesian_goal 时即真目标,joint_goal 时为占位。
    if data is None:
        if planning_mode == "cartesian_goal" and q_goal_or_pose is not None:
            data = _as_floats(q_goal_or_pose, JOINTS_PER_WAYPOINT, "target_pose")
        else:
            data = _as_floats(pipe.call("info", "get_xquat", {"arm_id": arm_id}),
                              JOINTS_PER_WAYPOINT, "ee_pose")
    else:
        data = _as_floats(data, JOINTS_PER_WAYPOINT, "data")

    text = [
        f"mp.version={MP_VERSION}",
        "mp.intent=plan",
        f"mp.planning_mode={planning_mode}",
        f"mp.scene_input={scene_input}",
    ]
    if scene_input == "live":
        text.append(f"mp.scene_camera={scene_camera or MP_SCENE_CAMERA_DEFAULT}")

    kwargs = {
        "text": text,
        "data": data,
        "arm_id": arm_id,
        "q_current": q_current,
        "q_other_arm": q_other_arm,
    }
    if q_goal is not None:
        kwargs["q_goal"] = q_goal
    if planner_config is not None:
        kwargs["planner_config"] = planner_config

    _log(rt, "plan_joint_path", arm=arm_id, mode=planning_mode, scene_input=scene_input)
    t0 = time.time()
    try:
        # 走 .call 而非 .reasoning():服务返回 tuple(text_out, flat),.reasoning() 要求 Mapping 会拒。
        result = pipe.call("reasoning", "motion_planning_stereo", kwargs)
    except Exception as exc:  # PipelineError 或传输异常
        raise PlanFailed(f"motion_planning_stereo 失败: {exc}", reason="backend") from exc

    if not (isinstance(result, (list, tuple)) and len(result) == 2):
        raise PlanFailed(
            f"motion_planning_stereo 返回结构异常: {type(result).__name__}",
            reason="bad_result",
        )
    text_out, flat = result
    waypoints = _reshape_flat(flat)
    _log(rt, "plan_joint_path_done", n=len(waypoints), sec=round(time.time() - t0, 2))
    return PlanResult(
        waypoints=waypoints,
        planning_mode=planning_mode,
        scene_input=scene_input,
        text_out=list(text_out) if isinstance(text_out, (list, tuple)) else [str(text_out)],
        n_waypoints=len(waypoints),
    )


def execute_path(rt, waypoints, *, arm=None, gpos=None,
                 converge_tol=QPOS_CONVERGE_TOL, settle_timeout_s=EXEC_SETTLE_TIMEOUT_S):
    """流式下发关节航点,只对终点核对收敛,返回 ExecResult。

    中间点按 ``EXEC_STREAM_DT_S`` 连发；终点通过 ``get_qpos`` 确认，失败时
    重发末尾航点。过长路径会均匀抽稀，但始终保留终点。ctrl 的 HTTP 返回值
    只表示请求被接受，不能替代关节回读。

    waypoints : N 个 7-DoF 关节航点(PlanResult.waypoints 或裸 list[list])。
    arm       : 0/1;None 时取 rt.arm_id。
    gpos      : 可选夹爪目标(随 qpos_move 一起下发);None 时不带。
    """
    if isinstance(waypoints, PlanResult):
        waypoints = waypoints.waypoints
    pipe = _pipe(rt)
    arm_id = int(arm) if arm is not None else int(getattr(rt, "arm_id", 1))

    pts = [_as_floats(wp, JOINTS_PER_WAYPOINT, f"waypoint[{i}]")
           for i, wp in enumerate(waypoints)]
    n_planned = len(pts)
    if not pts:
        _log(rt, "execute_path_done", n_sent=0, n_converged=0, reached=False)
        return ExecResult(reached=False, n_sent=0, n_converged=0, per_waypoint_maxdev=[])
    pts = _downsample(pts, EXEC_MAX_WAYPOINTS)
    if len(pts) != n_planned:
        _log(rt, "waypoints_downsampled", n_planned=n_planned, n_kept=len(pts))

    def send(wp):
        kw = {"arm_id": arm_id, "qpos": wp}
        if gpos is not None:
            kw["gpos"] = gpos
        pipe.call("ctrl", "qpos_move", kw)

    # 中间点:流式连发,不回读。它们只是把末端"带过去"的过渡姿态,
    # 单点是否精确到位不影响最终精度——终点才是被验收的那个。
    for wp in pts[:-1]:
        send(wp)
        time.sleep(EXEC_STREAM_DT_S)

    # 终点:必须确认收敛。不收敛时重发末尾几点(不是重跑整条路径)。
    goal = pts[-1]
    send(goal)
    converged, dev = _wait_qpos(pipe, arm_id, goal, converge_tol, settle_timeout_s)
    retries = 0
    while not converged and retries < EXEC_ENDPOINT_RETRY:
        retries += 1
        for wp in pts[-min(3, len(pts)):]:
            send(wp)
            time.sleep(EXEC_STREAM_DT_S)
        converged, dev = _wait_qpos(pipe, arm_id, goal, converge_tol, settle_timeout_s)
        _log(rt, "execute_endpoint_retry", arm=arm_id, attempt=retries,
             converged=converged, maxdev=round(dev, 5))

    _log(rt, "execute_path_done", n_planned=n_planned, n_sent=len(pts),
         endpoint_converged=converged, endpoint_maxdev=round(dev, 5),
         retries=retries, reached=converged)
    # n_converged 只统计终点(中间点按设计不回读),保持 ExecResult 语义不撒谎。
    return ExecResult(
        reached=converged,
        n_sent=len(pts),
        n_converged=1 if converged else 0,
        per_waypoint_maxdev=[round(dev, 5)],
    )


def _downsample(pts, max_n):
    """均匀抽稀到 ≤ max_n 个点,**必含终点**(终点是唯一被收敛确认的点,不能丢)。"""
    if len(pts) <= max_n:
        return pts
    k = math.ceil(len(pts) / max_n)
    kept = pts[::k]
    if kept[-1] is not pts[-1]:
        kept.append(pts[-1])
    return kept


def _wait_qpos(pipe, arm_id, target, tol, timeout_s):
    """等本臂关节收敛到 target(各关节残差 < tol)。返回 (converged?, 末次最大残差)。"""
    t0, dev = time.time(), float("inf")
    while True:
        try:
            q = _as_floats(pipe.call("info", "get_qpos", {"arm_id": arm_id}),
                           JOINTS_PER_WAYPOINT, "get_qpos")
            dev = max(abs(a - b) for a, b in zip(q, target))
            if dev < tol:                       # 已到位则立刻返回,不空等一轮
                return True, dev
        except Exception:
            pass
        if time.time() - t0 >= timeout_s:
            return False, dev
        time.sleep(EXEC_POLL_S)


def rot_error_along_path(pipe, arm_id, target_quat):
    """诊断辅助(非 helper):读当前 EE 姿态与 target_quat(XYZW)的夹角(度)。

    用于逐点采样 rot_error，检查姿态误差沿路径不发散。target_quat 缺省或读不到 → None。
    纯几何,无副作用。"""
    if target_quat is None:
        return None
    try:
        x = _as_floats(pipe.call("info", "get_xquat", {"arm_id": arm_id}), 7, "get_xquat")
    except Exception:
        return None
    return _quat_angle_deg(x[3:7], list(target_quat))


def _quat_angle_deg(q1, q2):
    """两个 XYZW 四元数的最小夹角(度)。"""
    dot = abs(sum(a * b for a, b in zip(q1, q2)))
    dot = max(-1.0, min(1.0, dot))
    return math.degrees(2.0 * math.acos(dot))
