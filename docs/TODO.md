# docs/TODO.md —— 执行 TODO（按依赖排序 · 三批 · 决策点）

> **版本**：v2（2026-08-03，按 D-18/D-19 重排；上位依据 `PROPOSAL.md` v4）。v1（2026-07-31）的任务编号、判据与人日**未被砍单波及者原样保留**；被砍任务就地划线标注不删行（D-16 同款纪律）。
> **本文只回答三个问题：做什么、什么顺序、什么算完成。**
> 实验设计、判据推导、预算模型在 `docs/EXECUTION.md`（§1 实验与验收、§2 代码框架、§4 预算），本文不复述。**E-AMB / E-DO / E-CHAIN / E-ROB 四个新实验尚未注册进 EXECUTION §1**——注册是任务 T-REG，开跑前必须完成。
> 与 `EXECUTION.md §3` 的关系：本文**取代** §3 的任务清单（§3.4 阻塞项、§3.5 sim 排班表继续有效并在本文 §7 重列）。冲突处以本文为准。
> **执行模式（2026-08-03 起）**：执行由 Opus 会话按本文任务条目认领，每条的「完成判据」即验收面；验收由主线会话把关——测试类任务交付**红/绿两份输出**，实验类任务交付 run 目录 + 判据对照表。

---

## TL;DR（先看这十条）

> **⚡ 排序更新（2026-08-03 晚，D-21「系统效果先行」，覆盖下文与之冲突的排期表述）**：近期唯一目标 = 已测任务（insert_tubes / stack_bowls，备选 deposit_coin）的**端到端真实成功率**（内部 robodojo 评测计分）。路径：因果链 MVS（不变，进行中）→ D0 → **直接进执行链** P1-02（运动规划）→ P1-04（非特权感知最小集）→ P1-08（闭环补偿）→ **P2-04 提前**（首批 20-seed 数字）+ CaP-Agent0 基线。**T-THM 暂停；T-GEN-2 / T-BP / T-ROB / E-CHAIN 后置**到效果里程碑（≥1 任务真实抓取端到端成功且可归因）之后——排序不是取消。

> **⚡ D-23（2026-08-03 深夜，采纳方案 v5「Demo-Conditioned Deliberative Execution」，上位依据 `PROPOSAL.md` v5）**：
>
> **① 运行时形态按 v5 改写**：**D-01「运行期零 LLM」已撤销**，运行时 VLM 在三处且仅三处回归——**选择深思**（L4③）、**有界修正**（L7 第②级）、**验证证据**（L6，`passed` 仍由 `gates.py` 几何谓词算，VLM 只出证据）。**D-18 的「砍 L5」部分撤销**：修正层以「修正阶梯第②级」形态复活，D-18 其余条款（一主张两证据 / 砍 G1G2G3 消融 / 砍最小性措辞 / 真机不作主结果前提）**全部不变**。因此下文所有「运行期零 LLM」「corrector 已砍」的表述**以本块为准**：~~P2-03 corrector~~ **不恢复旧编号**，改由新任务 **T-COR** 承载（判据按 v5 §2.3 重写）。D-22② 的 E-A6-swap-static 移除**不受影响**。
>
> **② 新增 §2C 五条 L4 工作流占位（只占位，判据与人日待 PI 签字后补）**：
>
> | 编号 | 一句话 | 依赖 |
> |---|---|---|
> | **T-SIM** | **预演引擎**：fork 当前 Genesis 状态，虚拟执行候选 + 后续链前缀，收集各阶段 gate 结果（v5 的新核心模块，全场空位） | 效果里程碑（单集打通）+ 感知 |
> | **T-SEL** | **VLM 深思**：候选覆盖层渲染进场景图，对照 demo 关键帧与约束**排序**，必须引用支持该选择的约束；**不打分、不出连续量** | **T-SIM**（要看预演后果）+ L3 感知 |
> | **T-COR** | **修正阶梯**四级：gate 重试 → 有界 VLM 修正 `{-1,0,+1}`×固定步长 → 回 L4 重选 → trace 引导代码修复（每 seed ≤3 次重放，debug/eval seed 严格分离） | **T-SIM**、**T-SEL** |
> | **T-QC** | **demo 质检**：出画 / 跨镜头 / 相机运动 / 无交互四类废片过滤（L0） | 无（可提前独立开工） |
> | **T-ALIGN** | **分阶段进度对齐**：SDTW+TCC 自监督对齐，按链上阶段边界分段（治 DTW 在重复子阶段串段） | L1 抽取产物 |
>
> **③ 当前执行顺序不变**：**修爪 → 单集 → 感知 → L4**。上表五条**排在 L4 阶段**，不插队、不改动下文既有任务表的编号、判据与人日。§2A/§2B 的 D-21/D-20 治理**一并不变**。
>
> **④ 成本约束撤销**（D-23③）：不再以「运行期成本/延迟」为由删减模块；成本表**仍要报**但降为如实记账，不作可防守点。

1. **第一项且唯一的第一项仍是接通约束因果链。** 当前 `solve()` 一次都不读约束、`gates.evaluate()` 一行不读 `stage["constraints"]`（它那个叫 `constraints_hold` 的字段算的其实是 acceptance）。在这条链通之前，funnel、消融、验收率**全部数字无意义**——不是精度不够，是量根本没连上因。改动集见 §1，共 6 个文件、约 3.0 人日。D-18/D-19 没有改变这一条的地位：**E-DO / E-CHAIN / E-ROB 全部依赖它**。
2. **D-18/D-19 已落账（2026-08-03），砍单落地**：~~P2-03 corrector~~、~~P2-07 E-ABSTR~~、~~P0-07 契约变体~~ 作废；§9 未决 #1/#2/#8/#11 关闭（处置更新块见 §9 头部）；漏斗 L3 从「不进 MVS」**升格为方法主体**，由 T-BP 承载并推广为 k 步链式反传。
3. **新增五条工作流（§2A）**：T-THM（分离定理，PI 亲自，W1 起纸面开工）／ T-GEN（链长任务族生成器，**与 §2 的 A 串行线文件集不相交、全程并行**）／ T-BP（k 步反传算子，D0 后）／ T-ROB（版本空间鲁棒反传，D0 后）／ T-REG（四个新实验的判据注册，PI 签字）。
4. **招牌实验 E-CHAIN 有且只有两个前置**：因果链闭合（D0）+ 生成器 v0（T-GEN-2）。两线并行推进，W4 可跑 oracle 态试点（只作上界，D-05 口径）；主数字感知态**已裁决（D-20）**：官方口径 = 非特权感知链，**P1-04/05/06/07 升格必做、不可裁剪**。榜单轨见 §2B。
5. **「排序偏好」裁决的判据重预注册义务不变**：`CC-0`/`CC-1′`/`CC-2′`/`P-3′` 需 PI 签字后 commit（EXECUTION §0 预注册纪律），开跑前不许改。CC-0（K≥3、top-1 改变率 ≥60%）仍是 D0 独立闸门。
6. **EXECUTION §2.5 #1b 字面执行会破契约。** 它写「`solve()` 增加本阶段 `constraints` 入参」，但 `contract.py:19` 是 `def solve(self, hole_name: str)` 单参、policy 侧调用形态是 `rt.solve("tube_left_grasp_pose")`。正解是签名不动、`KWRuntime.solve()` 内部从 `self._hole_index[hole_name]` 拿到的 stage 里自取 constraints 委派给 `binding`。本文按此执行。**D-18 之后 `contract.py` 不再有任何计划内破例点**（原 P2-03 破例窗口随 corrector 一起消失）。
7. **三个先修破口全部排进 P0，不进 P1**（原 TL;DR-6 原文有效）：破口①→ P0-02/P0-14；破口②→ P0-04/P0-03；破口③→ P0-15。P0 还没产真 episode，此刻去特权成本最低。
8. **GraspNet 定位不变**：P1 后段对照臂（P1-11），不在关键路径。
9. **总量口径更新**：P0 17.5（原 18.5，砍 P0-07）+ P1 21.5 + P2 11.2（原 16.2，砍 P2-03/07）+ 新增 §2A 12.0 = **合计 ≈ 62 人日**；但关键路径变短了——**MVS 双线 = 因果链 9.0 人日 ∥ T-GEN-1/2 3.5 人日，两周内 E-CAUSAL-OFF 与生成器 v0 同时到位**，E-CHAIN 试点 W4 可跑。真开发清单更新：corrector 工位删除，新增 `taskgen`/`backprop`/`compat`/`robustrank`。
10. **验收纪律（执行交给 Opus 后生效）**：任何任务不得改动本文之外的判据；测试先红后绿、两份输出入 PR；涉及 `kwadapter.py`/`gates.py` 的 A 串行线任务禁止并行认领；B 组并行任务必须 worktree 隔离（全局纪律见 CLAUDE 侧「并行调研、串行执行」）。

