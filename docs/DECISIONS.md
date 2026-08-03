# DECISIONS：关键裁决与理由

- 建立日期：2026-07-30
- 用途：记录「为什么现在是这样」。**只记裁决，不记进度**——进度看 `PROGRESS.md` 与 `harness/PHASE0_ROUND2.md` / `harness/PHASE1_M1A_STATUS.md`。
- 读法：先看 §0 索引表，需要理由再往下翻对应条目。
- 纪律：改动本文件已生效的裁决前，先确认该裁决的「理由」是否已被新证据推翻；推翻要留痕（加一条新裁决并把旧条改为「已撤销」），**不要就地删改**——`archive/ARCHIVE.md` §6.1 的 SUPERSEDED 处理方式即为范例。
- 状态词只有三个：**生效** / **已撤销** / **待复核**。「裁决生效但代码未兑现」的情况一律写进「影响」栏并汇总在 §3。

---

## 0. 裁决索引

| ID | 裁决 | 日期 | 状态 | 证据强度 |
|---|---|---|---|---|
| D-01 | 运行期不放 LLM：编译一次、冻结复用 | 2026-07-29 立，2026-07-30 复申 | **已撤销（2026-08-03，D-23）** | 文档 + 代码 |
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
| D-18 | 贡献结构收敛为「一主张两证据」；砍 L5 / G1G2G3 消融 / 最小性措辞 / 真机前提；**D-01 维持生效，BLK-5 解除** | 2026-08-03 | 生效 | 对话裁决（本文件为首个文档出处） |
| D-19 | 论文框架换轨 preimage/funnel 复活叙事；新增分离定理、链长任务族生成器、版本空间鲁棒反传三个贡献载体；「抽象层自动发现」存档为第二篇 | 2026-08-03 | 生效 | 对话裁决（本文件为首个文档出处） |
| D-20 | RoboDojo 官方榜单升格为**第二 headline**：激活门 = D0∧官方通道开放，失效门 = 9/15 未开放降回层 B；预算 ≤15 人日；#14 按官方口径关闭（非特权感知链必做）；论文内 baseline = CaP-Agent0 | 2026-08-03 | 生效 | 对话裁决（本文件为首个文档出处） |
| D-21 | **系统效果先行**：近期唯一目标 = 已测任务上的端到端真实成功率（内部 robodojo 评测计分）；T-THM 定理**暂停**（取 D-19 降级路径「纯实证」档）；T-GEN-2/T-BP/T-ROB/E-CHAIN **后置到效果里程碑后**——是排序不是取消 | 2026-08-03 | 生效 | 对话裁决（本文件为首个文档出处） |
| D-22 | **D0 判定 = 通过**（CC-0/1′/2′/3 四硬性全过，证据 `experiments/causal/D0_REPORT.md`），执行链解锁。三条处置：① CC-4 拆半——「UNKNOWN<20%」挪 P1 首批真实 episode 观测（挂 D1），「验收模型离线 acc」降级为 evaluator 侧 shadow 任务（P1-12），不作硬前置；② E-A6-swap-static 从 L0 实验批**正式移除**（其测量对象 corrector 已被 D-18 砍除，永不可解锁）；③ E-A1b/E-A1c 的素材缺口随 T-GEN 后置（与 D-21 一致），不卡执行链。附：PI 确认 sim 已停、可自由占用（BLK-2 排期约束消除） | 2026-08-03 | 生效 | 对话裁决 + `experiments/causal/D0_REPORT.md`（✅实测,预注册判据 SIGNED 在先） |
| D-23 | **采纳方案 v5「Demo-Conditioned Deliberative Execution」**：① 运行时 VLM **回归**（选择深思 + 有界修正 + 验证证据）——**D-01 撤销**，D-18 的「砍 L5」**部分撤销**（修正层以「修正阶梯第②级」形态复活）；② 新增核心模块 = **仿真状态克隆预演**（全场空位）；③ **成本约束撤销**（不得为省成本删掉显然能提升能力的模块）；④ 消融阶梯为论文论证结构，**「有链 vs 无链」成对消融是归因主线**；⑤ 加分轴「demo 流形上的有界玩耍」留二篇接口；⑥ 背景 = 八篇精读证据整合 | 2026-08-03 | 生效 | 对话裁决（本文件为首个文档出处）+ 八篇论文实测数字（`PROPOSAL.md` v5 §3/§4） |

---

## 1. 核心方法裁决（动这些等于换课题）

### D-01 运行期不放 LLM：编译一次、冻结复用

