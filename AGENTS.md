# demo-graph-lab 项目方案

本文件只定义项目长期稳定的目标、方法边界和协作约定，不记录当前进度、实验结论或 next todo。

开始任何工作前必须依次读取：

1. `AGENTS.md` §1–§7：方法边界、GT 防火墙、API 原则与验证纪律（长期稳定，路线变更不豁免）；
2. `docs/PROPOSAL.md`（2026-07-29）：当前执行路线的权威来源，**取代 v1
   `docs/archive/PROPOSAL_v1.md`**（取代关系由 `docs/PROPOSAL.md:4` 自声明）；
3. `harness/PHASE0_ROUND2.md`、`harness/PHASE1_M1A_STATUS.md`：最近一轮实测数字与当前阻塞；
4. `docs/archive/ALGORITHM_PLAN.md`：方法假设与四阶段拆解；
5. `docs/PROGRESS.md`：历史实验总账（⚠️ 更新纪律未被执行，见 §8.5）。

完整文档花名册与每份文档的权威范围见 §8.1；逐份阅读顺序与禁读清单见 §8.2。

用户当前明确指令优先于上述文档。动态状态一律维护在 `docs/PROGRESS.md`，不要写回本文件。

## 1. 项目定位

本项目属于“研究先行、未来迁移”工作线：先研究 coding agent 如何从演示中获得可执行的
manipulation 约束，以后再迁移到自家机器人基础设施。它不是公司工程部署项目，也不与
k1-scene 等项目共用目标。

项目的核心问题是：

> 能否把演示视频转成带约束的子任务图，再由 coding agent 结合执行期感知，把图逐节点编译为
> 可执行、可验证、失败后可局部恢复的机器人策略？

演示不是用来复现逐帧轨迹，而是用来提供任务结构和关系；执行期感知负责把关系落到当前场景的
度量位姿。

## 2. 方法总览

完整管线分为五层：

```text
演示视频
  → 关键事件与子任务 trace
  → 带 typed holes 的约束图
  → 执行期感知/规划器填洞
  → coding agent 逐节点编译、执行、验证和局部恢复
```

### 2.1 演示证据层

从视频中提取：

- 子任务边界与执行顺序；
- actor、操作对象和目标对象；
- 抓取发生在物体的哪个相对区域；
- 抓取前后的 approach 与重定向关系；
- 放置/插入的目标关系、轴、槽位和资源顺序；
- 支撑上述判断的关键帧、时间范围、置信度和 provenance。

均匀抽帧只是原始证据，不等于关键帧。关键帧应围绕状态跃迁选择，例如接触、闭爪、离面、
重定向、预对准、插入和释放。

### 2.2 约束图层

图由子任务节点和跨节点边组成。每个操作节点至少要显式表达四类核心约束：

1. 抓取的自由/锁定 DoF；
2. 放置或插入的自由/锁定 DoF；
3. 抓取区域或抓取位置；
4. 放置点、目标区域或插入轴。

边表达顺序、资源互斥、对象依赖、carry constraint 和 collision avoidance。图应描述
“什么关系必须成立”，而不是把一次场景中的世界坐标伪装成可泛化策略。

每条约束必须带来源和依赖链。主方法允许的 provenance 包括：

`demo_video / task_instruction / runtime_perception / generic_prior / derived`

其中 `derived` 必须列出 `derived_from`，并继承上游信息的权限等级。`privileged_oracle` 可以作为
上界或诊断标签存在，但含有该标签或依赖它的字段不得进入主方法生成的图、agent prompt 或执行决策。

### 2.3 Typed hole

视频不能可靠提供的度量量应保留为 typed hole，而不是猜一个常数。typed hole 至少包含：

- 类型、shape、单位和坐标系；
- 合法搜索域；
- 候选求解器；
- 求解所需输入；
- 运行时验证方式；
- 失败后的有界恢复策略。

典型 hole 包括 6D grasp pose、精确目标 pose、插入深度和接触阈值。

### 2.4 图编译与执行

coding agent 不一次生成整段不可观察的长脚本，而是逐节点闭环：

