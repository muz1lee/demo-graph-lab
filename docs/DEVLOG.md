# 开发日志

只记录最近的工程动作、可复查产物和停点。稳定设计写进 README/API，后续工作写进 TODO/MILESTONES。

## 2026-08-07：三组消融改为共享动作骨架

- 5090 `20260807_backchain_v6` 的首个独立生成三元组给出了一条反例：`vanilla_000` 与 `local_000` 合法且 action ops 逐项相同，`backchain_000` 虽正确复制全部 selection refs，却把 stage 1/3/5 的 `actions` 写成空列表，被 validator 拒绝。这个结果不能解释成下游约束无效；它证明原实验把「selection 方法」与「三次独立 StageProgram 生成的动作随机性」混在了一起。
- `dgl cap-ablate` 因此改成每个 repeat 只调用一次 backend，生成一份共享 primitive sequence + hole wiring；可信代码从它确定性派生 `vanilla / local / backchain` 的 selection block，再走原有 validator、compiler、静态检查和两条 dry-run。三组现在共享同一动作骨架，`summary.json` 增加 `generation_design=one_shared_program_per_repeat`、`generations` 与每条 arm 的 `source_generation`。
- 这不是 repair：共享骨架本身动作非法时三组一起失败，不让模型重写、不补动作；确定性派生只改变本来就由 graph 和实验 mode 唯一决定的 `current_constraints / downstream_constraints`。模型调用数从每轮 3 次降为 1 次，也移除了无研究价值的抄写噪声。

本地 targeted 测试已覆盖「每轮只调用一次」「三组 action ops 相同、selection code 不同」和「共享调用失败时三组如实失败」；本地与 5090 全量回归均为 `703 passed`，两个 CLI help smoke test 与 `git diff --check` 通过。代码提交 `21aed36` 已推送 Gitea，并通过 bundle 快进同步到 5090 的干净 checkout；原 dirty checkout 未动。

5090 v7 位于 `/home/knowin-sim/dgl-cap-experiments/20260807_shared_v7`，共 5 个 repeat，仍在 tmux `dgl_cap_shared_20260807_v7` 运行。`shared_000` 因 provider 连续 5 次响应 JSON 解码失败而让三组共同失败，未进入 StageProgram validator；`shared_001` 在第 2 次 provider attempt 得到合法共享骨架，三组 action ops 逐项相同：`vanilla` 为 3 `begin_candidates` / 0 `rank_by` / 0 `require_future` / 3 `choose`，`local` 为 3 / 5 / 0 / 3，`backchain` 为 3 / 5 / 13 / 3。三组均有 3 个 `grasp_at`、没有 grasp pose 走普通 `solve`，validator、静态检查及 normal/retry dry-run 全过。这只证明下游约束已经进入同一 CaP 骨架的 selection dataflow；真实 compatibility 尚未接入，不能据此声称长程任务成功率提升。v6 原始诊断产物继续保留。

## 2026-08-06：研究主线纠偏为 constraint-guided CaP

- 核心因果路径改为：demo graph 提供当前与下游约束，backend 生成带 `selection` 的 StageProgram，compiler 把它降成显式的 `begin_candidates / rank_by / require_future / choose`，runtime 只执行这段候选数据流。新 CaP program 不再沿用 `begin_stage()` 里的旧隐藏选择；legacy program 仍保留原行为。
- 下游回传规则保持最小：只取后续阶段中 manipulated object 相同、且参数确实提到该对象的非 derived constraint；constraint ref 是可读的 `s<stage>:c<offset>:<name>`，没有 SHA 或额外协议。
- 新增配对实验 `dgl cap-ablate`：`vanilla` 不看约束、`local` 只写当前 constraint、`backchain` 再写下游 constraint。实验固定 graph、模型和 primitive contract，产出每次 StageProgram、policy 与 `summary.json`。
- synthetic 反例已验证：局部排序最高但 `axis_parallel=FAIL` 的抓取会被 backchain 淘汰，选择另一个可插入候选。该结果只证明算法接线，不是实际机器人效果。
- 首轮 5090 有效样本反向审计出 fixture 语义错误：stage 4 实际重新抓 `tube_left`，但 `tube_left_grasp_pose` 被标成 `motion_derived`，导致生成 policy 走普通 `solve() → grasp_at`，绕过 selection。fixture 已改为 `grasp_candidate`；显式 CaP 模式的 validator 现在也会拒绝任何从非候选 pose 发起的 `grasp_at`，防止同类假绿。reviewed fixture 目前有 stage 0/2/4 三个 grasp stage，全部带 local/backchain context。
- 本地全量回归 `636 passed, 15 skipped`；CLI help 与 Python compile 通过。5090 旧 run `runs/insert_tubes/20260804_122155/graph.json` 的 hole 没有 resolver，不能直接做三组消融；隔夜代码生成实验使用修正后的 `tests/fixtures/graphs/insert_tubes.graph.json`。

当前停点：真实 `future_constraints` 还没有由几何/规划器计算，只有 synthetic fixture；下一步先跑代码生成合法率，再把真实 compatibility 接进同一个 API，不能把 fixture 标签当方法成功率。

## 2026-08-06：补上 `reorient_held_axis` 的两个缺口——compile prompt 闭集改从代码渲染 + planning-only 硬停

上一条留下的两个缺口，本轮各补一处。两处都只动接缝，不动这条原语的语义。

- **compile prompt 的原语闭集不再有第二份手写副本。** `prompts/compile_policy.md` 里那段链序串加逐条参数表被删掉，改成一句「权威闭集见下方 `## PRIMITIVE TABLE`」；`_render_primitive_table` 从 `repair.py` **上提到 `compiler.py`**（compile 与 repair 两条 prompt 现在共用同一个渲染器，repair 侧只是改成 import，渲染输出逐字节不变），compile 侧新增 `compile_prompt(graph)` 把静态正文、渲染出来的链序与参数表、`CONTRACT SOURCE`、`GRAPH JSON` 拼起来。表达不了的两条语义（`lower_until` 不接标量深度或 release/grasp 条件、`retreat` 只在 `release` 之后）保留为 hard rule 文字。prompt 现在真的依赖 `policy/program.py` 的两张表，所以 compile 调用的 `input_refs` 补上 `package:policy/program.py`（与感知段列 `package:perception/program.py` 同一口径）；
- **这是 prompt 运行资产变更，对生成结果有预期影响**（D-13 口径）：backend 在**首次编译**时第一次看得见 `reorient_held_axis` 和它两个必填 `axis_3d` 参数，也第一次看到把它排在 `lift` 与 `transport` 之间的链序位。所以这次改动**会改变模型的候选动作集**——此前只有走修复回路才可能用到这条原语，现在首编译就可能提案它。已发布产物一行没动，本轮**没有调用 backend**，因此这条预期影响目前是推断，尚无任何编译样本佐证；
- **`PlanningOnlyRuntime` 补 `reorient_held_axis` 的 `ExecutionDisabled`**：此前调到它抛 `AttributeError`——仍然 fail-closed，但错误类型不是契约里那个显式规划期停机。顺手把那条测试从手写清单改成**对闭集全覆盖**（`{method.__name__} == set(PRIMITIVES)`），下一条新原语再漏就直接红，不用等有人想起来；
- 测试 `693 passed`（基线 692 + `tests/test_stage_program.py` 一条 compile prompt 渲染断言：九条原语全在、链序串按 `PRIMITIVES` 渲染、`reorient_held_axis` 两个 `axis_3d` 参数在表里，且 `compile_policy.md` 源文件里 grep 不到 `lift/transport/align/reorient_held_axis`），两个 CLI `--help` 通过。

当前停点：`docs/TODO.md` 里那条「两个缺口」待办已删除。**明确没做**：这条原语的 gate 谓词 `held_axis_parallel`、谓词词表里可查的「持有」谓词、抓取点零平移补偿——三项仍然欠着，验收口径不变（本轮仍只有第 2 档离线单测，没有 episode、没有成功率）。repair prompt 的正文一个字没改。

## 2026-08-06：第一条模型提出、人类评审、修订后 admit 的原语 `reorient_held_axis`

- **这一条的分量在流程，不在功能。** 项目此前所有原语都是人写的，backend 模型只在既定闭集里选序列和接线。这次是**模型自己提出一条新原语的契约**（今天的受控提案实验，零泄题条件下一次产出，提案证据在 5090 `evidence/reorient_proposal/`），人类评审后**修订三处再 admit**。口径要说准：**admit 的是契约与实现，不是任何执行效果**——本轮只有第 2 档（离线单测），没有跑过仿真、没有 episode、没有成功率；
- **模型契约原文**：`reorient_held_axis(obj, object_axis, target_direction)`，把已被持有物体的长轴重新定向到目标方向；两个轴句柄解出刚体旋转（叉积定轴、点积定角），腕部走完该旋转并保持夹持，不平移抓取点；后置条件 `object_axis ∥ target_direction`；拒绝路径写了一条：未持有则拒；
- **评审修订三处**（都进了实现 docstring 和 `docs/API.md`）：① **补拒绝路径「旋转不可达」**——模型只写了未持有一条，但 ep2/ep3 实测腕姿残差有 18° 量级，执行中必须能停：连续 `SERVO_PATIENCE` 轮剩余角无进展即停、记 `no_rotation_progress`，不硬转；② **补「轴句柄缺失/退化」**——缺失或零向量记 `unsupported_param` 后拒，两轴近平行时旋转是恒等、直接记 `already_aligned` 成功且一条指令都不发；③ **把「未持有」的判据钉死成非特权证据**——夹爪角在「夹住带」内 **且** 末端外力达 `lift` 的承重阈 `LIFT_LOAD_FORCE_N`，`is_gripping` 只当停转信号（它读到 False 只说明此刻没在顶着走）、只进账本不参与判定，两个信号任一读不到即拒。另外**明确不采纳**一处：模型漏提「抓取朝向影响可达性」，评审决定不把它做成硬前置——policy 可以先用 `grasp_at(axis=)` 优化抓取朝向，够不着的情形由拒绝路径兜底。`target_direction` 在插入任务里天然由 rack 孔轴（`part_axis` + hole anchor）求解，模型选 `axis_3d` 这个洞类型经核对是合理的；
- **一个模型没提、实现里必须自己长出来的判断**：长轴是**无向的直线**不是射线，所以 `dot < 0` 时先把目标方向取反走短程；否则为了对齐同一条线要白转 180°，还多担一次可达性风险。账本里记 `flipped_target`；
- **不新增经验常数。** 角度判据全部复用 `SERVO_ROT_TOL`（执行器分辨不出更小的角差，另立更严的阈值只会造出永不收敛的循环），迭代上限取 `LIFT_MAX_ITERS`，无进展判据取 `SERVO_PROGRESS_EPS_DEG / SERVO_PATIENCE`，力阈取 `LIFT_LOAD_FORCE_N`。唯一新提的常量是 `GRIP_ANGLE_TOL_DEG = 2.0`，它不是新数——原本就写死在 `_wait_grip` 的默认参数里，这次提成常量给「夹住带」两端共用，`_wait_grip` 改为引用它，行为不变；
- **闭环而不是发够步数**，与 `lift` 同一个理由（上游控制器单条指令只交付约 74%，开环必然欠冲）。每轮回读 `get_xquat` 算剩余角，按 `SERVO_STEP_DEG` 限幅 slerp 一步；每步的 `target_xyz` 就是当轮回读到的位置——既不下发平移，也顺带纠掉旋转带来的漂移；
- **测试 22 条**：`tests/test_reorient_held_axis.py` 19 条（正常收敛并**独立复核**长轴与目标方向真的平行了、零平移、无向轴走短程、四种轴句柄退化、三种未持有、两种证据读不到、`is_gripping` 单独不能放行也不能否决、夹住带边界、无进展停、部分交付走满预算记 `budget`、已平行零指令、两条非特权纪律），`tests/test_stage_program.py` 3 条（链序位在 `lift` 与 `transport` 之间、两个轴参数必填、只收 `axis_3d`）。链序合法性与参数反射走的都是既有机制：`ARGUMENT_SPECS` 的参数名与必填性由 `RuntimeAPI` 签名反射校验，repair prompt 的原语闭集由 `_render_primitive_table` 从代码渲染（补了一条断言钉住新原语确实被渲染进去）；
- **反向验证 6 项**：去掉持物硬前置 → 7 条转红；去掉无向轴取反 → 1 条；去掉无进展停止 → 1 条；去掉「已平行不发指令」短路 → 2 条；链序位挪到 `transport` 之后 → 2 条；`object_axis` 放开收 `pose_se3` → 1 条。每项的红点都精确落在对应判据上，不是别处坏了；验证后已复原；
- 本地 `667 passed`（基线 645 + 22），两个 CLI `--help` 通过；本轮没有调用 backend、仿真、planner 或 control。

当前停点：只动了 `policy/api.py`、`policy/program.py`、`policy/fake_runtime.py`、`execution/oracle_runtime.py`、测试与 docs。**明确没做、且会让这条原语现在还用不起来的两个缺口**：① **`prompts/compile_policy.md` 里那份手写的原语闭集没同步**——repair prompt 是从代码渲染的（新原语自动在），compile prompt 却留着第二份手写副本，所以 backend 在**首次编译**时根本看不到 `reorient_held_axis`，只有走修复回路才可能用到它。这是本轮范围外（改的是 prompt 运行资产），但它是这条原语真正被用起来的前提；② **`PlanningOnlyRuntime` 没补对应的 `ExecutionDisabled` 硬停**，调到它会抛 `AttributeError` 而不是契约里那个显式硬停——仍然是 fail-closed，但错误类型是错的，且违反 `AGENTS.md`「新增高层动作要同时更新 planning-only 硬停」。两条都已写进 `docs/TODO.md`。另外**明确没做**：gate 谓词 `held_axis_parallel`（本轮不做，`evaluation/predicates.py` 另有人在改）——后置条件目前只有 runtime 自己在 `reorient_done` 里记腕姿残差，**属于「自己验自己」，不能当验收**；谓词词表里也缺一个可查的「持有」谓词，前置条件在 gate 侧同样问不出来；以及抓取点零平移的补偿（现在只锁 EEF 原点，爪尖随腕部画弧，这个近似已如实记在 docstring 里）。
## 2026-08-06：第三集 episode 的两个 bug——长轴索引空间搞混（上一轮的修复本身是错的）+ gate ctx 通道字段名不对接

