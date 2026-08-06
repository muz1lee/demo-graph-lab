# TODO

只列未完成工作。稳定事实写进 README/API，已完成验证写进 MILESTONES/DEVLOG。

## 当前顺序

1. 在 5090 上用同一份 `insert_tubes` graph 跑共享动作骨架的 `vanilla / local / backchain` 配对 CaP 实验，统计共享 StageProgram 合法率并保存三组 policy。
2. 检查真实 graph 是否能为每个抓取阶段回传正确的同对象下游约束；人工复核错误 coreference，不能让字符串匹配替代任务语义。
3. 把 `candidate.features.future_constraints` 的 synthetic 标签替换为真实几何/规划 compatibility result，先覆盖“当前 grasp 是否保留插入轴对齐、inside 和夹爪 clearance”。
4. 冻结同一 observation 和候选集，比较三组 top-1、下游拒绝原因、UNKNOWN 率和计算成本。
5. 只在上述选择链跑通后接真实 stage 执行，测长程成功率与第一失败阶段。

当前不做 runtime backend ranking、SHA、复杂协议、自动 recovery 或训练；算法迭代只围绕“constraint-guided CaP 是否改变并改善长程选择”。

## 离线语义质量

- 增加镜头切换和无交互片段检查，避免关键帧跨场景。
- 用真实视频重新生成并人工复核同类多对象 coreference；reviewed fixture 已固定 `tube_mid/right/left` 的初始身份，不把 fixture 当自动抽取效果。
- 审阅 `compile_report.json` 的 unwired holes：删除不需要的洞，或给真正的控制参数增加明确 API；不能静默忽略。
- 人工抽查 goldset，分别报告 constraint precision/recall、object identity 和 stage boundary，不只报 schema pass。

完成定义：固定数据重复运行时对象 ID 和关键约束稳定，失败 sample 能从 `model_calls/` 复查。

## Perception 与候选

- hand camera 在实时 EEF frame transform 明确前不接。
- 在正确 scene 上验证 Qwen 单框、SAM3 二值 mask、mask-first cloud、pixel lineage 和 assignment 的完整 artifact 链；错误或歧义样例必须 fail-closed。
- 给 `height_fraction` 与重力相对的 `approach_tilt_deg` 写独立、可检查的派生逻辑；禁止把 camera-frame 裸 `approach_dir` 直接用于 cone 排序。
- 采集真实 point-cloud manifest 与 K1 grasp-center→runtime-EEF/TCP 标定 artifact；变换值、frame 约定和独立 evidence ref 必须一起保存，只有矩阵转 quaternion 不算完成 pose 语义转换。
- `programs` 路径上同一 anchor 的 `part_center` 与 `part_axis` 已由一条 `fit_opening` 链一次发布；单 anchor `ground/segment/project` 链仍是每个 hole 一次模型调用，是否收敛到同一实现留待多 anchor 组装时一并裁决。
- 实现完整 frame transform，包括旋转；不允许只做平移或把 simulator world pose 混入。
- 对 live adapter 做最终依赖审查，确认没有 `/state`、官方 probe 或 control side effect；记录同步 render 这一只读传感副作用。
- 把失败重试改成 append-only attempt；成功前不发布 canonical assignment，失败 payload 必须进入 manifest，不能由残留目录永久锁死同一冻结 observation。

完成定义：只读主方法输入能产生多个可追溯候选，代码路径不含特权状态。

## Selection 与 binding

- 实现真实可达、碰撞和夹爪宽度 checker；异常和 `UNKNOWN` 继续 fail-closed。
- 用真实固定候选集重复反事实测试：只改变 region/cone 标签时，top-1 按预期变化；synthetic contract fixture 不作为效果数据。
- 为每个候选计算当前 graph 中 downstream constraint ref 的兼容结果；不能长期把测试 fixture 的标签当方法结果。
- 将 gate outcome 回填同一 decision record，形成完整的 selection → outcome 数据。

完成定义：每次选择能从 observation、完整候选、硬过滤证书、ranking meta 和 gate outcome 重放。

## Execution

- 保持 `PlanningOnlyRuntime` 的控制原语全部 `ExecutionDisabled`，直到当前顺序 1–6 验收。
- 获得允许后，先接单个 stage 的非特权 runtime；无候选、hole 不合法、gate `UNKNOWN` 都 abort。
- 解决抬升时物体滑脱，并删除剩余固定 sleep，统一用状态回读判断动作结束。
- `lower_stop` 的 runtime descriptor 要明确路由到非特权 contact / motion plateau，而不是自由文本猜测。
- 实现可信 retreat target solver：从当前 EEF、接近路径和碰撞检查生成候选；禁止回退到对象质心，完成前保持 Oracle 硬停。
- `reorient_held_axis` 目前只锁住 EEF 原点，爪尖随腕部旋转画弧；要做到契约写的「不平移抓取点」，先要拿到抓取点在工具系里的位置，再绕它补一段平移。

完成定义：至少一个稳定持握可重复，第一失败动作能由 decision/action/gate 日志定位。

## Evaluation

- 正式任务成功使用固定人工或 benchmark evaluator；模型提议的 acceptance 只用于阶段归因。
- 把实际 grasp point 和 approach direction 传给对应 predicate。
- 为 `carry` 设计非特权检查；检查不了继续返回 `UNKNOWN`。
- 补 `held_axis_parallel` 谓词，独立验收 `reorient_held_axis` 的后置条件。现在只有 runtime 自己在 `reorient_done` 里记腕姿残差，属于「自己验自己」，不能当验收。同时词表里缺一个可查的「持有」谓词——`reorient_held_axis` 的前置条件目前只有 runtime 侧的非特权证据，gate 侧问不出来。
- 用明显正例和负例校准 predicate，再用于任务评价。

完成定义：gate 不把 `UNKNOWN` 当通过，并区分“动作被调用”和“任务关系成立”。

## 后续：recovery

- 先做确定性换候选 recovery，再考虑 VLM 的离散修正建议。
- 所有 recovery 有次数上限；调试 seed 与评测 seed 分开。

完成定义：能与当前阶段贪心、无 recovery 基线做同候选同预算比较。