| | |
|---|---|
| **裁决** | LLM 只允许出现在**编译期**（Phase 0：阶段切分 / 约束提取 / 三层漏斗 tie-break；graph→policy 编译一次）。**执行期 runtime 零 LLM**，闭环由约束残差驱动，不由模型驱动。 |
| **日期** | 2026-07-29（`archive/PROPOSAL_v2.md` §4.8 定），2026-07-30 复申（`harness/DESIGN_GRASP_AND_LOOP.md` §4） |
| **背景** | 相邻路线全都把模型放进环里：ReKep / CoPa 每集 VLM 在环生成约束；VIA 式 agent 每步在环操作，成本 $4–15/集、≤1h/集。2026-07-30 讨论 `robocurve/inspect-robots` 时又一次冒出「用动作级 ReAct 顶掉伺服」的诱惑。 |
| **理由** | ① 差异化：摊销是本课题相对 per-episode VLM 路线的**主要可防守点**，成本/延迟表要进论文；② 可审计：冻结代码后换 seed/layout 只有感知返回值变化，失败可归因到具体约束；③ 一旦运行期有 LLM，「冻结后跨场景复用」这个主张自毁——原文：「方法路径绝不把 LLM 放进运行期循环——否则核心主张自毁」。 |
| **影响** | LLM 被切成三个工位：**A. bring-up/标定**（LLM 在环，仅当实验室仪器，学到的姿态可达域只能作 per-robot 标定烘进 L1 硬可行性层）；**B. 方法（冻结）**运行期无 LLM；**C. 基线**（no-demo frontier agent，每步在环，论文对照位）。A 和 C 用 LLM **不违反**本裁决，B 用即违反。代码上：`harness/cli.py` 把 `compile` 排除在 `all` 之外，编译是显式动作；`harness/phase1.py` / `harness/fakerun.py` / `harness/kwadapter.py` 全链无 LLM 客户端。 |
| **状态** | **已撤销（2026-08-03，D-23）**——运行时 VLM 回归（选择深思 / 有界修正 / 验证证据）。**正文一字未删（D-16）**：上面「裁决 / 背景 / 理由 / 影响」四栏保留原样，其中「摊销」「可审计」两条理由**仍然成立**且被 v5 继承（一次编译的约束链仍冻结复用，运行时模型只做**离散选择与有界修正**，不重做结构推理）；被撤销的只有「执行期 runtime 零 LLM」这一条**口径**。三工位划分（A 标定 / B 方法 / C 基线）随之作废——工位 B 现在允许运行时 VLM，边界改由 v5 §2 的 L4/L6/L7 逐层职责表与「VLM 不打分不出连续量」纪律承接。 |
| **证据** | `archive/PROPOSAL_v2.md` §4.8（「执行期 runtime **无 LLM**」）、§1.1（摊销对照 VIA 成本）、§3（「LLM 只在此出现一次」）；⚠️ **与 v3 冲突，待 PI 复裁**：`PROPOSAL.md` §5 把「冻结」重定义为「运行期不注入任务特定信息」，且 §3.1 的 L5 有界修正本身就是运行期 LLM——本条「执行期零 LLM」的口径已被 v3 撤销，摊销与可审计两条理由仍成立；`harness/DESIGN_GRASP_AND_LOOP.md:66-72`（三工位表）、`:77`（「B 不变…否则核心主张自毁」）；`harness/cli.py:65-67`（compile 独立子命令）与 `:68-75`（`all` 分支不含 compile）；`harness/kwadapter.py:1-8`（运行期 runtime 的模块 docstring，无 LLM 通路） |

### D-02 H3 → H3'：伺服从独立闸门降级为「连续绑定」档位

| | |
|---|---|
| **裁决** | 撤销 v1 的 H3（伺服作为独立假设 + 独立闸门），改为 **H3'**：两级 ReAct = 阶段间 gate（验收不过不放行）+ 阶段内有界修正（残差 = 约束 − 现状）。伺服（连续闭环）只是**绑定档位**中的一档，不再单独立闸门。 |
| **日期** | 2026-07-29（v2 §2 / 附录 A），2026-07-30 在 v1 里程碑文件顶部就地标注作废范围（该标注现存于 `archive/ARCHIVE.md` §6.1/§6.2） |
| **背景** | v1 把「有没有伺服」当成一条可独立开关的假设，M2.c 整节的入口条件写成「仅当 H3 开启」。实际做下来，伺服和「入口感知一次」「静态先验」是同一件事的不同刻度，不是二值开关。 |
| **理由** | 绑定档位（静态 / 入口绑定 / 连续绑定）是连续谱，编译器按可观测性派发默认档，held-out 上同一约束反复挂 gate 则自动升档。这样失败归因产出的是**具体动作（换档）**，而不是笼统 retry；也避免「H3 关/开」这种在实现上根本切不干净的消融。止损：升档规则学不出就退化为固定派发表，仍算贡献。 |
| **影响** | ① v1 里程碑文件中 7 处 H3 引用（原始行号 41/47/51/86/99/105/149，清单见 `archive/ARCHIVE.md` §6.2）作废；§M2.c 入口条件与「H3 关闭时整节跳过」**双向悬空**，`ServoSpec` TODO 需按绑定档位重写。② 消融 A（推导 Spec vs 手调常数）原挂伺服层，改挂「连续绑定」档位。③ 组 4 vs 5 的「闭环增量（视 H3）」改读为 H3' 的 gate/修正消融。 |
| **状态** | **生效**（v1 的 H3 = **已撤销**） |
| **证据** | `archive/PROPOSAL_v2.md` §2（H3' 原文）、§4.5（绑定档位与失败升档）、附录 A（差异行）；v3 对应位在 `PROPOSAL.md` §2.3–§2.5（两级闭环与「不是伺服」的措辞修正）；`archive/ARCHIVE.md` §6.2（逐条列出作废面与后果）、§1.1 消融 A 行（改挂「连续绑定」档位） |

### D-03 demo 只给关系不给数值（typed holes）；文字变成对数值的检验函数

| | |
|---|---|
| **裁决** | 从 demo 提取的一切**只有关系与阶段结构，没有度量数值**；所有度量量留 typed hole，由执行期感知/生成器绑定。原则一句话：**文字永远不直接变数值；文字变成「对数值的检验函数」，数值只从感知/生成器来。** 图中出现任何世界坐标度量字面量即校验失败。 |
| **日期** | 2026-07-26 立（信息隔离边界同批），2026-07-29 在 v2 §4.1/§4.2 定型 |
| **背景** | B7 实验：注入物理荒谬的度量常数（±0.30 m，大于架半宽 0.108 m），8 份 LLM 生成代码 **0 拒绝 0 修正**——模型对数字零物理校验、只照搬。CaP-X 风格「LLM 直接写常数」由此被证伪。 |
| **理由** | ① 度量常数是泛化的死穴，也是 CaP-X 反模式的病灶；② 关系可跨 seed / 跨 layout / 跨本体复用，数值不行；③ 把「文字→检验函数」而不是「文字→数值」，让 demo 的粗标签（如 `region_grasp(obj, 中上部)`）编译成可计算谓词，落在三层漏斗第 2 层，可审计、可消融。 |
| **影响** | ① 约束词表封闭（`axis_parallel` / `center_align` / `region_grasp` / `approach_direction` / `above` / `inside` / `order` / `carry` / `clearance` / `axis_vertical`），提取只准从表里选；② T3 度量字面量扫描器进 validate；③ VLM 不得输出数值、不得在 >3 候选中自由挑；④ 谓词阈值只准从通用几何推导或全任务共用一套，**禁 per-task 手调**。 |
| **状态** | **生效（设计层）；Phase 1 运行期未兑现——见 §3-G1，这是当前最大的名实落差** |
| **证据** | `archive/PROPOSAL_v2.md` §0（北极星）、§4.1（封闭词表 + 度量字面量即失败）、§4.2（检验函数原则、禁 per-task 手调）、§1.2（B7 证据）；v3 对应位在 `PROPOSAL.md` §1（demo 的产物必须是约束图）、§2.1（候选筛选链条与三条纪律）、§5（字面量扫描）；`harness/DESIGN_GRASP_AND_LOOP.md:34`（L2「demo 提供，**只给关系不给数值**」）；`AGENTS.md:79-88`（typed hole 六要素）、`:133-136`（可以表达「夹持试管中段」，不能预填试管长度/孔心坐标） |

