# DECISIONS：关键裁决与理由

- 建立日期：2026-07-30
- 用途：记录「为什么现在是这样」。**只记裁决，不记进度**——进度看 `PROGRESS.md` 与 `harness/PHASE0_ROUND2.md` / `harness/PHASE1_M1A_STATUS.md`。
- 读法：先看 §0 索引表，需要理由再往下翻对应条目。
- 纪律：改动本文件已生效的裁决前，先确认该裁决的「理由」是否已被新证据推翻；推翻要留痕（加一条新裁决并把旧条改为「已撤销」），**不要就地删改**——`RESEARCH_MILESTONES.md:2-8` 的 SUPERSEDED 处理方式即为范例。
- 状态词只有三个：**生效** / **已撤销** / **待复核**。「裁决生效但代码未兑现」的情况一律写进「影响」栏并汇总在 §3。

---

## 0. 裁决索引

| ID | 裁决 | 日期 | 状态 | 证据强度 |
|---|---|---|---|---|
| D-01 | 运行期不放 LLM：编译一次、冻结复用 | 2026-07-29 立，2026-07-30 复申 | 生效 | 文档 + 代码 |
| D-02 | H3 → H3'：伺服从独立闸门降级为「连续绑定」档位 | 2026-07-29 | 生效 | 文档 |
| D-03 | demo 只给关系不给数值；文字变成对数值的检验函数 | 2026-07-26 起，2026-07-29 v2 定型 | 生效（Phase 1 运行期未兑现，见 §3） | 文档 + 反证代码 |
| D-04 | GT 防火墙约束运行期数据流，**不**约束版本控制 | 2026-07-26 立，2026-07-30 澄清 | 生效 | 文档 + 代码 |
| D-05 | ORACLE-M1A 只是集成测试与上界，不得报为方法结果 | 2026-07-30 | 生效 | 文档 + 代码 |
| D-06 | 主仓迁内网 Gitea；GitHub `origin` 停止维护 | 2026-07-29 | 生效 | 文档 + git |
| D-07 | release check 分 private / public 两档 | 2026-07-30 | 生效 | 代码 + git |
| D-08 | `adapters/` 改惰性导入，解开 `method/` 与 Phase 1 主链路的焊死 | 2026-07-30 | 生效 | 代码 + git |
| D-09 | 放行 motion planning 路线（raw IK 直达零先例） | 2026-07-30 | 生效 | **口头裁决，无文档出处** |
| D-10 | 投递目标定 RSS 2027，放弃 ICLR / ICRA 2027 | 2026-07-29 | 生效 | 文档 |
| D-11 | Phase 0 先不动仿真，只做 demo 视频理解 harness | 2026-07-29 | 生效（Phase 0 已于 2026-07-30 达标结束） | 文档 |
| D-12 | 零污染原仓：k1-sys / knowin-world 零文件改动 | 2026-07-30 | 生效 | 文档 + 实践 |
| D-13 | 歧义对验收门改判为素材缺陷，不计入本轮 | 2026-07-30 | 生效 | 文档 |
| D-14 | `push_T` / push 原语挂起，M1 不实现 | 2026-07-30 | 生效 | 文档 + 代码 |
| D-15 | 手写研究资产必须纳入版本控制（撤销旧 `.gitignore` 排除） | 2026-07-30 | 生效 | 文档 + git |
| D-16 | 过时文档加 SUPERSEDED 块，正文一字不删 | 2026-07-30 | 生效 | 文档 |
| D-17 | v3/v4 代次错配用仓外 override 绕过；`knowin_sim_v4verify` 不迁 | 2026-07-30 | 生效 | 文档 |

---

## 1. 核心方法裁决（动这些等于换课题）

### D-01 运行期不放 LLM：编译一次、冻结复用

| | |
|---|---|
| **裁决** | LLM 只允许出现在**编译期**（Phase 0：阶段切分 / 约束提取 / 三层漏斗 tie-break；graph→policy 编译一次）。**执行期 runtime 零 LLM**，闭环由约束残差驱动，不由模型驱动。 |
| **日期** | 2026-07-29（`RESEARCH_PROPOSAL_V2.md` §4.8 定），2026-07-30 复申（`harness/DESIGN_GRASP_AND_LOOP.md` §4） |
| **背景** | 相邻路线全都把模型放进环里：ReKep / CoPa 每集 VLM 在环生成约束；VIA 式 agent 每步在环操作，成本 $4–15/集、≤1h/集。2026-07-30 讨论 `robocurve/inspect-robots` 时又一次冒出「用动作级 ReAct 顶掉伺服」的诱惑。 |
| **理由** | ① 差异化：摊销是本课题相对 per-episode VLM 路线的**主要可防守点**，成本/延迟表要进论文；② 可审计：冻结代码后换 seed/layout 只有感知返回值变化，失败可归因到具体约束；③ 一旦运行期有 LLM，「冻结后跨场景复用」这个主张自毁——原文：「方法路径绝不把 LLM 放进运行期循环——否则核心主张自毁」。 |
| **影响** | LLM 被切成三个工位：**A. bring-up/标定**（LLM 在环，仅当实验室仪器，学到的姿态可达域只能作 per-robot 标定烘进 L1 硬可行性层）；**B. 方法（冻结）**运行期无 LLM；**C. 基线**（no-demo frontier agent，每步在环，论文对照位）。A 和 C 用 LLM **不违反**本裁决，B 用即违反。代码上：`harness/cli.py` 把 `compile` 排除在 `all` 之外，编译是显式动作；`harness/phase1.py` / `harness/fakerun.py` / `harness/kwadapter.py` 全链无 LLM 客户端。 |
| **状态** | **生效** |
| **证据** | `RESEARCH_PROPOSAL_V2.md:147`（「执行期 runtime **无 LLM**」）、`:31`（摊销对照 VIA 成本）、`:69`（「LLM 只在此出现一次」）；`harness/DESIGN_GRASP_AND_LOOP.md:66-72`（三工位表）、`:77`（「B 不变…否则核心主张自毁」）；`harness/cli.py:65-67`（compile 独立子命令）与 `:68-75`（`all` 分支不含 compile）；`harness/kwadapter.py:1-8`（运行期 runtime 的模块 docstring，无 LLM 通路） |

