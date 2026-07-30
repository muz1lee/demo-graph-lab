# EXECUTION.md —— 执行文档（实验 · 框架 · TODO · 预算 · 环境）

## 0. 本文定位

- **与 `PROPOSAL.md`（v3）的关系**：v3 定 **idea 与框架**（假设 A1–A7、L1–L5 分层、冻结定义、止损条款）；本文定 **怎么做、做到什么算数、按什么顺序**。v3 是权威，本文是它的执行绑定。
- **取代关系**：本文**取代旧 v2 proposal 的实验设计与验收部分**（v2 其余内容按 D-16 保留原文 + SUPERSEDED 块）。
- **预注册纪律**：所有阈值开跑前写死并 commit；事后调整必须留 git 记录并**声明该批数据作废**。
- 本文合成自 experiments / framework / todo 三份设计并修掉了交叉检查的 blocker/major/minor，凡与三份原稿冲突处以本文为准。

---

## 1. 实验与验收

### 1.1 G0：开工前必做的三项事实核验（半天，不做完不许排期）

| ID | 核验 | 做法 | 影响 |
|---|---|---|---|
| **G0-a** ✅**已完成 2026-07-30 晚** | **夹爪到底动不动 → 能动** | 5090 `~/knowin_sim` runtime，场景 `v4_protocol_smoke`，机器人 `k1u_v4_w_claw_26w27_1d`；`GET :8000/run?action=ctrl&name=set_gripper&kwargs={"arm_id":0,"angle":N}`，**`angle` 0（张开）–100（闭合），超值被 `gripper.max_angle=100` 截断**；比对腕相机 `left_hand/left` | **结论：夹爪可动 → BLK-1 解除、§1.5 恢复正常可执行、P2′ 不触发。**证据：`angle=0` → 73938 B / md5 `4505170dd4`；`angle=100` → 85611 B / md5 `3ef9e77851`；肉眼可见两个颗粒纹理指垫进入视野。**教训：此前判「不动」的根因是自己用错参数名——`gpos=` 调 `set_gripper` 静默无效（pipeline 仍回 `ok=True`、画面零变化），被误读成通道不通；`kwadapter.py:507-513` 注释已就地更正。**判「通道通不通」前先确认参数名与调用形态。**未随本次结清**：claw 暴露几个自由度仍未核（环境事实 15 / 注释 12），见 §5 末行 |
| **G0-b** | 测试基线 | `python3 -m pytest tests/ adapters/tests/ -q` | 实测 **36 passed**（非原稿写的 88）。所有增量判据以 36 为基数 |
| **G0-c** | 单位成本 | 读 `harness/runs/*/cost.jsonl` | 实测一次编译 **31–37 次调用 / $1.38–1.62（≈$0.045/次）**，非从 `HARNESS_COST_CAP=8.0` 反推的 $5–8 |

### 1.2 存亡实验 E-CAUSAL（约束因果性）

> 改一条 demo 约束的实参，行为是否可预测地改变？唯一决定 $2000+ 是否开销的闸门。

**观测量修正**：不看「调用序列 / solve 的 hole 名集合」——hole 名是图里声明的、对 arg 改动不变，`fakerun.FakeRuntime.solve()` 只做成员检查后返回 `Handle("hole", name)`，改约束恒等，0/6 可观测。**观测量必须是 mock 实体态下 `solve()` 的返回值**（即 `tests/test_solve_dispatch.py` 形态）。

| 臂 | 变体 | 重编译 | 观测量 | 载体 |
|---|---|---|---|---|
| **value-level** | V1 V2 V3 | 否 | `solve()` 返回位姿 | `tests/test_constraint_causality.py` |
| **structure-level** | V4 V5 V6 | **是**（约束值被烤成源码字面量，见 `runs/harness_insert_tubes_20260730_003434/policy.py:6` 的 `rt.approach("tube_left", cone={...})`） | 编译后调用序列 / cone 实参 | 重跑 `compile` |

**6 变体必须照真实 graph.json 重写实参**：原稿的 `hole_A/hole_B`、`bowl_3` 在图里不存在（insert_tubes 是 `inside(tube_left, rack)` 物体级、无逐孔粒度；stack_bowls 真实实参是 `(bowl_top_right, bowl_mid_right)`）。预注册文件 `experiments/causal/variants.json`，**先照 5 份真实图定稿再 commit**。

**三段递进闸门**：OFF（L0 离线，V1–V3 + 重编译 V4–V6，0 ep，$0–8）→ L1（不需抓取，V1 V2 V4，`get_xquat` 末端落点，60 ep，<$20）→ L2（完整抓取，V1–V6，`/state` 物体终态，120 ep，~$200）。前段不过不进后段。

**预注册协议**：每变体在跑任何 episode 前写死①该变的量+方向+量级（例 V3 `upper_body→bottom`，抓取点世界 z 下降 ≥ 物体高 40%）②**不该变的量清单**（V3 不应改放置阶段目标 xy）③判失败的样子。配对设计：同 scene seed / 同摆放 / 同编译种子，只改一条约束，n=10/变体；先跑 10 次同约束重复得 σ_base。

| 方向命中率 | 效应量 d=\|Δmean\|/σ_base | 特异性（不该变的量 \|Δ\|<2σ_base） | 判决 |
|---|---|---|---|
| **≥4/6** | **≥1.5** | **≥0.80** | **是** —— 全案继续，这是论文核心图 |
| ≥4/6 | ≥1.5 | <0.80 | 半 —— 过度耦合，修 solve 参数隔离，**只许重跑一次** |
| 3/6 | 0.8–1.5 | 任意 | 半 —— 定位到 §2.5 具体断链，修**一条**，重跑**一次** |
| ≤2/6 | <0.8 | 任意 | **否** —— 全案止损，按 v3 §10 降级为系统贡献，撤 A1/A2 科学主张 |

**必须先做完 §2.5 断链修复（尤其 solve 消费 constraints args + `approach()` 消费 `cone`）才允许结算**，否则会把「代码没接通」误判成「约束无因果力」而错误止损。

### 1.3 L0 离线实验（无机器人）

| ID | 杀谁 | 臂 / 样本 | 判据 | 成本 | 人日 |
|---|---|---|---|---|---|
| **E-CAUSAL-OFF** | 存亡 | §1.2 | ≥5/6（value 臂 3/3 + structure 臂 ≥2/3） | $0–8 | 1 |
| **E-A1b** 结构等价 | A1 | 同 demo 重复 vs 跨 demo；2 任务 × 3 demo × 5 seed = 30 次 `extract+enrich` | 同 demo 阶段一致率 ≥0.90 / Jaccard ≥0.85；跨 demo ≥0.80 / ≥0.70 | ~$75 | 2 |
| **E-A1c** 成对可分 | A1 | 盲分类器把 6 张去名图匹配回任务，6×5 | 正确率 **≥0.90**（chance 0.50）；<0.70 → A1 严重受损 | $0.5 | 0.5 |
| **E-A6-scan** | A6 | 见下 | 见下 | 0 | 0.5 |
| **E-A6-swap-static** | A6 | 两任务实例化后 corrector prompt `diff` | diff 为空；非空即冻结主张当场不成立 | 0 | 0.5 |
| **E-GATE-off** | 验收可靠性 | 用 `runs/` 已有帧离线跑 verifier，固化 prompt 与 schema；50 题判卷集（含 20 条「看起来对但没做成」空洞样本） | acc **≥0.80** 或 κ **≥0.6** | $15 | 1.5 |

**E-A6-scan 重写**（原稿认错扫描器）：`harness/validate.py` 扫的是**图的 args**；policy 字面量扫描已在 `compilepolicy.static_check`（AST 级，STAGES 字典外任何数字字面量一律拒绝）强制成立 → ①标为「既存门禁，仅回归确认」。真缺口是 ②`corrector.py`/`verifier.py` 的 prompt 与工具描述（**0 命中，硬失败不可协商**）与 ③`kwadapter.py:303` 的 `top - 0.03`、`:317` 的 `value=0.05`（须归零或给「通用几何推导」注释来源）。新扫描器接进 `scripts/public_release_check.py`。

