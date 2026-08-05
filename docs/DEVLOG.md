# 开发日志

只记录最近的工程动作、可复查产物和停点。稳定设计写进 README/API，后续工作写进 TODO/MILESTONES。

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