### D-02 H3 → H3'：伺服从独立闸门降级为「连续绑定」档位

| | |
|---|---|
| **裁决** | 撤销 v1 的 H3（伺服作为独立假设 + 独立闸门），改为 **H3'**：两级 ReAct = 阶段间 gate（验收不过不放行）+ 阶段内有界修正（残差 = 约束 − 现状）。伺服（连续闭环）只是**绑定档位**中的一档，不再单独立闸门。 |
| **日期** | 2026-07-29（v2 §2 / 附录 A），2026-07-30 在 `RESEARCH_MILESTONES.md` 顶部就地标注作废范围 |
| **背景** | v1 把「有没有伺服」当成一条可独立开关的假设，M2.c 整节的入口条件写成「仅当 H3 开启」。实际做下来，伺服和「入口感知一次」「静态先验」是同一件事的不同刻度，不是二值开关。 |
| **理由** | 绑定档位（静态 / 入口绑定 / 连续绑定）是连续谱，编译器按可观测性派发默认档，held-out 上同一约束反复挂 gate 则自动升档。这样失败归因产出的是**具体动作（换档）**，而不是笼统 retry；也避免「H3 关/开」这种在实现上根本切不干净的消融。止损：升档规则学不出就退化为固定派发表，仍算贡献。 |
| **影响** | ① `RESEARCH_MILESTONES.md` 中 7 处 H3 引用（第 41/47/51/86/99/105/149 行，为 2026-07-30 加注前的原始行号）作废；§M2.c 入口条件与「H3 关闭时整节跳过」**双向悬空**，`ServoSpec` TODO 需按绑定档位重写。② 消融 A（推导 Spec vs 手调常数）原挂伺服层，改挂「连续绑定」档位。③ 组 4 vs 5 的「闭环增量（视 H3）」改读为 H3' 的 gate/修正消融。 |
| **状态** | **生效**（v1 的 H3 = **已撤销**） |
| **证据** | `RESEARCH_PROPOSAL_V2.md:56`（H3' 原文）、`:133`（§4.5 绑定档位与失败升档）、`:247`（附录 A 差异行）；`RESEARCH_MILESTONES.md:28`（S2-3 逐条列出作废面与后果）、`:15`（消融 A 改挂） |

### D-03 demo 只给关系不给数值（typed holes）；文字变成对数值的检验函数

| | |
|---|---|
| **裁决** | 从 demo 提取的一切**只有关系与阶段结构，没有度量数值**；所有度量量留 typed hole，由执行期感知/生成器绑定。原则一句话：**文字永远不直接变数值；文字变成「对数值的检验函数」，数值只从感知/生成器来。** 图中出现任何世界坐标度量字面量即校验失败。 |
| **日期** | 2026-07-26 立（信息隔离边界同批），2026-07-29 在 v2 §4.1/§4.2 定型 |
| **背景** | B7 实验：注入物理荒谬的度量常数（±0.30 m，大于架半宽 0.108 m），8 份 LLM 生成代码 **0 拒绝 0 修正**——模型对数字零物理校验、只照搬。CaP-X 风格「LLM 直接写常数」由此被证伪。 |
| **理由** | ① 度量常数是泛化的死穴，也是 CaP-X 反模式的病灶；② 关系可跨 seed / 跨 layout / 跨本体复用，数值不行；③ 把「文字→检验函数」而不是「文字→数值」，让 demo 的粗标签（如 `region_grasp(obj, 中上部)`）编译成可计算谓词，落在三层漏斗第 2 层，可审计、可消融。 |
| **影响** | ① 约束词表封闭（`axis_parallel` / `center_align` / `region_grasp` / `approach_direction` / `above` / `inside` / `order` / `carry` / `clearance` / `axis_vertical`），提取只准从表里选；② T3 度量字面量扫描器进 validate；③ VLM 不得输出数值、不得在 >3 候选中自由挑；④ 谓词阈值只准从通用几何推导或全任务共用一套，**禁 per-task 手调**。 |
| **状态** | **生效（设计层）；Phase 1 运行期未兑现——见 §3-G1，这是当前最大的名实落差** |
| **证据** | `RESEARCH_PROPOSAL_V2.md:13`（北极星）、`:99-101`（封闭词表 + 度量字面量即失败）、`:105`（检验函数原则）、`:120`（禁 per-task 手调）、`:38`（B7 证据）；`harness/DESIGN_GRASP_AND_LOOP.md:34`（L2「demo 提供，**只给关系不给数值**」）；`AGENTS.md:79-88`（typed hole 六要素）、`:133-136`（可以表达「夹持试管中段」，不能预填试管长度/孔心坐标） |

