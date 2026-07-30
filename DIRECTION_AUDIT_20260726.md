# 方向审计与一个月冲刺方案（2026-07-26）

状态：**讨论稿；尚未据此启动新实验。**  
目的：把学生当前资产、竞品、最新 related work 和一个月内可证伪的路线放在同一张图上。

## 0. 推荐结论

不建议把论文主张写成：

> Demo2Skill：从一段演示视频拆子任务、生成 graph，再让 agent 写代码。

这个表述中的三个关键词都已拥挤：

- Demo2Code 已做“演示 → latent specification → code”；
- Neural Task Graphs 已做“单段视频 → task graph → unseen-task policy”；
- 2026 年的 semantic-geometric task graph、Graph-as-Policy、AgentChord、ASPIRE/RATs 和
  HOST 等工作分别覆盖了几何图、图执行/恢复、自改进和单视频学技能。

建议把主张收紧为：

> **Demonstration-Conditioned Constraint Programs for Code-as-Policy**：演示不是轨迹数据，而是
> 一份“任务意图契约”。系统从演示中提取必须保持的对象关系、抓取/放置自由度、抓取区域、
> 放置关系和顺序；执行期只用非特权感知生成度量候选，并把后续放置/插入约束反向传播到前面的
> grasp 选择。相同约束同时用于候选选择和阶段验证，使 coding agent 能逐节点执行和局部修复。

工作名可暂用 **Demo2Constraint**，不要急着定论文名。

一句更容易讲的 story：

> **A demonstration teaches the agent what must be preserved, not which trajectory to copy.**

这个组合仍然不是天然新颖；论文能否成立取决于能否实证两个机制，而不是取决于 graph schema：

1. **跨节点约束传播**确实能选出“稳定但会妨碍后续任务”的 grasp candidate 之外的正确候选；
2. **约束的双向编译**确实比 prose/trace 更少交互、更准确定位失败，并在新布局中复用。

一个月的主目标应是“做出这两个机制的强证据”，不是承诺公开榜第一。

## 1. 学生当前工作地图

### 1.1 没有一个统一的“学生主仓”

当前链路由四块目录拼成：

| 职责 | 位置 | 当前事实 |
|---|---|---|
| agent / RoboDojo 任务执行 | `/mnt/data/wht/kw-aspire-robodojo/knowin-skill-manager` | 当前最接近“主工作区”，但目录本身不是 git 仓库 |
| runtime | `/mnt/data/wht/kw-aspire-robodojo/runtime/knowin-world` | git HEAD `e5549fe1`，共享且 dirty |
| 视频 trace / keyframe evidence | `/mnt/data/wht/robot-subtask-seg` | 14 份 refined 目录，主目录不是 git 仓库 |
| grasp proposals | `/mnt/data/wht/graspnet_service` | 服务代码 + synthetic probe，主目录不是 git 仓库 |
| 较早的 CaP-X 复现实验 | `/mnt/workspace/wht/cap-x-re/cap-x` | git HEAD `53e9966`；不是当前试管执行主线 |

这首先是工程风险：现在无法用一个 commit 重建“视频 → 图 → agent → 执行”的结果。一个月冲刺中，
`demo-graph-lab` 应作为唯一 integration/control repo；学生目录先当只读 upstream，通过
source manifest 记录路径、commit/mtime 和输入产物，不要先大搬家。

### 1.2 视频侧已经有素材，但还没有可执行约束图

已有的 base task：

`align_blocks / deposit_coin / general_pickup / insert_key / insert_tubes /
plug_in_charger / pour_balls_into_vase / push_T / put_bottles_into_dustbin /
stack_blocks / stack_bowls`

另有 `push_T_random / stack_blocks_random / stack_bowls_random`。

`insert_tubes` 的 refined trace 已表达三次“抓取/运输/插入”的顺序，但仍只有 6 个粗段。它没有可靠地
表达：

- grasp region、approach hemisphere 和 object-to-gripper DoF；
- 抬起后是否需要重定向；
- tube axis、hole axis、pre-alignment 和 clearance；
- 每个阶段的 postcondition 与恢复域。