```text
读取节点约束
  → 填充当前节点的 holes
  → 生成/选择动作
  → 执行
  → 检查 postcondition
  → 成功后进入下一节点，失败则只在允许域内恢复
```

每条约束应尽量双向编译：

- 正向成为动作参数或规划约束；
- 反向成为 postcondition、verifier 或运行时断言。

恢复反馈必须指出“哪个节点的哪条约束失败、哪些参数仍可调整”，避免无边界地重写整个 workflow。

## 3. 信息边界

单目、无标定或遮挡严重的视频适合提供拓扑与相对关系，不适合直接承诺度量级 3D 真值。
仿真只是执行与评测后端，不能成为 graph generator 的答案库。项目采用与真机一致的
**observability contract**：一个量是否可用取决于它通过什么观测路径获得，而不只取决于它的数值。

主方法可见的信息只有：

- 演示视频中的关系、顺序、相对抓取区域和任务偏好；
- 任务指令中的目标语义，但不包括仿真为该实例预填的精确答案；
- 运行时传感器与感知 API 的输出，例如 RGB-D、点云、检测/分割、尺寸估计、当前 6D pose/DoF
  估计、轴向估计和 grasp proposals；
- 机器人自身状态、动作反馈以及从上述允许信息推导出的结果；
- 与具体仿真实例无关、明确披露的通用先验。

主方法禁止直接或间接读取：

- scene/asset library、USD/URDF/MJCF、mesh、碰撞体、预存 affordance 或资产标注；
- simulator 中对象的精确 pose/DoF、AABB、尺寸、body/instance ID、ground-truth mask、接触状态；
- task/evaluator 内预存的孔位、目标坐标、成功状态或其他答案；
- 由上述 privileged 数据计算后换名包装的“感知结果”。给 ground truth 套一个 perception API
  名称仍然属于泄露。

因此，图可以表达“夹持试管中段”“试管长轴对齐孔轴”以及对应 typed hole，但不能预填
scene library 中的试管长度、精确世界位姿、物体系抓取矩阵或孔心坐标。这些量必须由执行期感知
和规划器估计、生成并验证。

oracle 信息只允许用于三类隔离用途：评测、基础设施 sanity check、方法上界/故障归因。其输入、
产物和指标必须标为 `privileged_oracle`，与主方法的 graph/prompt/run 分目录保存，不能回流到
候选生成、候选排序、动作选择或恢复。自动校验应拒绝任何 provenance 依赖链中出现
`privileged_oracle` 的主方法图。

## 4. API 与抽象层

机器人 API 是执行基座，不是论文贡献本身。接口设计可以参考 CaP-X，但不整套照搬。

稳定的 API 原则：

- 底层能力、通用几何工具和任务专属 skill 必须分层；
- 每个接口明确参数 shape、dtype、单位、frame、四元数顺序和失败语义；
- 坐标系转换是独立、可测试的纯函数；
- 允许用薄适配器暴露已有能力，但薄适配器不得偷偷加入任务策略；
- perception API 必须返回观测值、置信度和可追溯的 sensor evidence；不得用普通接口名包装
  simulator exact state。为了 bring-up 暂时使用的 oracle API 必须显式命名并从主方法 allowlist 移除；
- agent 必须能看到完整契约、可用范围和失败信息；
- 高层 pick/place 包装可用于基础设施 sanity check，但主方法不能依赖它隐藏约束推理。

引入一个 API 前必须回答：

1. 它属于感知、几何、控制、验证还是任务 skill？
2. 它填充约束图中的哪个 typed hole？
3. 它引入了什么人工先验？
4. 失败能否被观察并归因？

## 5. Grasp candidate 的位置

GraspNet 或其他 grasp planner 是 grasp-pose hole 的候选求解器，不直接决定最终任务策略。

推荐保持三层分离：

1. `generate_candidates`：从传感器深度、相机参数和感知模型产生的实例 mask 生成多个 6D grasp
   与原始分数，主方法不得使用 simulator instance ID 或 ground-truth mask；
2. `filter_candidates`：按可达性、碰撞、夹爪宽度和图中的硬约束过滤；
3. `rank_from_demo`：结合演示抓取关键帧，对剩余候选的抓取区域、approach 和 DoF 相似度排序。

