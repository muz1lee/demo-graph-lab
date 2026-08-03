# docs/ARCHITECTURE.md —— 代码架构与接口形状

> **权威顺位**：`PROPOSAL.md`（idea 与假设）> `EXECUTION.md`（实验闸门 / 模块名准绳 / 预算 / sim 排班）> `TODO.md`（任务编号、人日、依赖）> **本文**（架构与接口形状）。凡涉及模块名、实验顺序、人日与预算，本文一律引用不自造。
> **证据分级**（全文强制）：`✅实测` 已跑过有输出 · `⚙代码在` 仓里有代码但零调用/未测 · `📋计划` 只在文档里，零行代码。
> **行号纪律**：`file:line` 是溯源链的一部分，全部按 **2026-07-31 实测复核**。

---

## 1. 分层总览

```
demo 视频 ──[编译期 Opus]──> graph.json ──[compilepolicy]──> policy.py
                                                              │ 只写调用序列，零数值
        ┌─────────────────────────────────────────────────────┘
        ▼
 ①契约面 rt.*（G3/G2/G1 三份契约变体）    ← policy 的唯一合法调用面
        ▼
 ②可信运行时 binding/regions/graspfunnel/predicates/perception/robotapi/bounds
        ▼                                    （我方手写，所有数值产生于此）
 ③仿真栈原语 reasoning:* / ctrl:* / info:* / WebUI    ← 上游给定，零污染
```

| 层 | 谁写这层代码 | 可否含数值常数 | 文件 |
|---|---|---|---|
| ① 契约面 | 我方手写，且是**版本化 prompt 资产**（D-13，`DECISIONS.md:201`） | 否 | `harness/contract.py` + `contract_g2.py`/`contract_g1.py`（📋，受未决 #2 阻塞） |
| policy | **编译期模型生成**，只出调用序列 | **禁**（`static_check` 拒绝 STAGES 外任何数字字面量） | `runs/*/policy.py` |
| ② 可信运行时 | **我方手写**（论文主张就落在这一层） | 是，但必须来自感知或标定 | `harness/{binding,regions,graspfunnel,predicates,perception,robotapi,bounds,evidence}.py` |
| ③ 底层原语 | 上游仿真栈，**不改**（零污染原仓） | — | `adapters/knowin_world/pipeline` |

---

## 2. ★ API 的三个颗粒度（本文核心）

PI 要求「cap 在写 code 的时候需要不同颗粒度的 APIs」。对照 CaP-X §3.1 的 S1–S4（成功率随抽象层级单调上升）。**三层同时暴露，编译期模型自己选。**

```
G3  一步到位   rt.approach / grasp_at / align / lower_until …  （现 contract.py，一行不改）
G2  分步暴露   propose_grasps / rank_by_preference / filter_feasible / select / plan+execute
G1  自由组合   observe / segment / get_depth / mask_to_points / compute_obb / verify_predicate
```

### 2.1 三份契约的完整签名

| 层 | 文件 | 接口签名 |
|---|---|---|
| **G3** | `contract.py`（**原文一行不改**） | `solve(hole_name)` · `residual(constraint)` · `approach(target, cone=None)` · `grasp_at(grasp_pose)` · `lift(obj)` · `transport(obj, target)` · `align(obj, target, axis=None)` · `lower_until(stop_condition)` · `push(obj, contact, toward)` · `release()` · `verify(constraint) -> bool` |
| **G2** | `contract_g2.py` = G3 + 下列 9 个 | `propose_grasps(obj, *, mask=None, topk=20) -> list[GraspCandidate]` · `rank_by_preference(cands, *, region=None, cone=None) -> list[GraspCandidate]` · `filter_feasible(cands) -> (kept, dropped)` · `select(ranked) -> SelectionResult` · `get_object_pose(query, *, track="qwen") -> ObjectPose` · `read_state(arm_id=0) -> ArmState` · `plan_joint_path(*, tcp_pose=None, q_goal=None) -> list[q7]` · `execute_path(waypoints, *, converge_tol) -> ExecResult` · `set_grip(state)` |
| **G1** | `contract_g1.py` = G2 + 下列 6 个 | `observe(sources=("head",), views=("overview",), *, require_fresh=True) -> EvidenceBundle` · `segment(query, *, source, view) -> list[Mask]` · `get_depth(*, source) -> DepthFrame` · `mask_to_points(mask, depth) -> (N,3)` · `compute_obb(points) -> OBB` · `verify_predicate(constraint, bundle) -> Verdict` |