**证据分级图例**（全文强制，混用即失效）：
`✅实测` = 已跑过、有输出/md5/日志 · `⚙代码在` = 仓里有代码但零调用或未测 · `📋计划` = 只在文档里，一行代码没有

---

## 1. 第一项：接通约束因果链（BLK-3 的唯一解法）

### 1.1 现状（全部经本次代码核对）

| 断点 | 位置 | 实况 |
|---|---|---|
| **环 1：约束不进 solve** | `harness/kwadapter.py:295-321` | `:296` 把 `hole` dict 绑进变量后**一次都没再引用**；`:299-321` 全是 `hole_name.lower()` 子串匹配；参照物取自 `stage_objects.target`，**从不取自约束**。`:305` 抓取点 = 物体中心 + 常量 `top - 0.03`；`:317` `value=0.05` 硬常量 |
| **环 2：约束不进 gate** | `harness/gates.py:60-117` | `:63` 只读 `stage["acceptance"]`，`stage["constraints"]` **一行不读**；`:71` 的字段名 `constraints_hold` **名不副实**——它算的是 acceptance 的合取，这个命名本身会让所有读报表的人误判 |
| **环 3：fail-open 五处** | `kwadapter.py:611/613`、`gates.py:55-56/68-69/94` | `:611` `else: detail="unchecked"` 但 `ok` 仍是 `:589` 初始化的 `True`；`:613` `except → ok=True`；`gates.py:55-56` 与 `:68-69` 的 `except → False`（方向相反但同样静默）；`:94` `effect_ok = … or (not observable)` |
| **环 4：oracle 进了控制原语** | `kwadapter.py:522-534`（`lift`）、`:547-577`（`lower_until:565-567`） | `lower_until` 拿 `rt.probes()` 的 `root_in_bbox ∧ axis_aligned` 当**停止判据**——特权量进了方法路径的控制回路，不是只在 evaluator 侧 |

### 1.2 精确改动集（6 文件 · 3.0 人日 · 严格串行）

| # | 文件:行 | 改法 | 不许做 |
|---|---|---|---|
| C-1 | `harness/kwadapter.py:295-321` | 整块删除，替换为 `return binding.solve_hole(hole, stage=st, constraints=st.get("constraints") or [], rt=self)`。**`KWRuntime.solve` 签名保持 `(self, hole_name)` 不变** | 不许改 `contract.py:19` 的签名（见 TL;DR-5） |
| C-2 | `harness/kwadapter.py:296` | 删掉 `.get(..., (self._current_stage, {...}))` 的兜底默认值 → `KeyError` 直接 `raise UnsolvedHole`，归因记 `L2_bind` | 不许回退到「当前阶段猜」 |
| C-3 | **新** `harness/binding.py` | 按 `hole["type"]` 派发 5 个求解器（`pose_se3`/`axis_3d`/`point_3d`/`scalar`/`runtime_condition`，来自 `vocab.py:26`）；参照物从**约束 args** 取（`center_align.obj_b` / `inside.obj_b` / `region_grasp.region`）；非 world frame 先过坐标变换纯函数；`solver_hint` 只用于选求解器 | `solver_hint` 不许建任务分支 |
| C-4 | **新** `harness/regions.py` | `region_grasp` 的 6 个值 → **单调偏好函数**（PI 今日裁决，非区间硬阈）：`upper_body → f(s)=s`、`bottom → f(s)=1−s`、`middle → f(s)=1−|s−0.5|·2`、`top → f(s)=s²`；`rim`/`handle` 显式 `UNCHECKABLE`（几何特征检测，v1 不做，**不许用区间硬凑**）。`s = (p·u − min)/(max − min)`，`extent` 用**全边长** | 表里出现任何任务名或物体名 = 硬失败 |
| C-5 | `harness/kwadapter.py:488-497` | `approach(target, cone=None)` 的形参 `cone` 当前**零引用**，改为真正传给 `regions` 参与排序 | 不许在候选生成阶段消费 cone（会让 E-CAUSAL 变同义反复） |
| C-6 | `harness/gates.py:60-117` | 拆两个字段：现有 `:71` 改名 `acceptance_hold`；新增真 `constraints_hold` 读 `stage["constraints"]`，`holds=="throughout"` 在 entry/exit 各查一次（中途违反记 `violated_midway`），`holds=="at_end"` 并入验收；`:110` `passed = acceptance_hold and constraints_hold and (effect_ok or not strict)` | 不许保留旧的 `constraints_hold` 语义（报表口径会静默换掉） |

### 1.3 验证方法（**判据已按 PI「排序偏好」裁决改写，需重新预注册**）

| 测试文件 | 断言 | 状态 |
|---|---|---|
| `tests/test_constraint_causality.py`（新） | **先红后绿**。同一候选集（要求 `K≥3`）分别在 `region_grasp(obj,"upper_body")` 与 `region_grasp(obj,"bottom")` 下排序：① top-1 的 `height_fraction` 满足 `s_upper > s_bottom` ② 两次排序的 Kendall τ < 0（**旧判据 `\|z(upper)−z(bottom)\| ≥ 0.5×h` 作废**——硬阈值取消后无对象） | 📋计划 |
| `tests/test_solve_dispatch.py`（新） | 86 洞全量：① 命中 86/86 ② `coin_pose`/`retract_pose`/`push_direction` 三个已知误派归位 ③ 未知 type → `UnsolvedHole` | 📋计划；86 洞 / 误派 30 / 兜底 28 / 非 world frame 43 为已核实数字 |
| `tests/test_gates_constraints.py`（新） | 造一个 `constraints` 全违反但 `acceptance` 全过的 stage → 断言 `passed=False`（旧行为是过） | 📋计划 |
| `tests/test_predicates.py`（新） | ≥20 例；三值 + margin；10 个词表约束 ≥8 个有可执行谓词 | 📋计划 |
| 门禁三连 | `pytest tests/ adapters/tests/ -q`（基线 ✅**36 passed**，目标 ≥43 且原 36 全绿）· `scripts/public_release_check.py --profile private` · `python3 -m harness.cli compile --task insert_tubes` 干跑不回退 | 前两条 ✅实测可跑 |

**必须同时预注册的三条新判据**（PI 签字后 commit 到 `experiments/causal/variants.json`，开跑前不许改）：

| 新 ID | 内容 | 取代 |
|---|---|---|
| **CC-0**（新增） | 筛选力下限：`K≥3` 的样本中，改 region 标签导致 top-1 改变的比例 **≥60%**。低于此 ⇒ L2 层退化为恒等映射，「排序偏好」的取舍失败 | 无（这是新裁决新引入的风险，原方案没有对应条目） |
| **CC-1′** | 见上表 test_constraint_causality 的 ①②，两条同时成立 | CC-1 |
| **CC-2′** | 6 region × 4 任务 = 24 格：≥22 格产生不同的 top-1 **或** τ<1；`regions.py` 任务名扫描 0 命中 | CC-2（原「≥22/24 产生不同 pose」在无淘汰的排序语义下不可测） |