两个 bug 都由 **8/6 ep3 实测**定位。第一个尤其值得记：它推翻的不是老实现，而是**上一轮那次"真长轴"修复**，而当时的 parity 测试全绿。

- **Bug 1：把「世界 AABB 边序号」当成「局部轴序号」。** `binding._long_axis` 与 `predicates._long_axis_world` 两侧都写着 `order = sorted(range(3), key=lambda i: extents[i], reverse=True)`（`extents` 是**世界** AABB 边长），然后把 `order[0]` 直接喂给 `_local_axis_in_world(quat, index)`——而这个函数要的是**局部**轴序号。两个索引空间只有在姿态轴对齐时才碰巧相等，姿态一斜就完全对不上。
- **实测铁证**：三根**同资产、同姿态、只差 yaw** 平躺的管子，谓词给出 **FAIL(86.0°) / PASS(8.7°，假) / UNKNOWN** 三种答案；正确答案（足迹拟合参考法）是三根都是**局部轴 1**、都约 **87.8°**、都该 **FAIL**。次/主比闸（0.8）本该兜住这类失真，但它判的也是**世界**跨度比——同一根管子换个 yaw 比值就变，tube1 比值 **0.648** 照样过闸。动作侧同错 → `_grasp_quat` 拿到的"长轴"与真管轴几乎正交 → 抓取 yaw 与管轴**平行**而非正交 → 够到了仍**夹空，把管子推走 54 mm**。
- **修法（通用，无物体先验）**：世界 AABB 跨度满足 `S_j = Σ_k |R[j][k]|·e_k`（`e` = 局部三边长，`R` = quat 的旋转矩阵）。这是一个 3×3 线性系统，用**列主元高斯消元**解 `|R|·e = S` 把 `e` 反求出来（纯 Python，不引 numpy）。局部长轴序号 = `argmax(e)`，世界向量 = `R` 的对应列；歧义闸挪到**正确的量**上——判 `e` 的次/主比（阈值 0.8 不变，语义同 `fit_principal_axis`）；`|R|` 奇异（如绕竖直轴**恰 45°**：`|R|` 前两行相同，世界 AABB 对那两条局部边长完全无信息）或解出负边长（这组 AABB 与 quat 自相矛盾）→ 新 reason `axis_extents_unrecoverable`，拒绝，不猜。
- **两侧收敛成一份实现**：新增 `binding.long_axis_world(ent) → (vec, length, reason)` 作为唯一口径，`binding._long_axis` 转 `UnsolvedHole`、`predicates._long_axis_world` 转三值 reason，都是薄封装；`predicates._AXIS_DOMINANCE_MAX_RATIO` 改成直接引 binding 的常量，`predicates._local_axis_in_world` 这份副本删掉。`solve_axis_3d` 的 `axis_source` 由 `aabb_longest_edge` 改为 `local_extents_from_aabb`（旧名字描述的正是那个错做法）。
- **⚠️ parity 教训（本轮最该记住的一条）**：上一版在 `tests/test_predicates.py` 里做的是两侧**逐值 parity**——它**全绿**，而两侧**同时是错的**。parity 只能保证「两边一致」，永远保证不了「两边正确」；把两份同构实现互相对照当作唯一护栏，等于把同一个 bug 稳稳钉死在两边，还给了一份虚假的安全感。所以本轮测试一律改成**已知答案**：先定死局部边长与姿态，用 `S = |R|·e` **正向**算世界 AABB 造实体（这条正向公式与被测实现不共享任何代码路径），再要求实现把 `e` 解回来。
- **测试新增/修订（`tests/test_long_axis_band.py` +21、`tests/test_predicates.py` 重写 parity 段）**：① 6 种姿态（单位、绕长轴自转 ±4.3°、立起 90°、yaw 25°、多轴复合）下局部边长**逐分量**解回真值、局部长轴序号恒为 0、世界向量与真长轴同线；② **三管同答案判别测试**——三个不同 yaw 平躺姿态，局部长度一致、离竖直角一致（差 < 1°、均为 87.8°）、`axis_vertical` 一致 FAIL 且 margin 一致；③ **反向验证对照组**：同样三根管子换回"世界边序号当局部轴序号"读法，结果散成 **87.8° / UNKNOWN(次-主闸) / 2.2°(假 PASS)** 三个答案——正是 ep3 实测的那个签名；④ 立管 → `axis_vertical` PASS；近立方 → UNKNOWN(`axis_ambiguous_extents`)；恰 45° yaw → UNKNOWN(`axis_extents_unrecoverable`)；AABB 与 quat 不自洽（解出负边长）→ 同上；退化四元数 → `axis_unobserved`；⑤ 判别力自证：断言斜姿态下世界跨度确实 ≠ 局部边长，否则上面几条测不出东西。
- **被修订的旧测试（都属于「靠错实现或不可能的快照碰巧通过」）**：`test_long_axis_parity_with_binding`（6 参数）整条换成 `test_long_axis_recovers_the_known_local_axis`——旧版断言 `length == max(世界 extents)`，在自洽构造下这条**本来就不成立**（斜姿态时世界跨度 > 局部边长），现改为断言 `max(局部 extents)` 并加断"世界向量与已知局部轴同线"；`test_axis_vertical_fail` / `test_axis_vertical_lying_tube_is_not_a_false_pass` / `test_axis_parallel_lying_pair_is_not_a_false_pass` 三条把**斜姿态**的实体构造从 `axis_ent`（把局部边长直接当世界 AABB 写进快照 = 物理上不可能的实体）换成新的 `axis_ent_from_local`；`test_lying_cylinder_long_axis_is_world_x` 跟着改 `axis_source` 断言。轴对齐姿态（单位四元数、180°、90°）下两种构造等价，那批用例原样保留。
- **Bug 2：gate ctx 通道两端字段名根本没对上（一行级）。** runtime 记的是 `{"op":"grasp_point","xyz":[...]}` / `{"op":"approach_dir","dir":[...]}`（`oracle_runtime._log` 与 `policy.fake_runtime._log` 都是 `{"op": 名字, **载荷}` 这一套），而 `runner._stage_ctx` 做的是 `record.get("grasp_point")`——**名字在 `op` 里、向量在 `xyz`/`dir` 里**，那个同名字段在仓里**没有任何生产者写过**，于是 ctx 恒空，`region_grasp` 与 `approach_direction` 两条谓词从 ep1 到 ep3 三集**永远 UNKNOWN**。修消费端（`_CTX_VECTOR_KEYS` 元组 → `_CTX_VECTOR_OPS = {"grasp_point":"xyz","approach_dir":"dir"}` 映射，按 `op` 匹配后取对应载荷字段），**生产端一行没动**。
- **旧形状不做兼容**（读代码后的判断，非默认）：`record.get("grasp_point")` 这个同名字段形状是消费端和它的测试在同一次提交（`66a3dc0`）里一起发明的，从来没有生产者写过，它不是"曾经的约定"而是一份自证的虚构。留兼容只会把这个虚构固化下来，所以直接把 `tests/test_runner.py` 里 5 处伪造记录改成**真实记录形状**。
- **Bug 2 测试**：`_stage_ctx` 直接喂真实记录形状 → 两个键都取到；反向验证按 `record.get("grasp_point")` 读真实记录 → 两个键都是 `None`；**ep3 实测值回归**：`approach_dir=[-0.046,-0.016,-0.999]` 经通道喂进 `pred_approach_direction(top_down)` → **PASS，夹角 2.8°**；生产端/消费端同源对账（用 `inspect.getsource` 断言 `_log_grasp_evidence` 里记的 op 名与载荷字段名就是 runner 读的那两组，任一侧改名即红）。
- **反向验证**：Bug 1 —— 换回"世界边序号当局部轴序号"，三管同答案测试转红（对照组用例本身就是这条反向验证，常驻仓里）；Bug 2 —— 把消费端按回同名字段读法，`tests/test_runner.py` **5 条转红**，验证后已复原。
- 本地 `670 passed`（基线 645 + 25），两个 CLI `--help` 通过；全部离线，本轮没有调 Qwen、SAM3、camera、GraspNet、planner，也没有跑 simulator。

当前停点：只动了 `selection/binding.py`、`evaluation/predicates.py`、`execution/runner.py` 与对应测试、本文件。**明确没做**：`effect`「推倒也算数」判据——已确认现状未变，`gates.evaluate` 的 effect 判据仍只看 manipulated 实体的**位移模长** ≥ `MIN_DISPLACEMENT_M`，不区分"被搬运"与"被夹空推倒"，ep3 那次夹空推走 54 mm 在这条判据下仍会记 `effect_status=PASS`；这条本轮按范围要求只确认、不改。另**明确没做**：重跑 ep3 验收（两个修复目前只被离线单测钉住，`ep3` 是它们的**动机证据**不是验收证据）、把长轴反求推广到非长方体近似（`|R|·e = S` 这条关系对长方体包围盒精确、对圆柱是包围盒意义上的精确，对不规则网格只是近似）、给 `axis_extents_unrecoverable` 这类拒绝加上游重试或换视角策略。

## 2026-08-06：跨程序身份守卫被真实数据击穿——判据从「逐元素相等」升级到 IoU ≥ 0.90

- **失守案例（完整记录）**：08-05 那条跨程序身份守卫（同一次 observation 内两个 `object_id` 不同的程序命中同一个框 → 双双 `UNKNOWN`）今天首次上真实数据就被绕过。Qwen 给 `tube_right` 的框是 `[935, 279, 1039, 349]`，给 `tube_third` 的框是 `[935, 279, 1039, 348]`——**只差 1 个像素**（下沿 349 vs 348），IoU **0.9857**，overlay 目视与两条链各自解出的主轴几乎相同，三份证据一致指向**同一根物理管子**。守卫按逐元素精确相等比较，`349 != 348`，于是**静默放行**，两个洞都带着 `PASS` 进了 `program_results.json`。最后是**人工 identity-accept 这道闸门把它挡下来**的——不是守卫。教训一句话：模型分不清两个查询时交出的是**近重复框**，不是重复框，精确相等这个判据从一开始就打不中真实的失效模式；
- **修法**：判据升级为「精确相等 **或** `IoU >= IDENTITY_COLLISION_IOU = 0.90`」，命中后的处置一个字没改（双双 `UNKNOWN`、`value=null`、`reason=grounding_identity_collision`、`failed_step=localize`、`collides_with` 互相点名）。阈值不是拍的，两侧都有出处写在常量注释里：下界是今天这个实测案例（IoU 0.9857 实为同一物体，必须被抓住），上界是几何常识（两个不同物体的框即使紧邻，重叠也远达不到 0.9），0.90 卡在这两簇之间。判定同时从「按框分组」改成**逐对比较**——IoU 不传递，分组这个形状在新判据下已经没有定义；对同一批数据两者只在「同 `object_id` 的两个程序同处一个混合组」这一角上有差别（此时它们不再互相点名，但仍各自因为撞上第三方而降级），降级结果不变；
- **判定依据落盘**：envelope 与 program 摘要新增 `collision_basis`，形如 `{"p2_2": {"match": "iou", "iou": 0.9857...}}`，`match` 只有 `exact`/`iou` 两种，没撞的程序是 `{}`（key 集合不随判定结果变化，与 `collides_with` 同规）。这样审计时不必拿两个框回头重算一遍 IoU——判定和它的依据同处一份文件，与 08-05 把 `bbox_pixel` 记进摘要是同一条理由；
- **测试新增 5 条**：① 今天实测数字的端到端回归（1 像素之差 → 双双 `UNKNOWN`，`collision_basis` 记 `match=iou`、IoU 四位小数 0.9857）；②③ 阈值两侧各一（74/81 = 0.9136 判降级、72/81 = 0.8889 照发）；④ IoU 0.5 的两框（真不同物体）不误伤；⑤ 同一个 rack 的两个 hole 各被一个程序问一次、框 IoU 恰好 0.90 压在阈值上，因 `object_id` 相同仍全 `PASS`。精确相等这条老路径不回归，由 08-05 那条实测回归用例继续钉着，并补测它的 `collision_basis` 记的是 `match=exact`、`iou=1.0`；
- **测试侧顺带修了假 Qwen 的一处失真**：它给不同 anchor 的默认框此前是 `[i%4, 0, W-i//4, H]`，在 10×8 画幅上相邻两个的 IoU 正好 **0.90**——旧判据下无害，新判据下会让「不同 anchor 拿到不同框」这个前提假性同框，把一批无关用例染红。改成 6 个两两 IoU 最高只 0.8 的框（x 三档 × y 两档，索引 0 仍是整幅画面，自定义 mask 的用例靠它稳住），并在超过 6 个不同查询时直接 assert 失败，而不是悄悄发出重复框。这不是为了让测试变绿：**不同物体的框应当明显不同**，本来就是这个 fake 该表达的事实；
- **反向验证**：把判据按回精确相等（只留 `box == peer_box` 分支）后，①②两条转红（`PASS != UNKNOWN`），其余 22 条全绿——分歧精确落在新判据上，不是别处坏了。验证后已复原；
- 本地 `645 passed`（基线 640 + 5），两个 CLI `--help` 通过；本轮没有调用 Qwen、SAM3、camera、GraspNet、simulator、planner 或 control。

当前停点：只动了 `execution/program_record.py` 与它的测试、`docs/API.md`、本文件。明确没做：动 `object_record.py` 的单 anchor 链（一次只有一个 anchor，没有跨程序冲突语义）、把阈值做成配置项或 CLI 参数（一个从证据长出来的常量，改它应当是一次带证据的裁决而不是调参）、把判定粒度放宽到 `(object_id, part, instance)` 全 anchor（rack 的两个 hole 命中近重复框仍**不算**冲突，这条 08-05 挂起的裁决继续挂着）、动 registry 与 prompts（「anchor 的 distinguisher 必须单帧可判」这个上游根因仍是待办研究项）。**这一轮只是把守卫补到能拦住今天这个案例，不等于它拦得住下一个**——今天真正救场的是人工 identity-accept 那道闸门，那道闸门不能因为守卫变强就撤。

## 2026-08-06：第二集 episode 的四项运行时修复 + gate ctx 两条记录

