# TODO

只列未完成工作。稳定事实写进 README/API，已完成验证写进 MILESTONES/DEVLOG。

## 当前顺序

1. 只读采集一份 head RGB-D、米制 depth、点云、本体状态和标定，保存为完整 observation record。
2. 离线调用 GraspNet 后保存原始 `/predict` 回复；显式建立模型 object ID 到 graph registry ID 的映射，并从受检的 rotation/object extent 派生排序特征。
3. 接三个真实 hard checker：reachability 检查未裁剪目标和最终残差；collision 固定并记录 K1 参数；gripper width 在米制 opening 标定完成前保持 `UNKNOWN`。
4. 把 observation、normalized candidates 和三个 certificate 冻结为第一份真实 replay，复现过滤、排序、日志和无候选路径。
5. 审查一个非特权 stage 的 gate 输入与 abort 行为，然后停下评审；得到明确允许后才连接控制。

完整 episode 稳定前，不实现 runtime backend ranking、跨阶段 `compat`、向后传播或训练。

## 离线语义质量

- 增加镜头切换和无交互片段检查，避免关键帧跨场景。
- 修复同类多对象的跨阶段 coreference，并人工复核 `insert_tubes` registry。
- 审阅 `compile_report.json` 的 unwired holes：删除不需要的洞，或给真正的控制参数增加明确 API；不能静默忽略。
- 人工抽查 goldset，分别报告 constraint precision/recall、object identity 和 stage boundary，不只报 schema pass。

完成定义：固定数据重复运行时对象 ID 和关键约束稳定，失败 sample 能从 `model_calls/` 复查。

## Perception 与候选

- 实现 head camera 的 live-to-record adapter；hand camera 在实时 EEF frame transform 明确前不接。
- 接入真实 grasp proposals，并为每个 candidate 保存原始回复、观测证据、frame、object mapping 和 hole values。
- 给 `height_fraction` 与重力相对的 `approach_tilt_deg` 写独立、可检查的派生逻辑；禁止把 camera-frame 裸 `approach_dir` 直接用于 cone 排序。
- 采集真实 point-cloud manifest 与 K1 grasp-center→runtime-EEF/TCP 标定 artifact；变换值、frame 约定和独立 evidence ref 必须一起保存，只有矩阵转 quaternion 不算完成 pose 语义转换。
- 为 graph hole 增加结构化 object anchor，并让 validator/StageProgram 检查；完成前多对象 stage 的 candidate binding 保持 `UNKNOWN`。
- 实现完整 frame transform，包括旋转；不允许只做平移或把 simulator world pose 混入。
- 对 live adapter 做最终依赖审查，确认没有 `/state`、官方 probe 或 control side effect；记录同步 render 这一只读传感副作用。

完成定义：只读主方法输入能产生多个可追溯候选，代码路径不含特权状态。

## Selection 与 binding

- 实现真实可达、碰撞和夹爪宽度 checker；异常和 `UNKNOWN` 继续 fail-closed。
- 用真实固定候选集重复反事实测试：只改变 region/cone 标签时，top-1 按预期变化；synthetic contract fixture 不作为效果数据。
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
