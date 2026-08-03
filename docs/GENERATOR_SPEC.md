# GENERATOR_SPEC：链长任务族生成器规格（T-GEN-1）

> **草案，待 PI 签字**（`TODO.md` §2A T-GEN-1 完成判据）。本文只定义 spec，不实现代码（实现是 T-GEN-2）。
> **上位依据**：D-19（新增生成器为第二贡献载体）、D-18（主张收敛到反传改序、砍最小性）、PROPOSAL v4 §2（脊椎 $F_i$）/§4（分离定理）/§6.1·§6.3·§6.4（E-AMB / E-CHAIN / E-ROB 对素材的要求）/§9（玩具感风险与两端锚定）/§10-13（三旋钮正交性开放问题）。
> **对齐义务**：κ 旋钮必须支撑 §4 分离定理的对抗构造（T-THM-2 硬要求：图和定理互为证据）；阶段原子必须编译到 `harness/vocab.py` 的封闭约束词表；金标图 schema 对齐 `oracle/insert_tubes_000.graph.yaml` 并过 `harness/validate.py` 的 T3 度量字面量扫描。

## 0. TL;DR

1. **生成器造的不是任务，是「链上耦合结构」**：给定链长 $L$、耦合强度 $\kappa$、歧义度 $\alpha$ 三旋钮，程序化拼装抓取/转移/放置/插入四族阶段，产出 (场景, oracle 金标图) 对。E-CHAIN（§6.3）招牌图的地基。
2. **三旋钮各有可计算定义 + 至少一个反例**：$L$=阶段计数、$\kappa$=当前阶段候选中「贪心最优但下游死路」的结构占比、$\alpha$=同指令下合法目标数与可区分度。反例分别钉死「什么不算耦合 / 什么不算歧义」——后者点名 D-13 踩过的坑。
3. **正交性不假设、显式检验**：正面回应 §13「长链天然更歧义」的纠缠风险——固定两旋钮扫第三个，承认哪些格点不可达（§4 表）。
4. **两端锚定真实任务**：insert_tubes 与 stack_bowls 表达为族内点（§6），素材复用 v4 现成 37 任务 suite 资产、不手写 yaml（P1-10 同规），压住 §9 玩具感质询。
5. **需 PI 批准的词表扩项：有 1 项**（`compat` 的跨阶段版本，§2.3）。诚实交底 4 条未验证假设（§7）。

---

## 1. 三旋钮可操作定义

每个旋钮给：可计算定义、取值档位、**至少一个反例**（什么不算）。反例是本 spec 的防线——旋钮若无反例，就无法证明它旋的是那个变量而不是别的。

### 1.1 链长 L（阶段计数）

| | |
|---|---|
| **定义** | $L$ = oracle 金标图 `nodes` 中 `role=core` 的阶段数（`home`/纯 retreat 等收尾节点不计，对齐 `enrich.py` 的 `role="core"` 分组口径）。一个「阶段」= 一次目标类别切换（`STAGE_VOCAB` 的一个原子对应一次关键事件切分），**不是**一次原语调用。 |
| **档位** | $L \in \{2,3,4,5\}$（PROPOSAL v4 §6.3）。 |
| **计数规则** | insert 一根管 = grasp + insert 两阶段；transport 若无独立约束（纯自由移动）**不单独计阶段**，并入相邻 grasp/place 的后条件。 |
| **反例（什么不算增 L）** | 把同一 grasp 拆成 approach+grasp+lift 三个 `STAGE_VOCAB` 词条**不算** $L$ 从 1 变 3——它们共享同一 typed hole、同一 `region_grasp` 约束、无独立候选集。$L$ 计的是**独立候选决策点**（有独立 $C_i$ 的阶段），不是词表切分粒度。 |

### 1.2 耦合强度 κ（下游反传的因果力密度）