### D-04 GT 防火墙的边界：约束运行期数据流，不约束版本控制

| | |
|---|---|
| **裁决** | 主方法（图生成 / 候选选择 / executor）只能用：演示证据、任务指令语义、带 provenance 的运行期感知、机器人自身状态与动作反馈、allowlisted 任务无关先验。禁读 scene/asset 库、仿真实体精确 pose/DoF/AABB/尺寸、GT mask、evaluator 答案，**也禁止把这些量换名包装成 perception API**。<br>**2026-07-30 澄清**：防火墙约束的是**运行时数据流**，**不是版本控制**——`oracle/`（人工手写的上界基准图）纳入 git 不违反本节，只要方法代码不在运行期读它。 |
| **日期** | 2026-07-26 10:39 拍板（信息隔离边界）；2026-07-30 澄清版本控制边界 |
| **背景** | 澄清的触发点是一次真实的数据丢失：`oracle/`、`tools/`、`*_AUDIT.md` 被误列进 `.gitignore` 的「可再生成产物」段，理由被当成「安全措施」。后果是 `PREDICATE_AUDIT.md` 与 `PROVENANCE_CORRECTION.md` **从磁盘永久消失**，`PRIMITIVE_API_AUDIT.md` 在实验机上取不到——「在 5090 跑 Phase 1，但那台机器看不到 Phase 1 的 API 文档」。 |
| **理由** | 防火墙防的是**信息在运行期流进方法**，git 是离线的历史记录面，二者正交。把二者混为一谈的代价是：门禁长期为红而失去信号作用，手写研究资产被当成脏东西删掉。原文一句话：「**把手写资产排除出版本控制不是安全措施，是数据丢失。**」 |
| **影响** | ① `oracle/` 从门禁的 `FORBIDDEN_PARTS` 移到 `PUBLIC_ONLY_PARTS`（内网私有仓允许，对外发布时再筛）；② Phase 1 的 `ORACLE-M1A` 产物**可以留档**，但不得报为方法结果（→ D-05）；③ 防火墙本身一条没松：oracle 只进 evaluator / sanity check / 上界与故障归因三类隔离用途，产物标 `privileged_oracle`，分目录保存，不得回流到候选生成、排序、动作选择或恢复；自动校验拒绝 provenance 链里出现 `privileged_oracle` 的主方法图。④ 本项目比 GaP（`sim.check_success` 直接暴露给图）与 CaP-X（privileged API 不与视觉判据类型隔离）都严，这条差异写进论文。 |
| **状态** | **生效** |
| **证据** | `SECURITY.md:25-32`（防火墙正文）、`:34-36`（版本控制澄清）、`:19-21`（`.gitignore` 误列事故记录）；`PROGRESS.md:33-37`（2026-07-26 10:39 原始裁决）；`AGENTS.md:111-140`（§3 信息边界完整清单，`:137-140` oracle 三类隔离用途）；`harness/PHASE1_API_PLAN.md:55-59`（§4 防火墙细则与两仓对照）；commit `138af50`、`ee1d21c` |

### D-05 ORACLE-M1A 模式的定位：集成测试与上界，不得报为方法结果

| | |
|---|---|
| **裁决** | Phase 1 M1a 的 `solve` 走 EvalServer `GET /state` 的特权实体位姿，属**特权路径**。该模式的全部产物必须带 `ORACLE-M1A` 标签，只作**集成测试与方法上界**，**不得报为方法结果**、不得表述为「任务成功」。M1b 起 `solve` 切非特权（dgl-perception），适配器接口不变。 |
| **日期** | 2026-07-30 |
| **背景** | 为了先把「sim → EvalServer → 适配器 → 编译 policy → 两级 gate → episode 报告」这条软件链打通，M1a 允许暂时用特权态填洞。同期又踩到 B4 式陷阱：`stack_bowls` 报告里 stage 0–2 的 "passed" 是**平凡真检查放行**（三只碗位移 0.0000 m，谓词在 reset 时就已为真）。 |
| **理由** | ① 不这样标，一条「跑通了」的 episode 日志会在半年后被当成方法效果引用，而它同时吃了 oracle 感知和空洞 gate 两份便宜；② B4-probe 的教训是**执行完整 ≠ 任务成功**，验收必须独立于执行；③ 上界本身有研究价值（把「感知损失」与「编译/执行损失」拆开），但只有标清楚才有价值。 |
| **影响** | ① 代码里落成常量 `ORACLE_BANNER = "ORACLE-M1A"`，注释即纪律；② gate 改为 effect-aware：验收约束成立 **且** 对有效果的阶段要求观测到物体位移；阶段入口就已为真的约束记为 `vacuous_pass`，不带证据权重；不可观测世界（fake 干跑）记录而非静默放行；`vacuous_pass_total` 本身是研究数据；③ 同源纪律外扩到编译步——「5/5 全绿」是 fake 干跑 + AST 静态检查，任何转述必须保留「fake 干跑」四字；④ 截至 2026-07-30，M1a 的**真实抓取次数为 0**，episode 产物只在 5090 `~/phase1/artifacts/`，本 checkout 内没有任何 episode 产物（相关数字为文档声称、mac 侧未核实）。 |
| **状态** | **生效** |
| **证据** | `harness/kwadapter.py:19`（`ORACLE_BANNER` 常量与注释）、`:1-8`（模块 docstring 声明 M1a ORACLE 模式）；`harness/PHASE1_API_PLAN.md:20`（「标注 ORACLE，只作集成测试与上界」）；`SECURITY.md:35-36`；`AGENTS.md:283-293`（§8.4 结果标注纪律，含 fake 干跑与「真实抓取 0 次」）；`RESEARCH_MILESTONES.md:55`（「任何汇报不得把 oracle / fake 链路的结果写成机器人效果」）；`PROGRESS.md:23`（一句话状态）；commit `e826e67`（effect-aware gating，10 个单测） |