**三层是同一套实现的三个切口**：G3 内部调 G2，G2 内部调 G1，`solve()` 走的就是 §5 的派发链。层间**不允许**出现只在某一层存在的数值路径，否则消融的自变量不干净。

### 2.2 同时是「抽象层级」消融的实验载体

- 对应 **`PROPOSAL.md §6` 假设 A7**（「薄底座仅底层原语足以表达任务」，证伪设计明写「把动作面的抽象层级当实验变量」）。⚠️ **口径纠正：`PROPOSAL §6` 假设表只到 A7，不存在 A8**，此前口头引用的「A8」请一律改为 A7。
- 实验载体 = **P2-07 / E-ABSTR**：同一任务在 `G3-only` / `G3+G2` / `全开` 三档重新编译并执行，报成功率 + 各层 `static_check` 字面量拒绝率。
- ⚠️ **诚实交底**：CaP-X 是 7 任务 × 100 trials，我方样本量远小于此 → **E-ABSTR 只能当 conditioning 变量，不能当 finding**；且它**未在 `EXECUTION §1` 注册、不在预算内**（`TODO §9-11`）。

### 2.3 ⚠️ 本节整体受未决事项 #2 阻塞

「分颗粒度」与「`contract.py` 明令不动」正面冲突：让模型自己选层 ⇒ 细颗粒 API 必须出现在 `contract.Runtime` ⇒ 必须改 `contract.py`（`compilepolicy.py:83` 用 `inspect.getsource` 把整个文件拼进提示词，改它 = 静默改提示词 = 已编译 policy 与新契约不同源）。三选项见 `TODO §9-2`，**我方建议 (c) 契约变体**（代价：三份同步维护 + 三套 digest + 重编译 ≈ $22.5）。**这是 PI 拍板项，未裁决不得开工 P0-07/08/09。**

---

## 3. A 组 · 感知与规划接口

**已实测可用、可直接包一层的现有服务**：`reasoning:qwen_dof_xquat` ✅（抓取候选，`topk_pick_records_by_arm` 带 `pick_xquat`/`confidence`/`contact_width`/`composite_score`）· `reasoning:motion_planning_stereo` ✅（运动规划，端到端出过多航点轨迹）· `reasoning:qwen_xquat` / `sam_xquat` / `bbox_xquat` ✅（位姿，两路互测差 3.5mm）· `ctrl` 六件套 ✅ · `info` 五件套 ✅ · WebUI 4 源 × 5 视图 ✅ 全 200。