现有 `video_evidence` 是 2D SAM3/CoTracker 证据，明确缺 metric depth、标定、6D pose 和机器人状态同步；
不同试管的可靠跟踪率也不稳定。因此它可以提供顺序、相对区域和关系，不能直接填世界坐标。

README 中有 operation-structure 阶段，但当前没有找到对应的正式输出。实际集成终点仍是
`demonstration_bundle.json`，还没有接到 graph generator。

### 1.3 GraspNet 目前只是独立 smoke，不是当前任务能力

`/mnt/data/wht/graspnet_service/outputs/rgbd_probe_001/` 的输入是 synthetic depth/mask，
坐标系标为 `synthetic_camera_from_depth`，产出 5 个 proposals。尚未完成：

- RoboDojo/KW 真实 RGB-D 与模型分割接入；
- camera → world → robot frame 标定链；
- IK、碰撞、夹爪宽度过滤；
- 与演示 grasp 关键帧或下游 placement constraint 的排序；
- 执行与保留抓取的验证。

所以 GraspNet 应被写成**可替换的 candidate generator**，不是论文贡献，也不能把当前 smoke 写成
“已经接入 grasp planning”。

### 1.4 当前试管执行还没有抓住

学生 7 月 24 日最新 function-call 证据位于：

`/mnt/data/wht/kw-aspire-robodojo/knowin-skill-manager/experiments/
codex_harness_function_calls/`

其中 guarded rerun 使用了手写精确 xyz：

- pre-grasp 成功；
- grasp pose 成功；
- close gripper 返回 `Control failed: 1`。

本项目自己的 M1 trial 虽把 tube 抬高了 116.7 mm，但 `is_gripping_sth` 返回字符串 `"False"`，
被 `bool("False")` 错判为真；物体抬起后本已接近竖直，固定 wrist “reorient”反而把 tube axis
从 2.8° 转坏到 53.36°。因此目前既没有有效 grasp success rate，也没有“抓取 + 空中转向 + 对准”
的闭环成功证据。

### 1.5 当前 task spec 有严重特权信息

学生的 `insert_tubes_000/task.yaml` 含精确 scene/asset 路径、初始 pose、对象 ID、目标 rack pose、
predicate 和 binding。这些可以供隔离 evaluator/bring-up 使用，但不得进入 graph generator、
prompt、candidate ranker 或恢复逻辑。

当前最先要审计的不是“graph 写得够不够丰富”，而是 agent prompt 的数据流是否读取过这些字段。

## 2. zyh 竞品审计

主要仓是 `/mnt/data/zyh/BCap-X`，检查时 HEAD 为 `d88824d`，分支工作围绕 RoboMEx/AgentWorld。

它的方向是：

```text
当前 observation + instruction
  → reactive planner 产生一个 ActionIntent
  → 动态 coding-agent swarm
  → grounding / affordance / motion candidates
  → AgentWorld 视觉想象
  → selector
  → 尝试执行一个动作并重新观察
```

与我们的关系：

- **相同**：逐步闭环、graph/orchestration、candidate generation、coding agents、非特权边界；
- **不同**：它的 graph 主要是 agent workflow；不是从演示学习任务程序，也没有把演示中的
  grasp/place DoF 编译成跨节点约束。

它的工程规模明显大于我们，AgentWorld、swarm、selector、admission、event log 和测试框架都较完整。
但审计到的最新 LIBERO live run 还没有形成成功闭环：多个 planner intent 反复使用同一 observation，
selector 因 evidence schema 和 motion candidate 错误失败，没有看到 physical commit/success。

因此它是**强工程竞品、弱结果竞品**。不要复制它的大编排框架；我们应抢先拿出一个机制清楚、
可复现实验闭环。若一个月也陷进“多 agent 基础设施”，会在它最强的轴上追赶，而不是建立差异。

## 3. Related-work 结论

### 3.1 已被占据的主张