---

## 2. 工程与流程裁决

### D-06 主仓迁内网 Gitea；GitHub `origin` 停止维护

| | |
|---|---|
| **裁决** | 主仓 = 内网 Gitea **私有**仓（remote 名 `gitea`），`gitea/main` 是唯一权威历史。GitHub `origin` 降级为历史备份：**不 push、不做对齐枢纽**；其上已推送的内容一律视为**已公开**。 |
| **日期** | 2026-07-29（commit `179dede`，19:14） |
| **背景** | 此前的前提是「本仓是公开、净化后的 source-of-truth」。研究进入 Phase 1 后，内部主机、端口、NAS 路径进文档是刚需（文档要能直接用），公开仓形态与之冲突。 |
| **理由** | 私有仓让文档写实话；同时保留「已推送到 GitHub 的内容视为已公开」这条不可逆假设，避免事后自欺。 |
| **影响** | ① 同步拓扑固定为 mac 编辑/commit → `git push gitea main` → 5090 用 `ssh -A`（agent forwarding 必需，`IdentitiesOnly` 钉住身份）`git pull`，Gitea 不可达时才 rsync 兜底（排除 `.git`、`runs/`、`knowin-world/`、`venvs/`、密钥、`configs/local/`，禁止盲目 `--delete`）；② 收工前核对 5090 HEAD 与 `gitea/main` 一致；③ 触发 D-07 的门禁分档；④ **待复核项**：GitHub 侧仓库当前可见性未核实，公开性假设需 PI 裁决后才能写回文档，裁决前按最严口径处理。 |
| **状态** | **生效**（GitHub 公开性假设 = 待复核） |
| **证据** | `RESEARCH_PROPOSAL_V2.md:212`；`AGENTS.md:316-324`（§9.1 拓扑三点）、`:339-350`（§9.3 同步工作流，`:350` 「`gitea/main` 是唯一权威历史」）；`SECURITY.md:3-4`；commit `179dede`（2026-07-29 19:14） |

### D-07 release check 分 private / public 两档

| | |
|---|---|
| **裁决** | `scripts/public_release_check.py` 新增 `--profile private\|public`，**默认 private**。private 下内部主机/端口只报 WARN、不影响退出码；真凭据、模型权重、大文件、SOURCE_MANIFEST 一致性仍然阻断。**对外发布前必须跑 `--profile public` 且清零。** |
| **日期** | 2026-07-30（commit `138af50`，`oracle` 的处理在 `ee1d21c`） |
| **背景** | D-06 迁私有仓后，内部 endpoint 出现在文档里既正常又必要，但门禁把它和密钥混在一个模式里 → 门禁长期为红。红灯常态化等于没有门禁。 |
| **理由** | 「端点不是密钥」。把 `PUBLIC_ONLY_PATTERNS` / `PUBLIC_ONLY_PARTS` 从 `SECRET_PATTERNS` / `FORBIDDEN_PARTS` 拆出来，让 private 档的红灯**只对真问题亮**，保住信号价值；public 档保留完整严格度，作为投稿/开源前的一次性关卡。 |
| **影响** | ① `oracle/` 一并从 `FORBIDDEN_PARTS` 移到 `PUBLIC_ONLY_PARTS`（理由同 D-04）；② `components/` 4 份 README 的既有脱敏用 manifest 的 `public_sanitizations` 机制登记（保留 `upstream_sha256` 作上游字节凭证，`sha256` 更新为脱敏后值），而不是回退 README；③ 门禁现状：`release check [private]` OK（7 warning）exit 0，`--profile public` 仍如实报 7 条；全量测试 **88 passed**。④ **文档漂移待修**：`README.md:41` 仍写「预期 88 tests / 87 passed，已知 1 例 manifest 漂移失败」，该失败已在 `ee1d21c` 修复，README 未同步。 |
| **状态** | **生效** |
| **证据** | `scripts/public_release_check.py:73-74`（默认 private 的理由注释）、`:315-318`（`--profile` 定义）、`:326-330`（按档分流 errors/warnings）；`SECURITY.md:6-17`（两档要求表 + 门禁行）；commit `138af50`、`ee1d21c`；`README.md:38-42`（测试命令 + 待修的过时预期） |

### D-08 `adapters/` 改惰性导入，解开 `method/` 与 Phase 1 主链路的焊死