| # | 接口 | 返回 | 坐标系 | 实现方式 | 档 | TODO |
|---|---|---|---|---|---|---|
| A1 | `observe(sources, views, *, require_fresh)` | `{frames:{(src,view):Frame}, bundle_id, degraded}` | — | **包一层** `GET /api/frame.jpg` ✅ + 新鲜度门（同 md5 连续两次 → 重取 ≤3 次） | 薄 | P0-18 |
| A2 | `read_state(arm_id=0)` | `{q(7), xquat_wxyz(7), extforce(3), gripper_cmd, untrusted:{is_gripping_sth}}` | base | **包一层** `info:get_arm_info` ✅ + `get_xquat` + `get_ee_extforce` ✅ | 薄 | P0-08 |
| A3 | `get_object_pose(query, *, track)` | `{xyz, quat_wxyz, bbox_px, grasp_angle_hint, score, disagreement}` | base | **包一层** `qwen_xquat`/`sam_xquat` **双轨** ✅；两轨差 >1cm 记 `disagreement=True`，**不静默取一** | 薄→中 | P0-09 / P1-04 |
| A4 | `compute_obb(points)` | `{center, extent(全边长), R(3,3), axes}` | base | **自建** numpy/open3d，纯本地无网络 | 薄 | P1-07 |
| A5 | `mask_to_points(mask, depth)` | `(N,3)` | base | **自建**，纯 numpy 反投影 | 薄 | P1-07 |
| A6 | `segment(query, *, source, view)` | `[{mask, box_px, score, label}]` | 像素 | **passthrough** 我方 SAM3 `192.168.20.212:5081 /segment_raw` ✅在网；**不移植 cap-x 的 8114 服务端** | 薄 | P1-06 |
| A7 | `get_depth(*, source)` | `{depth float32(H,W) 米, K, T_base_cam}`，depth 做 lazy thunk | 相机→base | **真开发**（A 组唯一）。**不修上游 `pixels_base3d`**（`visual_processor.py:588/636` 的 `np.int64` vs `isinstance(x,int)` 分派 bug，改它污染原仓） | **重** | P1-05 |
| A8 | `propose_grasps(obj, *, mask, topk)` | `[{T_base, score, width_m, contact_xyz, track}]` | base | **候选源双轨**：T1=`qwen_dof_xquat` ✅包一层；T2=**GraspNet**（见下）；T3=OBB 派生解析退化，打 `degraded=true` | 中 | P1-04 / **P1-11** |
| A9 | `rank_by_preference(cands, *, region, cone)` / `filter_feasible(cands)` | 有序候选 / `(kept, dropped)` | base | **自建** `regions.py`，**任务无关**映射表，零 per-task 分支 | 薄 | P0-03 |
| A10 | `select(ranked)` | `SELECT(idx)` / `REJECT_ALL` / `REQUEST_EVIDENCE` | — | **复用现成** `method/demo_graph/candidates.py::CandidateSelector` ⚙（`:76` 类 / `:79` `select` / `:14-15` 三态） | 薄 | P0-10 |
| A11 | `plan_joint_path(*, tcp_pose, q_goal)` | `list[q(7)]` | base | **包一层** `motion_planning_stereo` ✅；要处理 `mp.version`/`mp.intent`/`mp.planning_mode`/`mp.scene_input`/`mp.scene_camera` + 扁平 list reshape 成 N×7 | 中 | P1-02 |
| A12 | `execute_path(waypoints, *, converge_tol)` | `{reached, residual_q, steps_done}` | — | **包一层** `ctrl:qpos_move` ⚙ 逐点下发 + `get_qpos` 收敛核对。**绝不整段下发大跳**（MotorNode 停 70–80%） | 薄 | P0-08 |
| A13 | `verify_predicate(constraint, bundle)` | `{verdict ∈ PASS/FAIL/UNKNOWN, margin, evidence_kind}` | — | **自建** `predicates.py`，替换五处 fail-open（见 §8） | **重** | P0-05 |

**GraspNet 与 `qwen_dof_xquat` 并列为候选源双轨**（PI 要求保留）。定位 = **同一漏斗、两个候选源**，产出两源的下游通过率对照；**不在关键路径**（P1-11，依赖 A7）。已知风险：CUDA 扩展未编译（无 `.so`）、`.venv` 无 pip、config 路径全指 1021 沙箱、smoke 输入 160×120 而相机是 1280×720 → 2.0 人日是**乐观值**。⚠️ 其「定位二选一」（候选质量消融 vs 可复现性论证）**尚未裁决**（`TODO §9-12`），不裁则 P1-11 完成判据为空。

---

## 4. B 组 · 执行期微调工具

> ⚠️ **BLK-5 阻塞**：L4 verifier 与 L5 corrector 两个运行期模型工位当前**不启用**。`DECISIONS.md` 的 **D-01「执行期零 LLM」状态仍为生效**，`PROPOSAL §5` 已声明撤销其口径但 **D-18 未落账**；且 `TODO §9-1` 明确：**这不是补一条 D-18 就能了结的记账问题，它改变论文相对 ReKep/CoPa/VIA 的主张**。
> **纪律：架构先建好（类型、签名、限幅、隔离全部就位），工位不启用。** 落点 P2-03。