**E-A5-off 移层**：原稿放 L0 是错的——`adapters/grasp_proposals/service.py` 只是数据结构 + HTTP 边界（权重与 graspnet-baseline 源码明令不入仓），`experiments/insert_tubes/candidate_chain.py` 是一组正则**日志解析器**而非候选生成器，且标注要用 `/state` 即需活的 EvalServer。**E-A5-off 改挂 L1.5，依赖 T0-9 + T1-4。**

### 1.4 L1 实验（需 sim，不需抓取）

前提：`python -m sim.runtime --serve --serve-port 7480 --web-port 7481 --backend cuda`（`--web-port` 必须配 `--serve`），单实例。

| ID | 内容 | 判据 |
|---|---|---|
| **E-FRESH** | `evidence.py` 取图记内容 md5 + 时刻；同 source/view 连续两次 md5 相同 → 重取（≤3 次） | **统一为一条线**（作废原稿的 0% / <20% / ≤2% 三条）：3 次重试后仍 stale **≤2%** 通过；**2–10%** 降级为 `degraded=true` 并记 `INFRA`；**>10%** 图像通道不可用，verifier 与 corrector 全部不许上线 |
| **E-GATE-live** | §1.6 | §1.6 |
| **E-VIEW** | (a)`overview` 单张 /(b)`+left+right` /(c)`+depth` | (b)−(a) balanced acc **+8pp** 或角度 MAE 降 **≥25%**；差 <3pp → 降单视角，砍 L2 约 2/3 图像 token |
| **E-CAUSAL-L1** | V1 V2 V4，看末端在 approach/pre_align 停在哪 | 方向命中 ≥2/3 |

### 1.5 L2 实验（需完整抓取）

> **G0-a 已证夹爪可动（2026-07-30，§1.1）→ BLK-1 解除，本节恢复正常可执行**，不再转 §3.3 的 P2′。
> **但 ep 数、成本与周期约束一条未松**：ep 数仍是占位值（见下「主指标两处修正」第 2 条，须先跑 20 ep 实测每 ep 节点观测数），单集成本仍待实测回填（§4.1），sim 独占仍是吞吐硬顶（BLK-2）。抓取「能动」≠「能抓稳」——真实抓取成功率至今仍是 0 次。

| ID | 臂 | 主指标 | 判据 | ep | 杀谁 |
|---|---|---|---|---|---|
| **E-A1a** | (a) 全约束图 /(b) 仅点位 /(c) 无 demo 只语言 | 节点级通过率 | 重排场景下 (a)−(b) **≥15pp**（McNemar p<0.05） | 360 | A1 |
| **E-A2** | (a) 只出增量 /(b) 允许新增目标 | 同上 | (b)−(a) ≥+10pp 且 p<0.05 → **A2 假**，重声明赌注并重打两边 baseline | 160 | A2 |
| **E-A3** | 复用 A1a/A4 log 离线标注 | AUC / 误杀率 | AUC ≥0.75；误杀率 ≤0.25 | 0 | A3 |
| **E-A4** | (a) 全信息 /(b) 关修正器 /(c) 目标剥离 /(d) 单视角 | 节点级通过率 | (a)−(b) ≥+10pp；**(a)−(c) <3pp ⇒ A4 新颖性死** | 240 | A4 |
| **E-A6-swap** | A 的 corrector 原样用于 B | 同上 | 掉幅 ≤5pp；>10pp ⇒ 任务泄漏 | 60 | A6 |
| **E-A7** | (a) 仅底层原语 /(b) 额外允许人手 yaml | episode 成功率 | (b)−(a) ≥20pp ⇒ 该类报告能力多来自 yaml 作者 | 100 | A7 |
| **E-CAUSAL-L2** | §1.2 | §1.2 | §1.2 | 120 | 存亡 |

**主指标两处修正（缺一不可，ep 数与周数均为占位值）**：

1. **节点级通过率必须按「可检查谓词」归一化并同时报 UNCHECKABLE 占比。** 当前 `kwadapter.verify` 的 `else: detail="unchecked"` 仍返回 True、`except → ok=True`；insert_tubes stage 0 的 3 条 acceptance 有 2 条（`clearance`/`region_grasp`）自动通过——今天量到的通过率是虚高。两臂 unchecked 比例不同时 15pp 判据无意义。§2.5 改动 #3/#3b 是前置。
2. **有效样本量必须实测。** 「每 ep 给 5–8 个节点观测」与 runner 相反：`fakerun.run_policy` 首节点失败即 `break`（记 `rollback_at`），薄底座下多数 ep 只产 1–3 个观测；deposit_coin 的 stage 0 与 stage 3 各只有 **1** 条 acceptance。**先跑 20 ep 实测「每 ep 平均节点观测数」再定 ep 数与周数。**

**归因准入**：每 ep 落 `harness/runs/ep_*/`，含 `{episode_id, node, layer, verdict, evidence_frames, llm_calls, cost, infra_error}`。**无法定位到「节点 × 层」的 ep 不计入任何结论**，但计入成本。

### 1.6 E-GATE：验收模型可靠性

**探针集**：4 类别（角度 `axis_parallel`/`axis_vertical`、距离 `center_align`、拓扑 `inside`/`above`、区域 `region_grasp`）× 75 = **300** 帧组；扰动近阈值/中/远各 1/3；正负比 1:1；scripted `qpos_move`/`delta_move` + `go_home` 摆位，**不需抓取**，约 60 个 scripted ep。n 依据：区分 bal acc 0.80 vs 0.65，α=0.05，power=0.9，二项检验需 n≈70/类别。

| 档 | 指标 | 通过线 | 通过则 verifier 可以 |
|---|---|---|---|
| **0（必须过）** | balanced accuracy / **假阳率** / 假阴率 | **≥0.80** / **≤0.10** / ≤0.25 | — |
| A | 角度 MAE（0–45°） / 中心距 MAE | **≤10°** / **≤15 mm** | 输出连续量直接喂 corrector |
| B | 三档粗分级（<5°/5–20°/>20°） | **≥0.70** | 只出粗等级，corrector 按等级选步长 |
| C | 方向标签（左右前后顺逆） | **≥0.85** | 只出方向，corrector 用固定步长 |
| **地板** | 方向标签 | **<0.70** | **L5 整层删除**，收缩为 gate-only 开环，L2 预算砍半 |

假阳最严的理由：假阳 = 把结构性失败放行进下一节点，同时污染 A3（早止损）与 A4（有界修正）；假阴只是浪费预算，故两侧阈值故意不对称。**档 0 不过** → verifier 不能单独否决，降级为「verifier + 几何谓词的与门」（压 FP，FN 上升，需在 A3 重测误杀率）。

**`region_grasp` 的 ground truth**（无任何底层信号）：evaluator 侧规则 —— 物体几何中心与末端 TCP 的相对位姿在连续 K=5 帧内位移方差 <1 mm ⇒ 判「已刚性握持」，再由相对位姿落在物体 AABB 哪一段判 region。须在 E-GATE 前用 20 个人工标注 ep 校准，一致率 <0.90 则该类别整体作废。**前提「确认爪子自由度数目」仍未结清**——G0-a 只证了可动，自由度口径（15 vs 12）未随本次核实，见 §5 末行。

---

## 2. 代码框架

### 2.1 统一模块名与职责（**三份原稿起了三套名字，此表为唯一准绳**）

