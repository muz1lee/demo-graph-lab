# STACK：技术栈冻结定案

- **建立日期**：2026-08-03
- **上位裁决**：**D-24**（`docs/DECISIONS.md`）——技术栈冻结 + D-23② 仿真预演撤销
- **用途**：**本表是选型的唯一权威源**。「这一槽用什么」只看本表，不看 `PROPOSAL.md` / `ARCHITECTURE.md` / `TODO.md` 的行文（那三份写的是「为什么」和「怎么排期」，不是「用哪个」）。
- **读法**：按层看，每行一个槽位；**状态列**是本表的硬信息，措辞不得含糊。

**状态图例**（与 `TODO.md` 的证据分级同源，混用即失效）：

| 记号 | 含义 |
|---|---|
| ✅实测 | 已在 5090 或本仓跑过，有输出 / md5 / 日志可核 |
| 🔧建设中 | 已定选型、代码未通或未接线 |
| ⚠️待解 | 已实测但存在未解决的失败面，**不得当作可用** |

---

## 1. 基础设施层（上游给定，零污染原仓 D-12）

| 槽位 | 定案 | 状态 | 出处 |
|---|---|---|---|
| 仿真主体 | `/home/knowin-sim/knowin_sim`（5090 现役） | ✅实测 | D-21 证据栏（5090 侦察 2026-08-03） |
| 机器人模型 | `k1u_v4_w_claw_26w27_1d` | ✅实测 | `harness/EP2_REPORT.md` 抬头 |
| 场景 / 任务 suite | `scenes/robodojo_v4/…`，**37 suites** | ✅实测 | D-21 证据栏 |
| 评测判据 | **仿真内置 robodojo 评测**（不自建判据） | ✅实测 | D-21；两层不混报纪律见 D-20⑥ |
| 特权态读口 | EvalServer `GET /state`（**只进 evaluator / gate，方法路径禁读**，D-04） | ✅实测 | `harness/EP2_REPORT.md` §0 |
| 观测面 | WebUI **4 源 × 5 视图**（含腕相机 `left_hand/left`），全 200 | ✅实测 | `docs/ARCHITECTURE.md:66`；腕相机开合证据见 `docs/STATUS.md` B5 行 |
| 原语通道 | pipeline `:8000` → `reasoning:*` / `ctrl:*` / `info:*` | ✅实测 | `docs/ARCHITECTURE.md:66` |

> **零污染纪律（D-12）**：以上全部**不改**。我方一切改动落在 `harness/` 与门面层；上游 bug（如 `visual_processor.py` 的 `pixels_base3d` 分派）绕过，不修。

---

## 2. 离线层（编译期，一次性）

| 槽位 | 定案 | 状态 | 出处 |
|---|---|---|---|
| 约束提取（demo → 约束链） | **Opus**（`anthropic/claude-opus-4.8` via OpenRouter） | ✅实测 **micro P=0.931 / R=0.865** | `docs/STATUS.md` §2.1；`harness/PHASE0_ROUND2.md` |
| 约束链 → policy 一次编译 | **Opus**，分层生成 + 静态校验（度量字面量扫描） | ✅实测 | `harness/compilepolicy.py`；D-03 纪律 |

> **离线层不受 D-23① 运行时 VLM 回归影响**：编译一次、冻结复用这条脊椎不变（D-01 撤销的只有「执行期零 LLM」这一口径，见 D-01 状态栏）。

---

## 3. 运行时层（逐槽定案）

