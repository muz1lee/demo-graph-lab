"""[phase1 · robotapi] 运动规划执行层:任务无关的 ctrl/info/reasoning 原语唯一封装。

设计依据:docs/EXECUTION.md §2.2(helper 4/5、三条硬规则)、docs/TODO.md §4 P1-02 与 §7「中」档、
docs/DECISIONS.md D-09(放行 motion planning,代替 raw IK)。本模块 P1-02 只落地两个 helper:
  4. plan_joint_path —— 调 reasoning:motion_planning_stereo,处理 mp.* 控制 token 与扁平轨迹 reshape(N×7)。
  5. execute_path    —— ctrl:qpos_move 逐航点下发 + info:get_qpos 收敛核对,绝不整段大跳。

**三条硬规则(写进 docstring 且被门禁 lint 保护,违反即失败):**
  ①没有 solve_ik:raw IK 直达在本栈零成功先例(PHASE1_M1A_STATUS §墙),motion planning 取而代之。
    kwadapter._move 的限幅伺服保留为退化路径,每次使用打 degraded=true(本模块不调它)。
  ②抓取检测信号(info 侧那个恒返回字符串 'False' 的谓词)不许进 if——bool('False') is True。
    本模块**不引用**该信号;门禁断言该谓词名在 robotapi.py 之外出现即 fail。
  ③helper 签名与两份 runtime prompt 里**不许出现任务名或图里物体名**;本模块全部参数是几何量
    (q/pose/waypoints/arm),扫描命中即 fail。

**motion_planning_stereo 真实契约(2026-08-03 5090 实测,非 PRIMITIVE_API.md 文档面):**
  服务签名 `__call__(text, data, arm_id, q_current, q_goal, q_other_arm, tcp_trajectory,
  grasp_item, planner_config, ...)`。
    - `text`  = mp.* 控制 token 列表(mp.version/mp.intent/mp.planning_mode/mp.scene_input/
      mp.scene_camera),routing/环境级选项,**不是自然语言**。
    - `data`  = [target_pos(3), target_quat(4)] 7 浮点(intent=plan 时 _parse_arm_data 必需,
      即使 joint_goal 也要给一个名义目标位姿;传空触发 "len(data) must be 7")。
    - `q_current`(必需)/`q_goal`(joint_goal 必需)/`q_other_arm`(闲臂避碰)。
  返回 `(text_out:list[str], flat:list[float])`,flat 长度 = N×7,reshape 成 N 个 7-DoF 关节航点。
  末航点 ≈ 目标。**注意 PipelineClient.reasoning() 要求返回 Mapping,而本服务返回 tuple**,
  故必须走 pipe.call("reasoning",...)(返回 wire_value 后的 tuple),不能走 .reasoning()。
  规划后端是一个内网 HTTP 服务(host/port 经 MOTION_PLANNING_URL/HOST/PORT env 配置),
  scene_input=live 时按 scene_camera 抓图内联建 TSDF;
  scene_input=cache 需先 build_map,否则 no_tsdf_cache。
"""

from __future__ import annotations

import math
import time
from dataclasses import dataclass, field

JOINTS_PER_WAYPOINT = 7

# mp.* 控制 token 常量(派生自服务枚举 MP_PROTOCOL_VERSION=1 与 MP_*_ 集合,不新增魔数)。
MP_VERSION = "1"
MP_SCENE_CAMERA_DEFAULT = "head"  # 非特权 head 立体相机;hand 模式另需 ee_xquat,P1-02 不用

# 执行期收敛核对常数,派生自 kwadapter 既有伺服容差(不新增独立魔数):
#   QPOS_CONVERGE_TOL 取 MotorNode qpos_check_tolerance=0.05 rad(kwadapter.py:51 注释),
#   即「单个关节判到位」的既有阈值;每航点最多等 EXEC_SETTLE_TIMEOUT_S。
QPOS_CONVERGE_TOL = 0.05          # rad/关节
EXEC_SETTLE_TIMEOUT_S = 8.0       # 单航点收敛墙钟上限
EXEC_POLL_S = 0.2                 # get_qpos 轮询间隔


class PlanFailed(Exception):
    """规划失败(后端 no_tsdf_cache / IK 拒解 / 传输错误)。归因字段 layer='L2_plan'。"""

    layer = "L2_plan"

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
    """从 runtime 取 PipelineClient 句柄(KWRuntime 暴露 .pipe)。"""
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

    代替 raw IK(D-09):底层调 reasoning:motion_planning_stereo。

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

    失败(后端 no_tsdf_cache / IK 拒解 / 传输错误)抛 PlanFailed(layer=L2_plan)。
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
    """逐航点下发关节目标并核对收敛,返回 ExecResult。

    底层 ctrl:qpos_move 逐点 + info:get_qpos 回读核对(EXECUTION §2.2 helper 5)。
    **绝不整段下发大跳**:MotorNode 对大幅 qpos 跳变收不敛(停在 70~80% 处放弃),故逐航点走,
    每点等到各关节残差 < converge_tol 或超时再发下一点。ctrl 是 fire-and-forget(HTTP ok=True
    不代表到位,见 PRIMITIVE_API §控制原语),唯一可信判据是 get_qpos 回读。

    waypoints : N 个 7-DoF 关节航点(PlanResult.waypoints 或裸 list[list])。
    arm       : 0/1;None 时取 rt.arm_id。
    gpos      : 可选夹爪目标(随 qpos_move 一起下发);None 时不带。
    """
    if isinstance(waypoints, PlanResult):
        waypoints = waypoints.waypoints
    pipe = _pipe(rt)
    arm_id = int(arm) if arm is not None else int(getattr(rt, "arm_id", 1))

    n_converged, per_dev = 0, []
    for idx, wp in enumerate(waypoints):
        wp = _as_floats(wp, JOINTS_PER_WAYPOINT, f"waypoint[{idx}]")
        kw = {"arm_id": arm_id, "qpos": wp}
        if gpos is not None:
            kw["gpos"] = gpos
        pipe.call("ctrl", "qpos_move", kw)
        converged, dev = _wait_qpos(pipe, arm_id, wp, converge_tol, settle_timeout_s)
        per_dev.append(round(dev, 5))
        if converged:
            n_converged += 1
        _log(rt, "execute_waypoint", arm=arm_id, i=idx, converged=converged,
             maxdev=round(dev, 5))

    reached = n_converged == len(waypoints) and len(waypoints) > 0
    _log(rt, "execute_path_done", n_sent=len(waypoints), n_converged=n_converged,
         reached=reached)
    return ExecResult(
        reached=reached,
        n_sent=len(waypoints),
        n_converged=n_converged,
        per_waypoint_maxdev=per_dev,
    )


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

    用于 P1-02 判据「沿路点 rot_error 单调不发散」的逐点采样。target_quat 缺省或读不到 → None。
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