| 层 | 模块 | 状态 | 职责 |
|---|---|---|---|
| L1 | `ingest/stages/keyframes/registry/extract/enrich/validate/report/metrics/vocab` | **零改动** | Phase 0 已达标 P=0.931/R=0.865，冻结 |
| L2 | `harness/binding.py` | **新** | solve 实现主体：按 `hole["type"]` 派发、消费 `frame`/`solver_hint`、**并消费本阶段 `constraints` 实参** |
| L2 | `harness/regions.py` | **新** | region/cone 符号 → 几何带的**任务无关**映射（6 region × 3 cone），零 per-task 分支 |
| L2 | `harness/kwadapter.py` | 改 | 只留 IO 与委派，目标 **<400 行**（现 619） |
| L2 | `harness/perception.py`(P1)、`harness/graspfunnel.py`(P1) | 新 | `PHASE1_API_PLAN.md §2` 的 12 个非特权 API 门面；三层漏斗 + tie-break 记录 |
| L3 | `harness/targets.py` | **新** | 确定性 pass（无 LLM）：节点 constraints/acceptance → `target_spec` → `targets.json`，**corrector 唯一目标来源** |
| L3 | `harness/compilepolicy.py` | 改 | 尾部增写 `targets.json` |
| L3 | `harness/contract.py` | **明令不动** | 被 `compilepolicy.py:83` 用 `inspect.getsource` 整体拼进提示词，改它 = 静默改提示词 = 已编译 policy 与新契约不同源 |
| L4 | `harness/predicates.py` | **新** | 约束 → 可执行检验函数，返回 `(verdict ∈ {PASS,FAIL,UNKNOWN}, margin, evidence_kind)` |
| L4 | `harness/verifier.py` + `prompts/runtime_verifier.md` | **新（工位 B）** | 多视角图 + 约束 → 每条 `satisfied/unsatisfied/unknown` + 置信度；**只返回 `Verdict`** |
| L4 | `harness/gates.py` | 改 | 消费 `constraints`、三值、去特权（§2.5 #2/#3b/#5） |
| L5 | `harness/corrector.py` + `prompts/runtime_corrector.md` | **新（工位 A）** | 多视角图 + `target_spec` + 上次自己的增量结果 → **只返回 `Delta`**（体固定 mm/deg） |
| L5 | `harness/bounds.py` | **新** | 单步限幅 / 节点累计上限 / 次数·墙钟·成本预算 / 越界拒绝并记录 |
| L5 | `harness/robotapi.py` | **新** | 8 个任务无关 helper，`ctrl/info/reasoning` 原语唯一封装 |
| 横切 | `harness/evidence.py` | **新** | 多视角取图**唯一入口** + 帧新鲜度门 → 带 digest 的 `EvidenceBundle` |
| 横切 | `harness/episode.py` | **新** | `Delta`/`Verdict`/`EvidenceBundle`/`StationCall`/`EpisodeLedger` + `assert_isolation()` |
| 横切 | `harness/attribution.py`(P1) | 新 | group-by 出「节点 × 层」首失败表，不做任何推断 |
| 横切 | `harness/util.py` | **不动位置** | 唯一 `__file__` 路径锚点 |
| 门禁 | `scripts/public_release_check.py` | 改 | 并入隔离 lint、任务名扫描、字面量扫描（**不另起 `check_separation.py`**） |

**废弃别名**：`gatemodel.py`/`judge.py` → `verifier.py`；`frames.py`/`camera.py` → `evidence.py`。

**「运行期 gate 的 `passed` 由谁算」——本文按 framework 口径落地，但列为 PI 拍板项（§6-2）**：`gates.py` **留在原地继续算 `passed`**，`verifier` 只出 `Verdict` 作为它的一个证据源；`gates.py` 原先读 `rt._entities` 的特权路径搬到 evaluator 侧作**上界对照**。不采用「gates 整体迁走、验收模型接管判定」，否则 A3 误杀率无确定性基准。

### 2.2 运行时接口层（8 个 helper）

```
policy.py（契约 8 原语 + solve/residual/verify，冻结）→ kwadapter.KWRuntime（语义层，委派 binding/regions/predicates）
  → robotapi.RobotAPI（8 个任务无关 helper）→ PipelineClient /run: ctrl / info / reasoning
corrector 的 Delta 唯一通路： runner → bounds.clamp() → robotapi.nudge()   （bounds 无任何参数能被模型输出影响）
```

| # | helper | capx 对位 | 底层 | 关键约束 |
|---|---|---|---|---|
| 1 | `observe(spec) -> EvidenceBundle` | — | `GET /api/frame.jpg?source=&view=` ×4 | **唯一取图口**，必过新鲜度门 |
| 2 | `segment(query)` | `segment` | dgl-perception :8114 | P1；未上线前 hole 判 `UNSOLVED`，不静默退化 |
| 3 | `propose_grasps(...)` | `plan_grasp` | GraspNet :8115（相机系） | P1；TCP 偏移与世界变换在调用方 |
| 4 | `plan_joint_path(q_goal\|tcp_traj)` | `solve_ik`+`traj_plan` | `reasoning:motion_planning_stereo` | **代替 solve_ik，raw IK 不给**（直达零成功先例） |
| 5 | `execute_path(waypoints)` | `move_to_joints` | `ctrl:qpos_move` 逐点 + `get_qpos` 收敛核对 | 绝不整段下发大跳（MotorNode 停 70–80%） |
| 6 | `nudge(d_xyz_mm, d_rpy_deg)` | — | `ctrl:local_delta_move`（体固定） | **corrector 唯一出口**，入口强制过 `bounds` |
| 7 | `set_grip("open"\|"close")` | `open/close_gripper` | `ctrl:set_gripper` | **参数名必须是 `angle`（0 张开–100 闭合，超值被 `max_angle=100` 截断）；写 `gpos=` 会静默无效且仍回 `ok=True`**。开合角**不可读**；MotorNode 秒回 `SUCCESS` 是假阳性（`_wait_gripper` 拿上一条指令值比目标），**不得当「已到位」证据**。只记指令值，禁止读回读 |
| 8 | `read_state() -> ArmState` | — | `get_qpos`/`get_xquat`/`get_ee_extforce` | 非特权；`is_gripping_sth` 只作旁路遥测，打 `untrusted=True` |

**三条硬规则**（写进 `robotapi.py` docstring 且有 lint）：①没有 `solve_ik`，`kwadapter._move` 的限幅伺服保留为退化路径但每次使用打 `degraded=true`；②`is_gripping_sth` 不许进 `if`（返回字符串 `'False'`，`bool()` 判真），`robotapi.py` 之外出现该名字即 fail；③helper 签名与两份 runtime prompt 里不许出现任务名或图里物体名，扫描命中即 fail。

### 2.3 两个模型工位

**I/O（严格 JSON，多余键即拒绝）**：`corrector.build_input(target_spec, bundle, last_step)` 签名上**不接受 `Verdict`**，输出 `{"action":"adjust|stop","delta_mm":[..],"delta_deg":[..],"referenced_predicates":[..],"rationale":"<=200 chars"}`；单位 **mm/deg**（量纲错位是实测最常见的模型错误）；`referenced_predicates` 为空或非输入谓词子集 → 整条提案作废。`verifier.build_input(constraints, bundle, phase)` 签名上**不接受 `Delta`**，输出 `{"judgements":[{"id","satisfied":true|false|"unknown","confidence","evidence_view"}]}`；`unknown` 必须允许；`satisfied=true` 且 `confidence<0.60` 自动降级为 `unknown`，**0.60 是全局常量，禁 per-task 调**。

**隔离机制的诚实分级**（原稿把事后检查称为「结构性保证」，此处更正）：

| 级别 | 机制 |
|---|---|
| **真结构性（唯一一条）** | `contract.Runtime` 不含 corrector/verifier API → 编译出的 policy **在语法上无法调用**它们（`contract.py` 不改即成立） |
| 类型级 | `Delta`/`Verdict` frozen dataclass；`bounds.apply()` 与 `gates.evaluate()` 首行 `isinstance` 断言 |
| 签名级 | 两个 `build_input` 都没有能接住对方输出类型的形参 |
| 信息级 | corrector 拿不到「哪条约束没过」，只拿编译期 `target_spec` 全量谓词 |
| **事后作废级** | `assert_isolation(ledger)` digest 交叉断言、证据重采（corrector 后必须重新 `observe()` 才允许调 verifier）、独立 model slug 与独立记账 —— episode 结束时跑，**只能判该集作废，拦不住泄漏** |