| | |
|---|---|
| **定义** | 对阶段 $S_i$，令 $C_i$ 为候选集。$\kappa_i = \dfrac{\lvert\{c \in C_i : g(c)=\text{top-1 by L2 序},\ \max_{c'} F_{i+1}(c')=0\}\rvert}{\lvert C_i\rvert}$——即「本阶段贪心/L2 最优、但使下游不可行（$F_{i+1}\equiv 0$）」的候选占比。族级 $\kappa = \max_i \kappa_i$（最坏阶段）或 $\text{mean}_i$（预注册二选一，T-REG 钉死）。$F_i$ 定义见 PROPOSAL v4 §2.1。 |
| **档位** | 两档（v4 §6.3）：`low`（$\kappa \approx 0$，贪心与反传同解）/ `high`（$\kappa \ge \kappa^\*$，贪心 top-1 以概率 $\ge \epsilon$ 命中死路，对齐 §4 下界的 $\epsilon(\kappa)$）。 |
| **操作化构造** | 用词表约束制造下游冲突：`region_grasp` 抓某区域 → 该抓法使后续 insert 阶段的 `clearance`/`axis_parallel` 无解（试管夹下半段则插入撞架，见 insert_tubes 的 `height_fraction:[0.55,0.85]` 注释）；或 `resource`/`collision_avoid` 边使先占的槽/位挡死后续候选。 |
| **反例（什么不算耦合）** | **纯难度提升但贪心也能过**不算 κ。例：把候选集加噪、把公差 `max_angle_deg` 收紧、把物体摆密——这些降低单阶段成功率，但**贪心最优候选仍落在下游可行域内**（$\max_{c'}F_{i+1}>0$），$\kappa_i=0$。κ 旋的是「贪心最优 ↔ 下游可行」的**结构性错位**，不是任务变难。判据：关掉 $F_i$ 递归项（退化为贪心臂）后成功率是否掉——不掉则 κ 名不副实。 |

### 1.3 歧义度 α（合法目标/策略的数目与区分度）

| | |
|---|---|
| **定义** | 同一条欠定指令下，物理上都成立的目标/策略数 $m$，及 demo 对其的可区分度：$\alpha>0$ 当且仅当 (a) $m\ge 2$ 个合法解，且 (b) 存在 demo 使编译产物**选定其中一个**（不同 demo → 不同但各自合法的产物，v4 §6.1 判据）。$\alpha=0$ = 目标唯一。 |
| **档位** | $\alpha \in \{0, >0\}$。E-CHAIN 主轴用 $\alpha=0$（隔离 κ 效应）；$\alpha>0$ 素材由 T-GEN-3 承载（E-AMB / E-ROB）。 |
| **操作化构造** | 通过 `slot`/`selection_rule` 或对称物体制造多合法解：如「插任一空槽」（$m$=槽数）、「叠任意两碗」（$m$=组合数），再用两段 demo 分别演示不同选择。 |
| **反例（什么不算歧义，点名 D-13）** | **布局随机但目标唯一不算 α>0。** D-13 正是踩此坑：`push_T` random 变体只随机布局、`deposit_coin` 单币单槽——目标始终唯一，Phase 0 的「歧义对区分 ≥3/4」门过不了是**素材无歧义**（改判素材缺陷），不是方法缺陷。α 要求的是**多个合法目标**，不是**同一目标的多个初始摆位**。生成器必须区分：改 seed 换布局 ≠ 升 α；只有增加合法目标数（多槽/对称/多组合）才升 α。 |

---

## 2. 阶段原子词表

### 2.1 四族原子（**不含 push**，D-14 / D-19 影响⑤）

| 族 | `action_class` | 对应 `STAGE_VOCAB` 词条 | 产生的约束模板（**仅取自 `vocab.py` 封闭词表**） | typed holes |
|---|---|---|---|---|
| **抓取** grasp | `grasp` | approach, grasp, lift | `region_grasp(obj, region)`、`approach_direction(cone)`、`axis_vertical(axis)` | `pose_se3`(grasp), `axis_3d`(approach) |
| **转移** transfer | `transport` | reorient, transport | `carry(relation)`、`clearance(obj_a, obj_b)`、`above(obj_a, obj_b)` | `point_3d`(waypoint), `scalar`(clearance 阈值经洞绑定) |
| **放置** place | `place` | pre_align, place, release | `center_align(obj_a, obj_b)`、`above(obj_a, obj_b)`、`order(stage_sequence)` | `pose_se3`(place), `point_3d`(target) |
| **插入** insert | `insert` | pre_align, insert | `axis_parallel(axis_a, axis_b)`、`inside(obj_a, obj_b)`、`center_align`、`approach_direction(cone)` | `pose_se3`(insert), `axis_3d`, `scalar`(insert depth), `runtime_condition`(force/contact) |

**纪律**：约束名只能是 `CONSTRAINT_VOCAB` 的 10 个键；`region` ∈ `GRASP_REGIONS`（top/upper_body/middle/bottom/rim/handle）；`cone` ∈ `APPROACH_CONES`（top_down/side/oblique）；hole `type` ∈ `HOLE_TYPES`。**零度量字面量**——一切数值走 hole（D-03，过 `validate.py` T3 扫描）。

### 2.2 阶段拼装规则

- 链 = 四族原子按 `order` 边串联，相邻阶段以 `carry` 边或 `collision_avoid`/`resource` 边传递耦合（这些边是 κ 的载体）。
- 每个 insert/place 阶段的候选集 $C_i$ 由生成器场景决定，oracle 图给约束不给数值（洞由运行期候选填，v3 §2.1）。
- $L$ 增长 = 追加「grasp→(insert|place)」对；κ 增长 = 在某对上注入下游冲突约束；α 增长 = 增加 `selection_rule` 的合法目标数。

### 2.3 需 PI 批准的词表扩项（**不许悄悄混入**）

| 扩项 | 为什么现词表不够 | 建议处置 |
|---|---|---|
| `compat(c, c')` **跨阶段候选兼容性谓词** | 现 `CONSTRAINT_VOCAB` 全是**单阶段内**关系（`clearance`/`above` 是同帧两物体，不是跨阶段两候选）。反传 $F_i$ 的核心项 $\max_{c'}\mathbf{1}[\mathrm{compat}(c,c')]F_{i+1}$（v4 §2.1）需要一个表达「阶段 $i$ 选 $c$ 时，阶段 $i{+}1$ 的 $c'$ 是否可行」的**跨阶段**谓词。这是 v4 §10-11 开放问题、T-BP-2 的 `compat.py` 载体。 |  **不进封闭提取词表**（提取器不该输出它——它不是 demo 能标注的关系，是运行期算子）。建议：作为 `harness/compat.py` 的**算子**存在，金标图用现有的 `resource`/`collision_avoid` 边**隐式编码**跨阶段冲突，由 compat 算子在运行期消费。PI 若同意此隔离，则**词表零扩项**；若坚持金标图显式声明跨阶段约束，才需新增 schema 级（非 vocab 级）边类型。**待 PI 拍板。** |