必须保留原始候选、坐标变换、过滤原因和最终选择依据。若必要输入缺失，应把 hole 标为未解，
不能静默退化成手写 pose。

## 6. 验证与失败信用分配

项目必须区分以下五个层面的成败：

1. executor/机器人基础设施是否具备动作能力；
2. 约束图是否包含完成任务所需的信息；
3. coding agent 是否正确编译并执行了图；
4. 自动视频提取是否恢复了正确图；
5. task predicate/verifier 是否正确评价真实结果。

workflow 无报错执行完不等于任务成功。评价应同时保留：

- 动作阶段结果；
- 关键状态的视觉证据；
- 可用的几何/接触 verifier；
- 最终 task predicate；
- 第一失败节点和违反的约束。

谓词和 verifier 应先用明显正例与明显负例做回归，证明能区分目标状态，再用于评价策略。
不能为了让某次 trial 通过而事后移动阈值。

## 7. 研究评价原则

实验设计要把不同误差源拆开：

- oracle 图用于测执行与编译上界；
- 自动提取图与 oracle 图的差距衡量提取损失；
- 文本 plan 与约束图的差距衡量结构化约束的边际价值；
- 固定图后的场景扰动衡量关系表示能否泛化；
- 失败后的局部恢复效率衡量约束图能否改进信用分配。

每次实验必须先写可证伪问题和验收标准。负面结果保留原始产物，不覆盖、不事后改指标。
若发现指标错误，应发布更正并保留旧目录。

Demo2Code、CaP-X 及相关工作构成强基线，因此“演示到代码”或“结构化中间表示”本身不能直接
作为新颖性结论。潜在贡献必须由机制和实验支持，例如几何 typed holes、约束双向编译、
跨节点资源约束或失败信用分配。新颖性判断与最新文献结论记录在 `docs/archive/ALGORITHM_PLAN.md` 或
专门的研究笔记中，不写在本文件。

## 8. 产物与上下文管理

### 8.1 文档花名册与权威范围（2026-07-30 校订；文件存在性经盘上 `ls` 核实）

| 文档 | 权威范围 | 状态 |
|---|---|---|
| `AGENTS.md` §1–§7 | 方法边界、GT 防火墙、API 原则、验证与研究评价纪律 | ✅ 有效 |
| `AGENTS.md` §8–§9 | 文档花名册、阅读顺序、产物纪律、仓库拓扑 | ✅ 2026-07-30 本次校订 |
| `AGENTS.md` §10 | 代码边界（`components/` 快照、`method/`、`adapters/`） | ✅ 有效 |
| `docs/PROPOSAL.md` | 当前研究主张、Phase 0/1/2 划分、Phase 0 验收门（§5.4）、infra 拓扑（§7） | ✅ 有效（2026-07-29） |
| `harness/README.md` | harness 目录约定、词表/提示词/金标/素材来源 | ✅ 有效 |
| `harness/PHASE0_ROUND1.md` | Phase 0 v0.1 提取器结果、系统性错误谱系（A–F）、编译步首轮 | ✅ 有效（历史轮次；病因分类仍是 v0.3 backlog 依据） |
| `harness/PHASE0_ROUND2.md` | Phase 0 v0.2 结果与**验收门终判** | ✅ 有效，Phase 0 最新一轮（2026-07-30） |
| `harness/PHASE1_API_PLAN.md` | Phase 1 method-visible 感知 API v1（12 个）、适配器架构、防火墙细则、bring-up 阶梯 | ✅ 有效（2026-07-30） |
| `harness/PHASE1_M1A_STATUS.md` | Phase 1 M1a 现场状态、reach 墙根因与剩余阻塞 | ✅ 有效，Phase 1 最新（2026-07-30） |
| `harness/DESIGN_GRASP_AND_LOOP.md` | 抓取候选三层漏斗、pose-in-hand、LLM 的三个合法工位 | ✅ 有效（2026-07-30）；改的是方法设计，不只是实现 |
| `harness/goldset/RUBRIC.md` | 金标标注口径 | ✅ 有效 |
| `docs/reference/constraint_graph_schema.md` | 约束图 schema v0.2 | ✅ 有效 |
| `docs/reference/PRIMITIVE_API.md` | 现有 ctrl 原语的 USABLE 参数面 | ✅ 有效；Phase 1 ctrl 映射依据（`harness/PHASE1_API_PLAN.md:52`） |
| `docs/archive/DIRECTION_AUDIT.md` | 竞品与占位审计 | ⚠️ 自标「讨论稿」，但被 `docs/PROPOSAL.md:7` 列为关联文档 |
| `docs/archive/ALGORITHM_PLAN.md` | 方法假设与四阶段拆解 | ⚠️ 2026-07-26 后未修订，与 v2 的 Phase 划分未对齐 |
| `docs/PROGRESS.md` | 历史实验总账（B7、slotgeom、D1–D5、wht 动态的原始证据路径） | ⚠️ stale，见 §8.5；「唯一动态总账」的**制度**位置不变 |
| `docs/archive/PROPOSAL_v1.md` | v1 路线 | ❌ 执行策略已被 v2 取代（`docs/PROPOSAL.md:4`） |
| `docs/archive/PLAN.md`、`docs/archive/MILESTONES.md` | 首月迁移计划与里程碑框架 | ❌ 部署拓扑已作废（仍写 1022/1024，见 §9.2） |
| `README.md`、`docs/SECURITY.md` | 对外说明与安全边界 | ⚠️ 仍含过时 1022/1024 边界，待按 §9 同步修订 |