| | |
|---|---|
| **裁决** | `adapters/__init__.py` 从 eager import 改为 PEP 562 模块级 `__getattr__`，导出符号登记在 `_LAZY_EXPORTS`，访问时才导入所在子模块。`from adapters import X` 写法不变。 |
| **日期** | 2026-07-30（commit `3f603d1`） |
| **背景** | 仓内四套目录并存：`harness/`（当前主线）、`method/demo_graph/`（v1）、`adapters/`、`experiments/`（v1）。`harness/kwadapter.py` 只需要一个 66 行、纯 stdlib 的 `PipelineClient`，却经 `adapters/__init__` → `m1_bindings` → `method.demo_graph` 把 **13 个模块**拖进 Phase 1 运行路径。 |
| **理由** | 后果不是性能问题而是**架构锁死**：`method/` 因此不可删、不可独立演化，任何「清理 v1 遗留」的动作都会打断当前主链路。惰性导入是最小改动的解耦（单文件 +43/−14 行），不删任何依赖。 |
| **影响** | 实测：`from adapters.knowin_world.pipeline import PipelineClient` 拉起的 `method.*` 由 **13 → 0**；`import harness.kwadapter` 的 `method.*` 为 0 且 `KWRuntime` 可用；访问 `BrokerPolicyBindings` 时才加载那 13 个（证明是惰性而非删依赖）；7 个导出符号全部可导入，`dir(adapters)` 不变；88 passed / release check [private] OK / harness CLI 冒烟正常。**后续**：v1 目录的清理或归档现在可以独立排期，不再阻塞 Phase 1。 |
| **状态** | **生效** |
| **证据** | `adapters/__init__.py:9-14`（改动说明）、`:21`（`_LAZY_EXPORTS`）、`:34`（`__getattr__`）；commit `3f603d1`（含全部实测数字）；`AGENTS.md:366-370`（§10 代码边界：新方法只写 `method/demo_graph/`，外部接入只写 `adapters/`） |

### D-09 放行 motion planning 路线

| | |
|---|---|
| **裁决** | 允许经 `motion_planning` 类服务下发运动，不再坚持 raw IK（`xquat_move`）直达。 |
| **日期** | 2026-07-30 |
| **背景** | M1a 夜间冲刺撞墙：右臂 raw IK 过不了 x≈0.24，物体在 x≈0.44–0.61；顶抓姿态 IK 全拒（`self_collision_violation`，pos_error 仅 1.8 mm）；躯干电机在本 sim 的 zenoh 桥未接通（`ArmCtrl decode failed name=all_motors`）。旁证：eval-runs 历史成功（`stand_up_bottle` / `stack_bowls`）**全部经完整 KSM 技能栈（运动规划）**，1022 上 wht 的链也是经 KSM 规划——**raw IK 直达在本环境无成功先例**。当晚给出三选项：A 修 sim infra（治本，越出零污染边界）/ B 放行运动规划（绕行）/ C 换可达性友好的场景。 |
| **理由** | 上游所有成功先例都走这条路；选项 B 的代价只是「`motion_planning` 服务在 `services/` 非 common 目录 = 原禁区」，按「ctrl 类可斟酌」的底座规则放行即可，adapter 一天内接上。相比之下 A 需要 infra 同学动手且破坏零污染（D-12），C 只是换场景不解决问题。 |
| **影响** | 底座规则的「禁非 common reasoner」保持不变，本次放行只覆盖 **ctrl / 运动规划类**调用；`PRIMITIVE_API_AUDIT.md:185` 已记 `motion_planning_stereo`（参数面 `q_current` / `q_goal` / `tcp_trajectory` / `grasp_item` / `planner_config` / `q_other_arm`）为 USABLE，可直接接。注意这不解除另一条阻塞：**夹爪通道在本栈不通（v3 控制器每臂只出 7 DoF），捏取类抓取当前不可能成功。** |
| **状态** | **生效** |
| **证据** | ⚠️ **口头裁决，无文档出处**——盘上只有待裁决的选项，没有裁决结论。选项与理由：`harness/PHASE1_M1A_STATUS.md:49-50`（选项 B 原文，「需老板裁决」）、`:41-42`（raw IK 零先例的旁证）、`:36-40`（三条墙的证据，`~/phase1/debug_grasp_evidence.json`，**产物在 5090，本 checkout 无**）；`harness/PHASE1_API_PLAN.md:3`（底座规则「ctrl 新增先斟酌」）；`PRIMITIVE_API_AUDIT.md:185`（`motion_planning_stereo` USABLE）；`harness/DESIGN_GRASP_AND_LOOP.md:86`（夹爪 7 DoF 阻塞）。**建议**：下次改 `harness/PHASE1_M1A_STATUS.md` 时把裁决结论就地补记，否则这条会在下一轮被重新讨论。 |

### D-10 投递目标定 RSS 2027，放弃 ICLR / ICRA 2027