前四项全部来自 **8/6 ep2 与任务 B 的真实 episode**，证据在 5090 的 `~/dgl-stack/evidence/ep2/` 与 `~/dgl-stack/evidence/taskb/`；第五项是给已合入的 gate 侧修复补 runtime 记录端。口径是**第 3 档「privileged Oracle 调试」**——只动了特权调试路径 `execution/oracle_runtime.py`，不是方法路径，也不构成任何阶段或任务成功率。

- **靶子 1（最高价值）：臂来自命令行默认值，不是来自目标在哪一侧。** ep2 的 2×2 选臂矩阵实测完美对角：arm0（左）→ 左管（y=+0.258）xy 残差 **4.9 mm** ✓、arm1（右）→ 右管（y=−0.365）**9.0 mm** ✓、跨身体两组 **25–69 mm** ✗。而 episode 默认 `--arm 1`、目标在左，**31 mm 够不着**却继续在空中闭爪，最后由 `lift` 以 `attached=empty` 结案——「够不到」和「夹了滑掉」两种物理事件被压成同一个 reason，归因无从下手。改法两条：① `begin_stage` 里按解析后目标实体（优先 `manipulated`，否则 `target`）的 y 符号选臂（+y 左 → arm0，−y 右 → arm1），`|y| < ARM_SELECT_DEADZONE_Y_M = 0.05` 时左右分不开就保持当前臂、解析不到也保持当前臂并记原因（都不 fail-open 成「随便挑一只」），换臂后用现有 `_park_idle_arm` 把刚空出来的那条臂归位（按新 `arm_id` 取闲臂，所以必须在赋值之后调）；**持物期间不换臂**——闭过爪即 `_holding=True`，到 `release` 才恢复重选（不看夹持回读：夹没夹到由 `lift` 的承重证据判，而不论夹没夹到，换臂都是危险动作）。② `grasp_at` 在翻转兜底（`_retry_flipped_branch`）之后再测一次 xy 残差，超过 `UNREACHABLE_XY_MM = 15.0`（取两簇实测之间：够得着 ≤9.0 mm / 够不着 ≥25 mm）就记 `grasp_failed(reason=unreachable_target)` 并**直接返回不闭爪**，与空夹语义分离；
- **靶子 2：张/闭爪必须是两个常数。** 任务 B 实测 `align` 总错配 **−27.2 mm**，其中 **−21.9 mm** 正是张/闭爪指尖差（张 −3.572 / 闭 +18.350）；沿用张爪值时管底与 rack 顶只剩 **+1.5 mm** 余量，名义应有 **+23.4 mm**。`CLAW_TIP_DZ` 拆成 `CLAW_TIP_DZ_OPEN = -0.0035`（`grasp_at` 下探定位 / `approach` 预抓取偏置——这两处爪子都张着）与 `CLAW_TIP_DZ_CLOSED = 0.01835`（`transport` / `align`——都是夹着物体移动）。全仓四个消费点逐一核对归类完毕，`CLAW_TIP_DZ` 这个名字已不存在（避免留一个语义含糊的旧名被误用）。这正是上一条 DEVLOG 里挂着的「已知接缝」的结案；
- **靶子 3：`lower_until` 步长 20 mm → 5 mm。** 任务 B 实测：间隙只剩 1.5 mm 时，单步 20 mm 下探的**第 1 步**就打出 **656 N**（接触判据阈 `CONTACT_FORCE_N = 20 N`，冲击超 **30 倍**）——一步就撞穿了，判据再灵也来不及在步内停。只改步长，**其余判据一行未动**；
- **靶子 4：MP 熔断。** 我方隔离总线上没有 `motion_planning_stereo` 后端，每次规划要白等 400+20 s，ep2 一集烧掉 **200 s（29% wall）**，100% 失败后全部走 degraded 伺服——重试没有任何信息增量，只在反复买同一个已知答案。改法：同一 episode 内 `plan_joint_path` 首次 **HTTP 400** 之后熔断，后续 `_move` 直接走 degraded 并记 `mp_fallback(reason=mp_disabled_after_400)`，熔断事件本身只记一次 `mp_disabled_after_400`。**只对 400 熔断**（后端不存在/拒绝受理，再试也是同一结果）；超时、传输中断、返回结构异常等可能是瞬时的，保留原来逐次 fallback 的语义。判别串对着 urllib `HTTPError.__str__` 的真实格式 `"HTTP Error 400"`，不是搜 `"400"`（坐标里也会出现 400）；
- **靶子 5：gate ctx 的两条记录端。** `region_grasp` 与 `approach_direction` 两条谓词早就有完整几何实现，缺的只是 ctx 输入（缺输入 → UNKNOWN）。runtime 侧在 `grasp_at` 的「定位完成、还没闭爪」时刻记两条：`grasp_point`（世界系**爪尖**点，= EEF 回读 z 减张爪指尖偏移，与 `eef = tip + CLAW_TIP_DZ_OPEN` 互逆——`pred_region_grasp` 拿 z 比物体 AABB 竖直跨度，管子直径才 33.6 mm，3.5 mm 的 EEF/爪尖差就是归一化坐标 s 的 **10%**）与 `approach_dir`（**实测达成**方向 = 下探段起止两次 `get_xquat` 位置差归一化）。两条**都取回读实得量而非命令值**——已定裁决：gate 若拿到自己这条链选定的命令方向就是在验证自己选的值，恒 PASS 没牙齿。够不到而中止时**不记**这两条（没发生的抓取不该给 gate 一个抓取点）；位移小到测不出方向时如实记 `dir=None`（谓词据此 UNKNOWN）。**与任务书的一处偏离（需确认）**：任务书写的是「`approach` 阶段 EEF 位姿差」，实现取的是**下探段**（预抓取位 → 抓取位）。理由是 `approach` 原语走的是到「站位点」的转移，而站位点恰恰是沿接近方向**反向**偏置出来的，那段位移方向只取决于上一阶段把手臂停在哪儿、与「从哪个方向接近物体」无关；而 `regions.cone_angle_deg` 量的正是方向相对竖直向下的**倾角**，用转移段会把一次正常的竖直抓取算成 ~90° 倾角、把 `top_down` 判成 FAIL。方法（两次 `get_xquat` 位置差归一化）、键名与形状均按任务书，只换了取哪一段；
- 测试：新增 `tests/test_ep2_runtime_fixes.py`（37 条）——选臂 11 条（左/右各选对臂、换臂才归位且闲臂是旧的那条、持物钉住、`release` 后恢复重选、死区内外行为相反、解析不到保持当前臂、无 `stage_objects` 不读实体表、只有 `target` 时用它）、不可达 6 条（独立失败记录且不闭爪、不进持物态、同侧两组实测残差照常闭爪、阈值上下两侧行为相反、阈值落在两簇之间、判不可达发生在翻转兜底**之后**）、双常数 6 条、细步 2 条、熔断 5 条（首次 400 后不再调规划且熔断只记一次、非 400 逐次重试、判别串对着真 `HTTPError`、熔断作用域是 runtime 实例不是进程全局）、gate ctx 证据 7 条（两条都在闭爪前出现、`grasp_point` 是爪尖不是 EEF 原点、`grasp_point` 跟回读不跟命令、够不到时两条都不记、竖直下探的 `approach_dir` 是 [0,0,−1] 且 `cone_angle_deg(top_down)=0°`、把预抓取位横向漂成 45° 斜线时实测方向必须暴露那 45°、位移测不出时 `dir=None`）。另在 `tests/test_motion_planning.py` 的 `_rt` 桩里补 `_mp_disabled` 初值（该 helper 用 `__new__` 绕过了真 `__init__`）。本地 `590 passed`（基线 553 + 37），两个 CLI `--help` 通过；全部离线，本轮没有调 Qwen、SAM3、camera、GraspNet、planner，也没有重跑 simulator；
- **反向验证**（改动摘掉后必须变红）：摘掉 `begin_stage` 里的选臂调用 → 选臂 11 条全红；摘掉持物闸 → 持物那条红；把不可达判据短路 → 4 条红；`CLAW_TIP_DZ_CLOSED` 按回张爪值 → 常数 2 条红，`transport`/`align` 的消费点按回张爪常数 → 接线 3 条红，`grasp_at`/`approach` 改用闭爪常数 → 另 3 条红（两组消费点各自被钉住，不是只钉了常数值）；`LOWER_STEP` 按回 0.02 → 细步那条红；摘掉熔断闸 → 熔断那条红，熔断改成对任何失败都触发 → 非 400 那条红；摘掉两条 gate ctx 记录 → 7 条里 6 条红，`approach_dir` 改记命令方向 → 2 条红，`grasp_point` 改记 EEF 原点 → 爪尖那条红，够不到时也记证据 → 「不记」那条红。

当前停点：四项都只在**离线单测**上被钉住，`evidence/{ep2,taskb}/` 是它们的**动机证据**，不是修复后的验收证据——**修完还没有重跑过 ep2**，下一步要在 5090 上跑一次，重点看选臂后的 xy 残差是否落进 5–9 mm 那一簇、`align` 的管底余量是否回到 +23 mm 量级、wall 时间是否掉掉那 200 s。**已知接缝（本轮刻意没动，需裁决）**：① `lower_until` 的 plateau 判据仍是硬编码的 `prev_z - z < 0.004`，它原先相当于步长的 **20%**，步长降到 5 mm 后变成 **80%**——按上游 74% 交付率算，5 mm 指令实得约 3.7 mm < 4 mm，**plateau 会在第 2 步就误判成「停住了」**（`stop_kind=contact` 时 plateau 不参与，不受影响；无 `stop_kind` 或 `stop_kind=plateau` 时会被打中）。本轮遵「其余判据不动」没改，但这条要么把阈值改成随步长走，要么显式裁决保留；② `LOWER_MAX_STEPS` 仍是 12，总下探行程随步长从 240 mm 降到 **60 mm**（名义间隙 23.4 mm 够用，走满预算会如实记 `reason=budget`，不会假装成功）；③ `grasp_at` 判不可达后**不闭爪直接返回**，后续 `lift` 仍会照常执行并记 `attached=empty` —— 失败类的分离目前只体现在账本里多出的 `grasp_failed` 记录上，没有把状态跨原语传给 `lift`（跨原语传状态是更大的改动，留待裁决）；④ `approach_dir` 记在 `grasp_at` 而不是 `approach`（理由见靶子 5），若图把 `approach_direction` 约束挂在**不含抓取**的单独阶段，该阶段的 gate 仍会拿不到 ctx → UNKNOWN，这要用一次真图确认阶段划分。另**明确没做**：改上游 74% 交付率的根因、把选臂推广到 `_move` 级（现在只在 stage 边界选一次）、给非 400 的规划失败加退避。
## 2026-08-06：模型看自己 episode 的失败轨迹改自己的程序（T-COR v0，第 6 个调用点）

新增 `dgl repair --run-dir <dir> --episode <episode.json>`：把一份**失败** episode 交回给提出该 program 的 backend model，让它改**自己写的那份 StageProgram**。这是 workflow 里第一条回路，也是 backend model 被允许出现的第 6 个调用点（`docs/API.md` 第 8 节）。口径是**第 1 档「静态检查或 fake dry-run」+ 第 2 档「离线 fixture 单测」**——本轮一次真实模型调用都没有发生，所有回复都是 canned，没有跑 simulator、Qwen、SAM3、GraspNet、planner。

- **改的是程序，不是判据。** 模型的输出 schema 只有两个字段：`attribution`（一句失败归因）和 `program`（完整 StageProgram）。graph、stage 名、holes、`constraints`、`acceptance` 和 gate 判据**根本不在这个 schema 里**——「改约束」不是被禁止的行为，而是结构上写不出来的东西；真塞进去，`validate_program` 的顶层未知字段检查会拒。可改集合只有两项：stage 内的动作序列、以及哪个已声明 hole/object 接进哪个 primitive 参数。原语闭集与参数表由 `repair.py::_render_primitive_table` 从 `PRIMITIVES / ARGUMENT_SPECS / RuntimeAPI` 签名渲染，prompt 里不留第二份副本；
- **发布门与 compile 逐条相同**，判据就是同一个 `compiler.report_ready`：零 program violation → 确定性重编译 → AST 静态检查 → `FakeRuntime` 正常与注入失败两条干跑。接线变了意味着可发布几何 hole 集合可能变，所以发布之后照 `dgl compile` 的规矩追加一段 `PerceptionProgram` 编译（`compile_perception` 新增 `out_dir` / `tag` 两个可选参数，产物进修复目录、记账留在原 run 目录的独立 tag）；
- **摘要而不是整份报告。** `summarize_episode` 是确定性提炼器：第一失败 stage、gate 判据结论（12 个字段，不含世界坐标与位移数值）、每 stage verdict、探针前后、调用流水尾部 `SUMMARY_TAIL_CALLS=12` 条。**墙钟字段被丢掉**（调用记录的 `t`、`wall_sec`）——同一次失败不能因为时间戳看起来像"另一次"，这条由单测钉住（把时间戳整体改掉，摘要必须逐字节相同）；
- **原产物不覆盖。** 修订版落在 `repairs/r<N>/`（`stage_program.json` + `policy.py` + 编译快照 + `compile_report.json` + `attribution.txt` + 条件 `perception_program.json`），归因只进留档不进任何被执行的产物。想执行修订版必须显式 `dgl-oracle episode --program-dir <run>/repairs/r1`；`--run-dir` 语义一行没动，不给 `--program-dir` 时行为与改动前逐字节一致。执行前那 8 道一致性门对修复目录同样全跑（`_load_artifacts` 加可选 `program_dir`：graph/validation/objects 仍只从 run 目录读，编译快照换成指定目录，所以比的是「修订版是不是对同一份示范的编译」）；
- **链式修复读的是真正失败的那份程序。** episode 报告新记一个 `program_dir` 字段，`dgl repair` 据此决定输入程序是原产物还是 `repairs/r<N>`。没有这条，第二轮就会拿着 r1 的失败轨迹去改原始程序——这是 3 次上限下的常规用法，不是边角情形。字段只接受 `.` 或 `repairs/r<N>`，越界直接拒绝；
- **记账与上限。** `repairs/repair_ledger.json` 每次尝试一条（序号、来源程序、episode 名与规范化指纹、`banner`、归因、是否发布、violations、感知段状态）。**每个 run 目录 3 次**，计的是尝试数——被拒的修订同样占一格（它花了钱、留了记录），超限拒绝并如实报错，不再调用 backend。成本与缓存走 `common/llm.py` 既有机制，tag 为 `repair_r<N>` / `repair_perception_r<N>`；
- **口径继承。** episode 目前只可能来自 `OracleRuntime`，`banner` 因此一路带进摘要与台账：由特权调试 episode 驱动的修复继承**第 3 档**，不构成任何阶段或任务成功率；
- 测试：新增 `tests/test_policy_repair.py`（21），另在 `tests/test_stage_program.py` 更新 2 处（`_load_artifacts` 新签名、episode 报告的 `program_dir`）。本地 `574 passed`（基线 553 + 21），两个 CLI `--help` 通过；全部离线；
- **反向验证**（改动摘掉后必须变红）：no-op 修订不再判违规 → 2 红；产物写回原 run 目录 → 6 红（"原产物一行不动"是被钉住的，不是靠自觉）；上限检查摘掉 → 1 红；摘要保留墙钟时间戳 → 2 红；忽略 episode 记录的程序来源 → 2 红（链式修复 + 越界拒绝）；执行侧一致性门不认修订目录 → 1 红。每条都只打红它自己那几条，定位是精确的。

