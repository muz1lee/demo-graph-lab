"""High-level API visible to the VLM-generated policy.

设计原则:
- 洞的唯一数值来源是 rt.solve();返回值是**不透明句柄**——policy 可传递、不可读出数字,
  硬编码度量在结构上不可表达。
- 控制原语是声明式的粗粒度动作;阶段调度与重试属于 runner.run_policy,
  LLM 不写。
- 验收属于可信 runner 和 gate，不暴露给生成 policy。
"""

from __future__ import annotations


class RuntimeAPI:
    """生成的 policy 每个阶段 handler 收到的唯一对象 rt。以下签名即全部合法调用。"""

    # ---- 感知求解(填洞) ----
    def solve(self, hole_name: str):
        """求解 graph 中声明的洞,返回不透明句柄(pose/axis/point/scalar/condition)。
        只能用图里该阶段 holes 列表出现过的 hole_name。"""

    # ---- 控制原语(声明式) ----
    def approach(self, target, cone=None):
        """接近目标(target=物体名或句柄;cone=图中 approach_direction 的离散标签)。"""

    def grasp_at(self, grasp_pose, axis=None):
        """按抓取位姿句柄合爪。axis(可选)= 被抓物长轴,开合方向取其正交向
        (夹横躺的棍状物时必需;缺省退回竖直下探锁腕姿)。"""

    def lift(self, obj):
        """提起物体。"""

    def transport(self, obj, target):
        """携带物体移动到目标上方/附近(target 为句柄或物体名)。"""

    def align(self, obj, target, axis=None):
        """把 obj 与 target 按 axis 句柄对齐(预对准)。"""

    def lower_until(self, stop_condition):
        """沿当前轴下放,直到 stop_condition 句柄(runtime_condition 洞)触发。"""

    def release(self):
        """张爪释放。"""