| | |
|---|---|
| **裁决** | 唯一投递目标 = **RSS 2027**（预计 2027-01/02 截稿）。ICLR 2027 明确排除（时间与形态均不匹配）；v1 里作为 stretch 的 ICRA 2027（约 2026-09 中截稿）作废。 |
| **日期** | 2026-07-29 |
| **背景** | v1 写的是「RSS/ICRA 2027」，其后又被一轮 ICLR 讨论覆盖，三个目标同时挂着。 |
| **理由** | 形态匹配：本课题的主结果是机器人执行与泛化，RSS 的评审口径对得上；时间匹配：ICRA 2026-09 截稿要求 M2 提前完成且数字强，而 Phase 1 至 2026-07-30 尚未有一次非特权端到端成功。 |
| **影响** | ① 里程碑按 RSS 2027 倒排——但 v2 §6 承诺的「另文更新」**至 2026-07-30 未产出**，这是一笔明账上的欠账；② `RESEARCH_MILESTONES.md` 的 M1–M4 / W1–W16 全部日历日期（~08-23 / ~09-20 / ~10-18 / ~11-15）作废，执行单位改为 Phase 0/1/2；③ 该文件的**止损判据与验收阈值仍然有效**，继续作为闸门规范源被引用（含唯一成文的 20-seed 阈值：≥16/20 抓取+转正+对准、≥12/20 inserted+upright）。 |
| **状态** | **生效** |
| **证据** | `RESEARCH_PROPOSAL_V2.md:5`（RSS 主 / ICLR 排除）、`:248`（附录 A：v1「RSS/ICRA 2027」→ v2「RSS 2027（定）」）、`:207`（「另文更新」承诺）；`RESEARCH_MILESTONES.md:27`（S2-2 ICRA 作废）、`:4`（阈值仍有效）、`:7`（另文未产出）、`:18`（20-seed 阈值） |

---

### D-11 v1/v2 双树共存：按能力退役，不按代次删除

- **裁决**（2026-07-30）：`method/` `adapters/` `experiments/` 三棵 v1 树**保留**，逐模块退役而非整树删除。
  判断规则：**「`RESEARCH_PROPOSAL_V2.md` 或实验矩阵里，有没有哪条假设/消融/验收门需要它？」**
  有 → 留（哪怕当前无人 import）；没有 → 删。
- **背景**：所有者提出「v2 是最新方案，之前没用的就该删」。这个判断对普通工程仓成立，本仓不成立。
- **理由**：v1 实现的不是「v1 的方案」，而是 **v2 的 H1 假设所依赖、`harness/` 尚未实现的部分**。
  实测 `grep 'freeze|frozen|digest|sha256|manifest' harness/*.py` 命中全是字符串字段——冻结协议
  在 harness 侧零实现；而 v2 §0 北极星与 H1 都明确要求「策略代码**冻结后**的 held-out 成功率」，
  v2 §0 第 6 行写明「执行与冻结协议实验**后置到 Phase 1/2**」（后置，不是取消）。
  一句话：**v1 有纪律没数字，v2 有数字没纪律，RSS 2027 两样都要。**
- **影响**：确认可删的只有 `adapters/runtime_doctor.py`（3 行 `import *` shim，本体在
  `adapters/knowin_world/runtime_doctor.py` 且有两条正规入口）。其余保留并加代次 README。
- **状态**：生效。逐树说明见 `method/README.md`、`adapters/README.md`、`experiments/README.md`。

### D-12 `harness/` 不建阶段子包，改用 docstring 阶段标签

- **裁决**（2026-07-30）：`harness/` 下 19 个 `.py` **保持平铺**，在每个模块 docstring 第 1 行
  加阶段前缀（`[phase0 N/9]` / `[compile]` / `[runtime]` / `[phase1]` / `[common]`）。
  `head -1 harness/*.py` 即完整归属图。
- **背景**：所有者反馈「19 个文件平铺，看不出谁属于哪个阶段，太失控」。诉求正当，
  原方案是建 `phase0/` `compile/` `phase1/` `common/` 子包。
- **理由**（对抗审阅的 4 条 blocker，均盘上复核成立）：
  1. `util.py:11` 的 `HARNESS_ROOT` 是全 harness 唯一 `__file__` 锚点，`prompts`/`runs`/`.env`
     三类资产由它派生；下移一层会**静默**把产物写到新位置，50MB 旧 `runs/` 变孤儿且不报错。
  2. `util.py:18` 的 `.env` 不存在时静默跳过——本地已 `export` key 则一切照跑，到 5090 才炸。
  3. `phase1.py → phase1/runner.py` 会硬破 `python -m harness.phase1`（破口在仓外，5090 上的
     tmux/wrapper 本机 grep 覆盖不到）。
  4. `contract.py` + `fakerun.py` 进 `phase1/` 会造成 **compile → phase1 反向依赖**
     （`compilepolicy.py:14` import contract），与 phase0→compile→phase1 流向相反。
  收益侧：目标「一眼看出归属」已被标签方案 100% 达成，成本约为移动方案的 1%
  （每文件恰好 1 增 1 删、行数不变 → 23 行 import 全不动、72 条 md 带行号锚零漂移）。
- **例外**：`contract.py` 豁免标签、**内容一字节不改**——见 D-13。
- **重开条件**：目录结构变更只在两个时点允许：(1) 进入 Phase 2 冻结协议前的「结构定型」；
  (2) 开源复现包发布前。理由：`docs/` 的 file:line 引用是 provenance 链的一部分，
  其失效成本与代码变更频率成正比，而 `kwadapter.py`/`gates.py` 当前仍是高频变更目标。