### 8.2 新窗口阅读顺序（硬性）

1. 本文件 §1–§7 —— 方法边界与 GT 防火墙，任何路线变更都不豁免；
2. `docs/PROPOSAL.md` §0（主张与北极星）、§5（Phase 0 范围与验收门）、§6（Phase 1/2
   预告）、§7（infra 与工作方式）；
3. `harness/PHASE0_ROUND2.md` —— Phase 0 最新数字与终判；需要错误谱系与改进杠杆时回读
   `harness/PHASE0_ROUND1.md`；
4. `harness/PHASE1_M1A_STATUS.md` —— 当前卡在哪、有哪些待裁决选项；
5. 要动 API / 适配器 / 抓取选择时补读 `harness/PHASE1_API_PLAN.md` 与
   `harness/DESIGN_GRASP_AND_LOOP.md`；
6. `docs/PROGRESS.md` —— 只当**历史证据库**查（B7、slotgeom、D1–D5、wht 动态），不要当作当前状态；
7. `docs/archive/ALGORITHM_PLAN.md`、`docs/reference/constraint_graph_schema.md` —— 需要方法层或 schema 细节时查。

❌ 不得以 `docs/archive/PROPOSAL_v1.md`（v1）、`docs/archive/PLAN.md`、`docs/archive/MILESTONES.md` 作为当前路线依据。

### 8.3 run 产物、里程碑更新与并发纪律

每个实验 run 应保存冻结后的 task spec、graph、workflow、API contract、代码版本/dirty 状态、
seed、日志、视频或关键帧、指标和简短报告。详细 trial 数据放在 run 目录，不堆进顶层文档。

每个里程碑结束后由主 agent 更新 `docs/PROGRESS.md`，至少写清：

- 更新时间和一句话状态；
- 本轮动作与结果；
- 证据路径；
- 被支持或推翻的假设；
- 当前唯一下一步；
- 是否仍有任务在运行。

subagent 不直接编辑 `docs/PROGRESS.md`，只向主 agent 返回证据，避免并发覆盖。
### 8.4 结果标注纪律（针对 Phase 0/1 现有产物）

- **Phase 0 的 P/R 是提取质量，不是机器人成功率。** `harness/PHASE0_ROUND2.md:16` 的 micro 合计
  P=0.931 / R=0.865（v0.1 为 P=0.897 / R=0.777，`harness/PHASE0_ROUND1.md:15`）衡量的是自动提取
  的约束集对人工金标的查准/查全；两道门 P≥0.7、R≥0.8 于 2026-07-30 判定通过
  （`harness/PHASE0_ROUND2.md:35-39`），**全程不涉及机器人执行**。同一节记录歧义对门 ❌ 未达、
  被改判为移交「素材构造」任务，引用时不得只报通过项。