### D-04 GT 防火墙的边界：约束运行期数据流，不约束版本控制

| | |
|---|---|
| **裁决** | 主方法（图生成 / 候选选择 / executor）只能用：演示证据、任务指令语义、带 provenance 的运行期感知、机器人自身状态与动作反馈、allowlisted 任务无关先验。禁读 scene/asset 库、仿真实体精确 pose/DoF/AABB/尺寸、GT mask、evaluator 答案，**也禁止把这些量换名包装成 perception API**。<br>**2026-07-30 澄清**：防火墙约束的是**运行时数据流**，**不是版本控制**——`oracle/`（人工手写的上界基准图）纳入 git 不违反本节，只要方法代码不在运行期读它。 |
| **日期** | 2026-07-26 10:39 拍板（信息隔离边界）；2026-07-30 澄清版本控制边界 |
| **背景** | 澄清的触发点是一次真实的数据丢失：`oracle/`、`tools/`、`*_AUDIT.md` 被误列进 `.gitignore` 的「可再生成产物」段，理由被当成「安全措施」。后果是 `PREDICATE_AUDIT.md` 与 `PROVENANCE_CORRECTION.md` **从磁盘永久消失**，`reference/PRIMITIVE_API.md` 在实验机上取不到——「在 5090 跑 Phase 1，但那台机器看不到 Phase 1 的 API 文档」。 |
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
| **证据** | `harness/kwadapter.py:19`（`ORACLE_BANNER` 常量与注释）、`:1-8`（模块 docstring 声明 M1a ORACLE 模式）；`harness/PHASE1_API_PLAN.md:20`（「标注 ORACLE，只作集成测试与上界」）；`SECURITY.md:35-36`；`AGENTS.md:283-293`（§8.4 结果标注纪律，含 fake 干跑与「真实抓取 0 次」）；`archive/ARCHIVE.md` §1.8（「任何汇报不得把 oracle / fake 链路的结果写成机器人效果」）；`PROGRESS.md:23`（一句话状态）；commit `e826e67`（effect-aware gating，10 个单测） |

### D-18 贡献结构收敛：一条算法主张 + 两条证据；砍 L5 / G 消融 / 最小性 / 真机前提

| | |
|---|---|
| **裁决** | 论文主张结构收敛为：**① 脊椎（唯一算法主张）＝约束链的后向可行性传播**——demo 给定性约束链（阶段/顺序/关系/typed holes），运行期感知给候选集与几何数值，算法把下游节点的可行性反向传播回当前候选排序（demo-conditioned goal regression）；**② 歧义对证据**——同一欠定指令 + 不同 demo → 不同但各自合法的策略；**③ 干预协议 + 独立验证归因降级为评测机制**，不作 headline。同时砍掉四项：**L5 在线 corrector**（整层删除，不是降级）；**G1/G2/G3 抽象层级消融**（E-ABSTR / P2-07 / P0-07 契约变体作废）；**真机闭环作为主结果前提**；**一切「最小/必要约束集」措辞**——只声称「足以且可控地引导候选选择」。 |
| **日期** | 2026-08-03（凌晨长对话收敛，当日晚间落账） |
| **背景** | 方案在「够不够 novel」上反复重开。诊断：原三条贡献里「可执行约束图」与「独立验证归因」单独拿出来已被 Li & Brock / GaP / ASPIRE 占位，宽框架不可防守；唯一没被占的象限是「下游约束反传改变当前决策」。PI 连续否决五个替代方案后拍板回到原 idea 并按此收敛。 |
| **理由** | ① 单点主张才有可防守面，宽框架必然与邻居撞车；② 砍「最小性」是绕过单 demo 可识别性漏洞的唯一低成本路径——省掉整套反事实引擎，主张从「抽出的约束是必要集」收缩到「足以引导选择」；③ 砍 L5 一并了结 BLK-5：D-01 与运行期模型工位的冲突以**维持 D-01、删除工位**告终，摊销与可审计两条卖点全额保留；④ G 消融 CaP-X §3.1 已覆盖，我方样本量只够 conditioning 变量不够 finding，做了也不能报；⑤ 真机不作前提后，sim 姿态发散墙（16°→52°）从主线阻塞降级为背景任务。 |
| **影响** | ① **BLK-5 解除**：`PROPOSAL.md`(v3.1) §5 预留的「D-18 = 撤销 D-01 口径」路径**作废**——本条即 D-18，内容与该预留相反；D-01 状态不变（生效），其证据栏中「与 v3 冲突待 PI 复裁」的警示由本条解除；方法路径（工位 B）运行期回到**零 LLM**。② 推论：运行期 gate 的 `passed` 由 `gates.py` 几何谓词计算（`TODO.md` §9-5 在 D-01 之下唯一自洽的选项 (a)），verifier 类模型只允许出现在 evaluator 侧 shadow 报告，不进方法路径；此推论 PI 可复议，复议前按此执行。③ 任务面作废：P2-03（corrector）、P2-07（E-ABSTR）、P0-07（契约变体）；`TODO.md` §9 未决 #1/#2/#11 关闭。④ CC-0 / CC-1′ / CC-2′ / P-3′ 的重新预注册需求**不变**（排序偏好裁决的后续义务，与本条无关）。⑤ 论文主实验定为 E1 歧义对 / E2 干预 / E3 链长 scaling（正式注册走 `EXECUTION.md` §1 增补，见 D-19 影响⑤）。 |
| **状态** | **生效（部分撤销：2026-08-03，D-23）**——**正文一字未删（D-16）**。撤销面**只有一处**：影响栏①的「砍 L5 在线 corrector（整层删除）」与「方法路径运行期回到零 LLM」——L5 以 **v5 修正阶梯第②级「有界 VLM 修正」**形态复活（看多视角含手摄 + 预演后果，只输出 {−1,0,+1}×固定步长离散修正，不生成连续量、不改目标、不参与验收）。**其余全部不变且仍生效**：一主张两证据的贡献结构、砍 G1/G2/G3 抽象层级消融（P2-07/P0-07 仍作废）、砍「最小/必要约束集」措辞、真机闭环不作主结果前提、影响栏②的「gate 的 `passed` 由 `gates.py` 几何谓词计算、verifier 类模型只进 evaluator 侧」（v5 L6 的 VLM 只出**证据**不出 `passed`，与本条一致）。连带复活：`TODO.md` 的 ~~P2-03 corrector~~ 以新任务 **T-COR** 形态重开（不是恢复旧编号，判据按 v5 §2 L7 重写）；D-22② 的 E-A6-swap-static 移除**不受影响**（其测量对象是旧 corrector 的静态交换测试，v5 修正器不走该形态）。 |
| **证据** | 对话裁决（2026-08-03），本文件为首个文档出处；方案面落点 = `PROPOSAL.md`(v4) §0/§2/§8；WAP（arXiv 2607.27599）实测 π0.5 布局迁移 6 任务 5 个 0%、失败模式为「沿用训练布局运动先验、在原目标坐标附近空抓」，作为 D-03「度量常数是泛化死穴」的外部定量佐证进论文动机 |