当前停点：这条回路**只被离线 canned 测试钉住**——没有任何真实模型提出过修订，也没有任何修订版被执行过。它证明的是「修复走的是同一道发布门、且改不到判据」，不证明模型真能修好任何东西。下一步要在 5090 上拿 ep1 的真实失败报告跑一次 `dgl repair`，看归因与修订是否可读，再用 `--program-dir` 跑一次对照。**明确没做**：自动重跑（跑不跑修订版是显式的下一条命令）、修复效果的任何统计口径、感知程序的独立修复回路。**与方案的一处偏离**：方案里"可改感知链组合"这一条没有做成"修复调用直接产出 PerceptionProgram"，而是让修订版发布后由既有 `compile_perception` 段按新接线重编——理由是感知侧本来就有自己的闭集 prompt、validator 和干跑门，把它塞进同一次修复回复会把输出 schema 和校验面加倍，而收益只是省一次调用。**已知接缝**：`calls_tail` 里的调用记录带着 oracle 调试路径产生的数值（残差、位移量级等），模型输出侧写不出数字（validator + AST 双拦），但它的**选择**可能被特权信息影响——这正是上面那条口径继承存在的原因；另外 8/6 之前写出的 episode 报告没有 `program_dir` 字段，会被当作"原产物失败"，用旧报告做链式修复前要自己确认这一点。
## 2026-08-06：第二集 episode 的 evaluation 侧三修（gate 死锁 + 假 PASS）

动机证据来自 **8/6 ep2**（连同 ep1 的复现）：gate 有四条结构性 UNKNOWN，使任何 stage 的 `passed` 永远不可能是 `True`；同时 `axis_vertical` 对一根**根本没被碰过**的横躺管子报 PASS（`angle=4.2°`）。口径仍是**第 3 档「privileged Oracle 调试」**——改的是 `evaluation/` 与 runner 的判定接线，不是方法路径，不构成任何阶段或任务成功率。本轮**没有重跑 episode**，全部结论只到离线单测这一档。改动面严格限制在 `evaluation/{predicates,gates}.py`、`execution/runner.py` 与其测试（`oracle_runtime.py` 由并行的另一条线在改，本轮一行未动）。

- **靶子 1：gate 的 ctx 接线（纯接线，几何实现本来就完整）。** `pred_region_grasp` 要 `grasp_point`、`pred_approach_direction` 要 `approach_dir`，而 `gates.evaluate` 调 `_verify3` 时不传任何 ctx → 这两条**永远** UNKNOWN。改法：`gates.snapshot / evaluate` 增加可选 `ctx`，`_verify3` 透传给 `verify3(constraint, **ctx)`；runner 每次 attempt 从 runtime 自己的调用记录里取本阶段实际记下的抓取点与接近方向（`runner._stage_ctx`，只看本次 attempt 新增的记录、取最近一次、只接受三分量世界系向量）。runner 只做搬运：runtime 没记就是空 ctx → 两条谓词维持 UNKNOWN，fail-closed 没有放松；`verify3` 不接受这些关键字的老 runtime 退回原调用形态，逐位与现状一致。入口探针**不**传 ctx（动作还没发生），所以 `throughout` 的这两条仍是 UNKNOWN；
- **靶子 2：结构性不可查谓词不再死锁整条判定（裁决落地）。** `carry` / `order` 在 `predicates.UNCHECKABLE_IN_RUNTIME` 里，三值合取 UNKNOWN→None→`passed` 恒非 True——只要 acceptance 含一条 `carry`，任何 stage 永远过不了。这不是「严格」而是「死锁」。改法：判定时把**白名单内且本次确为 UNKNOWN**的项排除出 hold 合取，但记账完整：verdict 新增 `excluded_uncheckable_keys` 与 `n_excluded_uncheckable`，这些键**仍然**留在 `unknown_keys` 里（被豁免 ≠ 被查过），`reason` 只点名真正挡路的 UNKNOWN。豁免面用 `predicates.UNCHECKABLE_IN_RUNTIME` 单一真源钉死：其他任何 UNKNOWN（谓词异常、缺 ctx、参照实体解析不到、词表外）照旧阻塞；runtime 若真能判 `carry`，PASS/FAIL 都照原样生效（豁免只吃 UNKNOWN）；acceptance 全部被豁免时合取里没有任何证据 → 仍是 `None`，豁免不凭空造 True。护栏测试断言白名单就是 `{"carry","order"}` 且与 predicates 同一对象，往里塞名字必须先来改测试；
- **靶子 3：`axis_vertical` / `axis_parallel` 换真实长轴（binding 侧同源问题已在 bf6f4bd 修，谓词侧补上）。** 谓词读的是物体**局部 +z**，而 ep2 那根管子横躺（世界 AABB 111×85×37 mm）时局部 +z 仍近竖直 → `angle=4.2°` 的假 PASS。改法：predicates 内实现与 `selection.binding._long_axis` **同构**的 `_long_axis_world`（AABB 最长边定局部轴序号 → 经四元数变到世界系），次长/最长 > 0.8（近立方）、四元数退化、无 AABB 三种情形返回 UNKNOWN + reason，不猜轴。这是有意的小面积复制（binding 侧抛 `UnsolvedHole`、要 hole 参数，谓词侧要三值 reason），按仓内惯例配**逐值 parity 测试**：6 组姿态的长轴向量与长度、两处拒绝的 reason、判据常量本身都与 binding 对照，防两侧单边漂移；
- 测试：`test_predicates.py` +13、`test_gates_constraints.py` +11、`test_runner.py` +5；另把 4 条原先拿 `carry` 当「普通 UNKNOWN」样本的 gate 用例换成白名单外的 `region_grasp`（它们要钉的语义是「普通 UNKNOWN 阻塞」，那条语义没有变）。本地 `582 passed`（基线 553 + 29），两个 CLI `--help` 通过；全部离线，没有调用模型、相机、simulator；
- **反向验证**（改动摘掉后必须变红）：`_long_axis_world` 按回局部 +z → 轴类 11 条红，ep2 那条复现出 `PASS angle=4.2°` 的原始假阳性（长轴口径下是 `FAIL angle=90.0°`）；`_excludable` 的白名单条件去掉、豁免面放宽成「任何 UNKNOWN」→ 7 条普通-UNKNOWN 阻塞用例红；`_verify3` 收下 ctx 但不透传（= 未接线前的行为）→ 5 条 ctx 判定用例红。

当前停点：三项都只在**离线单测**上钉住，ep2 尚未重跑。**已知接缝（下一步的真正瓶颈）**：`oracle_runtime` 目前**没有**把抓取点与接近方向记进 `_log`（只有 `grasp_axis` 的四元数、`grasp_close` 的角度、`approach_cone` 的 cone 名与候选 id），所以靶子 1 在真实 oracle episode 上仍然取不到值、那两条谓词仍是 UNKNOWN——接线已经就位，缺的是 runtime 侧一行记录，属并行那条线的改动面。另外，若将来由 runtime 记录「执行前**选定**的 approach 方向」，`approach_direction` 就成了自我验证（方向本来就是按同一个 cone 排序选出来的）；要让这条 gate 有牙齿，记录的应当是**实际达成**的接近方向（EEF 位姿差），这一点必须在接 runtime 记录时定下来。

## 2026-08-06：第一集 episode 的三个实测 bug + 一个同源 gate 映射

四项全部来自 **8/6 ep1 两次稳定复现的真实 episode**，证据在 5090 的 `~/dgl-stack/evidence/ep1/`。口径是**第 3 档「privileged Oracle 调试」**——改的是 oracle/selection/evaluation 的调试与选择路径，不是方法路径，也不构成任何阶段或任务成功率。感知链（`frames` / `program_projection` / `program_record`）与 compiler/prompts 一行未动。

- **靶子 1（最严重）：对象解析塌缩。** objects.json 里三根管的 `trace_aliases` 都是 `["tube"]`，`_resolve` 的别名分支命中多个实体时取 `/state` 里第一个含 "tube" 的键，于是 `tube_left / tube_right / tube_third` **全部**解析到 `tube0_prop`，写好的空间双射 `_family_bijection` 成了死代码。改法：别名/子串/同义词三个分支只在**唯一命中**时直取，多命中降级到空间双射，双射也定不下来就抛 `UnsolvedHole(ambiguous_object_reference)`；精确匹配唯一命中仍直取；「实体表里没有」仍是 `KeyError`，两种失败语义不合并。`_family_bijection` 同时改成 fail-closed：两侧基数不等（旧实现用 `min(i, len-1)` 截断，图名多于实体时会把多出来的名字全部塌到最后一个实体）、图名空间得分并列（次序只能靠字典序＝猜）、实体 y 并列（< `BIJECTION_Y_TOL_M`，左右分不开）三种情况一律返回 `None`。`tube_third` 这种没有空间词的名字**不是猜出来的**：左右两端被空间词钉死后，它由消去法唯一确定；
- **靶子 2+3（同一个根因）：真实长轴层。** 管子横躺——AABB 的 z 跨度就是直径 33.6 mm，而 `solve_axis_3d` 拿物体**局部 +z** 当长轴；横躺资产的局部 +z 仍近竖直（偏离 4.3°），于是 ① `upper_body`（s=0.80）沿 AABB 世界 z 取点，抓取点高出赤道约 1 cm，而半径才 16.8 mm，光滑圆柱赤道以上夹必滑出；② `_grasp_quat` 的 yaw 跟着那 4.3° 倾斜的**方位角**抖，两次 attempt 的抓取四元数差 180°（离线复算实测值：±4.3° 自转下旧长轴给出的两个抓取姿态测地角正好 180°）。新增 `_long_axis`：从 AABB 三边长取最长边所在的局部轴序号，经实体四元数变换到世界系；次长/最长 > 0.8（口径同 `perception/operators.py::fit_principal_axis` 的 PCA 判据）→ 近立方/近方形，主方向不可辨，拒绝。这条比值判据同时兜住了「AABB 边长 ≠ 局部边长」的失真情形——物体绕竖直轴转 45° 时 AABB 会被撑成近方形，正好落进拒绝区。`solve_pose_se3` 的 region 带改成**沿真实长轴**取段：段中点 =（质心 xy，AABB 竖直中点）即旧实现 s=0.5 的同一点，偏移 `(s-0.5)*长度*长轴`；长轴与世界 z 的方向余弦 < 0.9 时没有可靠信号说明哪一端算 upper → 取段中点并记 `end_ambiguous=True`，**不猜**——「横躺时抓取高度落在质心高度（赤道）」是这条规则的副产品，不是单独加的特例。物体立着时最长边就是局部 +z，长轴与四个 region 的区带都与旧公式逐点一致（测试用旧公式对照，1e-12）；
- **靶子 4（与靶子 1 同源）：gate 的 manipulated 名 → 实体键映射。** 图对象名是 `tube_left`，实体字典的键是 `tube0_prop`，位移检查拿前者查后者永远查不到 → `effect_status=UNKNOWN` → 任何 effectful stage 结构性过不了。改动面只在「注入映射」这一层：`gates.evaluate` 多一个可选 `resolve_object`，`runner.run_policy` 不给就从 runtime 取它自己的 `_resolve`（`OracleRuntime` 有，`FakeRuntime` 没有）。gate 侧自己不猜名字：解析抛异常、返回 `None`、或指向位移表里没有的键，一律退回既有的 id/前缀匹配，再拿不到就 `None` → 维持 UNKNOWN（现状即 fail-closed，没有放松）。verdict 多记一个 `manipulated_entity`，让「映到了哪个实体」可审计；
- **附带（语义诚实版）：`solve_pose_se3` 对 robot_base 洞的 refusal 与 episode 的冲突。** live 契约要求几何洞 `frame=robot_base`，而该 solver 见 robot 系即返回无数值描述子，`grasp_at` 崩在 `TypeError`，episode 只能靠 shim。改法：oracle 调试路径下，若能断言 world 与 robot_base 重合（实体表里**唯一**一个名字含 "robot" 的实体位于原点、单位四元数）则给出数值并把 `ref_source` 标 `world_equals_base_asserted`（锚点来源保留在 `anchor_source`，审计信息不丢）；根实体不唯一、不在原点、拿不到实体表、以及 `ee` 系（末端在动，原点重合断言对它没有意义）一律维持拒绝，**不无条件放行**；
- 测试：新增 `tests/test_object_resolution.py`（9）、`tests/test_long_axis_band.py`（19），另在 `test_solve_dispatch.py` +5、`test_gates_constraints.py` +6、`test_runner.py` +3。本地 `553 passed`（基线 511 + 42），两个 CLI `--help` 通过；全部离线，本轮没有调用 Qwen、SAM3、camera、GraspNet、planner，也没有重跑 simulator；
- **反向验证**（改动摘掉后必须变红）：别名分支按回「取第一个」→ 解析 6 条红；`_family_bijection` 按回 `min(i,len-1)` 截断 → 4 条红；world==base 断言改成无条件放行 → 3 条拒绝用例红，按回一律拒绝 → 给数那条红；`solve_axis_3d` 按回局部 +z → 长轴 7 条红；`end_ambiguous` 恒 False → 2 条红；区带按回世界 z 公式 → 3 条红；gates 忽略注入的映射 → 4 条红；runner 不从 runtime 取 `_resolve` → 2 条红。