---

## 2. P0 —— 不依赖机器人（或只需只读探针）

**目标：接通因果链 + 搭起分层接口骨架。** 并行列 `A`=串行主线（都改 `kwadapter.py`/`gates.py` 同一批行，必须串行）；`B`=可并行写（文件集不相交，需 git worktree 隔离）；`C`=需 5090 只读探针。

| 编号 | 一句话 | 依赖 | 产出物 | 完成判据 | 人日 | 并行 | 类型 |
|---|---|---|---|---|---|---|---|
| **P0-01** | 写反事实测试并让它**红** | 无 | `tests/test_constraint_causality.py` | 红 run 输出入 PR（这是 W1 的核心交付物，PI 当场看红/绿两份） | 0.5 | A | 测试 |
| **P0-02** | `binding.py`：solve 按 `type` 派发 + 消费本阶段 constraints（改动 C-1/C-2/C-3） | P0-01 | `harness/binding.py` | `test_solve_dispatch` 86/86；`test_constraint_causality` 转绿；`hole` 不再是死变量 | 1.5 | A | **真开发** |
| **P0-03** | `regions.py`：region/cone → **排序偏好**（改动 C-4/C-5） | P0-02 | `harness/regions.py` | CC-2′ ≥22/24；任务名命中 0；`approach()` 真消费 `cone` | 0.5 | A | 薄 |
| **P0-04** | `gates.py` 消费 `stage['constraints']`（改动 C-6） | P0-02 | 改 `harness/gates.py` | `test_gates_constraints` 绿；`acceptance_hold`/`constraints_hold` 字段级可分 | 1.0 | A | 真开发 |
| **P0-05** | `predicates.py`：约束 → 三值检验函数，**五处 fail-open 全部归零**（破口②） | 可并行起草，落地依赖 P0-04 | `harness/predicates.py` | ≥8/10 词表约束有可执行谓词；`PASS/FAIL/UNKNOWN` + margin；`unchecked` 归零；`tests/test_predicates.py` ≥20 例 | 2.0 | A | **真开发** |
| **P0-06** | `fakerun` 不许把 `push` 吞成绿（改动 #4） | 无 | 改 `harness/fakerun.py:49-57` | 从 `__getattr__` 白名单删 `push` + 显式 `raise NotImplementedError`；4 份 `push_T*` policy 的 8 处 `rt.push(` 编译期干跑就红（**这是期望行为**，D-14 仍生效） | 0.5 | B | 薄 |
| ~~**P0-07**~~ | ~~**分层 API 注册表 + 契约变体**（PI 决定 3 的载体）~~ **【作废 D-18：G1/G2/G3 消融砍除，契约变体再无用途；未决 §9-2 随之关闭】** | — | — | — | ~~1.0~~ | — | — |
| **P0-08** | `robotapi.py` 骨架：8 helper 签名 + docstring 三条硬规则 + lint | 无（原依赖 P0-07 已作废；本条改为直接服务 P1 感知/执行链） | `harness/robotapi.py` | 8 个 helper 有签名有单测桩；lint 断言 `is_gripping_sth` 在本文件外出现即 fail；helper 签名不含任务名/物体名 | 1.0 | B | 薄 |
| **P0-09** | `perception.py` 门面：口径统一层（**四元数 WXYZ / box 像素 / OBB 全边长**） | 无（原依赖 P0-07 已作废） | `harness/perception.py` | 三条口径各有转换单测（XYZW→WXYZ、0..1000↔1280×720、半长→全边长）；门面对外零 XYZW 泄漏 | 1.0 | B | 薄 |
| **P0-10** | `graspfunnel.py`：候选 → 偏好排序 → 选择（离线，mock 候选） | P0-03, P0-09 | `harness/graspfunnel.py` | 复用 `method/demo_graph/candidates.py::CandidateSelector`（⚙代码在，`SELECT`/`REJECT_ALL`/`REQUEST_EVIDENCE` 三态已实现）；每层 in/out 计数落盘；空集 → `REJECT_ALL` + `UnsolvedHole`，**不静默退化、不放宽重试** | 1.5 | B | **真开发** |
| **P0-11** | `bounds.py` 纯函数限幅 + 两级仲裁（无机器人） | 无 | `harness/bounds.py` | 单步 20mm/5°、节点累计 60mm/15°、次数 3/10；≤上限直发 / ~3× clamp / >3× reject；`bounds.apply()` 首行 `isinstance` 断言 | 0.5 | B | 薄 |
| **P0-12** | `targets.py` + `compilepolicy` 尾部增写 `targets.json` | P0-05 | `harness/targets.py`、改 `compilepolicy.py` | 5 份 graph 各出一份；谓词数 = acceptance 数；确定性 pass（**无 LLM**） | 1.0 | B | 薄 |
| **P0-13** | `episode.py` 双工位边界与隔离 lint（**不含模型**） | P0-11 | `harness/episode.py`、改 `scripts/public_release_check.py` | `Verdict`/`Delta` 无公共基类；lint 断言 `gates.py` 调用图不出现 corrector 符号；两条独立 call ledger；`assert_isolation()` 可跑 | 0.5 | B | 薄 |
| **P0-14** | **拆掉 `lift`/`lower_until` 里的 oracle 停止判据**（破口①，方法路径部分） | P0-05 | 改 `kwadapter.py:522-577` | `lower_until` 不再调 `rt.probes()`；停止判据改 `get_ee_extforce` + `get_xquat` z 收敛；`lift` 不读实体位姿判 attached；`tests/test_gates_no_privilege.py`：只有 `_entities` 有位移、`get_xquat` 无位移的假 rt → 断言 `passed=False` | 1.0 | A | 真开发 |
| **P0-15** | **消费 5 个静默丢弃的契约参数**（破口③） | P0-02 | 改 `kwadapter.py` | `align.axis`（**最要命**：`align:542-545` 与 `transport:537-540` 实现只差 `ALIGN_DZ=0.06` vs `PREGRASP_DZ=0.10` 一个常数，而生成的 policy 调 `align` 达 24 次）、`align.obj`、`lower_until.stop_condition`、`transport.obj`、`approach.cone`；消费或显式 `UNSUPPORTED` 记账，二选一，**不许继续静默** | 1.0 | A | 真开发 |
| **P0-16** | `kwadapter.py` 624 → <400 行 + 补测 | P0-02, P0-05, P0-15 | 改 `kwadapter.py` | 只留 IO 与委派；三块（binding/regions/predicates）各有单测；**不许砍测试**（只有 36 条，先红后绿是唯一护栏） | 1.5 | A | 重构 |
| **P0-17** | GraspNet 资产固化（**权重不入库**） | 无 | `third_party/DEPENDENCIES.md` | 5090 `~/dgl-perception/` 的 graspnet_baseline commit + 12M weights sha256 + 可重跑部署命令 | 0.5 | B | 薄 |
| **P0-18** | `evidence.py`：多视角取图唯一入口 + 帧新鲜度门 | 需 5090 只读 | `harness/evidence.py` | 4 源 × 5 视图全 200（✅已实测）；内容 md5 + 时刻双检，连续两次同 md5 → 重取 ≤3 次；3 视角 + depth bundle wall time <2 s | 1.0 | C | 薄 |
| **P0-19** ⬇低优 | `is_gripping_sth` / `current_limit` 恒 0 报上游 | 无 | `tools/gripper_repro.py` + issue 编号 | 根因：`_apply_gripper_control` 在 `fixed` 模式仍把 **v4 已移除的 `snap.torques[arm][7]`** 当 `current_limit` → 恒 0 → `is_gripping_sth` 恒假且返回字符串 `'False'`。**本方案不依赖该信号**（PI 已裁定 gate 约束更大、另用专门模型判抓取），报出去只为帮上游；我方只加 `_truthy()` 防御并标「不作为方案依赖」 | 0.5 | B | 薄 |
| **P0-20** | L0 实验批 | P0-05, P0-13, P0-16 | `runs/` + 报告 | `E-CAUSAL-OFF` / `E-A1b` / `E-A1c` / `E-A6-scan` / `E-A6-swap-static` / `E-GATE-off`（判据见 EXECUTION §1.3） | 1.0 | — | 实验 |