### D-19 学术框架换轨：preimage/funnel 复活叙事 + 三个新贡献载体

| | |
|---|---|
| **裁决** | ① 论文框架采用「经典 preimage backchaining / funnel composition 的基础模型复活」叙事：demo 提供每阶段的**定性 preimage 规格**（约束 + typed holes），运行期在采样候选集上**近似计算 preimage**，脊椎 = preimage 的链式回传。**措辞纪律：不得声称发明反传 / goal regression**（LMT 1984、STRIPS goal regression 在先），表述为「经典 preimage 回传在单 demo 定性约束链上的首次可计算实例化」。② 新增理论目标：**贪心 vs k 步反传的分离定理**（构造链式任务族使局部贪心成功率随链长几何衰减、反传保持常数）——PI 亲自推导，纸面工作即刻可启动；推不动的降级路径：定理 → 受限任务族上的命题 → 纯实证（不阻塞任何实验）。③ 新增第二贡献载体：**程序化链长任务族生成器**（链长 / 阶段间耦合强度 / 歧义度三旋钮可控），是 E3 的地基，同时以 benchmark 名义进论文——CaP-Bench 的 S1–S4 调 API 抽象层级，无阶段间耦合维度，此维度为空档。④ 新增机制升级：**版本空间鲁棒反传**——提取器 k=5 自一致性采样不再只投票取众数，保留全部候选约束链为版本空间，候选排序对整个版本空间做鲁棒（worst-case 或期望）反传；开工条件 = D0（因果链闭合）之后，零新增 LLM 成本。⑤ E3 对照臂增加 **ReAct-with-replanning**（每步在环重规划），同报成功率与成本/延迟，一张图同时回答「为什么不直接 ReAct」与「编译冻结的价值」。⑥ 「抽象层自动发现」（API synthesis，DreamCoder 式 library learning for robot APIs）**存档为第二篇候选**，本篇不做——sim 吞吐喂不起（BLK-2）、与 ASPIRE/内部第二篇撞车、废掉 Phase 0 资产。 |
| **日期** | 2026-08-03 |
| **背景** | D-18 收敛主张后，剩余焦虑是「反传是古典 idea，审稿人会不会认为不新」。裁决把 novelty 防线从「假装机制是新发明」换成「明写经典对应 + 用定理和招牌图证明这个实例化非平凡」：RSS 评审语境对 classical-planning-meets-foundation-models 叙事接受度高；版本空间机制把「单 demo 识别不出约束」这个最大审稿攻击点从防御项（D-18 砍最小性）升级为进攻项（在不确定性上做规划本身成为贡献）。 |
| **理由** | ① 经典对应是资产不是负债——前提是自己先写明，抢在审稿人指出之前；② 分离定理与 E3 招牌图互为证据，构成「算法论文」的完整形态，且吃 PI 的理论背景；③ 生成器解决 E3 无任务族可跑的硬缺口（现有 4 任务链长不可控），顺手占 benchmark 空档；④ 版本空间的可证伪预测干净：歧义 demo 上鲁棒反传显著优于 MAP 链反传、干净 demo 上持平——两个结果都有信息量；⑤ 附带推论「版本空间分歧度 → 第二段 demo 的边际价值」是没人画过的图。 |
| **影响** | ① `PROPOSAL.md` 升 v4 并按本条重写（v3.1 归档至 `archive/PROPOSAL_v3.md`，D-16 纪律：正文不删，顶部加 SUPERSEDED 块）；② `TODO.md` 新增三条工作流：T-GEN（任务族生成器，与因果链修复并行、文件集不相交）、T-ROB（版本空间鲁棒反传，D0 后）、T-THM（分离定理，PI 亲自、即刻）；③ E1/E2/E3/E-ROB 的判据**须按预注册纪律在开跑前写进 `EXECUTION.md` §1 并经 PI 签字 commit**——本条只立项不注册，注册草案任务见 `TODO.md` T-REG；④ E1 歧义对素材构造升为长杆任务（现有视频无目标歧义，D-13 改判后一直悬置）；⑤ 生成器的任务族与 D-14（push 挂起）不冲突：任务族以抓取-放置-插入类阶段为原子，不引入 push 原语。 |
| **状态** | **生效** |
| **证据** | 对话裁决（2026-08-03），本文件为首个文档出处；方案面落点 = `PROPOSAL.md`(v4) §2–§6；理论模板参照 WAP（arXiv 2607.27599）Theorem 1–3；经典出处 = Lozano-Pérez, Mason & Taylor (1984) preimage backchaining；Burridge, Rizzi & Koditschek (1999) sequential composition of funnels |