| 工具 | 参数 schema | 限幅规则 | 为什么存在 |
|---|---|---|---|
| `look` | `{cameras:[str]?, note:str 必填非空}` | 同一 observation 的同一相机**在机器人动之前不许重复拍**；每节点 ≤3 次 | 视觉**按需付费**而非每帧白送——我方是多视角，膨胀速度是 cap-x 的 4 倍 |
| `nudge` | `{delta_mm:[3], delta_deg:[3], note:str 必填}` | 单步 **≤20mm/≤5°**；节点累计 **≤60mm/≤15°**；次数 3/10；≤上限直发 / ~3× 则 clamp 记 `clamped` / **>3× 拒绝**记 `rejected`；连续 2 次 rejected → 终止本节点交回 gate | 唯一执行出口，体固定 EE 局部系，底层 `ctrl:local_delta_move`（参数最全、⚙零调用）。**累计上限是「用一串合法小增量实现新目标」这条绕过路径目前唯一的封堵** |
| `set_grip` | `{state:"open"\|"close", note:str}` | 只有二值，**不暴露连续开度** | 与 demo-graph 的离散约束对齐；成功判定接 `reasoning:pick_verifier` ✅ |
| `stop` | `{action:"done", summary}` / `{action:"give_up", reason}` | — | 必填字段**故意不叫 `note`**：`note` 只在「会动/会看」的工具上强制 |

**`NudgeResult` 是 B 组的核心返回体**：`applied_mm/deg` · `clamped` · `rejected` · `residual_mm` · `residual_axis` · `converged` · `compensation_rounds` · `narrative`。三条硬要求各对应一个实测坑：① `/run?action=ctrl` 是 **fire-and-forget**（handler 丢弃 `ControllerFuture` 并硬编码 `result=True`），「动作做完没有」这个信号运行时**根本不存在** → 必须用 `get_xquat` 收敛 + `get_ee_extforce` 自建完成检测；② `ctrl:delta_move` **严重欠行程**（指令 0.02m 实走 0.002–0.005m）→ 必须「发增量 → `get_xquat` 回读 → 补差」≤3 轮，**绝不开环信 `ok=true`**；③ residual 拼成自然语言喂回模型（现 `rt.residual` 是软 stub，只 log 后返 `{'kind':'residual'}`，零计算）。

**隔离机制（分级要诚实，只有一条是真结构性的）**：**真结构性**——`contract.Runtime` 不含 corrector/verifier API ⇒ policy **在语法上无法调用**（`contract.py` 不改即成立）；**类型级**——`Delta`/`Verdict` frozen dataclass 且无公共基类，`gates.evaluate()` 首行 `isinstance` 见 `Delta` 直接 raise；**签名级**——`corrector.build_input` 无形参能接住 `Verdict`；**信息级**——corrector 只拿 `target_spec` 全量谓词，拿不到判定结果；**事后作废级**——`assert_isolation(ledger)` digest 交叉断言 + corrector 之后必须重新 `observe()` 才允许调 verifier。⚠️ `harness/llm.py:24` 读**单一** `OPENROUTER_API_KEY`：要么真配第二厂商 key，要么**明说隔离只到「进程 + 静态门禁 + 事后作废」级，不许继续宣称「分 API key」**（`TODO §9-7`）。

---

## 5. 洞类型 → 接口链派发表

`vocab.py:25` 定义 5 型（注意是 `axis_3d` 不是 `axis`）。`solve()` 按 `type` 派发，落在 `harness/binding.py`（P0-02）。

| 洞 `type` | 接口链 | **禁止** |
|---|---|---|
| `pose_se3` | `propose_grasps` → `filter_feasible`（硬可行性，**唯一允许淘汰的层**）→ `rank_by_preference`(region/cone) → `select` → 退 standoff → 不透明 `Handle` | 禁「物体中心 + 常量偏移」 |
| `point_3d` | `segment` → `get_depth` → `mask_to_points` → `compute_obb` → 目标面中心 | **禁读 `/state` 实体位姿** |
| `axis_3d` | `compute_obb` 主轴（优先）/ `get_object_pose` 的 quat 导出 | 禁硬编码 `[0,0,1]` 兜底且不打标 |
| `scalar` | 从 `compute_obb.extent` 导出 | **禁常量偏移**（`kwadapter.py:317` 的 `value=0.05` 必须归零） |
| `runtime_condition` | `read_state.extforce` + `verify_predicate` 几何自证 | **禁 oracle 谓词当停止判据**（`kwadapter.py:566` 的 `root_in_bbox ∧ axis_aligned` 必须拆掉） |

找不到洞 → `raise UnsolvedHole`，**不回退到猜**，归因记「漏斗 L2_bind」。候选空集 → `REJECT_ALL`，**不静默退化、不放宽阈值重试**。

