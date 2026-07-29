"""API 契约:生成的 policy 只准调用本文件声明的 rt.* 接口(单一真源,编译提示词引用本源码)。

设计原则(对应 RESEARCH_PROPOSAL_V2 §3/§4):
- 洞的唯一数值来源是 rt.solve();返回值是**不透明句柄**——policy 可传递、不可读出数字,
  硬编码度量在结构上不可表达。
- 控制原语是声明式的粗粒度动作;tick 级控制/重试/回退属于可信 runner(fakerun.run_policy),
  LLM 不写。
- 验收与动作同源:gate 由 runner 拿图里的 acceptance 调 rt.verify(),policy 无法伪造成功。
Phase 1 时本契约的实现从 FakeRuntime 换成 knowin_sim_v2 适配器,policy 代码不变。
"""

from __future__ import annotations


class Runtime:
    """生成的 policy 每个阶段 handler 收到的唯一对象 rt。以下签名即全部合法调用。"""

    # ---- 感知求解(填洞) ----
    def solve(self, hole_name: str):
        """求解 graph 中声明的洞,返回不透明句柄(pose/axis/point/scalar/condition)。
        只能用图里该阶段 holes 列表出现过的 hole_name。"""

    def residual(self, constraint: dict):
        """感知当前场景,返回该约束的残差句柄(供阶段内修正;Phase 0 fake 恒小)。"""

    # ---- 控制原语(声明式) ----
    def approach(self, target, cone=None):
        """接近目标(target=物体名或句柄;cone=图中 approach_direction 的离散标签)。"""

    def grasp_at(self, grasp_pose):
        """按抓取位姿句柄合爪。"""

    def lift(self, obj):
        """提起物体。"""

    def transport(self, obj, target):
        """携带物体移动到目标上方/附近(target 为句柄或物体名)。"""

    def align(self, obj, target, axis=None):
        """把 obj 与 target 按 axis 句柄对齐(预对准)。"""

    def lower_until(self, stop_condition):
        """沿当前轴下放,直到 stop_condition 句柄(runtime_condition 洞)触发。"""

    def push(self, obj, contact, toward):
        """非抓取推动:在 contact 句柄处接触,朝 toward 句柄方向推。"""

    def release(self):
        """张爪释放。"""

    # ---- 验证(runner 也会用;policy 内可用于阶段内自检) ----
    def verify(self, constraint: dict) -> bool:
        """按图中的约束字典判定当前场景是否满足(与 gate 同源)。"""