**小计 17.5 人日（原 18.5，P0-07 作废）。**

**MVS 裁剪线**（PI「先搭起来跑通优先于完备性」，D-19 后升级为**双线**）：
- **A 线必做 9.0 人日（≈2 周）** = P0-01 + 02 + 03 + 04 + 05 + 06 + 10 + 15 + 20（~~07~~ 已作废）→ 因果链闭合、E-CAUSAL-OFF 出数
- **B 线并行 3.5 人日** = T-GEN-1 + T-GEN-2（§2A；与 A 线文件集不相交，worktree 隔离）→ 生成器 v0 到位，E-CHAIN 试点的两个前置在同两周内齐活
- **顺延 8.5** = P0-08/09/11/12/13/14/16/17/18/19（其中 **P0-14 去特权不许顺延过 W3**，越晚越贵，见 §8-6）

---

## 2A. 新增工作流（D-19，2026-08-03）——定理 / 生成器 / 反传算子 / 鲁棒反传 / 注册

> 上位依据 `PROPOSAL.md`(v4) §2–§6。T-GEN 与 §2 的 A 串行线**文件集不相交，全程可并行**（B 组纪律，worktree 隔离）；T-BP / T-ROB 依赖 **D0**（因果链闭合）——在 `solve()` 真正消费约束之前，反传算子没有可反传的东西。
>
> **⚡ D-21（2026-08-03 晚）：本节除 T-GEN-1（已交付）与 T-REG 外全部「后置」**——T-THM 暂停（复活须 PI 拍板）；T-GEN-2/3/4、T-BP、T-ROB 挂起到效果里程碑后再启动。下表保留作后置队列，人日与判据不变。

| 编号 | 一句话 | 依赖 | 产出物 | 完成判据 | 人日 | 并行 | 类型 |
|---|---|---|---|---|---|---|---|
| **T-THM-1** | 分离定理 statement 草案：任务族 𝒯(L,κ) 形式化 + 贪心几何衰减下界 + 反传覆盖假设上界 + 回溯代价推论 | 无（纸面，**PI 亲自**） | `docs/theory/SEPARATION.md` | 1 页 statement + proof sketch；PI 判定「可证 / 降级为命题 / 纯实证」三选一并记入 DECISIONS（PROPOSAL v4 §4 的降级路径） | — | 全程并行 | 理论 |
| **T-THM-2** | 定理构造与生成器旋钮对齐 | T-THM-1, T-GEN-1 | 同上文档 §2 | 定理的对抗构造可由 T-GEN 的 κ 旋钮取极值复现（纸面对照表）；对不齐则二者必须改到对齐为止——**图和定理互为证据是 D-19 的硬要求** | — | 并行 | 理论 |
| **T-GEN-1** | 生成器 spec：链长 L / 耦合强度 κ / 歧义度 α 三旋钮可操作定义 + 阶段原子词表（抓取-转移-放置-插入，**不含 push**，D-14）+ oracle 金标图 schema + 三旋钮独立性检验设计（PROPOSAL v4 §10-13） | 无 | `docs/GENERATOR_SPEC.md` | PI 签字；三旋钮各有可操作定义与至少一个反例（「什么不算耦合」）；≥1 个现有真实任务能被表达为族内一点（外部效度锚，PROPOSAL v4 §9） | 0.5 | B | 设计 |
| **T-GEN-2** | 生成器 v0：L∈{2..5} × κ 两档 × seed 化，输出场景（复用 v4 现成 37 任务 suite 资产做底料，**不手写任务 yaml**，与 P1-10 同规）+ oracle 约束图 | T-GEN-1 | `harness/taskgen/` | 每档 L × 3 seed 场景 sim 加载成功；oracle 图 100% 过 `validate`（零度量字面量，T3 扫描）；确定性（同 seed 同输出，md5 断言）；**不改 §2 的 A 线任何文件** | 3.0 | B | **真开发** |
| **T-GEN-3** | 歧义旋钮 α：同指令双合法目标场景对（E-AMB / E-ROB 素材；**D-13 悬置的素材构造任务在此落地**） | T-GEN-2 | 场景对清单 + 双 scripted demo 脚本 | ≥4 对；双合法性两名独立标注一致；每对两段 demo 的意图差异可用一句话陈述并入预注册 | 1.5 | B | 真开发 |
| **T-GEN-4** | demo 采集通道：scripted oracle policy 执行生成任务并录 demo 视频，喂 Phase 0 提取 | T-GEN-2 | `harness/taskgen/record.py` | 生成任务的 demo 过 Phase 0 harness 同一道门（P≥0.7 / R≥0.8）；不达标 = 生成任务对提取器过难，回 T-GEN-1 调词表——**这是生成器的真实性检验，不许降门** | 1.5 | C（短占 sim） | 真开发 |
| **T-BP-1** | k 步反传算子：把漏斗 L3 从「下游一步」推广为链到底递归（PROPOSAL v4 §2.1 的 F_i），纯排序、不生成数值、L1 外不淘汰 | **D0**, P0-10 | `harness/backprop.py` | 单测：3 阶段耦合 mock 上贪心 top-1 ≠ 反传 top-1；**k=0 退化为纯 L2 序**（E-CHAIN 的消融开关就是这一个参数）；文件内任务名扫描 0 命中 | 2.0 | A（D0 后） | **真开发** |
| **T-BP-2** | 兼容性谓词 compat(c,c′)：抓取→放置/插入的跨阶段可行性检查（可达 + 无碰，与 L1 同底座） | T-BP-1, P1-03 | `harness/compat.py` | ≥2 类阶段对有可执行 compat；单对耗时 <1 s（超此反传在 ep 内不可用，如实记录并报 PI——这是 PROPOSAL v4 §10-11 的实测面） | 1.5 | A | 真开发 |
| **T-ROB-1** | extract 保留版本空间：k=5 自一致性样本不再只投票，全量落盘为 V | D0 | 改 `harness/extract.py` | 每任务 `graph.json` 伴生 `versions.json`（k 条链 + 一致性元数据）；**现有 MAP 输出字节不变**（零回归，md5 对照） | 0.5 | A | 薄 |
| **T-ROB-2** | 鲁棒排序算子：min / 期望聚合二选一（在 T-REG 预注册后钉死，不许事后换） | T-ROB-1, T-BP-1 | `harness/robustrank.py` | 单测：V 中两链分歧的 mock 上鲁棒 top-1 ≠ MAP top-1；干净 V（k 链一致）下与 MAP **逐比特相同** | 1.0 | A | 真开发 |
| **T-REG** | E-AMB / E-DO / E-CHAIN / E-ROB 的判据 + ep 预算写进 `EXECUTION.md` §1，PI 签字 commit | T-GEN-1, T-THM-1 | `EXECUTION.md` §1 增补 | 预注册纪律（EXECUTION §0）：开跑前 commit，事后改动 = 该批数据作废；E-CHAIN 五臂（B0/B1/B2/OURS/B3）定义与 PROPOSAL v4 §6.3 逐字一致；ep 预算按实测单集成本核算入 $3500 顶 | 0.5 | — | 治理 |

**小计：T-GEN 6.5 + T-BP 3.5 + T-ROB 1.5 + T-REG 0.5 = 12.0 人日**（T-THM 为 PI 纸面工作，不计执行人日）。

