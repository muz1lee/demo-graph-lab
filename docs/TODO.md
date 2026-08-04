# TODO

只列未完成工作。稳定事实写进 README/API，已完成验证写进 MILESTONES/DEVLOG。

## 当前顺序

1. 接只读 RGB-D/点云 adapter 和真实 grasp candidate provider。
2. 接 reachability、collision、gripper-width 三个 hard checker，先跑 planning-only。
3. 对 candidate hole value 做 type、frame、calibration 和 finite 数值校验。
4. 用固定 candidate replay 验证过滤、排序、日志和无候选失败路径。
5. 到这里停下评审；得到明确允许后才接一个非特权 stage 的控制与 gate。

完整 episode 稳定前，不实现 runtime backend ranking、跨阶段 `compat`、向后传播或训练。

## 离线语义质量

- 增加镜头切换和无交互片段检查，避免关键帧跨场景。
- 修复同类多对象的跨阶段 coreference，并人工复核 `insert_tubes` registry。
- 审阅 `compile_report.json` 的 unwired holes：删除不需要的洞，或给真正的控制参数增加明确 API；不能静默忽略。
- 人工抽查 goldset，分别报告 constraint precision/recall、object identity 和 stage boundary，不只报 schema pass。

完成定义：固定数据重复运行时对象 ID 和关键约束稳定，失败 sample 能从 `model_calls/` 复查。

## Perception 与候选

- 将真实 sensor artifact 规范成 `ObservationPacket`，只使用 typed `Proprioception`。
- 接入 grasp proposals，并为每个 candidate 保存观测证据、frame 和 hole values。
- 实现完整 frame transform，包括旋转；不允许只做平移或把 simulator world pose 混入。
- 审查 adapter 依赖调用图，确认没有 `/state`、官方 probe 或 control side effect。

完成定义：只读主方法输入能产生多个可追溯候选，代码路径不含特权状态。

## Selection 与 binding

- 实现真实可达、碰撞和夹爪宽度 checker；异常和 `UNKNOWN` 继续 fail-closed。
- 在 `solve()` 前按 graph hole schema 校验 candidate value 的类型、shape、frame 和 calibration。
- 固定候选集做反事实测试：只改变 region/cone 标签时，top-1 按预期变化。
- 将 gate outcome 回填同一 decision record，形成完整的 selection → outcome 数据。

完成定义：每次选择能从 observation、完整候选、硬过滤证书、ranking meta 和 gate outcome 重放。

## Execution

- 保持 `PlanningOnlyRuntime` 的控制原语全部 `ExecutionDisabled`，直到当前顺序 1–4 验收。
- 获得允许后，先接单个 stage 的非特权 runtime；无候选、hole 不合法、gate `UNKNOWN` 都 abort。
- 解决抬升时物体滑脱，并删除剩余固定 sleep，统一用状态回读判断动作结束。
- `lower_stop` 的 runtime descriptor 要明确路由到非特权 contact / motion plateau，而不是自由文本猜测。
- 实现可信 retreat target solver：从当前 EEF、接近路径和碰撞检查生成候选；禁止回退到对象质心，完成前保持 Oracle 硬停。

完成定义：至少一个稳定持握可重复，第一失败动作能由 decision/action/gate 日志定位。

## Evaluation

- 正式任务成功使用固定人工或 benchmark evaluator；模型提议的 acceptance 只用于阶段归因。
- 把实际 grasp point 和 approach direction 传给对应 predicate。
- 为 `carry` 设计非特权检查；检查不了继续返回 `UNKNOWN`。
- 用明显正例和负例校准 predicate，再用于任务评价。

完成定义：gate 不把 `UNKNOWN` 当通过，并区分“动作被调用”和“任务关系成立”。

## 后续：compat 与 recovery

- 完整 episode 稳定后，再实现候选间 `compat(current, next)` 和向后可行性传播。
- 先做确定性换候选 recovery，再考虑 VLM 的离散修正建议。
- 所有 recovery 有次数上限；调试 seed 与评测 seed 分开。

完成定义：能与当前阶段贪心、无 recovery 基线做同候选同预算比较。