- **API key 口径统一**：`harness/llm.py:24` 读单一 `OPENROUTER_API_KEY`。要么真配第二个 key 并改 `llm.chat` 支持，要么**明说隔离只到「进程 + 静态门禁 + 事后作废」级**——不许继续宣称「分 API key」。
- **升为硬约束**：两工位若用同厂同代模型，视觉盲区高度相关，「评判者与被评者分离」只是名义上的。**`HARNESS_VERIFIER_MODEL` 与 `HARNESS_CORRECTOR_MODEL` 必须是不同厂商或不同代**，否则 E-GATE 的独立性结论不成立。
- **信息级隔离的代价必须认领**：corrector 可能去修一条本已满足的约束——由累计限幅兜底，并在 `corrections.jsonl` 统计「修了没必要修的比例」，这本身是 A4 的数据。

### 2.4 限幅、预算与两级仲裁（任务无关，来自本体标定）

| 量 | 值 | 出处 |
|---|---|---|
| 单步平移 / 旋转 | **20 mm** / **5°** | < `SERVO_STEP_M`=50mm、`SERVO_STEP_DEG`=14°；> 噪声 1.5mm / 0.4° |
| 节点累计 ‖Σδ‖ / Σ\|θ\| | **60 mm** / **15°** | < `PREGRASP_DZ`=100mm；远离 IK ~90° 拒绝阈与 `pair_id=178` 自碰撞区 |
| 节点 / 每集修正次数 | **3** / **10** | |
| 每集验收调用 | 2×节点数 + 重试，insert_tubes 6 节点 → **12–18** | |
| 每集运行期 LLM 调用上限 / 单次墙钟 | **28** / **20 s** | 与编译期 31–37 次对照 |
| 每集运行期成本 | `HARNESS_RUNTIME_COST_CAP`，**首批 20 集实测后回填**（不许拿 CAP 反推） | 与 `HARNESS_COST_CAP` 分开 |

越界处置：≤上限 → 直发；上限～3× → clamp 记 `clamped=true`；>3× → **拒绝**记 `rejected=true`；连续 2 次 rejected → 终止本节点修正交回 gate。**累计上限是 v3 §12 #6（用一串合法小增量实现新目标）目前唯一可执行的封堵。**

两级仲裁（确定性规则，不由模型裁）：物体/EEF 位移 <5 mm 或 grasp 类节点 acceptance 全 fail → **结构性**，直接 reject 不进修正；acceptance 部分 fail 且世界确有变化 → **微差**，进修正循环；全部 judgement=`unknown` → **不可判**，记 `GRAPH_GAP`/`INFRA`，不算任务失败。

### 2.5 断链最小改动集

统计口径：以每任务最新一份 `graph.json` 为准（**须把 5 个文件路径 + sha256 钉进 `experiments/causal/graphs.lock`**，`harness/runs/` 有 19 个 run 目录，「最新」本身有歧义）。实测 **86 洞 / 误派 30（35%）/ 28 掉进 `runtime_condition` 兜底 / 43 洞（50%）声明非 world frame 而 `solve()` 完全不读 `frame`**。

| # | 位置 | 现状 | 改法 | 验证 |
|---|---|---|---|---|
| **1a** | `kwadapter.py:295-321` `solve()` | 子串匹配；`:296` 把 hole dict 绑进 `hole` 后一次都没再引用 | 迁入 `binding.py`：`st, hole = self._hole_index[hole_name]`（KeyError → `raise UnsolvedHole`，不回退当前阶段）；按 `hole["type"]` 派发 5 个求解器；非 world frame 先过坐标变换纯函数；`solver_hint` 只用于**选求解器**，禁建任务分支 | `tests/test_solve_dispatch.py`：86 洞全量，断言 ①命中 86/86 ②`coin_pose`/`retract_pose`/`push_direction` 三个已知误派归位 ③未知 type → `UnsolvedHole` |
| **1b** | 同上 | 参照物取自 `stage_objects.target`，**从不取自约束** | `solve()` **增加本阶段 `constraints` 入参**，参照物从约束 args 取（`center_align.obj_b`/`inside.obj_b`/`region_grasp.region`） | `tests/test_constraint_causality.py` 先红后绿：`\|z(upper_body)−z(bottom)\| ≥ 0.5 × 物体高` |
| **1c** | `kwadapter.py:488-496` `approach(target, cone=None)` | 形参 `cone` 在函数体内**零引用** | `regions.py` 映射表；`approach()` 真正消费 `cone` | 6 region × 4 任务 = 24 格扰动矩阵产生不同 pose **≥22/24**；`regions.py` grep 任务名命中 **0** |
| **2** | `gates.py:60-117` `evaluate()` | 只读 `stage["acceptance"]`，`constraints` 一行不读 | 增参 `constraints`：`holds=="throughout"` 在 entry/exit 各查一次（违反即 fail 记 `violated_midway`），`holds=="at_end"` 并入验收；verdict 拆 `constraints_hold`/`acceptance_hold`，`passed = both` | `tests/test_gates_constraints.py` |
| **3** | `kwadapter.py:582-615` | 三处 fail-open：`:584` `ok=True` 初始化、`:611` `unchecked` 默认真、`:613` `except → True` | `predicates.py` 三值 `PASS/FAIL/UNKNOWN` + `margin`；未实现谓词显式列入 `UNCHECKABLE` 并计数（这是「验收覆盖率」研究数据） | `tests/test_predicates.py` ≥20 例；10 个词表约束 **≥8 个**有可执行谓词 |
| **3b** | `gates.py:55-56` / `:94` | 第四、五处 fail-open：`snapshot` 异常 → `pre_true=False`；`effect_ok = … or (not observable)` | `snapshot` 异常 → `UNKNOWN` + 空洞性标 `undecidable`；`evaluate` 增参 `require_observable`，Phase 1 传 `True`，不可观测即 fail + `code=INFRA` | 同上加两条负例 |
| **4** | `fakerun.py:49-57` | `push` 在 `__getattr__` 白名单被吞成绿，而 `kwadapter.push:575` 是 `NotImplementedError` → **干跑绿、实跑炸** | 从元组删 `"push"`，加显式 `def push(...): raise NotImplementedError`；4 份 `push_T*` policy 共 8 处 `rt.push(` 在编译期干跑就红 —— **这是期望行为**（不是实现 push，D-14 仍生效） | 扩 `test_harness_units.py:140` |
| **5** | `gates.py:32-46` `object_positions()` | 用 `rt._entities`（`/state` 特权态）驱动 retry/reject = 特权数据回流恢复决策，违反 AGENTS §3 / D-05 | method 路径改用 ①`get_xquat` EEF 位移 ②verifier 的 entry/exit 帧差；`_entities` 位移改名 `oracle_displacement_m` 只写进 verdict 的 `privileged_oracle` 段作上界，**不参与 `passed`** | `tests/test_gates_no_privilege.py`：只有 `_entities` 有位移、`get_xquat` 无位移的假 rt → 断言 `passed=False`（旧行为是过） |
| **6** | `contract.residual()` | 软 stub，`deposit_coin` 的 policy 第 15 行真的在调 | **保留契约签名、不动 `contract.py`**；kwadapter 侧返回 `UNSUPPORTED` 句柄并记账，只写进 evaluator 诊断段；等下一次统一 contract bump 时连同 4 份 policy 一起重编译 | — |

**门禁（改动前后各跑一次）**：

```bash
cd demo-graph-lab
python3 -m pytest tests/ adapters/tests/ -q                  # 基线 36 passed → 目标 ≥43 且原 36 条全绿
python3 scripts/public_release_check.py --profile private    # 期望 "release check [private]: OK"
python3 -m harness.cli compile --task insert_tubes           # 静态检查 + 干跑不得回退
```