**排期锚点（D-21 改写，原锚点作废）**：W1–W2 = 因果链 MVS（A 线，进行中）；W3 = **D0**；W3–W5 = 执行链冲刺——P1-02（motion planning 换手写 servo）→ P1-04（非特权感知最小集）→ P1-08（nudge 闭环补偿），目标 = 效果里程碑（≥1 已测任务真实抓取端到端成功且可归因）；W5–W6 = **P2-04 提前**：insert_tubes / stack_bowls 各 20-seed 首批数字（内部 robodojo 评测计分）+ CaP-Agent0 同任务基线；里程碑后 = 按 PI 拍板复活 T-GEN-2 → E-CHAIN 与 T-BP/T-ROB。

---

## 2B. 榜单轨（D-20，2026-08-03）——RoboDojo 官方 leaderboard

> **定位：第二 headline**（层 A 机制结果仍是第一，论文成立不依赖榜单）。**激活门 = D0 通过 ∧ 官方提交通道开放**；**失效门 = 2026-09-15 官方仍 Coming Soon → 自动降回层 B**。预算硬顶 **15 人日**，超支回 PI 重批。两层不混报（`archive/ARCHIVE.md` §5.4）：内部 robodojo_v4 数字永远不得称 leaderboard result。目标口径：**上榜 + Long-Horizon 维度领先**，不追总分第一。

| 编号 | 一句话 | 依赖 | 产出物 | 完成判据 | 人日 | 时点 |
|---|---|---|---|---|---|---|
| **RD-01** | 侦察：官方 URL / 榜单最新快照 / 提交规则（demo 政策、observation 面、embodiment、evaluator 版本）记入 repo——**本仓至今没记官方 URL，这是欠账** | 无 | `docs/reference/ROBODOJO.md` | 三件套入库（URL+快照+规则）；提交状态有明确「开放/未开放」结论与核实日期；demo 政策写清「官方是否给训练 demo、几段、什么形态」 | 0.5 | **即刻可做** |
| **RD-02** | 官方适配 scoping：Isaac Sim / XPolicyLab adapter 工作量评估（对照 P1-13 模式，**只评估不实现**） | RD-01 | 一页结论 | embodiment 差异清单；官方 observation → 我方 12 API 的映射可行性逐项判定；人日估算——**若 >15 人日预算即上报，不许悄悄开工** | 1.0 | D0 前可做 |
| **RD-03** | 榜单主体：官方 adapter + 官方观测下感知链联调 + 先跑视频已覆盖的 **10 个 base tasks**，再谈全 42 | **激活门**，RD-02，P1-04..07 | 官方 evaluator 出分记录 + CaP-Agent0 同任务同预算基线分 | 每个分数带官方 evaluator 出处；内部数字零混报；CaP-Agent0 臂与我方同 API、同预算、无 demo（兼任 A1 消融 agent 臂）；论文措辞按 D-20⑤——不宣称打败 SOTA | ≤13.5 | 激活门开后 |

---

## 3. 决策点 D0（P0 收口，W3 末）

> **✅ D0 已于 2026-08-03 判定:通过**(D-22;比原排期 W3 提前约两周)。CC-0=100%、CC-1′ 过、CC-2′=20/20、CC-3=8/10;CC-4 按 D-22① 拆半挂 D1。证据 `experiments/causal/D0_REPORT.md`。**执行链解锁**,下表保留作历史判据参照。

**五条全中才进 P1。** 前四条是 EXECUTION §3.7 的 CC-1..CC-4 改写版，第五条是新裁决新引入的。

| ID | 判据 | 不中时的转向 |
|---|---|---|
| **CC-0**（新） | `K≥3` 样本中改 region 导致 top-1 改变 **≥60%** | L2 层是恒等映射 ⇒ 「排序偏好」取舍失败。二选一：(a) 回到带死线的区间筛（承认那个人为常数）(b) 承认 L2 层无筛选力、三层漏斗表述改两层。**必须当面报 PI，不许悄悄按 (a) 回退** |
| **CC-1′** | 反事实测试红→绿；`s_upper > s_bottom` 且 Kendall τ<0 | 主张从**「约束筛候选」收缩到「约束判成败」**（约束只进 gate 不进 solve）。**重大主张收缩，必须写进 `docs/DECISIONS.md` 并当面通知 PI** |
| **CC-2′** | 24 格 ≥22 格 top-1 或排序改变；`regions.py` 任务名命中 0 | 同 CC-1′ |
| **CC-3** | ≥8/10 约束有谓词；五处 fail-open 归零；`unchecked` 归零 | 只卡在 `carry`/`order`/`clearance` 不算失败（天然需跨阶段状态，标注「本 runtime 不可检查」并在论文写明）。**但 `region_grasp` 必须可检查**——它是 A5 与机制 3 的唯一载体 |
| **CC-4** | `constraint_ledger` 的 `UNKNOWN` <20%；验收器离线 acc ≥0.80 或 κ ≥0.6 | **不许上机器人。** 修判卷集/prompt **只许一轮**；仍不达标则 gate 退回「几何谓词 + 官方 probe（标 privileged-eval）」，验收模型从方法主张降为工程组件 |

---

## 4. P1 —— 要机器人，不要抓取成功

| 编号 | 一句话 | 依赖 | 产出物 | 完成判据 | 人日 | 并行 | 类型 |
|---|---|---|---|---|---|---|---|
| **P1-01** | **离线 bundle 录包器**（解 BLK-2 sim 独占死结） | P0-18 | `tools/bundle_recorder.py` | 5 任务 × 3 seeds ≈ **200 帧**，每帧 `rgb/depth/K/T_world_cam` + 同刻 `/state` | 1.0 | 短占 sim | 薄 |
| **P1-02** | motion planning 换掉手写 servo | 5090 + `ssh -A` | 报告 + `robotapi.plan_joint_path` 接通 | 10 目标：到位率 **≥8/10**、末点 `rot_error <10°` 且沿路点单调不发散（对照现状 16°→52°）、零幽灵自碰。底座 ✅实测：`reasoning:motion_planning_stereo` 端到端出过多航点轨迹 | 3.0 | **独占 sim** | 中 |
| **P1-03** | 可达姿态域标定（per-robot，非 per-scene） | P1-02 | `harness/calib/reach_pose_envelope.json` | 标 `provenance=calibration`；与 `CLAW_TIP_DZ=0.052` 同类，不违反 GT 防火墙 | 1.5 | 占 sim | 中 |
| **P1-04** | 非特权感知最小集接通（**PI 决定 4 的落点**） | P1-01, P0-09 | `perception.py` 实接 | `qwen_xquat`/`sam_xquat`/`bbox_xquat` 三轨（✅实测可用）交叉验证，两轨差 >1cm 记 `disagreement=True` **不静默取一**；5 实体 × 3 seeds 位姿误差中位数 **<15 mm**、孔位 **<14.9 mm** | 3.0 | 离线 | 中 |
| **P1-05** | **`get_depth` bulk 出口**（A 组唯一真开发，点云链的根） | P1-01 | 新服务 + `perception.get_depth` | 返 `{depth float32(H,W) 米, K(3,3), T_base_cam(4,4)}`，depth 做 lazy thunk。**不修上游 `pixels_base3d`**（`visual_processor.py:588/636` 的 `np.int64` vs `isinstance(x,int)` 分派 bug，改它污染原仓） | 3.0 | 离线 | **真开发** |
| **P1-06** | `segment` passthrough（SAM3） | P1-05 | `perception.segment` | 转发我方 SAM3 部署 `192.168.20.212:5081 /segment_raw`（已确认在网）；**不移植 cap-x 的 8114 服务端** | 1.0 | 离线 | 薄 |
| **P1-07** | 点云链闭合：`mask_to_points` + `compute_obb` | P1-05, P1-06 | `perception.py` | 纯 numpy/open3d 反投影 + OBB；`extent` **全边长**有断言；`s` 归一化坐标可算 → `regions.py` 的偏好函数有真输入 | 1.0 | 离线 | 薄 |
| **P1-08** | `nudge` 闭环补偿实测 | P0-11, P1-02 | `robotapi.nudge` + `NudgeResult` | 底层 `ctrl:local_delta_move`（**参数最全、⚙零调用**）；`ctrl:delta_move` 实测**严重欠行程**（指令 0.02m 实走 0.002–0.005m）→ 必须「发增量 → `get_xquat` 回读 → 补差」≤3 轮，**绝不开环信 `ok=true`**（`/run?action=ctrl` 是 fire-and-forget，硬编码 `result=True`） | 1.0 | 占 sim | 中 |
| **P1-09** | **三层漏斗 + funnel 数字（P1 主交付）** | P1-03, P1-04, P1-07, P0-10 | funnel 首表 | 出表：候选 → L1(可达/无碰) → L2(偏好排序) → L3(下游可行)；**L3 明确否决掉多少个「L1+L2 最优但下游不可行」**——这就是消融 B 的数据，全程不需要夹爪。**注意：L2 层现在不产生淘汰计数，只产生排序变化**，表结构须相应改（这是 §8-3 需 PI 确认的口径） | 3.0 | 离线 | 真开发 |
| **P1-10** | counterfactual 场景（**用 v4 现成 37 任务 suite seed 筛，不手写 yaml**） | P1-09 | 场景清单 | ≥1 组「局部最稳 grasp ≠ 下游可行 grasp」 | 1.0 | 离线 | 薄 |
| **P1-11** | **GraspNet 对照臂上线**（PI 决定 2 · 后置） | P0-17, P1-05, **P1-09 出表后** | `graspfunnel` 第二候选源 | 单帧 ≥20 候选；变换到世界系后 ≥5 个落在目标 AABB 内；**同一漏斗跑两个候选源**，产出 `qwen_dof` vs `graspnet` 的 L3 通过率对照。**风险已知**：CUDA 扩展未编译（无 `.so`）、`.venv` 无 pip、config 路径全指 1021 沙箱、smoke 输入 160×120 而相机是 1280×720 —— 2.0 人日是**乐观值**，踩到编译问题按 4.0 重排 | 2.0 | 离线 | 真开发 |
| **P1-12** | verifier 接真图（**仍 shadow mode**） | P0-13, P1-01 | 一致率报告 | 在 approach/align 两个无需抓取阶段跑；与几何谓词一致率出数；分歧样本人工复核 ≥20 例。**W3 之前一律 shadow（写报告、不进 gate），参与判定要等 D1** | 1.5 | 离线 | 真开发 |
| **P1-13** | no-demo frontier agent 基线：**只做接入评估** | 无 | 一页结论 | 把本 sim 注册成 `inspect-robots` embodiment 的成本 | 0.5 | 全程并行 | 薄 |
| **P1-14** | L1 实验批 | P1-01, P1-09 | `runs/` + 报告 | `E-FRESH` → `E-GATE-live`(60 scripted ep) → `E-VIEW` → `E-CAUSAL-L1`(60 ep) → `E-A5-off`（判据见 EXECUTION §1.4） | — | **占 sim** | 实验 |