> 除上表外，本 spec 未引入任何 `vocab.py` 之外的约束名。

---

## 3. oracle 金标图 schema

对齐 `oracle/insert_tubes_000.graph.yaml`（现行实例，schema_version 0.1）。生成器每个任务产 (`scene`, `<task>_NNN.graph.yaml`) 对。

**顶层字段**（与现行一致）：`schema_version` / `graph_id` / `task_id` / `instruction` / `source_demo` / `bindings` / `objects` / `nodes` / `edges` / `task_success` / `compile_notes`。

**每个 `node`**：`id` / `action_class`（四族之一）/ `demo_segment` / `actor_arm` / `objects{manipulated,target}` / `preconditions` / `postconditions` / `constraints[]` / `verify` / `recovery`。

**typed hole 六要素齐全**（AGENTS.md §2.3；每个 hole 必带）：① 类型/shape/单位/坐标系；② 合法搜索域；③ 候选求解器；④ 求解输入；⑤ 运行期验证方式；⑥ 有界恢复策略。现行图以 `frame`/`approach`/`free_dof`/`locked_dof`/`recovery.allowed_changes` 承载——生成器沿用，但**所有 `*_hint` 数值必须替换为洞引用**（现行图的 `world_z_offset_hint: 0.075` 等 `hand_tuned` 值是 oracle 上界专用，生成任务里不许出现，否则 T3 失败）。

**零度量字面量**（D-03，过 `validate.py`）：`constraints[].args` 内不得出现数值/带单位串（`_is_metric_literal` 判违规）；`tube0` 这类带数字标识符**不违规**。阈值（`max_angle_deg`/`force_threshold_n`/`max_travel_m`）在生成任务里一律走 `scalar`/`runtime_condition` 洞。

**edges**：`sequence`（`carry_constraint` 载耦合）/ `resource`（`distinct_empty_slot` 类）/ `collision_avoid`（已放置物成障碍）——后两类是 κ 的图上载体。

---

## 4. 三旋钮独立性检验设计

**正面回应 §13 纠缠风险**（长链可能天然更歧义）：不假设正交，用「固定两旋钮扫第三个」构造 + 承认不可达格点。