- **金标是 bring-up 级标注，未经 PI 复核。** 标注者为 Claude / Fable
  （`harness/PHASE0_ROUND1.md:3`、`harness/PHASE0_ROUND2.md:4-5`），待复核争议点见
  `harness/PHASE0_ROUND1.md` §4 与 `harness/PHASE0_ROUND2.md` §5；论文级金标须 PI 抽查复核。
- **编译步的「5/5 全绿」是 fake 干跑 + AST 静态检查，不是执行效果。**
  `harness/PHASE0_ROUND1.md:64-73`：绿的是 AST 零违例、`harness/fakerun.py` 干跑通过、注入 gate
  失败后的重试分支恢复正确。任何转述都必须保留「fake 干跑」四字。
- **Phase 1 M1a 的 episode 全部跑在 oracle 模式，产物必须标 ORACLE。** 求解走 `GET /state` 实体
  位姿（`harness/PHASE1_API_PLAN.md:20`，仅 M1a，只作集成测试与上界）。截至 2026-07-30 03:40：
  端到端链路可重复运行，但**真实抓取次数为 0**（`harness/PHASE1_M1A_STATUS.md:20-21`）；
  stack_bowls stage 0-2 的 "passed" 是平凡真检查放行、物体没动
  （`harness/PHASE1_M1A_STATUS.md:32-33`）。此类结果一律不得表述为「任务成功」。
- 2026-07-30 上午 reach 墙拆除（幽灵自碰 0 次、右臂前伸 0.24→0.678 m，
  `harness/PHASE1_M1A_STATUS.md:1-8`）是 IK / 碰撞模型层面的结果；它是否已带来任何成功抓取，
  **未核实**。

### 8.5 动态真相源与其纪律缺口（2026-07-30 盘上核实）

`docs/PROGRESS.md` 仍是制度上唯一的动态总账，这条不改；但必须同时记录它当前没有被执行：

- `docs/PROGRESS.md:4` 自称「最后更新 2026-07-27 12:35」，而正文最新条目是 2026-07-28 的 wht 动态
  （`docs/PROGRESS.md:62`）——自述时间戳本身已失准。
- 全文 215 行中，`Phase 0`、`Phase 1`、`5090`、`harness/` 的匹配数**均为 0**：Phase 0 两轮结果、
  Phase 1 M1a 状态、5090 迁移全部没有回流。
- 同期实际产物只被 harness 文档引用：`harness/runs/` 19 个 run 目录、`harness/goldset/` 10 份金标
  JSON + 11 份 `.md` 说明。
- **因此在补记完成前，`harness/` 下四份阶段文档是 Phase 0/1 事实上的总账**，新窗口必须按 §8.2
  读它们，不能只读 `docs/PROGRESS.md`。
- 修复方向（不在本次补丁范围）：把 Phase 0 两轮结果、Phase 1 M1a 状态与 5090 拓扑摘要回写
  `docs/PROGRESS.md`，恢复「每个里程碑更新一次」的节奏，并同步 §8.1 表中 ⚠️ 行的状态。

## 9. 项目环境与改动安全

### 9.1 仓库拓扑（2026-07-30 现状；remote 与跟踪分支经 `git remote -v` / `git branch -vv` 核实）

本仓对外名是 **demo-graph-lab**（不是 ksm）。当前拓扑三点：

- **主仓 = 内网 Gitea 私有仓**（remote 名 `gitea`；主机与路径见 `git remote -v` 与
  `docs/PROPOSAL.md:211`，不抄进本文件）。本地 `main` 跟踪 `gitea/main`，权威历史在此。
- **实验机 = 5090 服务器**（主机与账号登记在 `docs/PROPOSAL.md:210`）。Phase 0 harness 与
  Phase 1 的 sim / pipeline 都跑在这台；5090 侧用 mac 转发身份（`ssh -A`）执行 `git pull`，
  rsync 只作兜底。
- **GitHub 远端（remote 名 `origin`）已降级为历史备份，不再维护**：不再向它 push，也不得把它
  当作同步枢纽或权威历史。⚠️ 随之失效的还有本节旧版前提「本仓是公开、净化后的
  source-of-truth」——主仓已是内网私有仓；GitHub 侧仓库当前可见性**未核实**，公开性假设需 PI
  裁决后再写回。在裁决前，涉密信息按 §9.4 的最严口径处理。