### D-20 RoboDojo 榜单轨升格为第二 headline

| | |
|---|---|
| **裁决** | ① RoboDojo 官方榜单从「层 B 可选」升格为**第二 headline**——层 A 机制结果（E-AMB/E-DO/E-CHAIN/E-ROB）仍是第一 headline，**论文成立不依赖榜单**。② 激活条件 = **D0 通过 ∧ 官方提交通道开放**；失效条件 = **2026-09-15 前官方仍 Coming Soon 则自动降回层 B**，不得以等待为由挂起其他线。③ 预算硬顶 **15 人日**（Isaac Sim / XPolicyLab 适配、官方观测下感知链联调、CaP-Agent0 基线跑分），超支须 PI 重批。④ `TODO.md` §9-14 就地关闭：官方 evaluator 为非特权观测，E-CHAIN 主数字按官方口径 = 非特权感知链，**P1-04/05/06/07 升格必做、不可裁剪**（v1「不砍 P1-04/05」升格为硬约束）。⑤ 论文内受控 baseline = **CaP-Agent0**（同 API、同预算、无 demo），兼任 A1 消融的 agent 臂；榜单上的对手不可选，以榜上为准。⑥ 两层不混报纪律（`archive/ARCHIVE.md` §5.4）原样保留：内部 robodojo_v4 数字**永远不得**称 leaderboard result。⑦ 目标口径：**上榜 + Long-Horizon 维度领先**，不追总分第一。 |
| **日期** | 2026-08-03 |
| **背景** | PI 裁决「想把分数刷高、冲击 benchmark 榜单，baseline 用 CaP-X 系」。事实面：2026-07-26 榜单快照 #1 = 20.07 score / 13.93% SR（任务极难、绝对分低），公开提交页当时 Coming Soon；官方 = Isaac Sim + XPolicyLab evaluator，非特权观测；内部 KW 链是 robodojo_v4 适配，非官方。 |
| **理由** | ① 榜单是外部效度最强背书，直接回应「生成器玩具感」与「私有 sim 可复现性」两条审稿质疑；② 「分数高」本身不构成 RSS 贡献，机制轨必须先行——官方接入的全部工程（adapter、非特权感知、embodiment 差异）都以 D0 产物为地基，D0 前投入 = 在断链上盖房；③ 官方无 oracle 使 #14 的「最严口径」成为唯一口径，反而消掉一个 PI 拍板项；④ CaP-X 系在几何变异任务上已知弱（GaP Table 1：0.01–0.22），论文措辞限定为「相对 code-as-policy 系的增量来自 demo 约束链」，**不得宣称打败 SOTA**；Long-Horizon 维度与链式反传主场重合，是性价比最高的上榜面。 |
| **影响** | ① `TODO.md` 新增 §2B 榜单轨（RD-01 侦察 / RD-02 scoping / RD-03 主体）；② `TODO.md` §9-14 关闭；③ `PROPOSAL.md`(v4) §6.3 感知态段按本条改写；④ T-REG 注册 E-CHAIN 时任务选择须考虑与官方 42 任务的可迁移性；⑤ RD-01 须把官方 URL、榜单快照、提交规则记入 `docs/reference/ROBODOJO.md`——本仓至今未记官方 URL，这是侦察欠账。 |
| **状态** | **生效** |
| **证据** | 对话裁决（2026-08-03），本文件为首个文档出处；`archive/ARCHIVE.md` §5.4（RoboDojo 事实与诚实边界、层 A/层 B 结构）；GaP（arXiv 2607.05369）Table 1（CaP-X 0.01–0.22）；官方提交状态待 RD-01 核实 |

### D-21 系统效果先行：定理暂停，任务族与招牌实验后置到效果里程碑后

| | |
|---|---|
| **裁决** | ① 近期**唯一**目标 = 在**之前测试过的特定任务**（主攻 insert_tubes / stack_bowls，备选 deposit_coin；push_T 维持 D-14 挂起）上做出**端到端真实成功率**——非特权感知 + motion planning + 约束因果链生效，评测用**内部已实现的 robodojo 评测**计分。② **T-THM 分离定理暂停**：D-19② 取其自带降级路径的「纯实证」档；复活须 PI 再拍板。③ **T-GEN-2 / T-BP / T-ROB / E-CHAIN 后置**到「效果里程碑」之后（里程碑定义：≥1 个已测任务真实抓取端到端成功、且失败可归因到「节点 × 约束」）。T-GEN-1 spec 保留在库，签字随生成器复活一并处理。④ D-20 榜单轨治理不变（激活门 / 预算 / 两层不混报照旧）；PI 确认内部 sim 已实现 robodojo 评测——**内部分数用于开发迭代与论文的受控对照，仍不得称 leaderboard result**。CaP-Agent0 基线（D-20⑤）同样适用于这批内部任务。 |
| **日期** | 2026-08-03 |
| **背景** | PI 裁决原话大意：「仿真里已经实现了 robodojo 的评测,只用之前测试的几个特定任务;现在不搞定理,先从系统上做出效果。」此前 Phase 1 真实抓取 0 次（D-05），系统尚未证明能动——在这个状态下投入定理与任务族属于次序颠倒。 |
| **理由** | ① 全案最大的未消风险不是理论也不是任务多样性，而是「执行链没有一次真实成功」；先把它消掉，其余投资才有地基。② 定理与生成器的价值都以「反传在真系统上有效」为前提——效果先行恰好是给它们买保险。③ 已测任务感知/评测/金标全部现成，是最短路径。 |
| **影响** | ① `TODO.md` 排期锚点改写：D0 后直接进执行链（P1-02 运动规划 → P1-04 非特权感知最小集 → P1-08 闭环补偿 → P2-04 提前出首批 20-seed 数字）；② §2A 的 T-THM/T-BP/T-ROB/T-GEN-2 标「后置（D-21）」；③ **论文叙事风险如实记账**：E-CHAIN 招牌图仍是 RSS 主张的核心证据，本条是**排序不是取消**——若后续演变为取消，论文主张须按 `PROPOSAL.md`(v4) §9 第一行的降级路径收缩（gate-only 系统贡献 + benchmark），不得默认原主张仍成立。 |
| **状态** | **生效** |
| **证据** | 对话裁决（2026-08-03），本文件为首个文档出处；上游事实 = 5090 侦察（2026-08-03）：现役 sim `/home/knowin-sim/knowin_sim`、37 suites、insert 类资产充足、内部 robodojo 评测在跑（`main.py --manifest scenes/robodojo_v4/...`） |