**⚠️ 反面教材（架构评审点名）**：`harness/kwadapter.py:305` 的抓取点 = 物体中心 + 常量 `top - 0.03`。一次踩三个雷：数值来自常量而非感知 / 物体高度一变就错 / E-A6 字面量扫描器会命中。**第一条设计律：粗标签是排序器与筛选器，不是生成器；精确 6-DoF 只能来自候选生成器。** 反了不但数值错，连 E-CAUSAL 都变同义反复（候选生成本身消费了标签，「改标签 → 行为变」证不出因果力）。同类还有 `:22` 的 `GRIP_CLOSE=160`（越界被 `max_angle=100` 截断）。

**⚠️ 语义口径（`PROPOSAL` v3.1「排序偏好」裁决）**：漏斗 L2 对 **region 已改排序语义、不再产生任何淘汰计数**；**`cone` 是否同改排序偏好尚未裁决**（`TODO §9-4`），落账前 cone 保留 `half_angle` 硬阈值，「25° 从哪来」的质询面**仍然敞着**，不得对外宣称已消除。另：**MVS 阶段实际运行的漏斗只有两层（L1+L2），任何报表不得写成三层**（漏斗 L3 下游反推由 P1-09 承载）。漏斗 L1/L2/L3 与 `PROPOSAL §3.1` 信息流 L1–L5 **是两套编号**，交叉引用必须带前缀。

---

## 6. 模块与文件布局

**两条已有裁决不得违反**：① **不建 `phase0/` `phase1/` 子包**（`DECISIONS.md:178`；改用 docstring 阶段标签，`head -1 harness/*.py` 即归属图；建子包会静默破坏 `util.py:11` 的 `HARNESS_ROOT` 锚点）；② **`contract.py` 内容不可轻改**（`DECISIONS.md:201`；它整段进编译提示词）。⚠️ `DECISIONS.md` 存在 **D-11/D-12/D-13 的 ID 重复**（`§0` 索引表 `:26-27` 里同名 ID 指向另外两条裁决），**引用一律带行号，按 ID 检索会拿到错的条目**。

| 新增文件（全部平铺在 `harness/`） | 职责 | TODO |
|---|---|---|
| `binding.py` | `solve()` 按 `type` 派发 + 消费本阶段 `constraints`（因果链的接点） | P0-02 |
| `regions.py` | region/cone → **任务无关单调偏好函数** + 硬可行性筛 | P0-03 |
| `predicates.py` | 约束 → 三值 `PASS/FAIL/UNKNOWN` + margin | P0-05 |
| `apilevels.py` + `contract_g2.py` / `contract_g1.py` | 三颗粒度注册表与契约变体（**受未决 #2 阻塞**） | P0-07 |
| `robotapi.py` | 8 helper；lint 断言 `is_gripping_sth` 出现在本文件外即 fail | P0-08 |
| `perception.py` | 口径统一门面（四元数 / box / OBB，见 §7） | P0-09 |
| `graspfunnel.py` | 漏斗 + tie-break + **每层 in/out 计数落盘** | P0-10 |
| `bounds.py` | 纯函数限幅 + 两级仲裁 | P0-11 |
| `targets.py` · `episode.py` · `evidence.py` | 目标谓词生成 / 双工位边界与隔离 lint / 多视角取图唯一入口 | P0-12/13/18 |
| `corrector.py` | L5 工位（**BLK-5，先建不启用**） | P2-03 |

**`kwadapter.py` 怎么收缩**：**624 → <400 行，只留 IO 与委派**（P0-16）。三块外迁：数值绑定 → `binding.py`、区域/锥体语义 → `regions.py`、约束判定 → `predicates.py`。**不许砍测试**（现只有 36 条，先红后绿是唯一护栏）。

---

## 7. 坐标与单位约定