| 我们可能想讲的主张 | 直接相邻工作 | 判断 |
|---|---|---|
| 演示生成 code | [Demo2Code](https://arxiv.org/abs/2305.16744) | 已直接覆盖 broad claim |
| 单视频生成 task graph | [Neural Task Graphs](https://arxiv.org/abs/1807.03480) | 2018 年已有直接先例 |
| 人类演示生成语义-几何图 | [Semantic-Geometric Task Graph](https://arxiv.org/abs/2601.11460) | 2026 年强直接 prior |
| graph 作为 robot policy | [Graph-as-Policy](https://arxiv.org/abs/2607.05369) | 已覆盖 perception/planning/control computation graph |
| task graph + failure recovery | [AgentChord](https://arxiv.org/abs/2605.11951) | 已覆盖预编译 recovery branches |
| coding agent 自执行、自修复、skill library | [CaP-X](https://arxiv.org/abs/2603.22435)、[ASPIRE](https://arxiv.org/abs/2607.00272)、[RATs](https://arxiv.org/abs/2606.19419) | 不能把 API、debug 或 skill accumulation 当核心贡献 |
| 从演示推断几何 DoF/constraint | [Geometric Nullspace from Human Demonstrations](https://arxiv.org/abs/2103.16092) | DoF/约束本身不是新问题 |
| 从多个 grasp candidates 选 task-aware grasp | [Task-Aware Grasping](https://arxiv.org/abs/2411.14917)、[GRIM](https://arxiv.org/abs/2506.15607) | “GraspNet + VLM 选最像”本身不够新 |
| 单段/少量人类视频直接教机器人 | [HOST](https://arxiv.org/abs/2607.20033)、[HumanEgo](https://arxiv.org/abs/2605.24934)、[EgoAERO](https://arxiv.org/abs/2606.08057) | 已是非常拥挤且快速发展的主线 |
| 人类视频转机器人数据/轨迹 | [Qwen-RobotManip](https://arxiv.org/abs/2606.17846)、[Do As I Do](https://arxiv.org/abs/2606.19333)、[EgoEngine](https://arxiv.org/abs/2606.12604) | 不应与大规模数据/retargeting 正面拼 |

### 3.2 尚可防守的缝隙

我们需要同时满足以下限定，差异才比较清楚：

1. **输入是演示教出的 task intent，不是完整轨迹监督**；
2. **输出是非特权、可执行的 constraint program，不只是摘要或 graph prompt**；
3. 度量几何保留为 typed holes，只能由执行期 RGB-D/模型感知、机器人状态和规划器求解；
4. **未来节点约束反向影响当前 grasp/approach 选择**，不只做局部图像相似；
5. 同一条约束既进入 candidate scoring/planning，也进入 verifier/failure attribution；
6. 不训练新 policy，通过当前机器人 API 编译执行，并能换布局/换实例复用。

这更像“demonstration-conditioned program synthesis + constraint-coupled execution”，而不是新的视频模仿学习。

## 4. 最小方法

### 4.1 Graph 只保留任务不变量

每个节点最少包含：

- `actor / manipulated_object / target_object`
- `grasp_region`：物体归一化坐标中的区域，不是世界 xyz
- `grasp_dof`：允许/锁定的相对平移和旋转
- `approach_relation`
- `placement_relation / insertion_axis / release_condition`
- `preconditions / postconditions`
- `metric_holes`
- `evidence / confidence / provenance`

边至少包含：

- temporal order；
- carry constraint；
- resource/slot occupancy；
- collision/clearance dependency；
- future-feasibility dependency。

### 4.2 GraspNet 的正确用法

```text
RGB-D + model-predicted mask
  → grasp proposals
  → frame transform
  → IK/collision/width hard filter
  → demo-local similarity
  → downstream placement/insertion feasibility
  → execute one candidate
  → verify lift/retention/object axis
```

选择不是简单“让 VLM 看图挑最像”，而应显式记录：

```text
score(c) =
  demo_grasp_similarity(c)
  + downstream_constraint_compatibility(c, graph)
  + reachability_margin(c)
  - collision_and_reorientation_cost(c)
```

其中 hard constraint 先过滤，soft score 后排序。权重不能从 simulator task spec 偷读。

### 4.3 同一个 constraint 的两次编译

以“tube axis 与 hole axis 对齐”为例：

- action 侧：生成 pre-align orientation 与 guarded insertion direction；
- verifier 侧：重新感知 tube axis/hole axis，测相对角并决定前进、重试还是回退；
- recovery 侧：只允许改 orientation/approach hole，不重写抓取以前的整个 workflow。

如果图最后只是 10k 字符 YAML 塞进 prompt，而没有改变 candidate set、planner 或 verifier，
它不会产生论文效果；本项目 B7 已经出现“316 字符 prose 胜过完整图”的反例。

## 5. Benchmark 与“刷榜”现实判断

[RoboDojo](https://robodojo-benchmark.com/) 当前有 42 个核心仿真任务、18 个真机任务，仿真覆盖
Generalization、Memory、Precision、Long-Horizon 和 Open 五个维度。2026-07-26 网站榜单快照的
第一名是 20.07 score / 13.93% SR，说明任务仍很难；但公开远程提交页目前仍写
[Coming Soon](https://robodojo-benchmark.com/eval)，所以一个月内无法诚实承诺“公开上榜”。

当前 KW/KSM/knowin-world 运行的是 RoboDojo 任务的内部复现/适配链，并未接入官方
Isaac Sim + XPolicyLab evaluator。内部结果不能叫 RoboDojo leaderboard result。

推荐两层评价：

### 层 A：一个月必须交付的机制结果

先用已有视频资产组成 **6-task constraint suite**：

1. `insert_tubes`
2. `insert_key`
3. `deposit_coin`
4. `plug_in_charger`
5. `stack_blocks`
6. `stack_bowls`

前四个共享“task-aware grasp → reorient → pre-align → constrained placement/insertion”，后两个检查
同一 graph/runtime 是否能迁移到关系放置。`insert_tubes` 官方本身按 0/20/40/100 分阶段，
单管成功可对应第一档，但内部复现必须明确标为 internal score。

### 层 B：有余力再做的 benchmark 结果

- 接官方 RoboDojo/XPolicyLab policy adapter；
- 只使用官方允许的训练 demo 与 policy observation；
- 先跑已有视频覆盖的 10 个 base tasks，再跑全部 42 个任务；
- 官方提交开放后才谈 leaderboard。

**不要**为了“榜单”把 unsupported task 默默排除、把 internal predicate 当官方 evaluator，或把每个
任务手调的坐标称为泛化方法。

## 6. 四周执行计划与止损门槛

### Week 1：先证明机器人真的能抓、持有、重定向

交付：

- 冻结 method-visible API allowlist 与 `privileged_oracle` denylist；
- 自动 provenance/leak lint：拒绝 scene/asset path、exact initial pose、object ID、GT mask、
  evaluator target/predicate 进入 graph/prompt；
- 修正布尔类型规范化；
- 每次动作后重新观察**物体轴**，不再假设 wrist pose 等于 object pose；
- 接一个最小的非特权 tube perception → grasp candidate → IK/reach filter；
- 单管执行 `grasp → lift → retain`。

门槛：

- Day 3：一份主方法 artifact 能通过 leak audit；
- Day 7：固定协议下 grasp/lift/retain 至少 **7/10**。

止损：

- 若 Day 7 未过，不做 graph/VLM 大开发；把问题定性为 perception/executor/physics，集中修 vertical slice。

### Week 2：打通单管闭环，并验证核心 candidate 机制

交付：

- 从现有 `insert_tubes` trace 选 contact/close/lift/reorient/prealign/insert/release 关键事件；
- 生成第一张自动 graph；不可靠度量全部留 typed hole；
- 同一批 grasp proposals 比较三种排序：
  1. proposal 原始稳定性分数；
  2. demo grasp 局部相似；
  3. demo 相似 + 下游 insertion/clearance feasibility；
- 完成 `grasp → lift → conditional reorient → prealign → guarded insert attempt`。

门槛：

- Day 10：到 pre-align 至少 **6/10**；
- Day 14：单管完整成功或官方第一档等价状态至少 **5/10**；
- full mechanism 对 candidate selection 的胜出必须能在失败视频和 constraint log 中解释。

止损：

- 若人工正确 constraint graph（但仍无 GT 度量）都达不到 5/10，说明上限卡在执行，不可把失败归给视频提取。

### Week 3：从“一个 demo”变成“一个方法”

交付：

- 扩到 `insert_key / deposit_coin / plug_in_charger`，尽量复用同一组节点编译器；
- 再加 `stack_blocks / stack_bowls` 检查非插入型关系放置；
- frozen graph 下改变物体初始布局、目标布局和同类实例；
- 构造至少一组“局部最稳 grasp 与下游可行 grasp 不同”的 counterfactual 场景。

门槛：

- 至少 4 个任务无需任务专属世界坐标完成一个非零阶段；
- full method 相对 `video → prose trace` 在 stage-weighted score 上提高至少 **10 个百分点**，
  或把达到同一阶段所需的执行/agent 交互数降低至少 **25%**。

止损：

- 若 B2 prose 与 full graph 接近，停止讲“graph 优越”；把贡献收缩到 constraint-coupled grasp/execution。

### Week 4：消融、随机化、报告和 official adapter

优先顺序：

1. 固定代码后跑足 paired seeds；
2. 完成主消融和 extraction/oracle gap；
3. 对已有 random variants 跑 frozen-graph generalization；
4. 写 failure taxonomy 和可复现实验包；
5. 余下时间才做官方 XPolicyLab adapter/full sweep。

最终 go/no-go：

- **Go paper**：至少 4–6 tasks，核心机制相对强基线稳定改善，并有 random-layout/generalization 结果；
- **Go workshop/system report**：只完成单管闭环，但信息边界、graph compiler 和失败归因扎实；
- **No-go paper claim**：只有手写 pose 的单条成功视频，或只有 graph/schema、没有对照效果。

## 7. 最小实验矩阵

主对照只保留五组：

| 组 | 输入/机制 | 回答的问题 |
|---|---|---|
| B1 | instruction + 相同 runtime APIs | 语言本身够不够 |
| B2 | demo → prose trace + 相同 runtime APIs | 视频提供顺序后是否已经够 |
| B3 | constraint graph，但 grasp 只看局部 demo similarity | structured local constraints 的价值 |
| B4 | B3 + downstream constraint propagation + dual verifier | **核心机制** |
| B5 | 人工审阅 graph 上界；仍只用非特权 runtime perception 填度量 hole | 自动 extraction 损失 |

另做一个隔离 oracle run，仅用于定位 simulator/perception 上限，绝不与 B1–B5 共用 artifact。

每个结果同时报告：

- official task score（仅官方环境）或明确命名的 internal stage score；
- grasp retention / reorientation / prealign / final success 漏斗；
- scene interaction 次数、agent turns 和恢复次数；
- 第一失败节点与 violated constraint；
- 自动 graph 与 human-reviewed graph 的字段准确率；
- random layout 下 frozen-graph 成功率。

## 8. 立即的项目管理动作

1. `demo-graph-lab` 作为唯一总控目录；不要同时在四个无版本目录散改。
2. 增加 source manifest，固定每次实验依赖的学生产物路径、git commit 或目录 hash。
3. 每个 run 保存 `method_visible_inputs.json`，使“agent 到底看了什么”可审计。
4. method 与 oracle 分进程、分目录；主方法 graph 的 provenance 链出现
   `privileged_oracle` 就 fail closed。
5. 当前先不启动大 batch；先由本方案讨论确认 Week 1 contract，再跑下一次 grasp-only trial。

## 9. 这次审计后的判断

- **idea 不是 low，而是 broad framing 太旧。** “演示告诉机器人到底怎么完成任务”是真问题；
  但必须把“怎么”操作化为可执行、可验证、可迁移的 constraint program。
- **GraspNet + demo 候选选择值得做，但只能作为机制载体。** 单纯“看起来最像”已不够；
  真正该测的是未来 placement constraint 是否改变当前 grasp 决策。
- **RoboDojo 值得押。** 新、难、Precision/Long-Horizon 与我们匹配；但当前内部复现和官方榜单必须分开。
- **最快的成果路径是窄而深。** 先拿一根试管的非特权闭环，再扩共享约束族；不是继续量最后插入了几毫米，
  也不是先建一套 zyh 规模的 agent platform。