**小计 21.5 人日 + L1 机时。** 若必须压到 3.5 周：砍 P1-10 到 P2 前置、P1-11 后移到 P2，**不砍 P1-04、不砍 P1-05**（点云链断了 `region_grasp` 就没有真输入，CC-3 的唯一载体作废）。

---

## 5. 决策点 D1（P1 收口，W7 末，视 P1-02 实耗可滑到 W8）

| ID | 判据 | 不中时的转向 |
|---|---|---|
| **P-1** | 非特权位姿误差中位数 <15 mm，孔位 <14.9 mm | 多视角/主动感知回修**只许一轮**；仍不达标则 P2 全部数字标 **oracle 上界**，可防守面收缩到「候选选择 + gate」 |
| **P-2** | motion planning 到位率 ≥8/10，末点 `rot_error <10°` 单调收敛 | 回 P1-02 |
| **P-3′**（改写） | funnel 非平凡：L1 通过率 ∈[0.1,0.9]；**L2 top-1 改变率 ≥60%（= CC-0 在真候选上的复核）**；L3 在 counterfactual 上 ≥1 次改 top-1 | L1 ≈0 或 ≈1 → 谓词阈值取法有问题，回 P0-05 改**谓词**不许改任务；L2 改变率不达标 → 见 CC-0 转向；L3 从不改 top-1 → PROPOSAL §2.1「下游约束反推」被证伪，机制 3 降为实现细节，三层漏斗表述改两层。**原 P-3「L2 否决率 ∈(0,0.8)」在无淘汰语义下作废** |
| **P-4** | verifier 与几何谓词一致率 ≥0.85 且**不系统性偏松**（FAIL 判 PASS 的数 ≤ 反向的 2 倍） | **立刻停用**退回几何谓词 + 官方 probe。这是硬边界 1 的守卫，没有商量余地 |
| **P-5** | E-GATE 档 0 通过（bal acc ≥0.80，FP ≤0.10） | EXECUTION §1.6 降级表；~~地板（方向标签 <0.70）→ **L5 整层删除**，L2 预算砍半~~ ← **D-18 后 L5 已不存在**，降级表涉 L5 各档作废；E-GATE 保留的用途 = verifier shadow 一致率（evaluator 侧，P1-12）与 gate 谓词质量 |
| **P-6**（新） | GraspNet 对照臂出数：两候选源在同一漏斗上的 L3 通过率有可报差异 | 不出数 → GraspNet 降为「可复现性论证」用途（不依赖闭源远端服务），**不进消融矩阵**，成本不再追加 |

---

## 6. P2 —— 完整抓取链（定位更新，D-18/D-19）

> **P2 的角色变了**：抓取链是**执行基底与 E-CHAIN 的落地载体**，不再是论文主结果本身。P2-04 的 2 任务 20-seed 双阈值降级为基底健全性检查；论文主数字来自 E-CHAIN 任务族与 E-AMB/E-ROB（判据走 T-REG 注册）。此降级 PI 可复议。

| 编号 | 一句话 | 依赖 | 产出物 | 完成判据 | 人日 | 并行 |
|---|---|---|---|---|---|---|
| **P2-01** | claw 自由度口径结清（G0-a 残留） | 无 | 一页记录 | `set_gripper(angle=0/100)` 前后逐个 diff `/state` 的 claw revolute，同时结清「15 vs 12 自由度」与「把可动性证据从图像升级为状态量」。**这是 `region_grasp` ground truth 规则的前提** | 0.2 | 占 sim ≤0.5h |
| **P2-02** | pose-in-hand：抓后估一次 + FK 传播 | P2-01, P1-04 | `harness/posinhand.py` | 闭合后估 `T_gripper→object` 一次；gate 失败或接触事件才重估，**不做在线密集追踪**。（`residual()` 按残差修正已被实测证伪两次，保留契约签名返 `UNSUPPORTED`） | 2.0 | 占 sim |
| ~~**P2-03**~~ | ~~`corrector.py` 落地（L5）~~ **【作废 D-18：L5 整层砍除，D-01 维持生效；`contract.py` 的「第一个破例点」随之消失，§9-8 关闭】** | — | — | — | ~~3.0~~ | — |
| **P2-04** | 首批数字 | P2-02, P2-03 | `runs/ep_*` | insert_tubes + stack_bowls 各 20 seeds；双阈值 **≥16/20**（抓取+转正+对准）、**≥12/20**（inserted+upright） | 3.0 | 占 sim |
| **P2-05** | Phase 2 冻结协议 | P2-04 | `RunManifest` | code digest；D/E seed 不相交；冻结后 policy/模型/配置/runtime 四项全禁改 | 2.0 | 否 |
| **P2-06** | 消融矩阵 | P2-04, P1-10 | 全部 L2 实验 | EXECUTION §1.5 全部；每条消融有成对数字与「节点 × 层」归因 | 4.0 | 占 sim |
| ~~**P2-07**~~ | ~~**E-ABSTR：抽象层级消融**（PI 决定 3 的实验兑现）~~ **【作废 D-18：G 消融砍除——CaP-X 已覆盖且我方样本量只够 conditioning 不够 finding；§9-11 关闭】** | — | — | — | ~~2.0~~ | — |

