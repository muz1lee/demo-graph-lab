# D0 判决材料 —— P0-20:L0 实验批 + D0 五条判据

> 任务 P0-20(EXECUTION §1.3 / TODO §2、§3)。执行于主 checkout,base=main `048b98f`,测试基线 **156 passed**。
> 本报告只判「过 / 不过 / BLOCKED」+ 数字 + 证据路径,**不执行 fail_transition**(那是 PI 的事)。
> 判据以 `experiments/causal/variants.json`(SIGNED,2026-08-03 PI 签字)+ EXECUTION §1.3 为准;variants.json 只读,不改。

**证据分级图例**(仓库惯例,混用即失效):
`✅实测` = 已跑过、有输出/日志 · `⚙代码在` = 仓里有代码但零调用或未测 · `📋计划` = 只在文档里,一行代码没有

生成日期:2026-08-03。产物根:`harness/runs/L0_causal_offline_20260803/`。

---

## 0. TL;DR

- **五条判据**:CC-0 ✅**过**、CC-1′ ✅**过**、CC-2′ ✅**过**(20/20 ≥ 18)、CC-3 ✅**过**(8/10)、CC-4 ⛔**BLOCKED**(验收模型 + 判卷集 + episode 边界三件均不存在)。
- **实验批六项**:E-CAUSAL-OFF ✅过 / E-A6-scan ✅过(回归确认,但新扫描器未接进门禁,见下)/ E-A6-swap-static ⛔BLOCKED / E-GATE-off ⛔BLOCKED / E-A1b ⛔BLOCKED / E-A1c ⛔BLOCKED。
- **总成本**:本批**新增 $0.00**(全部离线/零 LLM;BLOCKED 的两个 LLM 实验因缺前置未开跑,未花钱)。
- **我的建议**:**有条件通过**。因果链闸门(CC-0/1′/2′/3)四条硬性全过,「约束现在真的改变数字了」这条 D0 核心命题成立;但 CC-4(验收可靠性)整条 BLOCKED,须 PI 就「CC-4 是否为进 P1 的硬前置」拍板——一句话理由见 §5。

---

## 1. 五条判据逐条判定

### CC-0 —— 筛选力下限(D0 独立闸门) ✅ 过

- **判据**(variants.json):K≥3 样本,非等价 region 对上改 region 标签导致 top-1 改变的比例 **≥60%**。
- **判定**:**过**。20 格 × top-1 改变:`upper_body|bottom`、`top|bottom`、`middle|bottom`、`middle|top`、`middle|upper_body` 五对在全部 4 任务上 top-1 均改变 → **top-1 改变率 = 20/20 = 100% ≥ 60%**。
- **数字/证据**:`harness/runs/L0_causal_offline_20260803/cc2_region_matrix_20cell.json`(每格 `top1_a`/`top1_b`/`top1_changed`);脚本 `scripts/cc2_region_matrix.py`(固定 seed=20260803,K=5,mock 候选)。✅实测。
- **说明**:CC-0 与 CC-2′ 同口径复核(pair_vocabulary),此处 top-1 改变率是 CC-2′ 20 格的子度量。

### CC-1′ —— 反事实测试(约束筛候选) ✅ 过

- **判据**(variants.json):同一候选集(K≥3)在 `region_grasp(obj,'upper_body')` 与 `region_grasp(obj,'bottom')` 下排序:① top-1 的 height_fraction 满足 `s_upper > s_bottom`;② 两次排序 Kendall τ < 0。两条同时。
- **判定**:**过**。
  - ① `s_upper=0.90 > s_bottom=0.10`(`test_top1_height_fraction_orders_by_region` 绿)。
  - ② τ = −1.00 < 0(`test_ranking_kendall_tau_negative_between_regions` 绿)。
  - 附加病理断言 `test_region_label_changes_solve_output`:solve() 输出随 region 标签改变(upper_body→z=0.848、bottom→z=0.744,**逐比特不同**)——**先红后绿已转绿**。