### D-23 采纳 v5「Demo-Conditioned Deliberative Execution」：运行时 VLM 回归、预演进场、成本约束撤销

| | |
|---|---|
| **裁决** | 采纳方案 **v5：Demo-Conditioned Deliberative Execution**——一段示范编译成约束链，运行时在真仿真里预演、按示范证据深思后行动。六条：<br>① **运行时 VLM 回归**，三处入口：**选择深思**（L4：候选渲染进场景图，VLM 对照 demo 关键帧与约束排序）、**有界修正**（L7 第②级：看多视角含手摄 + 预演后果，输出 {−1,0,+1}×固定步长）、**验证证据**（L6：视觉差分文本作证据，`passed` 仍由几何谓词算）。**D-01「执行期零 LLM」撤销**；**D-18 的「砍 L5」部分撤销**——修正层以「修正阶梯第②级」形态复活，D-18 其余条款全部不变。<br>② **新增核心模块 = 仿真状态克隆预演**：fork 当前 Genesis 状态，虚拟执行每个候选 + 后续链前缀，收集各阶段 gate 结果，下游可行性**精确计算**而非估计。<br>③ **成本约束撤销**：不再以「运行期成本/延迟」为由删减模块。<br>④ **消融阶梯是论文的论证结构**（裸编译 policy → +确定性约束排序 → +仿真预演 → +VLM 深思 → +修正阶梯 → +修复）；**「有链 vs 无链」成对消融是归因主线**。<br>⑤ **加分轴「demo 流形上的有界玩耍」只留二篇接口**（内部撞车面已知）。<br>⑥ 背景 = **八篇精读证据整合**。 |
| **日期** | 2026-08-03（深夜，PI 批准） |
| **背景** | D-21 之后主线是「系统效果先行」，但 v4 的运行时形态（零 LLM + L5 整层砍除）是在**成本可防守性**这个前提下推出来的：D-01 的三条理由里，「摊销是相对 per-episode VLM 路线的主要可防守点」直接把成本对照表当成卖点。八篇论文精读改变了事实面——**能力缺口比成本缺口大得多**，且缺口位置正是被 v4 砍掉的那些模块：CaP-X 实测插入类**全行业归零**（最强 agent 0%）；WAP 实测**无结构 best-of-N 被系统性偏差击穿**（真值奖励 BoN-8=42% 输给「1 次预演 + 修正」的 60%）；ASPIRE 实测 **trace + 修复 14%→62%**（单点最大增益）；CaP-X 实测**前后帧差分成文字 S3 24%→55%**。PI 原话大意：「**不能为了成本低就把显而易见能提升能力的模块删掉。**」 |
| **理由** | ① **成本不是本课题的稀缺资源，能力是**——插入类 0% 的现状下，「比 ReAct 便宜 10–100×」是在一个全员为零的维度上争先；② **预演是全场空位**：CaP-X / GaP 运行时 / ASPIRE **均无**执行前预演，WAP 有但用 learned world model（自认接触失真）且 17 秒/次——**真仿真 fork 把这两个缺陷同时消掉**，这是八篇里没有一篇占住的象限；③ **运行时 VLM 回归不动脊椎**：约束链仍是一次编译、冻结复用，VLM 只做**离散选择与有界修正**（不打分、不出连续量、不改目标、不参与验收），D-01 的「可审计」与「摊销」两条理由因此**仍然成立**——真正被撤销的只有「零 LLM」这个口径；④ **结构才是我们的贡献，模块是别人的**——v5 的统一命题是「约束链是运行时深思的组织者」，每个强模块被同一份约束链条件化并向它归因；「有链 vs 无链」成对消融使**每一分增益都能归因到链**，这正是 v4 单点主张想要而缺少的证据形态；⑤ 结构缺席时同样的模块堆不起来，三个外部反例：GaP 去掉图结构成功率**归零**、WAP 无结构 BoN 被偏差击穿、RATs 无课程随机探索几乎零收益（**23.2→24.7**）。 |
| **影响** | ① **`PROPOSAL.md` 升 v5**（v4 归档至 `archive/PROPOSAL_v4.md`，D-16 纪律：正文不删、顶部加 SUPERSEDED 块）；架构从 v4 的三层漏斗展开为 **L0–L7 八层**。② **D-01 状态改「已撤销」**、**D-18 标「部分撤销」**，两条正文均一字未删（见各自条目状态栏）。③ **`TODO.md` 新增 §2C 五条 L4 工作流占位**：T-SIM（预演引擎）/ T-SEL（VLM 深思）/ T-COR（修正阶梯）/ T-QC（demo 质检）/ T-ALIGN（分阶段进度对齐）——**只占位，当前执行顺序不变**（修爪 → 单集 → 感知 → L4）。④ **成本/延迟对照表从「卖点位」降为「如实记账位」**——仍要报，但不再作为可防守点；v4 §5 的「成本对照表回到卖点位」措辞作废。⑤ **E-CHAIN 五臂里的 B3（ReAct-with-replanning）语义变化**：我方现在**也**在运行期用模型，B3 的对照轴从「用不用模型」改为「**有没有 demo 约束链组织它**」——CaP-Agent0（同 API、同预算、无 demo）升为主对照。⑥ 加分轴玩耍（RATs 式稀有度调度，实测玩耍库 +9.1 分、与执行侧正交可加合计 +21.1）**本篇不做**，只留接口。⑦ **v5 新增的判据同样受预注册纪律约束**（T-REG 未完成前不得开跑）。 |
| **状态** | **生效** |
| **证据** | 对话裁决（2026-08-03 深夜，PI 批准），本文件为首个文档出处；方案面落点 = `PROPOSAL.md`(v5) §1–§6；八篇论文实测数字逐条列在 v5 §2 八层架构表与 §3 占位表，本条引用的关键数字：CaP-X（插入类 0%、差分文本 S3 24%→55%）、GaP（去图结构归零、运行时验收 no-op）、ASPIRE（trace+修复 14%→62%）、WAP（BoN-8 42% vs 预演+修正 60%、learned WM 接触失真 17 s/次、组件消融 +8~24 / +6~14 / +4~14 分）、HOST（SDTW+TCC 对齐误差 0.006 vs 时钟 0.079、62% 靠 19.3 万条同平台预训练）、VIA（长程 100% / Fable 88%、8 mm 精度天花板、文本 waypoint 进 prompt 77%→100%）、RATs（随机探索 23.2→24.7、玩耍库 +9.1、执行侧验证重试 +13.1、合计 +21.1）、DoID（野外视频仅 4% 可用）、SAM-3D 引导扩散跟踪（人评 67% vs FoundationPose 15%、DexYCB F-10 0.93）。⚠️ **这些数字全部为论文声称值，未在本仓复现**（证据分级 = 文档声称，见 §5）。 |

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
| **证据** | `archive/PROPOSAL_v2.md` §7（代码同步拓扑）；`AGENTS.md:316-324`（§9.1 拓扑三点）、`:339-350`（§9.3 同步工作流，`:350` 「`gitea/main` 是唯一权威历史」）；`SECURITY.md:3-4`；commit `179dede`（2026-07-29 19:14） |

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
| **影响** | 底座规则的「禁非 common reasoner」保持不变，本次放行只覆盖 **ctrl / 运动规划类**调用；`reference/PRIMITIVE_API.md:185` 已记 `motion_planning_stereo`（参数面 `q_current` / `q_goal` / `tcp_trajectory` / `grasp_item` / `planner_config` / `q_other_arm`）为 USABLE，可直接接。~~注意这不解除另一条阻塞：**夹爪通道在本栈不通（v3 控制器每臂只出 7 DoF），捏取类抓取当前不可能成功。**~~ ← **2026-07-30 晚更正（只更正事实，D-09 裁决本身不变）**：该「另一条阻塞」是误判，夹爪实测可动，此前判不通是我方用错参数名（`gpos=` 而非 `angle=`，静默无效却仍回 `ok=True`）；出处 `harness/DESIGN_GRASP_AND_LOOP.md` §5 已更正。**仍未解除的是姿态路径发散**（`rot_error` 16°→52°），那正是本裁决要解决的问题。 |
| **状态** | **生效** |
| **证据** | ⚠️ **口头裁决，无文档出处**——盘上只有待裁决的选项，没有裁决结论。选项与理由：`harness/PHASE1_M1A_STATUS.md:49-50`（选项 B 原文，「需老板裁决」）、`:41-42`（raw IK 零先例的旁证）、`:36-40`（三条墙的证据，`~/phase1/debug_grasp_evidence.json`，**产物在 5090，本 checkout 无**）；`harness/PHASE1_API_PLAN.md:3`（底座规则「ctrl 新增先斟酌」）；`reference/PRIMITIVE_API.md:185`（`motion_planning_stereo` USABLE）；~~`harness/DESIGN_GRASP_AND_LOOP.md:86`（夹爪 7 DoF 阻塞）~~ → 该出处已于 2026-07-30 晚就地更正为「夹爪可动，原判系调用参数名错误」，见 `harness/DESIGN_GRASP_AND_LOOP.md` §5；**此条证据的失效不影响 D-09 的裁决与理由**（D-09 立足于 raw IK 零成功先例，与夹爪无关）。**建议**：下次改 `harness/PHASE1_M1A_STATUS.md` 时把裁决结论就地补记，否则这条会在下一轮被重新讨论。 |