### 2.6 产物格式与归因码

```
harness/runs/ep_<task>_<task_id>_<YYYYmmdd_HHMMSS>/
├── manifest.json  policy/graph/targets/contract/prompts 的 sha256 + model slugs + bounds 常量快照 + git commit + sim task_id
├── events.jsonl   {"ts","node","attempt","corr","layer","station","op","bundle_id","output_digest","result","cost_usd"}
├── ledger.jsonl   station/model/tokens/cost/latency/input_digests/output_digest
├── corrections.jsonl  proposed / clamped / applied / rejected / 之后的本体回读
├── verdicts.json  每节点×每约束：entry/exit 判定、confidence、vacuous 标记、constraint_ledger 的 margin
├── frames/n03_a1_c02_head_overview.jpg     └── outcome.json  终局 + first_failure{node,layer,code} + attributable(bool)
```

`layer ∈ {L0_infra, L1_graph, L2_bind, L3_compile, L4_gate, L5_correct}`；join key = `(episode_id, node, layer)`。

| code / layer | 判据 | 计入方法失败 |
|---|---|---|
| `INFRA` / L0 | 服务异常 / `bundle.degraded=true` / ctrl transport 失败 | ❌ |
| `BUDGET_EXHAUSTED` / L5 | ledger 触顶 | ❌ |
| `UNSOLVED_HOLE` / L2 | `UnsolvedHole` | ✅ |
| `GRAPH_GAP` / L1 | 该节点全部 acceptance = `unknown`/`UNCHECKABLE` | ✅（记 L1 非 L4） |
| `COMPILE_MISUSE` / L3 | 运行时 call trace 与图对账不符 | ✅ |
| `GATE_REJECT_STRUCT` · `GATE_FAIL_MICRO` / L4 | §2.4 结构性规则 / 修正预算耗尽 | ✅ |
| `CORR_CLAMPED_OUT` / L5 | 连续 2 次 rejected | ✅ |
| `TASK_FAIL` / 终局 | 全节点过但终局验收失败 | ✅ |

`attributable=false`（`assert_isolation` 失败 / manifest digest 对不上 / 帧全程 degraded）的 ep **不计入任何结论**。任一批次 `INFRA` 占比 >15% → **该批数据作废**，不计入结论也不计入止损分母。

---

## 3. TODO 与推进顺序

### 3.1 P0（**1.5–2 周，不是一周**）—— 不依赖机器人、不依赖夹爪

| 编号 | 一句话 | 依赖 | 完成判据 | 人日 |
|---|---|---|---|---|
| **T0-0** ✅**已完成** | G0-a/b/c 三项事实核验 | 5090 起 sim（机器人不动） | §1.1 三行全部有数 —— a：夹爪可动（2026-07-30 实测）；b：36 passed；c：$1.38–1.62/编译。**残留**：claw 自由度口径未核，归入 §5 末行 | 0.5（已耗） |
| **T0-1a** | 写反事实测试并让它**红** | 无 | `tests/test_constraint_causality.py` 红 run 输出入 PR | 0.5 |
| **T0-1b** | `binding.py`：solve 消费 `type/solver_hint/frame` **+ 本阶段 constraints** | T0-1a | 测试转绿；`hole` 不再是死变量 | 1.5 |
| **T0-1c** | `regions.py`：region/cone → 几何带任务无关映射 | T0-1b | 24 格 ≥22/24；任务名命中 0；`approach()` 真消费 `cone` | 0.5 |
| **T0-2** | `predicates.py`：约束 → 检验函数，三处 fail-open 归零 | 可并行起草 | ≥8/10 约束有谓词；三值 + margin；`tests/test_predicates.py` ≥20 例 | 2.0 |
| **T0-3** | `stage['constraints']` 进运行期（改动 #2） | T0-2 | 4 任务 dry-run 出逐阶段逐约束 `{verdict, margin}` 台账；`UNKNOWN` <20% | 1.0 |
| **T0-4** | dry-run 不许把 `push` 吞成绿（改动 #4） | 无 | 8 处 `rt.push(` 报 `unsupported_primitive` | 0.5 |
| **T0-5** | `kwadapter.py` 619 → <400 行 + 补测 | T0-1b, T0-2 | 三块有单测；**不许砍**（只有 36 条测试，先红后绿是唯一护栏） | 1.5 |
| **T0-6a** | 双工位边界与 lint（不含模型）：`episode.py` + `bounds.py` + 两 prompt 文件 + 隔离规则并入 `public_release_check.py` | 无 | `Verdict`/`Delta` 无公共基类；lint 断言 `gates.py` 调用图不出现 corrector 符号；两条独立 call ledger | 0.5 |
| **T0-6b** | 离线判卷集 50 题 + 验收器一致性 | T0-6a | `harness/goldset/judge_eval_50.json`；acc ≥0.80 或 κ ≥0.6 | 1.5 |
| **T0-7** | `evidence.py`：多视角取图 + 帧新鲜度门 | 需 sim | 4 源 × 5 视图全通；新鲜度按 §1.4 单一判据；3 视角 + depth bundle wall time <2 s | 1.0 |
| **T0-8** ⬇**低优先级** | `is_gripping_sth` 的 `current_limit` 恒 0 报 owner（**并行，永不阻塞**） | 无 | `tools/gripper_repro.py` + issue 编号。**报的不再是「夹爪不动」（已证伪，见 G0-a）**，而是 `_apply_gripper_control` 在 `fixed` 模式下仍把 **v4 已移除的 `snap.torques[arm][7]`** 当 `current_limit` 传入 → 恒 0 → `is_gripping_sth` 恒假；附「返回字符串 `'False'`」。**本方案不依赖该信号（PI 已裁定 gate 约束更大、另用专门模型判抓取），报出去只为帮上游**；我方只在本仓加 `_truthy()` 防御并标「不作为方案依赖」 | 0.5 |
| **T0-9** | GraspNet 资产固化（**并行**） | 无 | `third_party/DEPENDENCIES.md`：commit + 12M weights sha256 + 可重跑部署命令；**权重不入库** | 0.5 |
| **T0-10** | `targets.py` + `compilepolicy` 增写 `targets.json` | T0-2 | 5 份 graph 各出一份，谓词数 = acceptance 数 | 1.0 |

**裁剪线**（PI 按周检查以 W1 必做为准；T0-0 已完成不再占 W1，T0-8 降级后移入弹性）：**W1 必做 2.0 人日** = T0-1a + T0-1b；**W1 弹性 4.0** = T0-1c + T0-2 + T0-4 + T0-8 + T0-9；**W2 必做 4.5** = T0-3 + T0-6a + T0-10 + §1.3 的 L0 实验；**W2 弹性/顺延 4.0** = T0-5 + T0-6b + T0-7。

### 3.2 P1（3–3.5 周）—— 要机器人，不要抓取成功