- **状态**：生效（commit `458f37c`）。

### D-13 `harness/contract.py` 是版本化的 prompt 资产，改内容按改提示词的纪律走

- **裁决**（2026-07-30）：`contract.py` 的**文件内容**受提示词级别的变更纪律约束；
  改文件名安全，改内容不安全。它豁免 D-12 的阶段标签。
- **理由**：`compilepolicy.py:83` 用 `inspect.getsource(contract)` 把**整个文件（含模块 docstring）**
  拼进编译提示词的 `## CONTRACT SOURCE` 段。改内容会**静默改变 LLM 的输入**，可能改变生成的
  policy，而**没有任何工具会报这个错**——测试不覆盖 LLM 输出，门禁不看提示词内容。
  改名安全是因为反射走 `module.__file__` 而非硬编码路径。
- **影响**：任何对 `contract.py` 的内容改动，都应与「改 `harness/prompts/*.md`」同等对待：
  单独 commit、说明对生成结果的预期影响、必要时重跑编译对照。
- **状态**：生效。已记入 `harness/README.md`。

## 3. 次级裁决（同样已裁决，别重开）

| ID | 裁决 | 日期 | 理由摘要 | 状态 | 证据 |
|---|---|---|---|---|---|
| D-11 | Phase 0 先不动仿真机器人，只做「demo 视频理解」harness，执行与冻结协议后置到 Phase 1/2 | 2026-07-29 | 老板拍板；先把「理解层」做出可重复 CLI 与可量化指标，避免执行 infra 的不确定性淹没方法验证 | 生效（Phase 0 已于 2026-07-30 判达标：micro P=0.931 / R=0.865，两道门 P≥0.7、R≥0.8 全过，裁决「可开 Phase 1」） | `RESEARCH_PROPOSAL_V2.md:6`、`:151-155`、`:197-199`（明确不做清单）；`harness/PHASE0_ROUND2.md:16`、`:35-39` |
| D-12 | 零污染原仓：k1-sys / knowin-world **零文件改动**，自家感知服务全住本仓 `harness/perception_service/` | 2026-07-30 | 共享依赖 dirty 且不由我方掌控；改它等于把我方实验的可复现性押在别人的工作树上。也不经 pipeline 注册 remote-namespace（那需要重启共享 pipeline），适配器直连自家服务 | 生效 | `harness/PHASE1_API_PLAN.md:24-26`、`:3`（底座规则「零污染原仓」）；`AGENTS.md:358-360`（Knowin World 不作子目录/submodule/vendored） |
| D-13 | Phase 0 的「歧义对区分 ≥3/4」验收门 ❌ 改判为**素材缺陷**，移交「素材构造」任务，不计入本轮门 | 2026-07-30 | 现有素材不含目标歧义：random 变体只随机布局，`deposit_coin` 单币单槽——门没过是素材问题不是方法问题 | 生效 | `harness/PHASE0_ROUND2.md:37-38`；⚠️ `AGENTS.md:278-279` 明令「引用时不得只报通过项」 |
| D-14 | `push_T` 任务与 `push` 控制原语挂起，M1 不实现 | 2026-07-30（老板指示） | `push_T` 是 v0.2 唯一恶化任务（P 0.70→0.538、R 0.778→0.636），且 demo 本身有缺陷（粗推越推越歪）压低上限；非抓取类通路优先级低于主线 | 生效 | `harness/PHASE1_API_PLAN.md:69`（「push_T 挂起(老板指示)」）；`harness/kwadapter.py:574-575`（`raise NotImplementedError`）；`harness/PHASE0_ROUND2.md:14`、`:31` |
| D-15 | 手写研究资产（`oracle/`、`tools/`、`*_AUDIT.md`）必须纳入版本控制；撤销旧 `.gitignore` 的「可再生成产物」排除 | 2026-07-30 | 已造成不可逆损失（`PREDICATE_AUDIT.md`、`PROVENANCE_CORRECTION.md` 永久消失）。判据：**能不能重新生成**，不是「看起来像不像产物」 | 生效（旧排除 = 已撤销） | `SECURITY.md:19-21`、`:14`（两档要求表「手写研究资产」行）；commit `138af50` |
| D-16 | 过时文档不删正文，顶部加 SUPERSEDED 声明块，逐条列明有效/作废/歧义/建议/未核实 | 2026-07-30 | 正文里的编号、阈值和表格仍被 `PROGRESS.md` 的 13 行历史实验索引引用；改编号会打断可追溯性。作废的是**执行策略**，不是**判据** | 生效 | `RESEARCH_MILESTONES.md:2-8`（声明块与「正文一字未删」）、`:43`（历史引用面证据）、`:47`（改名建议与改动面比较） |
| D-17 | v3/v4 机器人代次错配用**仓外 override** 绕过（`~/phase1/cfg/sim_cfg.v3.yaml` + `ROBOT_CONFIG`/`ROBOT_MODEL` env 重启 pipeline）；`knowin_sim_v4verify` **不迁** | 2026-07-30 | 真因是 C++ IK 加载 v4 碰撞模型而 Genesis 跑 v3，产生与目标点无关的恒定幽灵自碰（`pair_id=263`）。零污染（D-12）要求不改他们的仓；`v4verify` 经核是 v2 的祖先 commit，迁移无收益 | 生效 | `harness/PHASE1_M1A_STATUS.md:1-10`（顶部更新块，含右臂前伸 0.24→0.678 零拒绝）；commit `3ce9d5e`；⚠️ `RESEARCH_MILESTONES.md:22` 提示：这是**新的仓外配置面**，正式 benchmark 前须与 runtime 洁净度同等固化 |