当前停点：四项都只在**离线单测**上被钉住，`~/dgl-stack/evidence/ep1/` 的两次复现是它们的**动机证据**，不是修复后的验收证据——**修完还没有重跑过 ep1**，下一步要在 5090 上跑一次并比对 grasp 四元数的稳定性与 effectful stage 的 `effect_status`。**明确没做**：gate 另外三条死锁（`carry` / `order` 需跨阶段状态量，在 `predicates.UNCHECKABLE_IN_RUNTIME` 里永远 UNKNOWN；`region_grasp` 缺 `grasp_point`、`approach_direction` 缺 `approach_dir`，而 `gates._verify3` 不传任何 ctx，因此这两条也永远 UNKNOWN）——本轮只确认现状，没有放松也没有修。**已知接缝**：`_world_equals_robot_base` 靠「实体键里含 robot」来找机器人根，如果 ep1 的 `/state` 根本不把机器人列进 `entities`，断言就不成立、`grasp_at` 仍需 shim——这条要用一次真实 `/state` 抽样确认；另外 `_long_axis` 把 AABB 边长当局部边长用，只在资产近轴对齐时成立，比值判据兜住的是失真的极端情形，不是把它变正确。

## 2026-08-06：光学系感知值经实测标定进 robot_base，第一次喂进 solve()

- **输入是今天那次标定战役的实测结果**，不是构造数：`camera_head_optical → robot_base` 的 `R`（det=+1，正交残差 1e-6）与 `t=[0.097078, 0.037055, 1.161351]`，基准 `q_lift=0`，验证背书是桌面法向差 0.055°、桌高经修正后残差 +0.69mm；
- 新增 `perception/frames.py`：闭集 schema `demo_graph_lab.camera_extrinsics.v1`（frame 对、OpenCV 光学系约定、`R` 或等价四元数二选一、米制 `t`、`lift_dependency`、`method`、`provenance`、`validation`），校验含 `SO(3)` 成员资格与 `det ≈ +1`——镜像矩阵会把每个轴悄悄翻过来，绝不能进 hole 值。两个变换分开写：`point_to_base` 用 `t_eff = t + axis*(q_lift - q_lift_assumed)`，`direction_to_base` **只吃 `R`**、永不加 `t`、结果重新归一。共用一个 helper 正是那个经典 bug，所以这里根本没有共用的余地；
- **升降拒绝规则**是这轮的硬约束：拿不到与该次 observation 同时刻的 `q_lift` → `q_lift_unavailable`；读数超关节限位 → `q_lift_out_of_limits`；记录声明 `correction=none` 而实际位移 >2mm → `q_lift_correction_unavailable`。三条都返回带 reason 的 `UNKNOWN` 而不是裸异常（一个洞失败不该拖垮同次观测的其它洞），**绝不发布静默偏移的位姿**——默认按标定姿态处理会让误差等于升降行程，而下游看不到任何异常。反向验证：把「缺 q_lift 就当 0」这一行改回去，`test_frames` 与 `test_program_projection` 共 3 条用例转红；
- **q_lift 入账落点选 `proprioception.json`**（`observation.robot_state.evidence_ref` 已经指向它），不新起平行结构，schema 随之 `readonly_proprioception.v1 → v2`。当前只读 proprio 通道只有 `get_qpos arm 0/1`，没有升降关节来源，因此 `capture` 如实写 `lift_position_m=null` + `lift_source=unavailable_no_lift_joint_in_readonly_proprio`；给了读数就必须同时给来源，否则 capture 直接失败。**这意味着真实记录现在所有 `point_3d` 洞都会被拒**——这是当前的真实状态，已写进 TODO 第 3 条，不是占位；
- 新增 `execution/program_projection.py`（落点选 `execution/` 而不是 `selection/`：它消费 record 目录与标定记录，属记录链的本地计算步；`selection/` 只做 typed-hole 求解与排序，不该认识 record 目录）。`planning-record --step project-base` 逐洞投影 `program_results.json` 的 envelope，写 `base_frame_values.json`；前四个键与 `selection/binding.py::_CANDIDATE_VALUE_FIELDS` 逐字对齐，`calibration_ref` 换成外参记录（变换后有效性由外参决定），内参留在 `source_calibration_ref`。上游已是 `UNKNOWN` 的洞保留自己的 reason（`grounding_identity_collision` 比「无法变换」具体）；
- **质心禁令**写成机制而不是注释：只有绑定到拟合几何中心的 resolver 能填 `point_3d`（当前只有 `part_center → fit_opening.center`），并额外要求链终点算子与 resolver 绑定一致。点云质心是「可见表面的重心」，实测比实体中心偏向相机约一个半径（共模 `x ≈ −10.7mm`、`z ≈ +12.9mm`）。v1 里 `fit_axis` 只发布 axis，所以这条路径**现在不存在**，名单的作用是让将来有人接上时必须先显式改名单；
- **identity 红线的落地**：新增 `planning-record --step identity-accept`，写独立的 `identity_acceptance.json`（`program / object_id / accepted_by / basis / accepted_at / bbox_pixel / evidence_dir`），四个字段都必须显式给。接受只能加不能减——不能接受 anchor 对不上的 `object_id`，也不能接受一个自身 `UNKNOWN` 的程序（否则「人工接受」就成了推翻同框守卫的后门）。闸门有两道且方向不同：候选只携带 `PASS` 且已接受的洞，派生 observation 也只把已接受对象列为「已观测」，所以手工拼的候选会在 `object_not_observed` 上被拦。反向验证：拿掉候选侧那道闸门，`test_projected_values_are_not_candidates_without_an_acceptance` 转红，而 observation 侧那道仍独立拦住 stage 0；
- `BaseFrameSources` 的两个方法直接就是 `PlanningOnlyRuntime` 的 observation/candidate provider，不另造 adapter 类。派生 observation 保留原 `observation_id`（同一次 capture，只是换了表述），`frame`/`calibration_ref` 换成 base 与外参，所以 typed-hole 校验的三项比较是同类相比，没有隐式 alias；
- 测试：新增 `tests/test_frames.py`（18）与 `tests/test_program_projection.py`（16），另在 `tests/test_planning_record.py` 加 3 条 q_lift 入账用例。端到端那条从冻结 record 一路 `begin_stage → solve`，断言：decision log 里 observation frame 是 `robot_base`、typed-hole 校验 `PASS`、洞值键集恰好是四元闭集、开口中心 base z = 0.75069（即 7.1mm → 0.69mm 的实测复算）、开口轴与 base 竖直差 <0.1°、`identity_status` 只出现在 provenance；另有 `UNKNOWN` 传播（缺 q_lift → 中心洞 `missing_required_value` → 物理 checker 一个都没跑）与未接受身份不出候选两条。本地 `496 passed`（基线 459 + 37），两个 CLI `--help` 通过；本轮没有调用 Qwen、SAM3、camera、GraspNet、simulator、planner 或 control。

当前停点：这证明的是**合约打通**——frame、calibration、observation 绑定、identity 闸门与 typed-hole 校验在一条真实标定数值上自洽；它**不**证明真实链跑通。三个 hard checker 仍未接入（端到端测试里用的是 PASS 桩，只为让 typed-binding 路径可跑），`execution_enabled` 保持 `False`，「执行前门槛」四项一条未变。明确没做：动 `oracle_runtime`、gates、compiler/prompts、真实网络；grasp（`pose_se3`）洞不在本轮，投影会记 `hole_type_not_projected:pose_se3`，它仍需要独立的 tool transform 与 evidence artifact。已知接缝：一个 base 系数值同时依赖内参与外参，而 envelope 只有一个 `calibration_ref` 位置，现在的取舍是「指向外参、内参留 `source_calibration_ref`」；要不要引入一份合并的 calibration bundle 记录留给下一轮裁决。
## 2026-08-06：Oracle 抬升两项落库（`CLAW_TIP_DZ` 重标、`lift` 闭环化）

两项都来自 **8/6 v4 单世界栈实测且 3/3 判据通过**（含 `release` 解焊 PASS），证据在 5090 的 `~/dgl-stack/evidence/slip/`，口径仍是**第 3 档「privileged Oracle 调试」**——改的是特权调试路径 `execution/oracle_runtime.py`，不是方法路径，也不构成任何阶段或任务成功率。

- **`CLAW_TIP_DZ`：−0.010 → −0.0035。** 语义钉死为**张爪**状态的指尖偏移——`grasp_at` 是以 `gpos=GRIP_OPEN` 张着爪下探的，定位常数必须与下探时的指尖同状态。三方交叉实测：自由腕姿张爪 **−3.34 mm**、抓取腕姿张爪 **−3.57 mm**（两者只差 0.23 mm，互为独立佐证）、同一抓取腕姿**闭爪 +18.35 mm**——开合一次指尖垂直行程 **21.9 mm**，**闭爪值不可用作定位常数**。v3 的 −0.010 疑为同一「张爪」语义但当时没做闭爪交叉验证，作为遗留待核写进注释。证据 `evidence/slip/claw_tip_dz_remeasure.json`；
- **`lift` 从开环固定步数改成闭环「抬到目标高度」。** 旧实现发 `LIFT_DZ/0.02` 条固定 20 mm 指令，每步 `_verify_moved` 的返回值被丢弃。新实现每轮回读 EEF 高度（非特权 `get_xquat`）算剩余量，`≤ LIFT_TOL_M = 5 mm` 即收敛退出，否则按剩余量再发一条 `delta_move`（`LIFT_STEP_MAX_M` 封顶）；`LIFT_MAX_ITERS = 12` 到顶仍未收敛就按实得高度**如实记账**（`converged=False` + `iters` + `ee_dz`），不假装成功。每轮进 `lift_step` 账本（`cmd_dz/achieved_dz/remaining_dz`，风格照 `mp_refine`），承重记账（外力 + 夹持回读 + `attached/reason` 三值）原样叠在闭环之上；
- **为什么必须闭环（实测定性）**：上游控制器每条 `delta_move` 只交付约 **74%** 的指令量，**空载与带载相同 → 负载无关，证伪了「重力把手臂压下去」的假设**；而且渐近停住——一条指令走完 74% 就不再动。固定步数的开环必然欠冲。按 74% 交付率剩余量每轮乘 0.26 几何收敛，实测 **6 次迭代到位**（轨迹 0 → 42.9 → 85.0 → 93.9 → 94.8 → 94.9 → 95.0 mm）。**根因归上游控制器**，本轮不改上游，只在我方语义层把「抬到目标高度」兜住。另有一个实时行为：`_wait_settle` 判静止返回后末端还会继续爬 **1.3–1.9 mm**，闭环在 settle 后加 `LIFT_CREEP_S = 1.5 s` 短等吸收，否则会把未完成的运动记成「已达高度」，下一轮剩余量就是错的；
- 测试：新增 `tests/test_lift_closed_loop.py` 5 条（桩 pipeline `_Plant` 把「每条指令只交付 delivery 比例」的上游行为建模出来）——74% 交付下收敛且剩余量逐轮 ≤ 上轮 0.30 倍、首条指令即全剩余量、每轮账本带指令/实得/剩余、100% 交付 1–2 轮即收敛、交付率 2% 时走满 12 轮预算并如实记 `converged=False` 与回读实得量、闭环调用集合不越出非特权白名单。`tests/test_gates_no_privilege.py` 的 4 条 lift 用例按新语义更新（新增 `converged/iters` 断言：特权位移不得把「非特权高度纹丝不动」救成收敛；达标即退出不再发多余指令），非特权纪律 8 条全绿。本地 `472 passed`（基线 467 + 5），两个 CLI `--help` 通过；
- **反向验证**（改动摘掉后必须变红）：把 `lift` 按回开环固定步数 → 新增 4 条 + 更新的 1 条全红，其余 8 条绿；`LIFT_TOL_M` 归零 → 几何收敛那条红（容差有牙齿）。**没有覆盖到的**：`LIFT_CREEP_S` 摘掉后测试仍全绿——那是墙钟时序行为，离线桩建模不出来，只由 5090 实测背书；
- 本轮**没有**调用 Qwen、SAM3、camera、GraspNet、planner，也没有重跑 simulator——依据是 8/6 那次实测留下的数字，测试全部离线。

当前停点：只动了 `oracle_runtime.py` 与两个测试文件，没碰 planning/perception/policy，`docs/API.md` 的 `lift` 签名与语义边界不变（闭环是原语内部的到位保证，不进契约）。**已知接缝（本轮刻意没动）**：`CLAW_TIP_DZ` 现在有两类语义不同的消费点——`grasp_at`/`approach` 是**张爪**下探（与新值同语义，正确），而 `transport`/`align` 是**夹着物体**移动、爪子处于闭合态，按实测闭爪指尖是 **+18.35 mm**，两者差 21.9 mm。这两处沿用张爪值属于遗留口径，要不要给闭爪单独立常数需要单独裁决（改了会动到 `align` 的对准高度，本轮没有对应的 3/3 实测背书）。另**明确没做**：改上游 74% 交付率的根因、把闭环推广到 `lower_until`（它已有非特权停止判据，语义不同）、给 `_wait_settle` 的 `timeout` 加调用方处置。

## 2026-08-06：Oracle 两项真机调试修复（关节回读来源、抓取 IK 分支翻转）

两项都来自 **8/6 v4 单世界栈实测**，证据在 5090 的 `evidence/slip/`，口径是**第 3 档「privileged Oracle 调试」**——修的是特权调试路径 `execution/oracle_runtime.py`，不是方法路径，也不构成任何阶段或任务成功率。