**小计 11.2 人日（原 16.2，P2-03/P2-07 作废）。**

### 决策点 D2（P2 收口，P2-02 起 +3 周）

达标 = ≥16/20 与 ≥12/20；未达 12/20 → 按 funnel 归因回修**一层**，只许一轮。
**P2′ 备用分支当前不触发**：原触发条件（BLK-1 夹爪不通）已于 2026-07-30 消失（✅G0-a 实测 `set_gripper(angle=0/100)` 指垫可见开合，md5 `4505170dd4` vs `3ef9e77851`）。新的回落条件（若有，应是「通道通但抓取长期不稳」一类）**未定**，见 §8-9。**在定出之前，本分支不得被任何人当作既有退路引用。**

---

## 7. 薄封装 vs 真开发（PI 按此判进度是否合理）

| 类型 | 项 | 依据 |
|---|---|---|
| **薄封装（≤1 人日/个，共 9+）** | `observe` · `read_state` · `get_object_pose` · `compute_obb` · `mask_to_points` · `execute_path` · `set_grip` · `rank_and_select` · `regions.filter/rank` · `segment` passthrough | 全部是「包一层 ✅已实测可用的能力 + 统一口径」，无新算法、无新服务。底座：`reasoning:qwen_xquat`/`sam_xquat`/`bbox_xquat`/`qwen_dof_xquat`/`motion_planning_stereo`（✅全实测）、`ctrl` 六件套、`info` 五件套、WebUI 4 源×5 视图 |
| **中（1–3 人日/个）** | `plan_joint_path`（要处理 `mp.version`/`mp.intent`/`mp.planning_mode`/`mp.scene_input`/`mp.scene_camera` 控制 token + 扁平 list reshape 成 N×7） · `propose_grasps` T1（`topk_pick_records_by_arm` 结构 + XYZW→WXYZ + **保留「raw 候选 + 我方排序」路径**，因为 `qwen_dof_xquat` 已经替我们选过一次，做候选质量消融时必须能禁用它） · `nudge`+闭环补偿 | |
| **真开发（3 人日+，共 5）** | `get_depth` bulk 出口（**A 组唯一**） · `predicates.py`（三值 + margin，替五处 fail-open） · `graspfunnel.py`（三层 + tie-break + 每层计数落盘） · verifier 工位 · corrector 工位 | |

**口径纠正（必须先广播，否则全队系统性低估自己）**：`docs/reference/PRIMITIVE_API.md` 的 USABLE/BLOCKED 分级是 **KSM 观察契约层**的裁剪，**对我们不成立**——我们直连 pipeline，被判 BLOCKED 的 `get_ee_extforce`/`sam_xquat`/`existence`/`pick_verifier`/`hand_pick_refine` ✅实测全部可调。按那份文档做差集分析会系统性低估可用面。

**反面教材（架构评审时点名）**：`harness/kwadapter.py:305` 的抓取点 = 物体中心 + 常量 `top - 0.03`。一次踩三个雷：①数值来自常量而非感知 ②物体高度一变就错 ③E-A6 字面量扫描器会命中。这是「把粗标签当生成器」的典型错误，**新架构的第一条设计律就是消除它：粗标签是排序器/筛选器，不是生成器；精确 6-DoF 只能来自候选生成器**。反了不但数值错，连 E-CAUSAL 都变成同义反复（候选生成本身消费了标签，「改标签→行为变」就证不出因果力）。同类还有 `:317` 的 `value=0.05` 与 `:22` 的 `GRIP_CLOSE=160`（越界被 `max_angle=100` 截断）。

---

## 8. 每周可交付（PI 按周当场复核）

| 周 | 交付物 | 一句话验收 | 占 sim |
|---|---|---|---|
| **W1** | ① `test_constraint_causality.py` 的**红 run 与绿 run 两份输出** ② `binding.py` + 86 洞派发表 ③ **CC-0/CC-1′/CC-2′ 三条新判据的预注册文件已 commit** | 「约束现在真的改变数字了」 | 空档（可提前挪 P0-18 或 P2-01） |
| **W2** | ① 24 格 region 排序矩阵（前后 top-1 对照 + Kendall τ） ② `predicates.py` 覆盖表（10 约束 × PASS/FAIL/UNKNOWN） ③ `constraint_ledger` 样例 JSON ④ **契约变体三份 + 分层注册表**（§8-2 裁决后） | 环 2 断点闭合；分层骨架立起 | 否 |
| **W3** | ① 五处 fail-open 归零的 diff ② `kwadapter.py` <400 行 + 单测数 ③ 离线判卷集 50 题混淆矩阵 ④ L0 实验批全部结果 ⑤ **D0 裁决** | 因果链闭合、破口全修、可以上机器人 | P0-18 取图验证 |
| **W4** | ① 200 帧 bundle 清单 ② motion planning 到位率表 ③ 取图重复帧率报告 | 机器人能按规划走了 | **P1-02 独占 3 天** |
| **W5** | ① `reach_pose_envelope.json` + 覆盖图 ② `get_depth` bulk 单帧点云 ③ 非特权 vs oracle 位姿误差表 | 点云链通了，漏斗三个输入齐了 | P1-03 标定 1.5 天 + E-FRESH/E-GATE-live 交替 |
| **W6** | ① **funnel 首表**（候选→L1→L2→L3，含 L3 否决明细 + L2 top-1 改变率） ② counterfactual ≥1 组 ③ verifier 接真图一致率 | **消融 B 数据到手，且全程没碰夹爪** | E-CAUSAL-L1 60 ep |
| **W7** | ① GraspNet 对照臂候选统计 + 两源 L3 通过率对照 ② nudge 闭环补偿残差表 ③ **D1 裁决** | 双候选源可比了 | 否 |
| **W8+** | 单管抓取 + pose-in-hand + 20-seed；按 D2 结算 | — | **是**（受 BLK-2 限流 60–100 ep/天） |

**并行边界**（「subagent 读、主线写」）：
- **A 串行**（都改 `kwadapter.py`/`gates.py` 同一批行）= P0-01→02→03→04→05→14→15→16
- **B 可并行写**（文件集不相交，**必须用 git worktree 隔离**）= P0-06 / 07 / 08 / 09 / 10 / 11 / 12 / 13 / 17 / 19
- **C 半独立**（需 5090，与 mac 本地零冲突）= P0-18 / P1-01 / P2-01
- **D 并行**（前提 P1-01 完成）= P1-02+P1-03（占 sim） vs P1-04+05+06+07+09（跑录包）
- **E 全程并行** = P1-13

**BLK-2 仍是排期唯一硬约束**（不是 GPU）：`/tmp/knowin_sim_camera.sock` 独占 → 物理串行化，吞吐 60–100 ep/天。P1-01 录包器把 P1-04/05/06/07/09 全部移到离线，是唯一的绕法。

---

## 9. 当前未决事项（按阻塞强度排序）