| 检验 | 固定 | 扫 | 期望（正交则成立） | 纠缠来源与处置 |
|---|---|---|---|---|
| **L ⊥ α** | κ=low, α=0 | L∈{2..5} | 各 L 下 $m$(合法目标)=1 恒定 | **纠缠真身**：长链更多阶段 → 更多引入多合法解的机会。**处置**：强制每阶段 `selection_rule` 唯一（distinct 但确定 demo_assignment，如 insert_tubes 的 `left_to_right`），使 α 不随 L 漂移。**可达性**：可达。 |
| **L ⊥ κ** | α=0 | L×κ 全 8 格 | κ 由注入冲突边数控制，与 L 独立 | κ=`mean` 口径下长链会稀释单阶段 κ_i；**处置**：κ 用 `max_i` 口径（族级取最坏阶段），使 κ 不被链长稀释。**可达性**：可达。 |
| **κ ⊥ α** | L 固定 | κ×α | κ=下游可行性错位，α=目标多义，构造上独立 | 多合法目标（α>0）可能自带下游冲突（选错槽→挡死）。**处置**：α 的多解设计为**下游对称**（任一合法目标都不害后续，$\kappa_i=0$），把「多义」与「错位」分离。**可达性**：可达。 |

**承认不可达格点**：`(L=5, κ=high, mean 口径)` 下 κ_mean 难达 high（长链稀释）——**故 κ 全程用 `max_i` 口径**（并入 T-REG 预注册）。`(α>0, κ=high)` 若坚持多解**非**下游对称，则该格点纠缠不可分离——本 spec **不承诺**该格点，E-ROB（§6.4）只用下游对称的 α>0 素材。

**检验判据**（T-GEN-2 交付时验证）：固定两旋钮时，第三旋钮的目标度量（$L$/$\kappa$/$m$）单调可控且互不漂移，漂移超 1 档 = 构造失败回炉。

---

## 5. κ 与分离定理的对齐（T-THM-2 接口）

**要求**（v4 §4 纪律）：κ 取极值（`high`）时复现 §4 对抗构造——每阶段贪心以概率 $\ge\epsilon$ 选中死路，成功率 $\le(1-\epsilon)^{L-1}$ 几何衰减；反传在候选覆盖假设下 $\ge 1-L\delta$ 平坦。

**具体极值构造例（文字 + 示意，$L$=3，κ=high）**：

链：`grasp_tube → insert_tube_A → insert_tube_B`（两根管进同一 rack 的两槽）。

- **阶段 1 grasp**：候选集 $C_1$ = {抓上半段 `upper_body`, 抓下半段 `bottom`}。L2 偏好序（demo 提取的 `region_grasp`）在**本阶段**对两者近乎无差（都能稳抓、都过 L1 硬可行）——故贪心/L2 top-1 以概率 $\epsilon\approx 0.5$ 落在 `bottom`。
- **死路**：抓 `bottom` → 阶段 2 insert 时夹爪占住管下段 → 与 rack 的 `clearance` 无解（`axis_parallel` 可满足但 `inside` 到底前撞架），$F_2(\text{bottom})=0$。抓 `upper_body` → $F_2>0$。
- **阶段 2 insert_A**：选先插的槽。候选 = {左槽, 右槽}。选左槽 → 阶段 3 insert_B 只剩右槽（`resource` 边 `distinct_empty_slot`），且已插的 A 成 `collision_avoid` 障碍。若阶段 2 贪心选了「离 base 近」的槽而它恰好挡死 B 的唯一可达入路 → $F_3=0$。
- **贪心臂**：每阶段独立看局部质量，两个决策点各以 $\ge\epsilon$ 命中死路 → 成功率 $\le(1-\epsilon)^{2}$。**反传臂**：$F_1$ 递归看到「bottom→撞架」「近槽→挡死B」，改序避开 → 平坦。

**对齐检查表**（进 T-THM-2 §2 纸面对照）：

| §4 定理元件 | 生成器旋钮/构造 |
|---|---|
| $\epsilon(\kappa)$（贪心命中死路概率） | κ=`high` 档：$\kappa_i \ge \epsilon$（§1.2 定义直接给出 $\epsilon$） |
| 链长 $L$（几何衰减指数） | $L$ 旋钮 |
| 候选覆盖假设（$\ge 1-\delta$ 含可行候选） | 生成器保证每阶段 $C_i$ 含 ≥1 下游可行候选（否则任务本身无解，回炉） |
| 回溯代价随 $L$ 指数（推论） | κ=high 下 B2 回溯臂的重试预算即该推论的实测面 |

---

## 6. 外部效度锚（两端锚定，压 §9 玩具感）

把真实任务表达为族内点。**素材来源**：复用 v4 现成 **37 任务 suite** 资产做底料（P1-10 同规：seed 筛，**不手写任务 yaml**）——本 spec 只描述来源与筛选流程，不实现。