- **修复一（真 bug）：`_arm_qpos` 的两臂交错假设在 v4 上不成立。** 旧实现假设 `/state` 的 `robot_qpos` 是「左偶右奇」并取 `[a::2][:7]`。实测证伪：`robot_qpos` 长度 **29**，右臂真实下标是 `1,3,6,9,11,13,15`，间隔 `+2/+3/+3/+2/+2/+2` **并不等距**。判别依据是**物理不可能性**——交错切片取出的 j6 = `-2.1813`，落在该关节自身限位 `[-1.308, +1.570]` 之外，关节不可能越过自己的限位；同一瞬间 pipeline `get_qpos` 返回的 7 元组全部在限位内。改为以 pipeline `info:get_qpos` 为关节真值来源（**按臂**的既有接口，与 `robot_api` 执行期收敛核对同源，是被验证过的那条），**不保留任何猜索引的路径**：读到的不是 7 元组就抛错，由调用方按「读不到」处理，不返回错值。唯一调用方是 `_wait_settle`（本臂 + `_park_idle_arm` 的闲臂），它已有的 `except → continue` 会把这种情况退化成 `timeout`。顺带效果：这条回读从特权 `/state` 降到**非特权**机器人状态；
- **修复二（兜底）：抓取的 IK 分支翻转。** 平行夹爪两指对称，`yaw` 与 `yaw+180` 描述同一个物理抓取，但对 IK 是两个解。实测同一次抓取：默认分支 xy 误差 **15.3 mm**、最小关节裕度 **0.297 rad**；绕工具接近轴翻转 180° 后 **3.6 mm**、**0.700 rad**（裕度翻倍、误差降到 1/4）。`grasp_at` 下探到位后测实际 xy 残差，超过 `GRASP_XY_RETRY_MM = 8.0`（命名常量，注释写明来源是这两组实测数字）就翻转一次重试，两支各自的残差与最终选择记进 `grasp_branch` 账本（风格照 `mp_refine`）；翻转反而更差时**退回默认分支**再闭爪，不在更差的构型上闭合。翻转是 `_qmul(q, Rz(180))` 的**右乘**（工具系），与 `_tdx` 的 `_qmul(TDX0, Rz)` 同一套约定——左乘会变成绕世界轴转，是另一个姿态。**没做关节裕度查询版**：仓内没有限位表，重试-on-error 是零新依赖的 v1，裕度版在代码注释里留了升级说明；
- 测试：`tests/test_motion_planning.py` 新增 8 条。关节侧 3 条（真值来自 `get_qpos` 且同一瞬间的交错切片越限位——旧路径的反证、`arm_id` 显式传入读对应臂、形状不对时 fail-closed 且 `_wait_settle` 退化成 `timeout`）；抓取侧 5 条（翻转四元数的数学单测：接近轴不变、指轴同线反向、翻两次回原姿、等于 `_tdx(yaw+180)`；腕姿不朝正下方时工具系右乘 ≠ 世界系左乘；15.3 mm→翻转→3.6 mm 触发且日志含两支残差；3.6 mm 时不翻转且只下探一次；翻转更差时退回默认分支）。本地 `467 passed`（基线 459 + 8），两个 CLI `--help` 通过；
- **反向验证**（改动摘掉后必须变红）：把 `_arm_qpos` 按回交错切片 → 关节侧 3 条全红、其余全绿；摘掉 `_retry_flipped_branch` 调用 → 抓取行为 3 条全红；`GRASP_XY_RETRY_MM` 归零 → 「小残差不翻转」那条红（阈值有牙齿）；翻转改成世界系左乘 → 数学 2 条红（乘序被钉住）；
- 本轮**没有**调用 Qwen、SAM3、camera、GraspNet、planner，也没有重跑 simulator——修改依据是 8/6 那次实测留下的数字，测试全部离线。

当前停点：只动了 `oracle_runtime.py` 与 `tests/test_motion_planning.py`，没碰 planning/perception/policy，`docs/API.md` 的 `grasp_at` 签名与语义不变（翻转是到位后的内部兜底，不进契约）。**明确没做**：`lift` 仍是**开环发固定步数**（`LIFT_DZ/0.02` 步，每步 `_verify_moved` 的返回值被丢弃，只在结束后用总位移和外力做承重记账）——闭环化是下一轮，本轮刻意没动；也没做关节限位表、没把翻转推广到 `align`/`approach`、没给 `_wait_settle` 的 `timeout` 加上调用方处置（现在两个调用方都只是继续往下走）。已知接缝：`_tool_axes` 的行内注释把下标 0 写成「+x 接近轴」、下标 1 写成「+y 开合轴」，与模块常量 `APPROACH_AXIS_IDX=2 / FINGER_AXIS_IDX=1` 和 `_tdx` 的文档（接近轴 = 工具 +z）不一致；代码按常量走是对的，注释是旧的，本轮外科手术式改动没有顺手动它。
## 2026-08-06：越框守卫两条链收敛补测试（裁决确认：共用一份守卫）

- **裁决确认**：`object_record` 的单 anchor 链与 `program_record` 的感知程序执行器**必须共用同一份越框守卫**。一张 mask 只有一个判定，不同规的话 `segment` 收下的记录会在另一条链上被判 `UNKNOWN`，同一份 Qwen 框加 SAM3 mask 走哪条路径决定它的死活——这不是可以按路径调的策略，是同一个物理事实；
- **代码侧本轮没动**：审计发现 08-05 的 `5c87aa3` 已经把 `program_record._segment` 的内联零容差换成共享的 `_mask_outside_box`，但那个 commit 只改了 `src/`，**没留测试、也没在 DEVLOG 记账**。因此下面 08-05「两项裁决落地」条目末尾那句「仍是零容差……留给下一轮裁决」在写下 3 分钟后（`f17681a` 10:54 → `5c87aa3` 10:57）就已经过期，本条即是它的结案；
- **测试补两条而不是一条**：只镜像 `test_segment_still_rejects_a_three_pixel_excursion` 是不够的——3px 拒绝在**旧的零容差实现下同样通过**，它测的是守卫有没有牙齿，不是两条链有没有对齐。真正钉住这次收敛的是新增的 1px 接受用例（`test_segment_accepts_one_pixel_overflow_like_the_single_anchor_chain`）：box `x0=3`、mask 越出恰好一列，程序须 `PASS`。两条都用 `box_overrides` 钉住 tube 查询的框，沿用既有假 Qwen/SAM3 手法，零网络；
- 反向验证：把 `_segment` 按回内联零容差后，1px 用例转红（`UNKNOWN != PASS`）、3px 用例仍绿（正是上一条说的盲区），而 `test_object_record.py` 的同名两条全程保持绿——分歧精确定位在两条链之间，不是共享 helper 坏了。验证后已 `git checkout` 复原；
- 顺带核实全仓不存在第四份实现：`src/` 内 `_mask_outside_box` 恰好三个调用点（`object_record` 的 `segment_record` 与 `_validated_mask_evidence`、`program_record` 的 `_segment`），两文件之外没有任何内联清框写法；
- 本地 `461 passed`（基线 459 + 2），两个 CLI `--help` 与 `git diff --check` 通过；本轮没有调用 Qwen、SAM3、camera、GraspNet、simulator、planner 或 control。

当前停点：只动了 `tests/test_program_record.py` 与本文件，`src/` 一行未改。明确没做：改容差常量的值、把容差做成可配置项、动 08-05 那条历史条目的正文（历史按当时事实保留，过期由本条结案）、把 `_mask_outside_box` 从私有 helper 提成公开 API（两个消费者同在 `execution/`，现在这样够用）。

## 2026-08-05：感知程序跨程序身份守卫（同框不同 object → 双双 UNKNOWN）

- **动机是一起真实的静默污染**（今晨 5090 实跑）：registry 里 `tube_third` 的 distinguisher 写成时序描述（「第三个被插入的」），单帧不可解析，Qwen 退化成「右边那根」，与 `tube_right` 返回**同一个 bbox** `[730, 387, 811, 483]`；`tube_third_long_axis` 因此拿到一个来自错误物体、逐位等于 `tube_right` 的 `PASS` 值。1/6 静默污染，现有守卫（单框、mask 越框、几何 UNKNOWN）全部在单个程序内部生效，一个都拦不住；
- 新守卫在 `execution/program_record.py`：同一次 capture 内收集每个程序**被接受**的 `bbox_pixel`，两个及以上 `object_id` 不同的程序命中逐元素相同的框时，这些程序**全部**降级 `UNKNOWN`（含已算出值的：`value` 置 null，all-or-nothing 语义不变），`reason=grounding_identity_collision`、`failed_step=localize`，envelope 与 program 摘要新增 `collides_with` 互相点名。判定精确相等、**无参数**：不引 IoU、不引阈值常量、不加配置项；
- **同 `object_id` 命中同框不受影响**——今晨同一次 observation 里 `tube_left` 被 p0_0 与 p1_1 各查一次、命中同一个框，那是合法的重复查询，一个 anchor 本来就可以被多条链观测；
- 落点选**全部程序跑完后、写 `program_results.json` 之前**的一次后处理，不放执行循环内：一个程序要等被 ground 之后才成为另一个程序的歧义证据，放循环里会让「先 PASS 后撞」与「后撞先 PASS」两个方向按文档顺序得到不同判定。产物目录 `programs/p<stage>_<index>/{grounding,segmentation,geometry}/` 与 `call.json` **照原样保留**（链确实跑完了，那份记录正是本判定的证据），降级只发生在 envelope 与摘要上；摘要另记被接受的 `bbox_pixel`，让判定与证据同处一份文件。已经因为自己链上失败记 `UNKNOWN` 的程序保留原 reason（更具体的事实），冲突只由 `collides_with` 记账；
- 测试：新增 5 个用例（两个不同 object 同框双双降级且互指、同 object 两次查询全 PASS、三程序两撞一独而独者 PASS、今晨真实数字 `[730,387,811,483]` 在 1280×720 画幅上的端到端回归、冲突对端保留自己的先发失败 reason）。把守卫调用摘掉后这 5 条全红、其余 12 条全绿，反向验证过。测试侧顺带修了假 Qwen 的一个失真：它此前对**所有**查询返回同一个框（正是本轮要抓的病态），现在按 anchor 给不同框，并留 `box_overrides` 显式造同框；`insert_tubes` fixture 的 9 程序用例因此同时成为「6 个 anchor、3 处合法重复查询」的不误伤证据。本地 `459 passed`（基线 454 + 5），两个 CLI `--help` 通过；本轮没有调用 Qwen、SAM3、camera、GraspNet、simulator、planner 或 control。

当前停点：只动了 `program_record.py` 与其测试/文档。**没动 registry 与 prompts**——「anchor 的 distinguisher 必须单帧可判」是这起污染的上游根因，属于待办研究项，本轮只在 `docs/API.md` 的信息边界里记下这条纪律，不改词表、不改 prompt、不给 distinguisher 加校验。明确没做：把判定放宽到 IoU 或 `(object_id, part, instance)` 全 anchor 粒度（现在 rack 的两个 hole 命中同一个框**不算**冲突，是否该算需要单独裁决）、动 `object_record.py` 的单 anchor 链（一次只有一个 anchor，没有冲突语义）、把冲突写进 `programs/p*/call.json`。

## 2026-08-05：两项裁决落地——抽取校验改逐洞丢弃、SAM3 越框守卫给 1px 量化容差

- **裁决一（抽取样本校验粒度）**：样本内某个 hole 校验失败只丢那个 hole，不再否决整个样本。`validate_stage_sample` 改返回 `(errors, dropped_holes)`：hole 级错误只进 `dropped_holes`（`index / name / errors`），`extract` 在投票前把这些洞从样本里删掉；`errors` 只留样本级问题，决定这份回复能不能进 `k_valid`。**约束级语义一个字没改**——一条坏约束仍然否决整个样本，P/R 口径不受这次改动影响；投票机制也没动，被丢的洞不参加洞投票，在多数样本里都写坏的洞自然到不了 `k//2+1`。同名洞按洞级处理（分不清哪个对，就都不投票）；`validate_final_graph` 与 live contract 保持全严，进 `graph.json` 的洞仍必须完全合法；
- 动机数据（2026-08-04 5090 实跑）：30/30 样本被整体否决，其中 29/30 只犯洞级错误；132 个洞错 vs 149 条约束里只有 1 条错。按新语义这批数据可救回 143 条约束和 60 个干净洞。典型洞错误是 scalar 洞缺 `frame`、`part` 写成 `whole_object`/`top`、`principal_axis`/`grasp_candidate` 带 qualifier；
- 洞级错误率单独可见：`graph.json` 的 stage 记录新增 `hole_drops = {count, reasons, dropped}`（`reasons` 抹掉洞下标后按错误类型计数），`extract` 的 stage 行与 `report.html` 的 stage 头带上丢弃数。落点选 stage 记录而非 `validation.json`：后者由 `validate_run_dir` 从 `graph.json` 反推，拿不到抽取期事实，而 `parse_fail/schema_fail` 已经在这里。`validate_final_graph` 不做 stage key 闭集检查，不需要同步 allowed 集。统计口径与 `parse_fail/schema_fail` 一致，覆盖全部 k 次尝试（含事后被约束级错误否决的样本），衡量的是洞的书写质量而不是投票损失；
- **裁决二（SAM3 越框守卫）**：先审两侧代码，结论是**取整约定没有错配**——`semantic_sources._bbox_1000_to_pixel` 与 `object_record._validated_grounding_reference` 用同一套覆盖式换算（min 边 `floor`、max 边 `ceil`），守卫与 `_pixel_bbox` 都按半开框比较。按这套换算 `bbox_1000=[486,285,605,343] @1280x720 → [622,205,775,247]`，昨夜报的右边 1px 溢出本来就不存在（那对应截断式换算 `[622,205,774,246]`，仓内没有这条路径）；剩下的上边溢出是真的：连续上边 `y=205.2`，mask 前景到 `y=204`。因此走裁决第二档，给守卫**每边 1px 量化容差**（`_MASK_BOX_QUANTIZATION_TOLERANCE_PX`，注释写明是量化抖动、不是放宽分割质量），两处守卫（`segment` 实时检查与 `_validated_mask_evidence` 重验）收敛到同一个 `_mask_outside_box`——不同规的话 `segment` 收下的记录会在 `project` 步猝死；
- 测试：新增 8 个用例。抽取侧 5 条（坏洞被丢而样本存活、同名洞双丢、坏约束仍否决整样本、`hole_drops` 形状实样、干净样本记账为空），感知侧 3 条（昨夜真实数字的换算与守卫单测、1px 溢出一路跑到 `OBJECT_CLOUD_RECORDED`、3px 溢出仍被拒）。把容差常量按回 0 时前两条感知用例会失败，守卫没有失去牙齿。更新了 4 处旧断言（`validate_stage_sample` 的返回形状，以及「洞缺 frame → 样本作废」那条）。本地 `454 passed`（基线 446 + 8），两个 CLI `--help` 与 `git diff --check` 通过；本轮没有调用 Qwen、SAM3、camera、GraspNet、simulator、planner 或 control。