- **数字/证据**:`tests/test_constraint_causality.py`(4/4 绿);批量输出 `harness/runs/L0_causal_offline_20260803/e_causal_off_tests.txt`;value-arm 直证见 §2 E-CAUSAL-OFF。✅实测。

### CC-2′ —— 20 格 region 排序矩阵 ✅ 过

- **判据**(variants.json,20 格制):5 非等价可查对 × 4 任务 = 20 格,**≥18 格**产生不同 top-1 或 τ<1;`regions.py` 任务名扫描 **0 命中**。
- **判定**:**过**。
  - 20/20 格达标(≥18);每格均满足 top-1 改变 **且** τ<1。τ 谱:`*|bottom` 三对 τ∈{−1.00,−1.00,−0.40}、`middle|top` 与 `middle|upper_body` τ=+0.40(<1,达标)。
  - `regions.py` 任务/物体名扫描(insert_tubes/stack_bowls/deposit_coin/push_T/tube/rack/bowl/coin)= **0 命中**。
- **数字/证据**:脚本 `scripts/cc2_region_matrix.py`(已按 20 格口径更新,交付物①);产物 `harness/runs/L0_causal_offline_20260803/cc2_region_matrix_20cell.{txt,json}`;任务名扫描证据 `harness/runs/L0_causal_offline_20260803/e_a6_scan.txt` 末节。✅实测。
- **口径更正记录**:旧 `cc2_region_matrix.py`(24 格,每格相对固定 ref_region 比 τ)是 W2 冒烟脚本,**测量对象错误**(把每格对 upper_body 的相对 τ 当判据,而 CC-2′ 要的是**成对两标签之间**的 top-1/τ)。已按 variants.json `pair_vocabulary` 重写为成对 20 格;pair 集与阈值从 variants.json 读取(零判据魔数)。

### CC-3 —— 谓词覆盖 + fail-open 归零 ✅ 过

- **判据**(TODO §3 D0):≥8/10 约束有谓词;五处 fail-open 归零;`unchecked` 归零;**`region_grasp` 必须可检查**。
- **判定**:**过**。
  - 覆盖:10 词表约束中 **8 个可检查**(`axis_vertical`/`axis_parallel`/`center_align`/`above`/`inside`/`clearance`/`region_grasp`/`approach_direction`),`carry`/`order` 标 `UNCHECKABLE_IN_RUNTIME`(CC-3 明许的两个跨阶段豁免)。8/10 ≥ 8。
  - `region_grasp` **在可检查集内**(CC-3 硬性要求满足)。
  - 三值 PASS/FAIL/UNKNOWN + margin;`check()` 把不可查/未知名/内部异常全部路由到 **UNKNOWN 而非静默 PASS**;gate 侧 effect 不可观测→UNKNOWN(替换旧 `or (not observable)` fail-open)。
- **数字/证据**:`harness/predicates.py`(`_PREDICATES` 8 函数、`coverage()`);`tests/test_predicates.py` **31 passed**(含 `test_coverage_meets_cc3`、`test_ok_property_three_valued`);gate 侧 `harness/gates.py::_verify3`。✅实测(`python3 -m pytest tests/test_predicates.py -q` → 31 passed)。

### CC-4 —— 验收模型离线可靠性 ⛔ BLOCKED