> **处置更新（2026-08-03，D-18/D-19 落账；下表原文保留作历史，以本块为准）**：
> - **#1 已关闭**（D-18）：BLK-5 以「维持 D-01、砍 L5 删模型工位」了结——即原选项 (c)。摊销与可审计卖点全额保留。
> - **#2 已关闭**（D-18）：G1/G2/G3 消融砍除，「API 分颗粒度 vs `contract.py` 不动」的冲突对象消失；契约变体方案 (c) 不再需要。
> - **#5 按 D-18 推论关闭为选项 (a)**：gate 的 `passed` 由 `gates.py` 几何谓词计算，验收模型只在 evaluator 侧出 shadow 报告（D-01 之下唯一自洽选项）。**PI 可复议**，复议前按此执行。
> - **#7 范围收窄**：verifier 只剩 shadow 用途，「两工位异厂」降级为 evaluator 侧建议，不再阻塞方法路径；单 key 现状可接受，但 shadow 报告中须如实标注同源风险。
> - **#8 已关闭**（D-18）：P2-03 作废后 `contract.py` 无任何计划内破例点。
> - **#11 已关闭**（D-18）：E-ABSTR 砍除，预算问题消失。
> - **#3（判据重预注册）/#4（cone）/#6（gate 去特权时点）/#9（L2 ep 数）/#10（P2′ 触发条件）/#12（GraspNet 定位）/#13（claw 口径）继续有效。**
> - **#14 已关闭（2026-08-03，D-20）**：官方 evaluator 无 oracle，选项 (a) 最严口径成为唯一口径——E-CHAIN 主数字 = 非特权感知链，**P1-04/05/06/07 升格必做、不可裁剪**。榜单轨全部治理面见 §2B 与 DECISIONS D-20。
> - **新增 #15**：E-CHAIN 任务族与真实 4 任务的关系口径——生成任务算不算「任务数量」（v3 §9 说通用性证据来自约束类型覆盖不是任务数量）。建议口径：任务族按「约束类型 × 链长」报，不按个数报；需 PI 确认后写进 T-REG。

| # | 事项 | 阻塞谁 | 选项与代价 |
|---|---|---|---|
| **1** | **D-01「运行期不放 LLM」与两个模型工位正面冲突（BLK-5，最高优先）** | 整个 L4/L5、P2-03 | `DECISIONS.md` 的 D-01 状态为**生效**，理由明写「摊销是本课题相对 per-episode VLM 路线的**主要可防守点**」「一旦运行期有 LLM，冻结后跨场景复用这个主张自毁」，只给 A(标定)/B(方法·冻结)/C(基线) 三工位豁免，**B 用即违反**——而 verifier + corrector 都在 B 的运行期循环里。选项：(a) 正式作废 D-01，接受成本/摊销对照表从卖点变劣势 (b) 把两工位重定义为 A 类（标定期在环、冻结后不在环） (c) 维持 D-01，删掉模型工位回纯残差闭环。**这不是补一条 D-18 就能了结的记账问题——它改变论文相对 ReKep/CoPa/VIA 的主张** |
| **2** | **「API 分颗粒度」与「`contract.py` 明令不动」的冲突**（PI 今日决定 3 引入） | P0-07/08/09/10、P2-07 | 让编译期模型自己选层 ⇒ 细颗粒 API 必须在 `contract.Runtime` ⇒ 必须改 `contract.py`（它被 `compilepolicy.py:83` 用 `inspect.getsource` 整体拼进提示词，改它 = 静默改提示词 = 已编译 policy 与新契约不同源，D-13）。选项：**(a) 分层只在 runtime 内部**（G3=现 8 原语不变，G2/G1 只供 binding 内部调）→ 纪律不破，但**「让模型自己选层」不成立、抽象层级消融做不了**，PI 决定 3 落空 **(b) 直接扩 `contract.Runtime`** → 破「P0/P1 零改动」硬纪律 **(c) 契约变体三份**（`contract.py` 原文一行不改，新增 `contract_g2.py`/`contract_g1.py`，按层参数选一份注入）→ 纪律不破 + 消融可做，代价是三份契约同步维护 + 三套 digest + 重编译 3×5×$1.5≈$22.5。**我方建议 (c)，但这是 PI 拍板项** |
| **3** | **三条判据随「排序偏好」裁决失效，须重新预注册** | W1 交付 | `CC-1`（`≥0.5×物体高`）、`CC-2`（24 格淘汰）、`P-3`（L2 否决率 ∈(0,0.8)）在无死线语义下均无对象。§1.3 与 §3/§5 给出 `CC-0`/`CC-1′`/`CC-2′`/`P-3′`，**须 PI 签字后 commit，开跑前不许再改**（EXECUTION §0 预注册纪律：事后调整必须留 git 记录并声明该批数据作废） |
| **4** | **`cone` 是否也改排序偏好** | P0-03 | PI 今日只明确了 `region`。若 `cone` 保留 `half_angle=25°` 硬阈，「少一个人为常数、消掉『0.5 从哪来』这个质询面」的理由**只兑现一半**——审稿人会照样问「25° 从哪来」。建议同构处理为角度偏好；但代价与 CC-0 相同：筛选力进一步下降 |
| **5** | **运行期 gate 的 `passed` 由谁计算** | P0-04 | (a) `gates.py` 算（本文默认，verifier 只作证据源）→ A3 误杀率有确定性基准 (b) 验收模型接管、`gates.py` 整体迁 evaluator → 「/state 只进 evaluator」边界最干净，但 gate 判定不可复现、A3 无基准。**两份原稿分别按两种方案写了验收判据，谁先动手另一份就失效** |
| **6** | **gate 去特权的时间点（改动 #5）** | P0-14 | (a) 立刻去 + `privileged_oracle` 段并行记录 —— 代价是重跑一遍 M1a 且 oracle 上界口径变了 (b) 先记账后去 —— 中间这批 ep 的 L4 判定不可用于 A3。**我方建议 (a)**：P0 阶段还没产真 ep，此刻成本最低 |
| **7** | **两工位模型「必须异厂或异代」与现行 provider 决策冲突** | P2-03、E-GATE 独立性 | EXECUTION §2.3 升为硬约束：同厂同代 ⇒ 视觉盲区高度相关 ⇒「评判者与被评者分离」只是名义上的。但现行 LLM provider 决策是 **OpenRouter 单 key 路由单一模型**，且 `harness/llm.py:24` 读单一 `OPENROUTER_API_KEY`。要么真配第二厂商 slug + 第二个 key 并改 `llm.chat`，要么**明说隔离只到「进程 + 静态门禁 + 事后作废」级**，不许继续宣称「分 API key」 |
| **8** | **`contract.py` 破例窗口** | P2-03 | (a) 留到 P2-03 集中一次改完并重跑 5 任务 compile（约 5×$1.5） (b) 现在就改并立即重编译。**与未决 #2 联动**：若 #2 选 (c) 契约变体，则 (a)(b) 的对象变成变体文件而非 `contract.py` 本身 |
| **9** | **L2 的 ep 数与周数何时锁定** | W8+ 排期与 $3000 硬顶 | 1040 ep / 4 周目前是**占位值**，依赖两个未实测假设：每 ep 平均节点观测数（`fakerun.run_policy` 首节点失败即 `break`，薄底座下多数 ep 只产 1–3 个观测；deposit_coin 的 stage 0 与 3 各只有 1 条 acceptance）、单集成本（✅实测编译期 $1.38–1.62/次，运行期未测）。(a) 先跑 20 集实测再锁（延后 3–4 天） (b) 按占位值排期并承担击穿硬顶的风险 |
| **10** | **P2′ 触发条件重定义** | D2 | 原条件（BLK-1 六周不解锁）已消失。新回落条件（「通道通但抓取长期不稳」一类）**未定**，留给 PI。在定出之前本分支不得被引用为既有退路 |
| **11** | **E-ABSTR（抽象层级消融）未在 EXECUTION §1 注册** | P2-07、总预算 | PI 决定 3 说「这同时是抽象层级消融的实验载体」，但该实验的 ep 数、成本、判据都不在现有实验表里，也不在 $3500 总顶的估算中。须补注册，否则 P2-07 是无预算任务 |
| **12** | **GraspNet 对照臂的定位二选一** | P1-11 判据 | (a) 候选质量消融对照（判据 = 两源在同一漏斗上的 L3 通过率差异可报） (b) 可复现性论证（判据 = 不依赖闭源远端服务也能跑通）。**两者判据不同、工作量不同**，不裁决则 P1-11 的完成判据是空的 |
| **13** | claw 自由度口径（15 revolute vs `kwadapter.py` 注释 12） | `region_grasp` 的 ground truth 规则 | 已排进 P2-01（0.2 人日），但**它是 §1.6 `region_grasp` GT 规则的前提**，若 W5 前不结清，E-GATE 的 `region_grasp` 类别（75 帧组）无法开跑 |