当前停点：两项都是离线语义修复，没有重跑 5090 抽取，也没有重跑感知记录——救回 143 条约束是按昨夜错误分布的推算，不是实测。明确没做：动 prompts、动 perception DSL、放松其他守卫、改 `record_result` 的 result.json 形状（洞级证据只落在 `graph.json`）。已知接缝：`execution/program_record.py` 的 `segmentation_mask_outside_box` 是同一条守卫在感知程序执行器里的第二份实现，仍是零容差，同一张 mask 在两条路径上会得到不同判定；要不要合并成一份留给下一轮裁决。

## 2026-08-04：PerceptionProgram 真实执行器（capture 为父、anchor 为子）

- 新增 `execution/program_record.py`：`perception_program.json` 的第一个运行时消费者，也是 `docs/API.md` 预告的结构演进——一次 capture 是父 observation，每个感知程序是一个 anchor 子任务。输入是一个已 `plan + capture` 的 record 目录加一份已发布的文档，按 `(stage, 文档索引)` 顺序逐程序执行；
- 算子接线与契约注释逐条对应：`localize → Qwen single-box client`、`segment → SAM3 binary-mask client`、`crop_points → project_masked_depth`（经 `build_object_point_cloud`，顺带拿到 `MODEL_PROPOSED` assignment 与 cloud manifest）、`fit_opening → estimate_planar_opening_geometry`、`fit_axis → operators.fit_principal_axis`。客户端走既有 `source_module` 注入手法，几何走 `GEOMETRY_IMPLEMENTATIONS` 注入；执行器的算子集合由测试钉死等于 `perception/program.py::OPERATORS`，契约加算子而执行器没跟上会直接失败；
- `localize` 的查询由 `planning_record._perception_request` 渲染——与单 anchor record 同一个渲染器，model 依旧写不了任何查询文本、参数或数值；
- 产物 `programs/p<stage>_<index>/{grounding,segmentation,geometry}/` 各自保存 request/raw/validated 记录，父 observation 的 JPEG 只冻结一份在 `programs/observation_input.jpg`；每个被 provide 的 `(stage, hole)` 在 `program_results.json` 里得到一条 envelope，含 `value / frame / calibration_ref / object_id / identity_status / status / reason / failed_step / evidence_refs / program`；
- **frame 如实写 `camera_head_optical`**。graph hole 请求 `robot_base`，标定链未建，所以下游 typed-hole 校验会因 frame 不一致而拒绝这些值——这是设计意图，不是缺陷。identity 一律 `MODEL_PROPOSED`，执行器不做任何自动接受；
- 失败语义 all-or-nothing：客户端异常、grounding 非单框、mask 非法或越框、几何 `UNKNOWN` 都让该程序全部 provides 记 `UNKNOWN`，带机器可读 reason（几何直接沿用估计器自己的 reason，例如 `insufficient_depth_contrast`）、`failed_step` 与已产出的 evidence refs。一个程序失败不影响同一次 capture 下的其它程序；
- CLI 新增 `planning-record --step programs`，与 `ground/segment` 同规要求 `--allow-model-read`，另需 `--perception-program`；manifest 推进到 `PROGRAMS_RECORDED`。没有新增任何一键端到端入口；
- 测试：新增 `tests/test_program_record.py`（假 Qwen/SAM3 + 合成 RGB-D，手法沿用 `tests/test_object_record.py`，零网络）12 个用例，含两种链形正常路径、单步失败 all-or-nothing、几何 UNKNOWN 传播、缺授权拒绝、envelope 的 frame/identity/provenance 断言、状态推进与重跑拒绝，以及把 `insert_tubes` 的 graph + perception_program fixture 组合起来跑完 9 个程序、断言 filled 与 `coverage_by_stage` 的 covered 逐项一致的表达力用例。本地 `446 passed`（基线 434 + 12），两个 CLI `--help` 与 `git diff --check` 通过；本轮没有调用 Qwen、SAM3、camera、GraspNet、simulator、planner 或 control。

当前停点：`programs` 只把值算出来并如实标注，**没有接 `PlanningOnlyRuntime.solve()`**，没有 frame 变换、没有 identity 接受、没有 candidate 消费（那是 B3b）。明确没做：动 compiler 与 prompts、接真实网络、GraspNet 进感知程序、把单 anchor 链与多程序链收敛成一套实现。已知接缝：record 目录的 manifest 状态是一条线性链，所以 `programs` 与 `ground/segment/project/predict` 在 capture 之后互斥——任一条推进了状态，另一条就不再接受这个目录；这是 fail-closed 的诚实表现，但两条路径最终要不要合并成一条需要单独裁决。另一处接缝：`programs` 仍要求 plan 里那个单 anchor `perception_request` 保持有效（`_revalidate_record_plan` 的前置检查），虽然它并不消费这个请求。

## 2026-08-04：PerceptionProgram 编译入口（信息边界调用点 4→5）

- **受治理的边界变更**：`docs/API.md` 中 backend model 被允许出现的调用点从 **4 个增到 5 个**，新增 `PerceptionProgram` 提议。它与其余四个同规——输出的是受限 JSON，不是 Python、查询文本、逐步参数或任何数值；`localize` 的查询仍由可信代码从 hole 已有的 anchor 渲染，model 只从闭集算子里选链并声明哪个字段发布哪个 hole；
- 封住上一轮记下的语义漏洞：`perception/program.py` 新增 `RESOLVER_BINDINGS`（`part_center → fit_opening.center`、`part_axis → fit_opening.axis`、`principal_axis → fit_axis.axis`）。被 provide 的 hole 声明了 resolver 时，(终点算子, field) 必须与绑定表一致，违规消息定向指出洞的 resolver 语义与链的算子语义；洞没有 resolver 时维持类型匹配即可。`insert_tubes` fixture 原样通过；
- `dgl compile` 现在产出两个 program。感知段在 `policy.py` 发布之后进行，覆盖目标由 `wired_hole_contracts_by_stage`（设计后首次有生产消费者）给出：只取 `StageProgram` 真正接线、类型为几何且 resolver 可发布的 hole。prompt `prompts/compile_perception.md` 陈述校验器全部规则，算子表与绑定表由代码渲染（单一真相源），few-shot 用 `insert_tubes` fixture 的真实片段；调用走既有 `common/llm.py`，独立 tag `compile_perception`，成本与缓存复用既有机制，单轮无修复回路；
- 发布门是「零违规 + `FakePerceptionRuntime` 干跑通过」，两者都过才写 `perception_program.json`。失败只落 violations 与 `model_calls/compile_perception/`，`StageProgram`/`policy.py` 的发布与 CLI 退出状态完全不受影响——未发布时相关 hole 继续走 graph resolver 老路。`compile_report.json` 新增 `perception_program` 段（status/ref/violations/coverage）；wired 几何洞里没有可发布目标时 `status=skipped` 且不调用 backend；
- 感知程序是新的 run 产物，上游六处 `invalidate_outputs` 一并把 `perception_program.json` 列入作废清单，避免它在 graph 变化后独自存活；
- 测试：新增 `tests/test_perception_compile.py`，全部走 canned 响应、零网络（只有成本/缓存一条按既有手法替换 `sys.modules['openai']` 以跑通真实落盘路径）；本地 `434 passed`（基线 420 + 4 绑定规则 + 10 编译段），两个 CLI `--help` 与 `git diff --check` 通过；本轮没有调用 Qwen、SAM3、camera、GraspNet、simulator、planner 或 control。

当前停点：`perception_program.json` 已经能被编译出来，但**没有运行时消费者**——`PlanningOnlyRuntime`、gates 和 execution CLI 都还没接它，链上算子也仍未接真实 grounding/segmentation/几何实现。明确没做：运行时消费（B3）、改 `StageProgram` schema 与 `compile_policy.md`、动真实感知实现、感知程序的修复回路（与现有 compile 一致保持单轮）。已知文档张力：`docs/PROPOSAL.md` 的「VLM 可以参与四个位置」是角色分类而非调用点计数，本轮只把「提议 program」那一条的措辞扩到两种 program，没有改动那个计数。

## 2026-08-04：PerceptionProgram v1 可执行骨架

- 新增 `perception/program.py`：感知侧程序的单一真相源，与 `policy/program.py` 对 `StageProgram` 同构。算子闭集 5 个（`localize / segment / fit_opening / crop_points / fit_axis`），`consumes/produces` 类型表把链钉成 `ANCHOR` 起、`GEOMETRY` 止的无环线性链；每个算子的注释指向背后的现有实现（grounding/segmentation client、`estimate_planar_opening_geometry`、`project_masked_depth`、`operators.fit_principal_axis`），本轮只固定契约，不接线；
- `validate_perception_program(doc, graph)` 输出确定性 violations 列表：顶层/条目 key 闭集、schema 串与 `task` 对齐 graph、stage 存在、链首尾衔接、`provides` 的 field 属于终点算子、hole 存在且类型一致、同一程序共享逐字段相同 anchor、`resolver` 限于 `PERCEPTION_RESOLVERS - {grasp_candidate}`、一个洞只能由一个程序发布。整个文档另跑一遍数值字面量扫描（`stage` 索引按路径豁免）做纵深防御；`coverage_by_stage` 给 per stage 的 covered/uncovered 名单，未覆盖不算违规；
- 新增 `perception/fake_runtime.py`：逐程序干跑，算子只记日志、只传不透明 handle，`fail_at=(program_index, op_name)` 注入单点失败，失败语义 all-or-nothing（该程序 `provides` 一个都不产出），注入没打中直接报错；
- 表达力判据：`tests/fixtures/graphs/insert_tubes.perception_program.json` 用 9 个程序覆盖 27 洞 fixture 中全部 12 个 `part_center/part_axis/principal_axis` 洞，过校验并干跑填满；未覆盖的 6 个洞逐一核对为 `grasp_candidate` 或 `motion_derived`。这份 fixture 同时是将来 compile prompt 的 few-shot 素材；
- 护栏：仿照 `tests/test_regions.py` 给两个新模块加源码禁词扫描，已反向验证会失败；数值字面量判据与 `policy/program.py` 各自持有实现，另有用例逐值对齐两者；
- 本地 `420 passed`（基线 383 + 37 个新用例），两个 CLI `--help` 与 `git diff --check` 通过；本轮没有调用 Qwen、SAM3、camera、GraspNet、simulator、planner 或 control。

当前停点：只有校验器和 fake 干跑，没有解释器、没有落盘产物、没有 compile 入口，`perception_program.json` 尚不存在于任何 run 目录。明确没做：改 `graph/validate.py` 与 `policy/`、改 compiler 与 prompts、接真实 grounding/segmentation/几何实现、建 `PerceptionAPI` 类（v1 线性链无逐步参数，注册表就是契约）、`fit_support_surface` 与 `detect_grasps`（前者无消费者，后者牵动 candidate 身份机制）。校验器目前不检查「终点算子与 hole 的 resolver 是否匹配」——`part_axis` 洞可以由 `fit_axis` 发布，语义上应该走 `fit_opening`；这条要不要收紧留给下一轮裁决。

## 2026-08-04：感知层算子化、去任务先验命名与词表收归

- 新增 `perception/operators.py`：`fit_plane`（SVD 平面拟合 + 平面度 RMSE，保留「第二奇异值 > 1e-8 才算张成 2 维」的退化防护）、`intersect_ray_plane`（保留平行与交点在相机后方两个防护）、`fit_principal_axis`（由 `execution/object_record.py::_principal_axis` 原样移动，失败仍是 raise）。算子用 `OperatorError(reason)` 报前置条件失败，调用方用显式映射表翻译成自己的产物 reason 码，未映射的码直接 KeyError；抽取零行为变化，另用 4000 组随机点集与重构前的内联算法对照，返回值与失败分支逐比特一致；
- 去 rack 命名：`estimate_rack_hole_geometry` → `estimate_planar_opening_geometry`、`RackHoleGeometry` → `PlanarOpeningGeometry`、schema 串升为 `demo_graph_lab.opening_geometry.v2`（全仓核实该产物无生产下游消费者，只有测试读，不留 v1 兼容层）；reason 码改为 `support_surface_not_planar` / `ray_parallel_to_support_plane` / `plane_intersection_behind_camera` / `estimated_from_rgbd_roi_and_local_support_plane`；record 层 artifact key `opening_geometry`、落盘 `object/opening_geometry.json`、result 字段 `opening_geometry_ref/opening_geometry_status`。graph anchor 词表里的 `part=="hole"` 不动；
- 唯一行为变化（单独 commit）：开口深度对比门从有符号改为 `abs(depth_contrast)`，凹陷与凸起开口都接受；`metrics.depth_contrast_m` 保留带符号原值，符号正是上层判凹凸的依据；
- 词表收归：resolver 闭集与 anchor 校验规则此前有三份不同步副本，宽严还不一致。`graph/vocab.py` 新增 `PERCEPTION_RESOLVERS` 与 `anchor_rule_errors(...)` 作为唯一实现，`graph/validate.py`、`execution/object_record.py`、`perception/object_pipeline.py` 全部改为调用；宽严统一到 graph 层的「hole anchor 恰好一个 qualifier」，fixture 与测试无一因此挂掉。`motion_derived` 语义保持拒绝——词表放宽后在 `_plan_context` 显式给出 "derived from execution state, not perception"，不靠「不在集合里」兜底；
- 护栏：仿照 `tests/test_regions.py`，给 `perception/object_pipeline.py` 与 `perception/operators.py` 加同款源码禁词扫描（tube/bowl/coin/rack/slot 等），作为去污的 enforcement；
- 本地 `383 passed`（基线 379 + 4 个新用例：凸起开口、motion_derived 拒绝、两个参数化护栏），两个 CLI `--help` 与 `git diff --check` 通过；本轮没有调用 Qwen、SAM3、camera、GraspNet、simulator、planner 或 control。

当前停点：这一轮只做算子抽取、命名/schema 归一和词表收归，是后续受限感知程序 DSL 的地基轮，本轮**不建** DSL。明确没做：`fit_support_surface`（现在没有消费者）、把算子失败改成 status 返回、anchor 词表里 `part=="hole"` 的改名（牵动 prompts/fixtures）、`ring_width_px` 物理化（等 live 标定）、`oracle_runtime` 的 SYNONYMS。`opening_geometry` 产物仍无下游消费者，v2 是首个被消费前的定版机会。