| 真实任务 | L | κ 估计 | α | 依据 |
|---|---|---|---|---|
| **insert_tubes** | **6** core 阶段（grasp/insert × 3，`go_home` 不计） | **high**：grasp `height_fraction` 错位撞架 + 三槽 `distinct_empty_slot` + `collision_avoid`（已插管成障碍）三重下游冲突 | **0**：`demo_assignment: left_to_right` 目标唯一 | `oracle/insert_tubes_000.graph.yaml`（现行金标实例，直接读出旋钮） |
| **stack_bowls** | **~4–6** core 阶段（pick/place/stack 交替，见 goldset `order` 边 `s0:pick<s1:place<s2:pick<s3:stack<s5:pick<s6:stack`） | **mid–high**：bottom-up 堆叠顺序 + `rim` 抓法（抓 body/bottom 挡后续 nesting，见 goldset note）→ 顺序错则下游无解 | **0**（现素材）；对称碗可升 α（「按颜色叠 vs 按大小叠」，T-GEN-3 造） | `harness/goldset/stack_bowls_gold_v2.json` |

**「生成任务与真实 4 任务同底座」方案**：生成器场景从 37 任务 suite 抽取物体资产（管/碗/槽/rack 等），按旋钮拼装布局与约束图；不引入 suite 之外的新资产、不手写 task yaml。**≥1 个真实任务落在族内作对照点**（insert_tubes 已满足，L=6/κ=high/α=0）——这是 §9 两端锚定的下锚，压住「合成任务外部效度」质询。

---

## 7. 诚实交底：未验证假设 + T-GEN-2 开工前必查

### 7.1 本 spec 未验证的假设

| # | 假设 | 风险 | 现状 |
|---|---|---|---|
| A1 | 37 任务 suite 资产覆盖四族原子（grasp/transfer/place/insert）所需物体 | 若缺 insert 类资产（rack/槽），高 κ 链造不出 | **未查**：本 spec 未清点 suite 资产清单。 |
| A2 | 生成场景 sim 加载成功率足够（T-GEN-2 判据要求每档 L×3 seed 加载成功） | BLK-2 sim 串行 + 资产拼装可能碰撞/穿模 | **未验**：加载成功率无实测。 |
| A3 | κ 定义可实测——即 $F_{i+1}$ 在生成场景上可算（依赖 compat 算子，T-BP-2） | compat 未实现前 κ 只是纸面定义，无法在 T-GEN-2 交付时验证「关掉递归项成功率掉」 | **依赖**：κ 的**实测**验证要等 T-BP D0 后；T-GEN-2 只能验证**构造侧**（注入了冲突边），不能验证**效果侧**。 |
| A4 | `region_grasp` 的 region 真能影响抓取位姿（κ 靠 grasp 区域制造下游冲突的前提） | **DECISIONS §4-G1 明载**：当前 `solve()` 只对 hole 名字串匹配，改 region（upper_body→bottom）产生的抓取位姿**逐比特相同**——即今天 region 不影响抓取 | **已知落差**：κ 经 grasp 区域的载体在 D0（因果链闭合）前**无效**。生成器可造图，但反传要真起作用须等 G1 修复。 |

### 7.2 T-GEN-2 开工前必须抽查

1. **清点 37 任务 suite 资产**，确认四族原子的物体齐备（消 A1）；缺项回本 spec §2.1 调词表/换族。
2. **抽 1 个 L=2 场景做 sim 加载 smoke**（消 A2），失败率高则先解拼装碰撞。
3. **确认 κ 的验证口径分两步**：T-GEN-2 只验构造侧（冲突边注入 + T3 过 + md5 确定性）；效果侧（关递归项成功率掉）挂到 D0 后（A3/A4）——**不许在 T-GEN-2 判据里假装 κ 已实测生效**。
4. **PI 拍板 §2.3 的 compat 扩项处置**（隐式编码 vs 显式 schema 边），否则 §5 的对齐构造在图上无落点。

---

## 8. T-GEN-2 实现最大风险点（一句话）

**κ 的「效果侧」在 D0 前不可验证**（§7.1 A3/A4 + DECISIONS G1）：生成器能造出「注入了下游冲突边」的图，但由于当前 `solve()` 不消费 region、compat 未实现，「关掉反传项成功率掉」这个 κ 名副其实的判据要等因果链闭合（D0）——T-GEN-2 交付时只能证明**构造正确**（图过 validate、确定性、含冲突边），不能证明**耦合生效**，二者不可混为一谈，否则 E-CHAIN 招牌图会建在未验证的 κ 上。