### 9.2 已作废的旧边界（保留记录以免重犯）

- 「唯一工作树 = 1022 `/mnt/data/wenqian/demo-graph-lab`」**已作废**：主战场自 2026-07-29 起是
  5090（`docs/PROPOSAL.md:209-211`）。1022 与学生工作区降为**只读 upstream**，仅经
  source manifest 登记后拉素材（`docs/PROPOSAL.md:213`、`harness/README.md:9`）。
- 1024 NAS 基础仓 `/mnt/nas/knowin_sim/sim_workspace/` 的禁令**继续有效**：可只读借用其中的数据
  （如 `knowin-world-data`）与既有 venv；**禁止**写入、部署、改配置或启停其服务。历史上曾误把
  工作副本放进该树，操作指引不得再指向那里。
- ⚠️ 同一段过时的 1022/1024 边界文本在仓内仍有多处副本：`README.md:16,18`、`docs/PROGRESS.md:9-14`、
  `docs/SECURITY.md:18,20`，另有 `docs/archive/PLAN.md:164-165`、`docs/archive/MILESTONES.md:30,161`、
  `experiments/insert_tubes/README.md:3-4` 及 `components/` 下若干 README。本次只改本文件，其余
  需单独补丁同步；未同步前一律以本节为准。

### 9.3 Git 同步工作流（mac ↔ 内网 Gitea ↔ 5090）

mac 负责编辑与 commit，Gitea 持有权威历史，5090 只消费。同步走：

1. **在 mac 编辑并 `git commit`**（本仓即工作副本）；
2. **`git push gitea main`** —— 只推内网 Gitea，不推 `origin`；
3. **5090 侧 `git pull`**：用 `ssh -A` 从 mac 转发身份登录后拉取（用 `IdentitiesOnly` 钉住身份，
   避免用错 key）；Gitea 不可达时才用 rsync 兜底，排除 `.git`、`runs/`、`knowin-world/`、
   `venvs/`、密钥与 `configs/local/`，且不得对目标盲目用 `--delete`；
4. **收工前核对 5090 的 HEAD 与 `gitea/main` 一致**，确认没有只存在于 5090 的未推送工作。

`gitea/main` 是唯一权威历史。`origin`（GitHub）是只读历史备份：**不 push、不用它做对齐**。

### 9.4 密钥与运行时 checkout

具体主机、账号、端口和密钥只写入被 Git 忽略的 `configs/local/` 与 5090 侧 `.env`（600 权限），
不写进本文件或 example config；§9.1 的「公开性未核实」不构成放宽理由。若存在独立 runtime
checkout，必须与本仓使用同一 Git commit，且仍不得落在 §9.2 所列 1024 基础仓路径内作为可写部署。

Knowin World 是外部、共享且可能 dirty 的依赖，不作为本仓子目录、submodule 或 vendored
源码。开发 run 可以记录依赖的 dirty diff digest，但必须标为 non-golden；正式评测只允许
clean、pinned 的 runtime 和 data revision。

未知改动一律视为用户或其他人的工作，不覆盖、不回滚、不顺手清理。敏感配置和密钥不得打印、
复制到报告或提交。迁移只允许显式 allowlist，禁止 `git add .`。各 agent 不得并发编辑同一文件
或覆盖同一 run 目录，subagent 不直接更新 `docs/PROGRESS.md`。

## 10. 代码边界

`components/` 保存 WHT 已有实现的字节级快照；首次导入不得混入算法修改。新方法只写在
`method/demo_graph/`，外部系统接入只写在 `adapters/`。若后续确实需要修改 WHT 组件，必须在
独立 commit 中说明原因、保留 upstream digest，并先证明 adapter 无法满足需求。

主方法生成 Python node policy。已有 KW YAML generator 作为 legacy baseline 原样保留；YAML
可以作为 Knowin World 内部运输格式，但不是方法要求的输出。生成代码运行在无网络、无仿真
数据挂载的隔离进程，只能通过 allowlisted broker 调用受信任的 perception/control API。