## 2026-08-04：record 与 selection 两处 fail-open 收口

- `project` 的 `object/result.json` 不再硬编码 `status="ACCEPTED"`：请求了几何但 `hole_geometry_status` 不是 `PASS` 时写 `GEOMETRY_UNKNOWN`，几何 `PASS` 或本次未请求几何才写 `ACCEPTED`；`hole_geometry_status` 字段本身不变。record 确实发生，所以 manifest 的 `OBJECT_CLOUD_RECORDED` 与 CLI 退出码不动，改的只是这个词；`docs/API.md` 同步该派生规则；
- `binding.solve_pose_se3` 对词表外 region 从静默退化成质心改为 `ValueError`，与 `regions.region_preference` 同规同错误串；`region` 缺省(非抓取语义)仍取质心，`rim/handle` 仍标 `uncheckable`；`regions.py` 中「与 binding 的 unknown_type 同规」这句与实现不符的注释一并改正；
- 测试：UNKNOWN 几何路径新增 `status == "GEOMETRY_UNKNOWN"` 断言，新增 record 级几何 `PASS → ACCEPTED` 用例（在 sensor 深度/亮度上造出真正的开口对比度），主链路补「未请求几何 → ACCEPTED」断言；binding 侧补词表外 region 抛错与无 region 仍走质心两个用例；
- 本地 `379 passed`（基线 376 + 3 个新用例），两个 CLI `--help` 与 `git diff --check` 通过；本轮没有调用 Qwen、SAM3、camera、GraspNet、simulator、planner 或 control。

当前停点：只收口了这两处已核实的 fail-open，没有做任何相邻重构。`object/result.json` 的 `status` 目前仍无下游消费者（全仓只有测试读它），真正的 gate 语义仍在 `evaluation/`；下游接入 candidate 时要显式决定 `GEOMETRY_UNKNOWN` 是否阻断。

## 2026-08-04：单 anchor object perception chain

- graph 几何 hole 增加闭集 resolver 与 object/part anchor；reviewed `insert_tubes` fixture 固定 `tube_mid/right/left → center/right/left hole`，抓取和 tube axis 复用 whole-object anchor，upper-body 只保留为排序约束；
- 新增 Qwen single-box 与 SAM3 binary-mask 只读 client；每次调用保存 request、raw、result 和 call，零框、多框、bbox 映射错误、非二值/全帧/越框 mask 都 fail-closed；
- 新增 mask-first RGB-D 投影、逐点 pixel lineage、`MODEL_PROPOSED` anchor binding、object cloud manifest，以及基于 RGB-D contrast 和 rack ring plane 的 hole center/axis `PASS/UNKNOWN`；
- `planning-record` 现在显式分成 `plan / capture / ground / segment / project / predict`；只有 `grasp_candidate` 能把 object-only cloud 交给 GraspNet，raw detector ID 原样保存且不生成 candidate；
- 每个 live/model step 都重验 graph、objects、embedded stage 与 perception request；project 重新绑定 frozen BGR/JPEG、Qwen box、SAM3 PNG/bool mask，predict 从 frozen depth+mask 重算 cloud/pixel lineage 后才允许调用 GraspNet；part geometry 不写成 whole-object observation；
- 静态核对 5090 现有接口：Qwen 是 OpenAI-compatible chat completion，SAM3 的 JSON `/segment` 接受 base64 image + box prompts，和新 client 契约一致；CLI 识别现有 Qwen/SAM3 环境变量，但非交互 shell 需要先显式加载实验环境；
- 本地与 5090 均为 `376 passed`，主 CLI 与 `planning-record --help` 通过，`git diff --check` 通过；本轮没有调用 Qwen、SAM3、camera、GraspNet、simulator、planner 或 control。

当前停点：V1 一个 record 只处理一个 anchor，尚不能在同一 observation 下组装一个 stage 的多个 holes；identity 仍是 `MODEL_PROPOSED`，camera/tool frame 标定和 candidate conversion 未接。下一步先在正确 `insert_tubes` scene 上做只读 component 验证，再设计父 observation + 多 anchor 子任务；执行继续关闭。

## 2026-08-04：首个只读 head → raw GraspNet record

### 完成的代码

- 新增 `perception/live_sources.py`：一次性 head capture、米制 depth 反投影、固定四次 `get_qpos/get_xquat` 读取，以及仅允许 loopback 的 GraspNet health/predict client；没有通用 action、planner 或 control 接口；
- 新增 `execution/planning_record.py` 与 `dgl planning-record`，显式分成 `plan / capture / predict`。默认 plan 零网络，两个 live step 都要单独给 `--allow-live-read`，没有一键跨过检查点的入口；
- observation 保存左右 BGR、float32 米制 depth、OpenCV head optical-frame 点云、完整 projection manifest、严格 binding manifest、内参和本体状态；
- raw GraspNet validator 保留原始 detector ID；删除了没有 assignment evidence 的旧 mapping converter，当前不发布 GraspNet→graph candidate 路径；
- `HTTP 200 + ok=false`、fixture backend、未就绪 health、schema/backend/input echo 漂移全部 fail-closed，并保留原始 payload 和调用状态。

### 5090 实跑产物

Record：`/home/knowin-sim/demo-graph-lab-workflow/runs/planning_records/20260804_181047`

- 状态：`RAW_GRASPNET_RECORDED`；`backend_model_enabled=false`，`execution_enabled=false`；
- observation：`head-134-700896236992`，720×1280 depth，921,600 个 finite 且 `z>0` 的米制 optical-frame 点；
- capture：一次同步 head render + 两臂 qpos/xquat，0.45 s；传感副作用完整写入 `sensor/call.json`；
- GraspNet：真实 baseline health ready，一次 predict 0.51 s，返回 20 个通过 raw schema/17D 一致性检查的 proposal；20/20 `object_id=-1`；
- 没有生成 `candidates.json`、hard-check certificate、replay、motion plan 或 action；
- 当前 simulator 运行的是 `scenes/smoke/stand_up_bottle.scene.yaml`，而 plan 引用 `insert_tubes` graph。主方法没有读取 `/state` 核验场景，因此 manifest 明确保留 `scene_identity_unverified`；这份 record 是 infra smoke，不是 insert-tubes 效果数据。

GraspNet 缺失的 `pointnet2._ext` 和 `knn_pytorch` 已在现有 venv 中按 RTX 5090 编译并通过模型 import；没有修改上游源码。构建日志位于 `/home/knowin-sim/dgl-perception/logs/`。服务只临时绑定 `127.0.0.1:8092`，record 完成后已关闭。

### 验证与停点

- 本地：`326 passed`，两个 CLI help、`planning-record --help` 和 `git diff --check` 通过；
- 5090：同一套 `326 passed`；record 的 observation、point-cloud binding、health、request、raw response、validation 和 call artifacts 均可复查；
- 本轮没有调用 backend model、simulator `/state`、reset、官方 task probe、motion planner 或 control；
- 当前停在 raw response。下一步必须先做任务匹配的 object mask/assignment，以及 lift-aware camera→robot-base 和 grasp→runtime-EEF 标定；在此之前不生成真实 candidate，更不进入执行。

## 2026-08-04：planning-only 候选契约与固定 replay

### 完成的代码

- 增加严格的 observation/candidate record adapter；多余或缺失字段、NaN/Inf、数值位置的 bool、空或重复证据都会失败；
- 增加 recorded GraspNet `/predict` raw validator：校验真实 schema、17D raw array、米制 point-cloud manifest、frame 与 observation identity；没有可信 object assignment 时不输出 graph candidate；
- Candidate 绑定 `observation_id`，几何 hole 统一使用 `{value, frame, calibration_ref, object_id}`；pose quaternion 和 axis 必须单位化；
- typed-hole 校验位于所有物理 checker 之前。类型、shape、frame、标定或对象不合法时，reachability/collision/width 不运行并留下 `UNKNOWN not_run` 证书；
- `PlanningOnlyRuntime.solve()` 做第二次 binding 校验；validated StageProgram 的 hole wiring 可直接决定每阶段 required holes；candidate provider 不能填写 scalar/runtime condition；
- 增加 synthetic fixed replay 和 `dgl planning-replay`：三个 hard-check certificate 只过滤一次，demo 与 candidate-ID baseline 共用 accepted candidates，输出一个 comparison JSON；
- replay fixture 明确标记 `synthetic_contract_fixture`，不作为真实效果或执行结果。

### 5090 只读接口盘点

- head RGB-D 与米制 depth 的读取路径存在；hand camera 的实时 EEF frame transform 尚不完整，暂不接；
- 实际 GraspNet 服务使用 `/predict` + point-cloud path，旧 `/propose` client 不兼容；输出不含 graph object ID、`approach_tilt_deg`、`height_fraction` 或碰撞结果；
- IK 会先 clip 越界 target，现成 planner wrapper 又丢失 success；两者当前都不能直接签发可信 reachability `PASS`；
- K1 gripper 只有 motor angle，缺米制 opening-width 标定，width checker 当前必须为 `UNKNOWN`；
- 没有找到同时含 RGB、depth、calibration、proprioception、candidates 和三个 certificates 的真实 replay，需要新采集。

### 验证与停点

- 本地 `305 passed`；两个 CLI help、planning replay CLI 和 `git diff --check` 通过；
- synthetic replay 接受 `c00/c01/c02`，固定 ID baseline 选 `c00`，demo region/cone 选 `c01`；这里只验证对照逻辑能改变 top-1，不报告方法效果；
- 本轮只静态读取远端代码，没有调用 HTTP/API、capture bridge、`/state`、reset、backend、pipeline 或 control，也没有启动 simulator；
- 当前停在 frozen planning replay。下一步是只读采集第一份真实 observation；在真实 checker 与 gate/abort 审查完成前继续保持 `ExecutionDisabled`。

## 2026-08-04：离线 workflow 与在线脚手架

### 远端保护

- 5090 原目录：`/home/knowin-sim/demo-graph-lab`；
- 完整归档：`/home/knowin-sim/archives/demo-graph-lab-pre-workflow-2026-08-04.tar.gz`；
- 归档 98 MB、权限 600，通过 gzip 和目录清单检查；原目录未移动或删除；
- 本轮代码和离线实验放在旁路目录 `/home/knowin-sim/demo-graph-lab-workflow`。

### 完成的代码

- 新增 `docs/OFFLINE_WORKFLOW.md`，集中说明从视频导入到 policy 编译的阶段、产物和 backend 调用记录；
- 四类离线 backend call 统一记录脱敏 request、input refs、raw、parsed、validator、实际模型、耗时和成本；
- stage split 全视频均匀采样；registry、constraint sample、hole 和 object ref 严格校验；
- 同一 run 在脱敏 request 完全相同时复用已完成 raw reply；prompt、model 或参数变化会重新调用；
- 图像内容进入脱敏指纹；cache 只复用 `status=ok` 的完整调用，同 tag 重调会保留旧记录；
- backend 只生成 `StageProgram`，可信代码校验 primitive sequence / hole wiring 并确定性生成 Python；
- final graph 必须完整对齐 stages manifest，holds/frame/evidence 投票与校验 fail-closed；compiler 会重验当前 graph，dry-run 成功后才发布 policy；
- stage manifest 会在抽帧前拒绝重复编号、重叠窗口和视频越界；final graph 强制记录请求数 `k` 和每阶段有效数 `k_valid`，不能跳过多数票；
- gate 对 `acceptance` 和 `constraints` 都做三值合取；`throughout` 与 `at_end` 独立记账，前者必须通过入口和出口检查；
- 新增 `purpose=lower_stop` 控制契约和独立的 `retreat(target)` opcode；validator 会拒绝错误接线。可信 retreat pose solver 尚未实现，Oracle 在运动前明确拒绝；停止信号的明确路由也仍列为执行前 TODO；
- 新增 planning-only 在线路径：typed observation/proprioception、immutable candidates、fail-closed hard filter、确定性排序、decision JSONL 和 opaque handles；
- 所有在线控制原语默认抛 `ExecutionDisabled`，runtime backend 固定关闭。

### `insert_tubes` 离线实跑

最终 run：`/home/knowin-sim/demo-graph-lab-workflow/runs/insert_tubes/20260804_122155`

- 6 stages、30 stage keyframes、5 个 registry objects；
- constraint extraction：每阶段 5/5 有效，30/30 raw replies 通过 schema；
- graph：45 个 constraint/acceptance items，0 violations，0 warnings；
- backend calls：1 registry + 30 extraction + 2 program proposals；第一次 program proposal 被 type/order validator 拒绝，修正 API 契约后第二次通过；
- `cost.jsonl` 保留 33 次调用；当前 `model_calls/` 有 32 份最终 call artifact，因为第一次无效 program 发生在 history 保留机制接入前并被同 tag 重调覆盖。后续重调不会再覆盖；
- 累计 backend 成本：约 USD 1.65；
- deterministic compile：program/static violations 均为 0；
- FakeRuntime：normal path 与 fail-once retry path 都通过，16 个 holes 被求解；
- `compile_report.json` 仍显式列出 unwired scalar、重复 stage holes 和 gate-only conditions，未把它们伪装成已消费。

实跑中修复了三类真实问题：dot-style hole 名与 snake_case validator 不一致；`carry.relation` 的 registry-id 边界误判；两阶段分组使用半数阈值会传播互相矛盾的 approach cone，现改为严格多数。

### 验证与停点

- 本地：`217 passed`，两个 CLI `--help` 通过，`git diff --check` 通过；
- 5090：`217 passed`，两个 CLI、归档和权限检查通过；对既有 run 做只读复核，30/30 raw samples、manifest、registry、45 个 graph items、StageProgram、当前确定性 policy 和两条 FakeRuntime dry-run 全部通过；
- 既有 run 的 `compile_report.json` 早于“报告精确绑定完整 StageProgram”规则。新 Oracle loader 会拒绝直接执行它；没有手工升级旧报告，执行前须在明确确认后重新通过 compile gate；
- 本轮没有启动 simulator，没有读取 `/state`，没有调用 pipeline/control，也没有下发机器人动作。

执行前 blocker：真实 sensor/candidate/check adapters、candidate hole type/frame/calibration 校验、固定 replay、单 stage gate/abort 检查。完成并评审这些项目后，才能把 planning-only runtime 接到控制层。