### D-10 投递目标定 RSS 2027，放弃 ICLR / ICRA 2027

| | |
|---|---|
| **裁决** | 唯一投递目标 = **RSS 2027**（预计 2027-01/02 截稿）。ICLR 2027 明确排除（时间与形态均不匹配）；v1 里作为 stretch 的 ICRA 2027（约 2026-09 中截稿）作废。 |
| **日期** | 2026-07-29 |
| **背景** | v1 写的是「RSS/ICRA 2027」，其后又被一轮 ICLR 讨论覆盖，三个目标同时挂着。 |
| **理由** | 形态匹配：本课题的主结果是机器人执行与泛化，RSS 的评审口径对得上；时间匹配：ICRA 2026-09 截稿要求 M2 提前完成且数字强，而 Phase 1 至 2026-07-30 尚未有一次非特权端到端成功。 |
| **影响** | ① 里程碑按 RSS 2027 倒排——但 v2 §6 承诺的「另文更新」**至 2026-07-30 未产出**，这是一笔明账上的欠账；② v1 里程碑的 M1–M4 / W1–W16 全部日历日期（~08-23 / ~09-20 / ~10-18 / ~11-15）作废，执行单位改为 Phase 0/1/2；③ 其**止损判据与验收阈值仍然有效**，现由 `archive/ARCHIVE.md` §1 承载并继续作为闸门规范源被引用（含唯一成文的 20-seed 阈值：≥16/20 抓取+转正+对准、≥12/20 inserted+upright，见 §1.2）。 |
| **状态** | **生效** |
| **证据** | `archive/PROPOSAL_v2.md` 抬头「投递目标」行（RSS 主 / ICLR 排除）、附录 A（v1「RSS/ICRA 2027」→ v2「RSS 2027（定）」）、§6（「另文更新」承诺；⚠️ 该另文是否已由 `EXECUTION.md` 兑现，未裁决）；`archive/ARCHIVE.md` §6.7（ICRA stretch 作废）、§6.1（阈值仍有效、另文未产出）、§1.2（20-seed 阈值） |

---

### D-11 v1/v2 双树共存：按能力退役，不按代次删除

- **裁决**（2026-07-30）：`method/` `adapters/` `experiments/` 三棵 v1 树**保留**，逐模块退役而非整树删除。
  判断规则：**「`PROPOSAL.md` §6 的假设 A1–A7、或 `EXECUTION.md` §1 的实验与验收里，有没有哪条需要它？」**
  有 → 留（哪怕当前无人 import）；没有 → 删。