| 编号 | 一句话 | 依赖 | 完成判据 | 人日 | 占 sim |
|---|---|---|---|---|---|
| **T1-0** | **离线 bundle 录包器**（解 sim 独占死结） | T0-7 | `tools/bundle_recorder.py`；5 任务 × 3 seeds ≈ **200 帧**，每帧 `rgb/depth/K/T_world_cam` + 同刻 `/state` | 1.0 | 短占 |
| **T1-1** | motion planning 换掉手写 servo | 5090 + `ssh -A` | 10 目标：到位率 **≥8/10**、末点 `rot_error <10°` 且沿路点单调不发散（对照现状 16°→52°）、零幽灵自碰 | 3.0 | **是** |
| **T1-2** | 可达姿态域标定（per-robot 非 per-scene） | T1-1 | `harness/calib/reach_pose_envelope.json`，标 `provenance=calibration`；与 `CLAW_TIP_DZ` 同类，不违反 GT 防火墙 | 1.5 | **是** |
| **T1-3** | 非特权感知最小集（API #1/#2/#4/#6/#11） | T1-0 | 5 实体 × 3 seeds：位姿误差中位数 **<15 mm**，孔位误差 **<14.9 mm**；四元数统一 wxyz、OBB extent 统一全边长 | 3.0 | 否 |
| **T1-4** | GraspNet 服务化 :8115（CaP-X `/plan`） | T0-9, T1-3 | `insert_tubes_000` 单帧 ≥20 候选；变换到世界系后 ≥5 个落在管体 AABB 内 | 2.0 | 否 |
| **T1-5** | 三层漏斗 + funnel 数字（**P1 主交付**） | T1-2, T1-4, T0-2 | 出表：候选 → L1 → L2 → L3；**L3 明确否决掉多少个「L1+L2 最优但下游不可行」**——这就是消融 B 的数据，全程不需要夹爪 | 3.0 | 否 |
| **T1-6** | counterfactual 场景（用 v4 现成 37 任务 suite seed 筛，**不手写 yaml**） | T1-5 | ≥1 组「局部最稳 grasp ≠ 下游可行 grasp」 | 1.0 | 否 |
| **T1-7** | verifier 接真图（**仍 shadow mode**） | T0-6b, T1-0 | 在 approach/align 两个无需抓取阶段跑；与几何谓词一致率出数；分歧样本人工复核 ≥20 例 | 1.5 | 否 |
| **T1-8** | no-demo frontier agent 基线：**只做接入评估** | 无 | 一页结论：把本 sim 注册成 `inspect-robots` embodiment 的成本 | 0.5 | 否 |
| **L1 实验** | E-FRESH → E-GATE-live(60 ep) → E-VIEW → E-CAUSAL-L1(60 ep) → E-A5-off | T1-0, T1-4 | §1.3 / §1.4 | — | **是** |

合计 16.5 人日 + L1 机时。若必须压到 3 周，砍 T1-6 到 P2 前置，**不砍 T1-3**。
**验收器上线节奏不可反**：边界 W1（T0-6a）→ 模型 W2（T0-6b）→ 接真图 W3–W4（T1-7）→ **参与判定要等 D1**。W3 之前一律 shadow mode（写报告、不进 gate）；一旦开始产真 ep 后再改验收通道，硬边界 1 直接判该批数据作废。

### 3.3 P2 / P2′ —— **走 P2；P2′ 保留为备用分支但不触发**（BLK-1 已于 2026-07-30 解除）

| 编号 | 一句话 | 依赖 | 完成判据 | 人日 |
|---|---|---|---|---|
| **T2-0** ✅**已解除** | 夹爪通道解锁 | ~~外部 owner~~ → 无 | G0-a 已证 `set_gripper(angle=0/100)` 使指垫可见开合（腕相机 md5 差异）。**残留 0.2 人日**：把证据从图像升级为 `/state` claw revolute 的逐个 diff，顺带结清自由度口径（15 vs 12） | 我方 0.2 |
| **T2-1** | pose-in-hand：抓后估一次 + FK 传播 | T2-0, T1-3 | 闭合后估 `T_gripper→object` 一次；gate 失败或接触事件才重估，**不做在线密集追踪** | 2.0 |
| **T2-2** | `corrector.py` 落地（L5） | T2-0, T0-6a | 三条冻结判据同时成立：prompt 逐字不变 / 交换测试 / 字面量扫描零命中；输出仅限幅体固定增量，越界由 runner 拒绝并记录 | 3.0 |
| **T2-3** | 首批数字 | T2-1, T2-2 | insert_tubes + stack_bowls 各 20 seeds；ARCHIVE §1.2 双阈值 **≥16/20** 抓取+转正+对准、**≥12/20** inserted+upright | 3.0 |
| **T2-4** | Phase 2 冻结协议 | T2-3 | RunManifest + code digest；D/E seed 不相交；冻结后 policy/模型/配置/runtime 四项全禁改 | 2.0 |
| **T2-5** | 消融矩阵 = §1.5 全部 L2 实验 | T2-3, T1-6 | 每条消融有成对数字与「节点 × 层」归因 | 4.0 |

**P2′（备用分支，当前状态：不触发）**：原触发条件是「BLK-1 六周内不解锁」，该条件已于 2026-07-30 消失（G0-a 证夹爪可动）。形态保留备查——主交付改为「候选质量指标 + 消融 B + no-demo 基线」（`harness/DESIGN_GRASP_AND_LOOP.md` §5 已给出这条路），执行成功率降为附录，同时启动 T1-8 的实际接入换基线数字，**§1.5 的全部 L2 实验作废或延后**。**重新触发条件目前为空**：原条件（通道不通）已消失，新的回落条件（若有，应是「通道通但抓取长期不稳」一类）**未定，留给 PI**——在定出之前，本分支不得被任何人当作既有退路引用。
**T2-2 是「`contract.py` 零改动」的第一个破例点**（修正器要新 API）：集中一次改完，立刻重跑 5 任务 compile 并记新 digest，**不许零敲碎打改**。

### 3.4 阻塞项与并行边界

| ID | 阻塞 | 性质 | 阻塞谁 | 绕法 |
|---|---|---|---|---|
| ~~**BLK-1**~~ ✅**已解除 2026-07-30**（**保留此行作为「曾被误判为阻塞」的记录**） | ~~夹爪通道不通~~ → **误判**。真因是我方调用写错参数名（`gpos=` 而非 `angle=`），`set_gripper` 静默无效却仍回 `ok=True` | ~~外部~~ → **内部调用错误**，无需改原仓 | ~~整个 P2 与 §1.5 全部 L2 实验~~ → 已全部解绑，恢复正常排期 | 证据见 §1.1 G0-a（腕相机 `angle=0`/`100` 两图 md5 不同、指垫可见开合）。**教训留档：先证伪自己的调用形态，再把失败归因给外部栈；「pipeline 回 `ok=True`」不构成参数被接受的证据。**残留的真缺陷已降级并转入 T0-8（`current_limit` 恒 0 → `is_gripping_sth` 恒假，本方案不依赖） |
| **BLK-2** | `/tmp/knowin_sim_camera.sock` **独占** | 物理串行化，**吞吐 60–100 ep/天，是排期唯一硬约束（不是 GPU）** | P1 全部需 sim 的线 | T1-0 录包器把 T1-3/4/5 移到离线；只剩 T1-1/T1-2/L1 实验排班 |
| **BLK-3** | 约束因果链未通 → 下游数字无意义 | **语义**阻塞 | T0-2/T0-3 的价值、T1-5 全部结论、§1.5 全部归因 | 无绕法，只能先做 |
| **BLK-4** | GraspNet 权重在 git 外 | 可复现性 | T1-4 产物入库资格、E-A5-off | T0-9 先固化 sha256 + 部署脚本 |
| **BLK-5** | **D-01「运行期不放 LLM」仍是生效裁决**，与两个模型工位正面冲突 | 治理 | 整个 L4/L5 方案 | 无绕法，见 §6-1 |

并行边界（「subagent 读、主线写」）：**A 串行** = T0-1a→1b→1c→T0-2→T0-3→T0-5（都改 `kwadapter.py`/`gates.py` 同一批行）；**B 与 A 并行** = T0-4 / T0-6a / T0-8 / T0-9 / T0-10（文件集不相交，并行**写**须用 git worktree 隔离）；**C 半独立** = ~~T0-0（已完成）~~ / T0-7 / T1-0 / T2-0 残留（需 5090，与 mac 本地零冲突）；**D 并行**（前提 T1-0 完成）= T1-1+T1-2（占 sim） vs T1-3+T1-4+T1-5（跑录包）；**E 全程并行** = T1-8。

### 3.5 sim 排班表（**必须上墙，否则两套日程都是虚的**）