| 项 | 约定 | 踩坑证据 |
|---|---|---|
| 坐标系 | **`world == robot_base`**；`get_xquat` 本来就是 base 系 | — |
| 四元数 | 门面**对外一律 WXYZ**；上游 `qwen/sam/bbox_xquat` 与 `ctrl:*` 全链路 **XYZW**；**只在门面边界转一次**，内部不再转 | 三处混用：控制器/reasoner=XYZW、WebUI `list_scene_assets`=WXYZ；`mp_head_to_target.yaml` 注释写 wxyz 但真实调用方喂 XYZW（过期注释） |
| OBB extent | **全边长**（不是半长），需有断言 | cap-x 用全长、GaP 用半长；用半长算归一化坐标 `s` 会让所有 region 带整体偏移一倍**且不报错** |
| box | 门面内部**统一像素**（1280×720） | `qwen_xquat` 返像素框、`bbox_xquat` 吃 0..1000 归一化——✅实测同一组数字当像素喂会定位到**另一个物体** |
| 单位 | 运行期模型 I/O 用 **mm/deg**；运行时内部用 **m/rad** | 量纲错位是实测最常见的模型错误 |
| 外参 | head 外参 = 相机→基座静态变换、**不补偿 neck 转动**；left/right_hand 外参 = 相机→末端局部系，要再合 FK | 一旦用了 `neck_lookat`/`neck_set_angle` 这份外参就不再对——已埋好还没炸的雷 |

---

## 8. 三个必须先修的破口（全部排进 P0，不进 P1）

| # | 破口 | 位置 | 落点 |
|---|---|---|---|
| ① | **GT 防火墙破在控制原语内部**（不是 evaluator 里）：`lower_until` 用 oracle 谓词 `rt.probes()` 的 `root_in_bbox ∧ axis_aligned` 当停止判据；`lift` 读 oracle 实体位姿判 attached | `kwadapter.py:566` · `:522-577` | P0-02 / **P0-14**（改用 `get_ee_extforce` ✅ 空载≈1N / 触桌≈57N + `get_xquat` z 收敛） |
| ② | ~~**`verify()` fail-open**：未检查项 `detail="unchecked"` 仍 `ok=True`，异常也 `ok=True`；`passed` 由（旧名不副实的）`constraints_hold` 与出 ⇒ 「**根本没检查**」与「检查通过」在报告里是同一个值~~ **【已修 P0-05】** 五处 fail-open 全部归零:`kwadapter.verify3`/`verify` 与 `gates` 走 `harness.predicates` 三值(PASS/FAIL/UNKNOWN + margin);检查不了 = UNKNOWN 记账(`n_unknown`/`unknown_frac`),不再静默 True;`effect_status` 显式记不可观测=UNKNOWN。 | `kwadapter.py` verify/verify3 + `gates.py` _verify3/effect_status（原五处) | P0-04 / **P0-05** ✅ |
| ③ | **5 个契约参数静默丢弃**：`align.axis`（**最要命**——`align:542-545` 与 `transport:537-540` 实现只差 `ALIGN_DZ=0.06` vs `PREGRASP_DZ=0.10` 一个常数，而生成的 policy 调 `align` 达 **24 次**）、`align.obj`、`lower_until.stop_condition`、`transport.obj`、`approach.cone` | `kwadapter.py` | **P0-15**（消费或显式 `UNSUPPORTED` 记账，二选一，**不许继续静默**） |

**为什么必须在 P0**：此刻还没产任何真 episode，去特权成本最低；一旦开始产 ep 再改验收通道，**硬边界 1（在线模型输出绝不进验收）直接判该批数据作废**。

---

**关键文件锚点**（行号 2026-07-31 复核）：`harness/contract.py`（53 行，`:19` 单参 `solve`）· `harness/kwadapter.py`（**624** 行 → <400；`:22`/`:305`/`:317`/`:566`/`:616`/`:618`）· `harness/gates.py:110`（`passed` 赋值；**上一版写 `:111` 是错的**）· `harness/vocab.py:22-25`（region 6 值 / cone 3 值 / hole 5 型）· `harness/compilepolicy.py:83`（`inspect.getsource`）· `method/demo_graph/candidates.py:76-127` · `harness/PHASE1_API_PLAN.md`（📋 是计划不是现状，12 个 API 零实现）· `harness/DESIGN_GRASP_AND_LOOP.md`（抓取与闭环设计裁定）· `docs/reference/PRIMITIVE_API.md`（⚠️ 其 USABLE/BLOCKED 分级是 **KSM 观察契约层**口径、**对直连 pipeline 不成立**——被判 BLOCKED 的 `get_ee_extforce`/`sam_xquat`/`existence`/`pick_verifier`/`hand_pick_refine` ✅实测全部可调；按那份文档做差集会系统性低估自己的可用面）。