- **判据**(TODO §3 D0):`constraint_ledger` 的 UNKNOWN <20%;验收器离线 acc ≥0.80 或 κ ≥0.6。
- **判定**:**BLOCKED**(缺前置,非「不过」)。缺失三件,任一即无法开跑:
  1. **无验收模型**:`harness/verifier.py`/`gatemodel.py`/`judge.py` 与 `prompts/runtime_verifier.md` **均不存在**。仓内唯一「verifier」是几何谓词三值检验器(`predicates.py`,零 LLM),不是 CC-4 要评的**模型**判卷器。模型工位按 D-18/BLK-5 与 corrector 一并砍除(D-01「运行期不放 LLM」维持生效)。
  2. **无 50 题判卷集**:`harness/goldset/judge_eval_50.json`(T0-6b 产物)**不存在**;goldset 目录只有约束**提取**金标(`*_gold_v2.json`),非运行期成败判卷集。
  3. **无离线判卷/一致性打分脚本**:`scripts/`/`tools/`/`tests/` 无任何算 acc/κ 的判卷 harness。
- **依赖**:T0-6b(判卷集)+ P0-13(`harness/episode.py` 双工位边界,含 `Verdict`/`EpisodeLedger`/`assert_isolation()`——同样**不存在**)。
- **证据**:三处 `find`/grep 均 0 命中(子代理复核);EXECUTION §1.3、§2.3、TODO P0-13/T0-6b。⛔不许用替代口径硬凑数字。

---

## 2. 实验批六项逐项结果

| 实验 | 判定 | 关键数字 | 证据路径 |
|---|---|---|---|
| **E-CAUSAL-OFF** | ✅**过** | value 臂:solve 输出随 region 翻转(z 0.848→0.744);structure 臂:5 类 type 全量派发命中、3 个已知误派归位、未知 type→UnsolvedHole | `tests/test_constraint_causality.py`(4/4)+`tests/test_solve_dispatch.py`(13/13)+`tests/test_gates_constraints.py`(7/7)=24 passed;`runs/L0_causal_offline_20260803/e_causal_off_tests.txt` |
| **E-A6-scan** | ✅**过(回归确认)** | policy 字面量扫描 5/5 pinned policy CLEAN + 负控捕获注入 `cone=0.05`;graph args 字面量扫描 0 命中/5 图;kwadapter `top-0.03`/`value=0.05` 均已移除;regions.py 任务名 0 命中 | `runs/L0_causal_offline_20260803/e_a6_scan.txt`;`harness/compilepolicy.py::static_check`、`harness/validate.py::check_item` |
| **E-A6-swap-static** | ⛔**BLOCKED** | — | 缺 `harness/corrector.py`(D-18 砍 L5,P2-03 作废);无任何按任务实例化的运行期 prompt 可 diff。依赖:P2-03(作废)/ D-01 复议 |
| **E-GATE-off** | ⛔**BLOCKED** | — | 同 CC-4:无验收模型、无 50 题判卷集、无判卷脚本、无 `episode.py`。依赖:T0-6b + P0-13 |
| **E-A1b** | ⛔**BLOCKED** | 全批预估 ~$40–47(30 次 extract+enrich × ~$1.4),> $15 自停线 | 跨 demo 臂无素材:每任务**只存在过 1 段视频**,所有 run 目录指向逐比特相同帧(无「3 段不同 demo」);视频目录已空。「纯标签降级」需改 `extract._stage_messages`(**生产代码,本任务禁改**) |
| **E-A1c** | ⛔**BLOCKED** | 成本非瓶颈(~$0.5) | 需从零写「去名图 anonymizer + 盲分类器 + 打分」——**均不存在**(grep 0 命中);且只钉 5 图(spec 说 6),push_T/push_T_random 去名后可能结构不可分,判据 ≥0.90 存疑。属缺前置基础设施,不硬凑 |

**E-A6-scan 判定口径说明**:三个底层条件离线全部可满足(① policy 扫描回归绿 + 负控活;② graph args 0 命中;③ kwadapter 旧字面量已删)。EXECUTION §1.3 预期的「gap ② corrector/verifier prompt 硬失败」已**失去对象**(模型工位被砍),不构成失败。**唯一未完成项**:EXECUTION §1.3 要求的「新任务名/字面量扫描器接进 `scripts/public_release_check.py`」尚未实现(该 lint 属 P0-13,门禁当前不含任务名扫描)。故 E-A6-scan 判「过」限于**现有门禁 + 语料回归确认**;「新扫描器落地」记 ⚙代码在(散落于各模块)/📋计划(未汇入门禁)。