| 周 | 占 sim 的活 | 需要抓取 | 冲突处置 |
|---|---|---|---|
| W1 | ~~T0-0（G0-a 核验 ≤2h）~~ **已完成，W1 的 sim 档期空出**；可提前把 T0-7 或 T2-0 残留（`/state` claw revolute diff，≤0.5h）挪上来 | 否 | — |
| W2 | T0-7 取图验证 | 否 | — |
| W3 | T1-0 录包（0.5 天）→ T1-1 motion planning（3 天，独占） | 否 | 录包完成后 T1-3/4/5 转离线 |
| W4 | T1-2 标定（1.5 天）+ E-FRESH + E-GATE-live 60 scripted ep | 否 | 二者争 sim，按半天交替 |
| W5 | E-CAUSAL-L1 60 ep（E-VIEW 复用 W4 帧，不占 sim） | 否 | — |
| W6 | E-CAUSAL-L2 120 ep | **是** | BLK-1 已解除，不再有「整块作废」分支；**剩余风险改为吞吐**——120 ep 受 BLK-2 sim 独占限流（60–100 ep/天），须与 W7 一起排 |
| W7+ | §1.5 其余 920 ep | **是** | 同上；**ep 数仍是占位值**，须按 §1.5「实测每 ep 节点观测数」+ §4.1 单集成本重算后才锁 |

### 3.6 周交付（PI 按周当场复核）

| 周 | 交付物 | 一句话验收 |
|---|---|---|
| **W1** | ①G0-a/b/c 三行事实（**a 已交付：夹爪可动**）②`test_constraint_causality.py` 的**红 run 与绿 run 两份输出** ③`is_gripping_sth`/`current_limit` issue 编号（T0-8，低优先级，可顺延） | 「约束现在真的改变数字了」（爪子问题已结论：**能动**，不再是 W1 的悬念） |
| **W2** | ①24 格 region 扰动矩阵（前后 grasp pose 对照）②`predicates.py` 覆盖表（10 约束 × PASS/FAIL/UNKNOWN）③`constraint_ledger` 样例 JSON ④验收器离线 50 题混淆矩阵 ⑤**D0 裁决** | 环 2 断点闭合；验收工位边界已封死 |
| **W3** | ①200 帧 bundle 清单 ②motion planning 到位率表 ③`kwadapter.py` <400 行 + 单测数 ④取图重复帧率报告 | 机器人能按规划走了 |
| **W4** | ①`reach_pose_envelope.json` + 覆盖图 ②GraspNet :8115 单帧候选统计 ③非特权 vs oracle 位姿误差表 | 漏斗三个输入齐了 |
| **W5** | ①**funnel 首表**（候选→L1→L2→L3，含 L3 否决明细）②counterfactual ≥1 组 ③verifier 接真图一致率 ④**D1 裁决** | **消融 B 数据到手，且全程没碰夹爪** |
| **W6+** | 单管抓取 + pose-in-hand + 20-seed（BLK-1 已解除，**无 P2′ 分支**） | 按 D2 结算 |

### 3.7 三个决策点

**D0 · P0 收口（W2 末）**——四条全中才继续：

| ID | 判据 | 不中时的转向 |
|---|---|---|
| CC-1 | 反事实测试红→绿；`\|z(upper_body)−z(bottom)\| ≥ 0.5 × 物体高` | 主张从**「约束筛候选」收缩到「约束判成败」**（约束只进 gate 不进 solve）。**重大主张收缩，必须写进 `docs/DECISIONS.md` 并当面通知 PI，不许悄悄降级** |
| CC-2 | 24 格 ≥22/24；`regions.py` 任务名命中 0 | 同 CC-1 |
| CC-3 | ≥8/10 约束有谓词；五处 fail-open 归零；`unchecked` 归零 | 只卡在 `carry`/`order`/`clearance` 不算失败（天然需跨阶段状态，按 R3 标注「本 runtime 不可检查」并在论文写明）。**但 `region_grasp` 必须可检查**——它是 A5 与机制 3 的唯一载体 |
| CC-4 | `constraint_ledger` 的 `UNKNOWN` <20%；验收器离线 acc ≥0.80 或 κ ≥0.6 | **不许上机器人。** 修判卷集/prompt **只许一轮**；仍不达标则 gate 退回「几何谓词 + 官方 probe（标 privileged-eval）」，验收模型从方法主张降为工程组件 |

**D1 · P1 收口（W5 末，视 T1-1 实耗可滑到 W6）**：

| ID | 判据 | 不中时的转向 |
|---|---|---|
| P-1 | 非特权位姿误差中位数 <15 mm，孔位 <14.9 mm | 触发 ARCHIVE §1.1 感知精度闸门：多视角/主动感知回修**只许一轮**；仍不达标则 P2 全部数字标 **oracle 上界**，可防守面收缩到「候选选择 + gate」 |
| P-2 | motion planning 到位率 ≥8/10，末点 `rot_error <10°` 单调收敛 | 回 T1-1 |
| P-3 | funnel 非平凡：L1 通过率 ∈[0.1,0.9]，L2 否决率 ∈(0,0.8)，L3 在 counterfactual 上 ≥1 次改 top-1 | L2 否决率 ≈0 或 ≈1 → 谓词阈值取法有问题，回 T0-2 改**谓词**不许改任务；L3 从不改 top-1 → PROPOSAL §2.1「下游约束反推」被证伪，机制 3 降为实现细节，三层漏斗表述改两层 |
| P-4 | verifier 与几何谓词一致率 ≥0.85 且**不系统性偏松**（FAIL 判 PASS 的数 ≤ 反向的 2 倍） | **立刻停用**退回几何谓词 + 官方 probe。这是硬边界 1 的守卫，没有商量余地 |
| P-5 | E-GATE 档 0 通过（bal acc ≥0.80，FP ≤0.10） | §1.6 降级表 |

**D2 · P2 收口（T2-1 起 +3 周；原「夹爪解锁 +3 周」的起算点随 BLK-1 解除失效）**：达标 = ≥16/20 与 ≥12/20；未达 12/20 → 按 funnel 归因回修**一层**，只许一轮。**原「BLK-1 六周内不解锁 → 转 P2′」一条删除**——P2′ 的触发条件已空（§3.3），是否为「抓不稳」另设回落条件由 PI 定。

---

## 4. 预算与止损

### 4.1 单位成本（**以 `harness/runs/*/cost.jsonl` 实测为准，禁止从 CAP 反推**）

| 单位 | 成本 | 依据 |
|---|---|---|
| 一次完整编译（31–37 次调用） | **$1.38–1.62**（≈$0.045/次） | 实测 cost.jsonl |
| 一次 `extract+enrich` 重跑 | ~$2.5 | 估算，首批后回填 |
| 一次 verifier 调用（4 图 @720p ≈4.4k img token + 1k prompt + 300 out） / corrector 调用 | ~$0.10 / ~$0.12 | 待实测 |
| **一个 L2 episode** | **待实测**（12–18 verifier + ≤10 corrector = ≤28 次，按 §2.4 计数） | **先跑 20 集实测再回填；在此之前 L2 的 ep 数与总预算均为占位值** |

> 原稿两份对单集成本差 2 倍（$1.6 vs ≤28 次调用），按后者 1040 ep 会当场击穿 $3000 硬顶。故本文不锁定 L2 总预算，改为「20 集实测 → 回填 → 重定 ep 数」两步走。

### 4.2 分层预算与闸门

| 层 | 预期 | 硬顶 | 止损规则 |
|---|---|---|---|
| **L0** | $60 | **$150** | E-CAUSAL-OFF value 臂 <3/3 且断链已修 → **停，不进 L1** |
| **L1** | $150 | **$300** | E-GATE 方向标签 <0.70 → **删 L5**，L2 预算砍半重规划 |
| **L2** | 待 20 集实测回填 | **$3000** | 单集 >$2.5 → 先砍 ep 数不砍臂；中点强制 interim：E-CAUSAL-L2 方向命中 ≤2/6 立即停 |
| **总** | — | **$3500 + 6 周机时** | 超顶须 PI 重新裁决，不得默默追加 |