- **背景**：所有者提出「v2 是最新方案，之前没用的就该删」。这个判断对普通工程仓成立，本仓不成立。
- **理由**：v1 实现的不是「v1 的方案」，而是 **v2 的 H1 假设所依赖、`harness/` 尚未实现的部分**。
  实测 `grep 'freeze|frozen|digest|sha256|manifest' harness/*.py` 命中全是字符串字段——冻结协议
  在 harness 侧零实现；而 v2 §0 北极星与 H1 都明确要求「策略代码**冻结后**的 held-out 成功率」，
  v2 抬头「本轮决策」行写明「执行与冻结协议实验**后置到 Phase 1/2**」（后置，不是取消）。
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
| D-11 | Phase 0 先不动仿真机器人，只做「demo 视频理解」harness，执行与冻结协议后置到 Phase 1/2 | 2026-07-29 | 老板拍板；先把「理解层」做出可重复 CLI 与可量化指标，避免执行 infra 的不确定性淹没方法验证 | 生效（Phase 0 已于 2026-07-30 判达标：micro P=0.931 / R=0.865，两道门 P≥0.7、R≥0.8 全过，裁决「可开 Phase 1」） | `archive/PROPOSAL_v2.md` 抬头「本轮决策」行、§5.1（目标与范围）、§5.5（明确不做清单）；`harness/PHASE0_ROUND2.md:16`、`:35-39` |
| D-12 | 零污染原仓：k1-sys / knowin-world **零文件改动**，自家感知服务全住本仓 `harness/perception_service/` | 2026-07-30 | 共享依赖 dirty 且不由我方掌控；改它等于把我方实验的可复现性押在别人的工作树上。也不经 pipeline 注册 remote-namespace（那需要重启共享 pipeline），适配器直连自家服务 | 生效 | `harness/PHASE1_API_PLAN.md:24-26`、`:3`（底座规则「零污染原仓」）；`AGENTS.md:358-360`（Knowin World 不作子目录/submodule/vendored） |
| D-13 | Phase 0 的「歧义对区分 ≥3/4」验收门 ❌ 改判为**素材缺陷**，移交「素材构造」任务，不计入本轮门 | 2026-07-30 | 现有素材不含目标歧义：random 变体只随机布局，`deposit_coin` 单币单槽——门没过是素材问题不是方法问题 | 生效 | `harness/PHASE0_ROUND2.md:37-38`；⚠️ `AGENTS.md:278-279` 明令「引用时不得只报通过项」 |
| D-14 | `push_T` 任务与 `push` 控制原语挂起，M1 不实现 | 2026-07-30（老板指示） | `push_T` 是 v0.2 唯一恶化任务（P 0.70→0.538、R 0.778→0.636），且 demo 本身有缺陷（粗推越推越歪）压低上限；非抓取类通路优先级低于主线 | 生效 | `harness/PHASE1_API_PLAN.md:69`（「push_T 挂起(老板指示)」）；`harness/kwadapter.py:574-575`（`raise NotImplementedError`）；`harness/PHASE0_ROUND2.md:14`、`:31` |
| D-15 | 手写研究资产（`oracle/`、`tools/`、`*_AUDIT.md`）必须纳入版本控制；撤销旧 `.gitignore` 的「可再生成产物」排除 | 2026-07-30 | 已造成不可逆损失（`PREDICATE_AUDIT.md`、`PROVENANCE_CORRECTION.md` 永久消失）。判据：**能不能重新生成**，不是「看起来像不像产物」 | 生效（旧排除 = 已撤销） | `SECURITY.md:19-21`、`:14`（两档要求表「手写研究资产」行）；commit `138af50` |
| D-16 | 过时文档不删正文，顶部加 SUPERSEDED 声明块，逐条列明有效/作废/歧义/建议/未核实 | 2026-07-30 | 正文里的编号、阈值和表格仍被 `PROGRESS.md` 的 13 行历史实验索引引用；改编号会打断可追溯性。作废的是**执行策略**，不是**判据** | 生效 | `archive/ARCHIVE.md` §6.1（声明块与「正文一字未删」）、§6.3（历史引用面证据、改名建议与改动面比较） |
| D-17 | v3/v4 机器人代次错配用**仓外 override** 绕过（`~/phase1/cfg/sim_cfg.v3.yaml` + `ROBOT_CONFIG`/`ROBOT_MODEL` env 重启 pipeline）；`knowin_sim_v4verify` **不迁** | 2026-07-30 | 真因是 C++ IK 加载 v4 碰撞模型而 Genesis 跑 v3，产生与目标点无关的恒定幽灵自碰（`pair_id=263`）。零污染（D-12）要求不改他们的仓；`v4verify` 经核是 v2 的祖先 commit，迁移无收益 | 生效 | `harness/PHASE1_M1A_STATUS.md:1-10`（顶部更新块，含右臂前伸 0.24→0.678 零拒绝）；commit `3ce9d5e`；⚠️ `archive/ARCHIVE.md` §1.7 提示：这是**新的仓外配置面**，正式 benchmark 前须与 runtime 洁净度同等固化 |

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
| **未验证 / 待核** | 已知有分歧或未查 | Phase 0 成本口径（文档记「全轮 ~$8」，盘上 5 个 v0.2 run 的 `cost.jsonl` 合计 **$5.79** / 单任务 $0.66–1.62，差异已在 `PROGRESS.md:122` 标为待核，以文档为准待复核）；GitHub 仓库当前可见性（D-06）；M1a 实跑所用 `graph.json` 是否为 Phase 0 自动提取图（决定「视频→图」闸门能否在效果层判开启，`archive/ARCHIVE.md` §6.4） |

> **底线纪律（来自 `AGENTS.md` §8.4 与 `archive/ARCHIVE.md` §1.8）**：Phase 0 的 P/R 是**提取质量**，不是机器人成功率；编译步的「5/5 全绿」是 **fake 干跑 + AST 静态检查**；Phase 1 的 episode 全部跑在 **ORACLE-M1A** 模式且真实抓取次数为 0。三者任何一个被转述成「机器人效果」，都是本文件要防的那类事故。