| 层 | 槽位 | 定案 | 状态 | 备注 / 出处 |
|---|---|---|---|---|
| L3 | **分割** | **SAM3** `192.168.20.212:5081 /segment_raw`（passthrough，**不移植 cap-x 8114 服务端**） | ✅实测在网 | `docs/ARCHITECTURE.md:75` A6 |
| L3 | **位姿** | `qwen_xquat` / `bbox_xquat` **双轨**，两轨差 >1cm 记 `disagreement=True`，**不静默取一** | ✅实测（两路互测差 3.5mm） | `docs/ARCHITECTURE.md:72` A3；⚠️ box 口径坑：`qwen_xquat` 返像素、`bbox_xquat` 吃 0..1000 归一化，混喂会定位到**另一个物体** |
| L3 | **抓取候选主源** | **GraspNet**（代码在 1022 服务器 wht 工作区；**部署勘察进行中**） | 🔧建设中 | **D-24①**：由 P1-11 对照臂**升为主源**；已知风险：CUDA 扩展未编译、`.venv` 无 pip、config 路径指 1021 沙箱、smoke 输入 160×120 vs 相机 1280×720（`docs/ARCHITECTURE.md:84`） |
| L3 | 抓取候选备用 / 对照 | `qwen_dof_xquat` 的 `topk_pick_records_by_arm`（**禁用其内置排序**，保留原始 `rank` 只作审计） | ✅实测可调 | **D-24①ordinal 降级**：由主源降为备用源与对照臂；接线清单见 `harness/EP2_REPORT.md` §5 |
| L3 | **深度 / 点云链** | `get_depth`（bulk）→ `mask_to_points` → `compute_obb` | 🔧建设中 | **D-24②立即建设**；`get_depth` 是 A 组**唯一真开发**（重），`mask_to_points`/`compute_obb` 为纯 numpy 自建（`docs/ARCHITECTURE.md:74-77` A4/A5/A7；原 P1-05/06/07） |
| L4① | 硬可行淘汰 + 偏好排序 | **三层漏斗** `harness/graspfunnel.py`（L1 硬可行唯一淘汰层 / L2 region·cone 偏好序） | ✅已测（单元测试 `tests/test_graspfunnel.py`） | 真候选源接线点见 `harness/EP2_REPORT.md` §5.4 |
| L4② | **下游前瞻** | **`compat(c,c')` 几何兼容性谓词**（无碰可达 / 转移可行），**采样近似**，由 **T-BP** 承载 | 🔧建设中 | **D-24③：仿真状态克隆预演已砍**。回归 v4 §2.1 原方案 $F_i(c)=\mathbf{1}[c\models\Phi_i]\cdot\max_{c'}\mathbf{1}[\mathrm{compat}(c,c')]\cdot F_{i+1}(c')$；`graspfunnel.run_funnel` 的 `downstream_rank_fn` 目前传入即抛 `NotImplementedError`，**接入前不要顺手塞排序** |
| L4③ | **VLM 深思** | **OpenRouter Opus**（`anthropic/claude-opus-4.8`）；候选覆盖层渲染 + demo 关键帧对照**排序**，**必须引用支持该选择的约束**；**不打分、不出连续量** | 🔧建设中 | **D-24④**：运行时 VLM 供应商定案。T-SEL |
| L5 | **运动规划** | `reasoning:motion_planning_stereo`，**每 stage ≤ 1 次** | ✅实测 | `harness/robotapi.plan_joint_path` / `execute_path` |
| L5 | **流式执行** | 替代逐航点 settle 轮询 | ✅实测 **70.6 s → 40.0 s**；顺带把终点精度 **6.7 cm → 1.4 mm** | `harness/EP2_REPORT.md` §4 |
| L5 | **伺服残差补偿** | MP 后笛卡尔回读补差（「关节收敛 ≠ 笛卡尔到位」） | ✅实测 **9.5 cm → 8.6 mm** | `harness/EP2_REPORT.md` §1.3 |
| L5 | **夹爪** | `set_gripper{arm_id, angle}`，**语义 0=闭 / 100=开**（实测派生，与旧文档相反） | ⚠️待解 | ✅实测：40 / 60 档**能夹住**（回读角停在大于指令角处 = 指垫被物理挡住）；**⚠️ 抬升会打滑，根因未查（PI 叫停）**。唯一可信回读 = `get_sensor_info(key="angle")` 第 7 位；`/state` 的 12 个爪子分量对 `set_gripper` **毫无响应**（恒 ±1.188），拿它判开合会得到假结论 |
| L5 | **接触感知** | `get_ee_extforce`（解析已修：返回值是 numpy `str()` 形态，须过 `_as_numbers`） | ✅实测 | touch test 通过：空中 **0.22–0.40 N** → 接触 **100.54 N**，指尖停在距管心 **2.4 mm**（`harness/EP2_REPORT.md` §1.1/§1.4）。⚠️ `docs/reference/PRIMITIVE_API.md` 判它 BLOCKED 是 **KSM 契约层**口径，**对直连 pipeline 不成立** |
| L6① | **验收（判定）** | **几何谓词 gate 三值**，`passed` 由 `harness/gates.py` 计算 | ✅实测 | **硬边界不变**：方法路径任何组件不参与成败判定 |
| L6② | 验收（证据） | **视觉差分文本**（前后帧差分成文字，**禁回灌原始图像**）→ **Opus**，只出证据**不出 `passed`** | 🔧建设中 | CaP-X 实测回灌原图更差（`PROPOSAL.md` §4.3 纪律） |
| L7 | **修正** | **有界 VLM 修正** → **Opus**；输出 `{-1, 0, +1} × 固定步长`；**gate 边缘触发**；不生成连续量、不改目标、不参与验收 | 🔧建设中 | T-COR；D-23① 复活的 L5 修正层 |

---

## 4. 运行时模型供应商（D-24④）

| 项 | 定案 |
|---|---|
| 供应商 | **OpenRouter** |
| 模型 | **`anthropic/claude-opus-4.8`** |
| 出现位置 | **三处且仅三处**：L4③ 选择深思 / L6② 验证证据 / L7 有界修正 |
| 纪律 | **不打分、不出连续量、不改目标、不参与验收**（`passed` 永远由 `gates.py` 几何谓词算） |
| 记账 | 成本 / 延迟表**仍要报**（每集调用次数、wall time、$/ep），但定位是**如实记账**，不是卖点（D-23③④） |

---

## 5. 已砍除槽位（留档，不得复活）

| 槽位 | 处置 | 依据 |
|---|---|---|
| **仿真状态克隆预演**（fork Genesis 状态虚拟执行候选） | **砍除** | **D-24③**。PI 理由：已在仿真中运行，再克隆一个预演**太重**，且有**循环论证嫌疑**。下游可行性检查降级回 `compat(c,c')` 几何兼容性谓词（v4 §2.1 采样近似），由 **T-BP** 承载 |
| 在线 corrector（旧 P2-03 形态） | 不恢复旧编号 | D-18 砍除 → D-23① 以 **T-COR** 新形态复活，判据按 v5 §2.3 重写 |

---

## 6. 冻结纪律（本表的效力条款）

1. **本表为唯一选型权威源。** 任何文档与本表冲突，以本表为准；`PROPOSAL.md` / `ARCHITECTURE.md` / `TODO.md` 只解释「为什么」与「怎么排」，不定「用哪个」。
2. **执行任务书第一行引用本表。** 每份下发给执行 agent 的任务书，第一行必须写明「选型依据 `docs/STACK.md` §<节>」；没有这一行的任务书不许开工。
3. **改选型须新增 DECISIONS 裁决。** 不许在本表就地改槽位定案；改动路径 = 先在 `docs/DECISIONS.md` 加一条新裁决（D-16 纪律：旧条正文不删，状态栏标撤销面），再回填本表。
4. **上机任务必须带最小验证门（infra-first）。** 任何上机任务书须包含一个 **touch-test 级**的最小验证门——即「先用一个廉价、判据唯一、能证明这条通道真的活着的动作」把 infra 验穿，再跑方法逻辑。
   - **为什么立这条**（D-24 背景，EP-2 三层 bug 为佐证）：① 接触力解析坏掉后**伪装成「这个仿真没有力反馈」**，还长出一整套兜底逻辑——**一个坏掉的读数比没有读数更危险**；② `CLAW_TIP_DZ` 的错值正是**从坏掉的力读数里标出来的**——**坏基线会自我繁殖出坏常数**；③ 「关节已收敛」被当成到位证据，实际末端横向偏 **9.5 cm**。三条都不是方法问题，全是 infra 问题，却消耗了整轮实验。
   - **交叉校验义务**：标定常数必须用**两个独立基准**互证才敢定值（`CLAW_TIP_DZ` 最终用「桌面高度 + 管子半径」两基准互证）。
5. **接口返回值形态先验证再消费。** 任何上游接口第一次接入时，先把原始返回值原样打出来看一眼形态，再写解析（`get_ee_extforce` 的 numpy 字符串形态即前车之鉴）。