---

## 4. 已裁决但实现未兑现（名实落差，勿当成「已做到」）

这一节不是新裁决，是把「裁决说了什么」和「代码今天做了什么」的差距摆在明处。**引用上面任何一条裁决时，先看这里有没有对应缺口。**

| # | 裁决 | 今天的实现 | 后果 |
|---|---|---|---|
| G1 | D-03「demo 只给关系不给数值」，且编译 prompt 明确告诉模型「grasp region 已烘进 grasp-pose 洞」 | `solve()` 只对 hole 的**名字字符串**做子串匹配，hole 的 `type`/`solver_hint`/`frame` 全部丢弃；实际抓取点 = oracle 质心 xy + AABB 顶 − 硬编码 0.03 | **demo 约束今天不影响抓取**：把图里 `region_grasp(tube_left, upper_body)` 的 region 改成 `bottom` 或 `rim`，产生的抓取位姿逐比特相同。证据：`harness/prompts/compile_policy.md:20`（「grasp region is already baked into the grasp-pose hole」）vs `harness/kwadapter.py:295-305`（子串匹配 + `top - 0.03`） |
| G2 | 约束是「双消费者」（同时生成动作决策与阶段验收） | gates 只读 `stage['acceptance']`，整个 `stage['constraints']` **不参与任何运行期判定** | 「双消费者」这条主张目前只在验收侧成立。证据：`harness/gates.py:51`、`:63-65` |
| G3 | 自一致性投票产出的 `confidence`、typed hole 的 `solver_hint` 是图的一等字段 | `confidence` 全仓唯一程序读取点是 `harness/extract.py` 的排序；`solver_hint` **零程序读取** | 图里写了、下游没人用 |
| G4 | D-05：Phase 1 走可信 runner + 显式 stub | `kwadapter.push` 是硬 stub（`raise`），但 4 个生成的 policy 调用它 8 次；`harness/fakerun.py` 的 `__getattr__` 把它吞成 no-op | **dry-run 全绿、只在真机炸**。证据：`harness/kwadapter.py:574-575` vs `harness/fakerun.py:49-55` |
| G5 | H3' 的「阶段内有界修正 = 残差」 | `kwadapter.residual` 是软 stub：只 log，无感知无数值 | H3' 的修正侧目前无实现 |
| G6 | `harness/PHASE1_API_PLAN.md` §2 的 12 个非特权感知 API | **零实现**；Phase 1 全部 `solve` 走 oracle | M1b 尚未开始；D-05 的「M1b 换非特权」是计划不是现状 |
| G7 | — | `harness/kwadapter.py` 619 行、churn 高度集中（全部 8 个历史 commit 都在 2026-07-30 当天；全仓最近 15 个 commit 中占 4 个，最近 20 个中占 8 个）、**零测试覆盖** | 主链路上风险最高的单文件 |
| G8 | D-01 编译步「一次编译」 | `compilepolicy` 是**单轮无修复回路**：`static_check` 在 `policy.py` 写盘**之后**执行，违规不回喂模型；LLM 调用**无 tools / 无 function calling** | 编译产物的静态违规只报不修。证据：`harness/compilepolicy.py:81-85`（三段拼接 prompt）、`:87`（T=0.1 / 4000 tok）、`:89-90`（写盘先于 `static_check`） |

---

## 5. 附：证据可靠性分级

| 级别 | 含义 | 本文件中的例子 |
|---|---|---|
| **已核实** | 本 checkout 内可复查的文件、行号、git 历史 | 全部 `file:line` 引用、全部 commit 号、Phase 0 的 P/R（已用 5 个 run 的 `metrics.json` 盘上核对） |
| **文档声称** | 文档写了，但产物不在本 checkout | Phase 1 的全部 episode 数字（产物在 5090 `~/phase1/artifacts/`）、reach 墙拆除后的 0.678 前伸、`debug_grasp_evidence.json` 的三条墙证据 |
| **未验证 / 待核** | 已知有分歧或未查 | Phase 0 成本口径（文档记「全轮 ~$8」，盘上 5 个 v0.2 run 的 `cost.jsonl` 合计 **$5.79** / 单任务 $0.66–1.62，差异已在 `PROGRESS.md:122` 标为待核，以文档为准待复核）；GitHub 仓库当前可见性（D-06）；M1a 实跑所用 `graph.json` 是否为 Phase 0 自动提取图（决定「视频→图」闸门能否在效果层判开启，`RESEARCH_MILESTONES.md:58`） |

> **底线纪律（来自 `AGENTS.md` §8.4 与 `RESEARCH_MILESTONES.md:55`）**：Phase 0 的 P/R 是**提取质量**，不是机器人成功率；编译步的「5/5 全绿」是 **fake 干跑 + AST 静态检查**；Phase 1 的 episode 全部跑在 **ORACLE-M1A** 模式且真实抓取次数为 0。三者任何一个被转述成「机器人效果」，都是本文件要防的那类事故。