记账：复用 `harness/util.append_cost` → `runs/<exp>_<ts>/cost.jsonl`，`HARNESS_COST_CAP` 按层设，超限时 `harness/llm.py` 已有的 raise 就是闸门；运行期另设 `HARNESS_RUNTIME_COST_CAP`。`INFRA`/`BUDGET_EXHAUSTED` 与「任务失败」在 `events.jsonl` 里必须字段级可分。

---

## 5. 环境约束备忘（v4）

**能做（已实测）**：4 源 × 5 视图 1280×720@6fps `GET /api/frame.jpg?source=X&view=Y`（三视角与深度现成，**不自建**）；`info get_xquat`/`get_qpos`（7/臂）；`info get_ee_extforce`（**接触事件唯一底层代理**）；`ctrl qpos_move / delta_move / local_delta_move / xquat_move / go_home / set_gripper`；`reasoning:motion_planning_stereo`(`q_current`,`q_goal`,`tcp_trajectory`,`q_other_arm`)（D-09 已放行，代替 raw IK）；EvalServer `GET /state`（**只进 evaluator 侧**）；任务面 `~/knowin-world-data/tasks/robodojo_v4/<task>/<task>_NNN.suite.yaml` 共 37 任务（覆盖 insert 紧公差 / stack 姿态平齐两类约束）。

| 缺陷 | 绕开 |
|---|---|
| **夹爪可动（已实测，2026-07-30 晚，5090 v4 `k1u_v4_w_claw_26w27_1d`）**——~~此处原写「夹爪根本不动」，系我方用错参数名导致的误判，已更正~~ | 唯一正确形态：`ctrl:set_gripper`，**kwargs 必须是 `{"arm_id":N,"angle":A}`，`A` 取 0（张开）–100（闭合），超值被 `gripper.max_angle=100` 截断**。证据：腕相机 `left_hand/left` 在 `angle=0` 73938 B/md5 `4505170dd4`、`angle=100` 85611 B/md5 `3ef9e77851`，指垫可见开合。**`gpos=` 是错参数名：静默无效、画面零变化、pipeline 仍回 `ok=True`——不要拿 `ok=True` 当参数被接受的证据** |
| **↑ 仍成立 ①：`SUCCESS` 是假阳性** | `_wait_gripper` 拿**上一条指令值**跟目标比，MotorNode 秒回 `result=SUCCESS`。**禁止把 SUCCESS 当「已到位」证据**；只能看画面或物理量，闭合后仍按 `kwadapter.grasp_at` 的固定等待处理 |
| **↑ 仍成立 ②：无任何 info 能读夹爪开合角度** | ①方法侧只记「命令了多少」②「有没有抓住」由 §1.6 evaluator 相对位姿方差规则判定 ③方法侧需要时只能走 `get_ee_extforce` + verifier 看图 |
| **↑ 仍成立 ③：`is_gripping_sth` 恒假且返回字符串 `'False'`**（`bool('False') is True`；根因 `_apply_gripper_control` 在 `fixed` 模式下仍把 v4 已移除的 `snap.torques[arm][7]` 当 `current_limit` → 恒 0） | 禁用。**PI 已裁定本方案不依赖它**（「它只是判断抓没抓住，但我们的 gate 实际约束更大，应该用另一个专门的模型来判断」），故它不构成阻塞，只由 T0-8 低优先级报给上游。`kwadapter.py` 加 lint：`robotapi.py` 外调用即 raise；`public_release_check.py` 加规则：禁止对 pipeline info 返回值直接 `bool()` |
| **`/api/frame.jpg` 可能回逐字节相同的缓存帧** | `evidence.py` 内容 md5 + 时间戳双检 + 3 次重试；判据见 §1.4 单一线 |
| **`xquat_move` 大位姿停在 70–80%**；**raw IK 直达零成功先例** | 大位姿一律走 KSM 分段规划，不做 raw IK 直达；**单步硬上限 15°/次、20 mm/次**（与 §2.4 同一个界），可信 runner 拒绝越界并记录，不截断后照做 |
| **pose-in-hand 不可解析测量；按残差修正已被实测证伪两次** | `residual()` **保留契约签名、不动 `contract.py`**，实现侧返回 `UNSUPPORTED` 并只写进 evaluator 诊断段；corrector 只收「多视角图 + 编译期目标约束」，输出方向性小增量 |
| **相机桥 socket 独占** | 单实例串行；排队脚本加互斥锁；排班表见 §3.5 |
| **claw 自由度数目口径不一**（环境事实 15 revolute，`kwadapter.py` 注释 12）——**G0-a 未覆盖此项，仍未结清** | 转入 T2-0 残留 0.2 人日：`set_gripper(angle=0/100)` 前后逐个 diff `/state` 的 claw revolute，一次同时结清「自由度数目」与「把可动性证据从图像升级为状态量」。这是 §1.6 `region_grasp` ground truth 规则的前提 |

---

## 6. 需要 PI 拍板的决策点

> 六条不拍板就会返工（原第 2 条「BLK-1 战略处置」已随 BLK-1 于 2026-07-30 解除而删除；新增第 6 条「P2′ 触发条件重定义」）。本文只列选项与代价，不替 PI 决定。

1. **D-01「运行期不放 LLM」的处置（最高优先，阻塞整个 L4/L5）。** `docs/DECISIONS.md` 里 D-01 状态为**生效**，理由明写「摊销是本课题相对 per-episode VLM 路线的**主要可防守点**」「一旦运行期有 LLM，冻结后跨场景复用这个主张自毁」，只给 A(标定)/B(方法·冻结)/C(基线) 三工位豁免，**B 用即违反**——而本文的 verifier + corrector 都在 B 的运行期循环里。选项：(a) 正式作废 D-01，接受成本/摊销对照表从卖点变成劣势；(b) 把两工位重定义为 A 类（标定期在环、冻结后不在环）；(c) 维持 D-01，删掉模型工位回到纯残差闭环。**这不是补一条 D-18 就能了结的记账问题——它改变论文相对 ReKep/CoPa/VIA 的主张。**
2. **运行期 gate 的 `passed` 由谁计算。** (a) `gates.py` 算（本文默认，verifier 只作证据源）——A3 误杀率有确定性基准；(b) 验收模型接管、`gates.py` 整体迁 evaluator——「/state 只进 evaluator」边界最干净，但 gate 判定不可复现、A3 无基准。**两份原稿分别按两种方案写了验收判据，谁先动手另一份就失效。**
3. **gate 去特权的时间点（改动 #5）。** (a) 立刻去 + `privileged_oracle` 段并行记录——代价是重跑一遍 M1a 且 oracle 上界口径变了；(b) 先记账后去——中间这批 ep 的 L4 判定不可用于 A3。
4. **`contract.py` 的破例窗口。** P0/P1 零改动是硬纪律，T2-2 是第一个破例点。(a) 留到 T2-2 集中一次改完并重跑 5 任务 compile（约 5×$1.5）；(b) 现在就改并立即重编译。
5. **L2 的 ep 数与周数何时锁定。** 本文把 1040 ep / 4 周降级为**占位值**（依赖两个未实测假设：每 ep 平均节点观测数、单集成本）。(a) 先跑 20 集实测再锁（代价：排期延后 3–4 天）；(b) 按占位值排期并承担击穿 $3000 硬顶的风险。
6. **P2′ 的触发条件重定义（新增，因 BLK-1 于 2026-07-30 解除而出现）。** P2′「无抓取形态」的原触发条件是「BLK-1 六周内不解锁」，该条件已消失，**目前触发条件为空**——即本仓当前**没有任何成文的执行退路**。选项：(a) 正式废除 P2′，全案压在 P2 上（风险：抓取长期不稳时无预案，且 P2′ 的论文骨架要临时补写）；(b) 用「通道通但抓不稳」重定义触发条件，须定出可预注册的阈值与判定时点（建议挂在 D2，但阈值由 PI 定，本文不代拟）；(c) 维持空置，写明「不得作为既有退路引用」（§3.3 现按此写）。**在拍板前，任何人不得把 P2′ 当既存兜底引用。**