---

## 3. 成本

| 项 | 调用数 | 成本 |
|---|---|---|
| E-CAUSAL-OFF(纯 pytest) | 0 LLM | $0.00 |
| E-A6-scan(AST/正则扫描) | 0 LLM | $0.00 |
| CC-2′ 20 格矩阵(mock 候选,离线) | 0 LLM | $0.00 |
| E-A1b / E-A1c | 未开跑(缺前置) | $0.00 |
| **本批新增合计** | **0** | **$0.00** |

- **成本纪律执行**:E-A1b 单任务估算依据已测 `cost.jsonl`(编译 31–37 次调用 / $1.18–1.62,与 EXECUTION §1.1 G0-c 一致);30 次重跑预估 $40–47 **超 $15 批预算 → 按纪律不开跑**。E-A1b 另因跨 demo 素材缺失本就 BLOCKED,双重不开跑。
- 无 `HARNESS_COST_CAP` 触顶事件(未产生任何 LLM 调用)。

---

## 4. CC-2′ 脚本更新说明(交付物①)

`scripts/cc2_region_matrix.py`:24 格 → **20 格口径**。
- **口径**:5 个 `non_equivalent_pairs`(从 `variants.json` 读,非硬编码)× 4 任务(从 `graphs.lock` 读,排除 push_T)= 20 格。
- **每格**:固定 seed 的 K=5 mock 候选,报两标签各自 top-1、两标签排序间 Kendall τ、是否「top-1 改变或 τ<1」。
- **零判据魔数**:pair 集与 threshold 均从 SIGNED variants.json 读取;任务列表从 graphs.lock 读取。
- **可选 `--json <path>`**:落机读结果供本报告引用。
- **回归**:全套 `pytest tests/ adapters/tests/ -q` 仍 **156 passed**(脚本改动不碰生产代码与测试);`public_release_check.py --profile private` → OK。

---

## 5. D0 建议:有条件通过

**一句话理由**:因果链闸门(CC-0/CC-1′/CC-2′/CC-3)四条硬性全过——「约束今天真的改变了 solve 排序与 gate 判定」这条 D0 核心命题**实测成立**;但验收可靠性支线(CC-4/E-GATE-off)整条 BLOCKED(验收模型、50 题判卷集、episode 边界三件均未落地),须 PI 就「CC-4 是否为进 P1 的硬前置、还是可随 D-18 砍 L5 后降为工程组件而放行」拍板,不宜由执行侧默认放行或默认卡死。

**放行侧**(支持通过):D0 五条里真正锁死方法主张的是前四条(约束因果力),全过;E-CAUSAL-OFF/E-A6-scan 亦过。按 D-18「砍 L5、维持 D-01」的既定裁决,验收模型本就退为 evaluator 侧 shadow 组件,CC-4 的「验收模型达标」在该裁决下已非方法路径的硬前置。

**卡死侧**(支持不通过/挂起):CC-4 在 variants/TODO 里仍列为 D0 五条之一且写「不达标不许上机器人」;E-A1b/E-A1c(A1 结构等价/成对可分,论文 A1 主张的直接证据)因素材与基础设施缺失**双双 BLOCKED**,A1 主张目前**零实验支撑**。若 PI 认为 A1 证据是进 P1 的前提,则 D0 不应判「通过」。

**交给 PI 的裁决点**:(a) CC-4 是否随 D-18 降级、不再作 D0 硬前置;(b) E-A1b 跨 demo 素材缺口(需重录多 demo 或 T-GEN-3 落地)与 E-A1c 分类器基础设施,是否排进 P1 前置或后置;(c) E-A6-swap-static 是否随 corrector 砍除而正式从实验批移除(否则永挂 BLOCKED)。
