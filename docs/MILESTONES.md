# 里程碑

里程碑按依赖推进，不绑定日期。fake dry-run、Oracle 调试、非特权执行和完整任务成功必须分开报告。

## 仓库结构清理

状态：完成。

- 核心代码位于 `src/demo_graph_lab/`；
- README、API、TODO、里程碑和单一 DEVLOG 各司其职；
- 没有旧实现双树或重复状态文档；
- 单测与两个 CLI 的 `--help` 可运行。

## 离线语义 workflow

状态：完成首个真实视频的离线 workflow 闭环。

- backend 调用保存 request refs、raw、parsed、validator、模型、耗时和成本；
- stage / registry / constraints / holes 都有严格 schema；
- final graph 必须完整对齐 stage manifest；部分 graph、空语义、越界证据帧和时序冲突都会失败；
- 无效 sample 不投票，同名阶段只传播严格多数约束；
- backend 输出 `StageProgram`，可信 compiler 确定性生成 Python；
- `insert_tubes` 完成 video → graph → program → fake dry-run，具体证据见 `docs/DEVLOG.md`。

注意：这证明 workflow 可运行，不证明自动抽取语义已经达到论文质量，也不证明机器人执行成功。

## 在线 planning-only scaffold

状态：完成。

- `ObservationPacket` 和 `Proprioception` 不接受任意 robot-state 字段；
- recorded observation/candidate adapter 使用闭合 schema，candidate data 是 immutable、finite、JSON-safe；
- recorded GraspNet `/predict` reply 只有在米制 point-cloud manifest 和带独立 evidence artifact 的 grasp-frame→runtime-EEF 变换存在时才转换为 pose envelope；candidate provenance 保留变换数值、frame、单位和四元数约定，不会发请求或猜排序/碰撞结论；
- candidate 必须绑定同一次 observation，并通过 hole type、shape、frame、calibration 和 graph object 校验；
- typed binding 失败时物理 checker 不运行，`solve()` 还会再次校验；
- reachability / collision / width 缺失、异常或 `UNKNOWN` 均 fail-closed；
- region 主、cone 次、candidate ID 最终 tie-break；
- decision JSONL 记录 observation refs、候选、证书、ranking meta 和选择；
- synthetic fixed replay 只过滤一次，并让 demo/no-demo 共用 accepted set；
- opaque handle 不暴露数值，所有控制原语抛 `ExecutionDisabled`。

注意：这只是合约与 synthetic replay 脚手架，当前没有 live sensor adapter、真实 hard checker 或合格的真实 replay。

## 真实候选链

状态：合约已完成，真实数据链未完成。

已完成：

- 严格 observation/candidate record schema；
- 实际 GraspNet raw response 的无网络 normalization adapter；
- typed-hole binding 与 StageProgram required-hole 接线；
- synthetic demo/no-demo replay 和 CLI。

验收条件：

- 从真实 RGB-D/点云生成多个 grasp candidates；
- 三个 hard checker 给出可追溯证书；
- candidate hole values 通过 type/frame/calibration 校验；
- 固定 replay 能复现 top-1 和无候选失败。

止损：候选源不能覆盖可行抓取时，先修候选生成，不评估排序。

## 非特权 Runtime

状态：执行未开始；planning-only 已完成。

验收条件：

- adapter 调用图没有 simulator `/state`、精确 AABB、官方 probe 或隐藏 control；
- 单 stage 的 solve → control → gate 全部使用非特权输入；
- hole 不合法、planner 失败和 gate `UNKNOWN` 都 abort；
- 同一 policy 可在 Oracle 和非特权 runtime 运行，但方法指标只取后者。

## 完整 Episode

状态：未完成。尚未在本轮下发任何仿真或机器人动作。

验收条件：

- `insert_tubes` 至少出现一次可重复的稳定持握；
- 抓取、抬升、搬运、对齐、下放、释放、退离都有可观测结果；
- 至少一次非特权完整 episode 达到固定任务 predicate；
- 失败报告包含第一失败阶段、候选、约束、动作和证据。

止损：单次成功不扩任务；先固定场景重复，排除偶然成功和 gate 误判。

## 下游可行性选择

状态：未开始，依赖完整 episode。

验收条件：

- 定义 `compat(current, next)` 的输入、成本和 `UNKNOWN`；
- 构造当前可行但后续必死的候选对；
- 比较当前阶段贪心与向后传播；
- 消融区分候选质量和传播本身的收益。

## 论文实验

状态：未开始。

- 运行前写定主假设、任务、指标、调试集和评测集；
- 至少比较无 demo、文本 plan、当前阶段约束和下游约束链；
- 分开报告抽取、候选覆盖、执行、完整任务和失败归因；
- Oracle、fake dry-run 和非特权结果绝不混报。
