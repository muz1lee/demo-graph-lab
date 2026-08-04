# TODO

只列当前方法需要的工作。完成项从本文件删除，稳定事实写进 README 或 milestone。

## 当前顺序

1. 接入真实 grasp candidates，并记录原始候选、过滤原因和最终选择。
2. 实现独立的非特权 runtime，方法路径不读取 simulator `/state`。
3. 在 `insert_tubes` 上取得可重复的稳定抓取和完整 episode。
4. 只有前三项完成后，才实现候选之间的下游兼容性检查。

## Demo 与约束图

- 增加镜头切换和无交互片段检查，避免跨场景关键帧。
- 修复同类多对象的跨阶段 coreference；`insert_tubes` 当前金标已记录错误案例。
- 让 `holds=throughout/at_end` 的提取和验证更稳定。
- 人工抽查一部分 goldset，记录标注分歧。

完成定义：固定数据上重复抽取得到稳定阶段和对象 ID；关键约束的 precision/recall 分任务报告，不只给总体平均。

## Policy

- 给高层 API 的每个方法补一个最小编译和 dry-run 测试。
- 需要执行未经检查的外部 policy 时，再增加进程隔离；当前 AST 检查只服务于本项目生成代码。

完成定义：缺 handler、未声明 hole、未支持 API 和数字字面量都会在动作执行前失败。

## Perception

- 定义最小观测：对象 pose/axis/extent、实例证据、相机标定和 grasp proposals。
- 接入真实 RGB-D/点云与候选源。
- 实现完整 frame transform，包括旋转，而不是只做平移。

完成定义：非特权观测可以为一个真实任务生成可规划候选，调用图中没有 `/state` 或官方 probe。

## Selection

- 将真实候选送入可达、碰撞和夹爪宽度硬过滤。
- 将 `rank_by_region` 与 `rank_by_cone` 接到真实候选，不再只测试手写样例。
- 固定候选集做反事实测试：改变 demo 标签时 top-1 应按预期变化。
- 在完整 episode 稳定后，实现相邻阶段候选的 `compat` 和向后检查。

完成定义：每个选择都能追溯到候选 ID、硬过滤结果、示范约束和下游兼容结果。

## Execution

- 解决抬升时物体滑脱。
- 删除剩余固定 sleep，统一用状态回读判断动作结束。
- 在非特权接口稳定后，把 `OracleRuntime` 的状态读取与运动控制拆成独立组件。
- 实现非特权 runtime；Oracle 只保留集成调试用途。

完成定义：至少一个稳定持握可以重复，完整 episode 的第一失败动作能从日志定位。

## Evaluation

- 把实际 grasp point 和 approach direction 传给对应 predicate。
- 为 `carry` 设计非特权检查；无法可靠检查时继续返回 `UNKNOWN`。
- 用明显正例和负例校准每个 predicate，再用于任务评价。

完成定义：gate 不把 `UNKNOWN` 当通过，且能区分“动作执行了”和“任务关系真的成立”。

## Recovery

- 先实现失败后换候选，再考虑 VLM 修正。
- VLM 修正只能选择允许参数和离散方向，不能重写整个 workflow。
- 调试随机种子和评测随机种子分开。

完成定义：恢复动作有次数上限，能说明修改了什么、依据哪条残差，并和无恢复基线比较。
