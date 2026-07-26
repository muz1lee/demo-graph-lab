# 进度总账：demo-graph-lab 约束图线

项目方案见 `AGENTS.md`，算法细节见 `ALGORITHM_PLAN.md`。本文件只记「跑了什么、结果是什么、下一步」。
最后更新 2026-07-26 13:10。路径若无前缀均相对本仓根目录；主机、密钥与本地 runtime 路径只保存在
被 Git 忽略的 `configs/local/`。

## 硬边界更正（2026-07-26 用户紧急纠正）

- **唯一技能迭代场地**：1022 `/mnt/data/wenqian/demo-graph-lab`（本地镜像：本仓）。对外名
  `demo-graph-lab`，不是 ksm。
- **1024 NAS 基础仓** `/mnt/nas/knowin_sim/sim_workspace/`：只可只读借用数据 / venv；**禁止**
  写入、部署、改配置、启停服务。
- **历史事实（已作废）**：曾误部署到 `.../sim_workspace/services/ksm`——保留此记录以免再犯，
  **禁止再做**；任何「去 1024 部署/跑本项目」的旧 plan 条款一律作废。
- GitHub 远程为 [`muz1lee/demo-graph-lab`](https://github.com/muz1lee/demo-graph-lab)
  （由旧名重命名）；本地 `origin` 已指向新 URL。

**一句话状态**：模块化主方法与 adapters 包已在本仓落地（本地新测 `54 passed`）；WHT
`knowin-skill-manager 90 passed, 1 skipped`，`video-perception 2`，`grasp-proposal-tools 7`；
`robot-subtask-seg` 因本机缺 `av` 未能收集用例。此前在错误边界上的只读 probe：有 grasp
candidate，缺顶层 `tube_axis`，place 报 `point cloud insufficient`。现已在仓内修复解析：
嵌套轴字段 + 由抓取 xquat 推导水平轴，并显式记录 `holder_pose_error`。下一步仅在 1022
对本仓 pipeline 复跑只读 probe 验证几何字段；**未经用户明确允许不发控制 / 不跑 grasp**。
未再产生非特权 scored trial；不能把 fake smoke 或旧特权诊断当任务效果。
M1 agent 实际跑完了 **5 个 trial**（10:23–10:54，与方向审计并行，冻结决定未及送达；
盘上已核实 5 个 `trial_*/` 各含视频与记录）。最好成绩 trial 5 到**第 4 阶段**：
抓住✓（40 mm 测试提升 gate）、提起✓ 124.9 mm、转正✓ 7.34°、对准✓ 1.42 mm，
下插✗（depth_ratio 0，管底停在顶板上方 11 mm，下插只走了 100 mm 中的 33.8 mm）。
两点更正：① 盘上最终记录的 P1 判据是测试提升，`is_gripping_sth` 存布尔 `false` 且标注
「不作为判据」，此前 tmux 中间态看到的 `bool("False")` 问题在最终记录不存在；
② 但按 10:39 信息隔离边界回溯，trial 5 的孔心用了父图特权世界坐标、gate 用了特权 snapshot，
所以 5 个 trial 全部归类为**先于契约的特权诊断**，不是主方法成绩。机制性发现（重力转正、
枢轴夹持、qwen_xquat 竖直盲区）见「已钉死的事实」。
旧 trial 到此冻结。谓词已有 1 个明显正例 + 3 个明显负例回归，v2 spec 足够用于当前评价，
不再追插入毫米数；下一次执行必须走新的非特权 API 边界。

## 0. 当前方向（2026-07-26 09:52 老板拍板）

- **大方向不变**：`ALGORITHM_PLAN.md`——演示视频 → 带约束图的子任务分解。核心约束四件事：**抓取的 DoF、放置的 DoF、抓取的位置、放置的点**。
- **效果优先，简化任务**：原话「我们要不就先实现抓一个试管放置也行，就先简化吧」。当前里程碑 **M1 = 单管：抓取 + 空中转 90° + 对准（+ 下插尝试）**，分五阶段漏斗打分（抓住/提起/转正/对准/插入尝试），容差放宽到孔半径量级。单管顺带绕开实例判别硬伤。
- **仿真谓词的精度问题降级为「之后要修」**：原话「不是无休止去看最后插入多少 cm 或者 mm」。打分以阶段漏斗 + 视频为准。
- **新颖性问题挂起**：原话「demo2code 这个方案我不知道有没有创新性……但先做出效果吧」。CaP-X 对照结论见第 3 节，先不纠结定位。
- **grasp DoF 的候选方案（老板提出）**：视频难直接提 DoF → 用 GraspNet 出多个 candidates，把演示关键帧 + candidates 一起喂给 VLM 选最像的。即 typed-hole 的 solver 之一。M1 先用简单顶抓，GraspNet 只查可用性。
- **API 层**：cap-x 的原子 API 可以按真实缺口逐个补，以薄转发为界；当前不建设大而全的协议层。
- **信息隔离边界（10:39 拍板）**：graph generator、候选选择和 executor 只能使用演示、任务语义、
  运行时传感器/感知 API、机器人反馈及其可追溯推导结果。禁止读取 scene/asset library、精确
  simulator pose/DoF/AABB/尺寸、ground-truth instance/mask、孔位和 evaluator 答案，也禁止把
  这些量换名包装成 perception API。仿真真值只进隔离的 evaluator、sanity check 和 oracle 上界。
  主方法图必须保留 provenance 依赖链，任何依赖 `privileged_oracle` 的字段都应被拒绝。
- **WHT 资产已沉淀为 components**：已有算法先保持原样，我们的新方法与 adapter 分目录增加。
- **进度纪律**：每个里程碑更新本文件；新窗口依次读 `AGENTS.md`、`ALGORITHM_PLAN.md` 和本文件。
- **当前运行状态**：没有新的 scored trial 在执行；下一步是在 **1022 本仓** 对本地
  pipeline 跑 `run_m1.py --mode probe`，确认 `tube_axis_source` / `holder_pose_error`
  诊断字段；runtime doctor 也只针对 1022 侧 endpoint。1024 NAS 仅可只读借用，勿写入部署。

### 方向审计增量（2026-07-26 11:01）

- **统一主仓已建立**。WHT KSM、视频拆解、CoTracker wrapper 和 GraspNet wrapper 分别位于
  `components/` 下；逐文件来源、hash 和排除项见 `components/SOURCE_MANIFEST.json`。
- **视频管线尚未产出正式 constraint graph**：14 个 refined 目录和
  `demonstration_bundle.json` 已有，但 `insert_tubes` 仍是 6 段粗 trace，缺 grasp region/DoF、
  reorientation、axis/clearance、postcondition 和 recovery。
- **GraspNet 尚未接任务**：当前证据只是 synthetic RGB-D/mask → 5 proposals；没有真实相机链、
  模型实例 mask、IK/collision filter、demo/downstream ranking 或执行。
- **ZYH 路线只作 clean-room 参考**：其强项是候选可视化和事件追踪，不是 demo-derived task
  constraint graph；不复制其动态 swarm 或 raw executor。
- **新颖性结论**：Demo2Code、single-video task graph、semantic-geometric graph、GaP、
  AgentChord 和 2026 人类视频学技能工作已覆盖 broad framing。暂定可防守机制是：
  demo 提取非度量 task intent；runtime perception 填 typed holes；后续放置/插入约束反向影响
  当前 grasp；同一约束同时进入 action 与 verifier。
- **榜单结论**：RoboDojo 当前很难且适合作为目标，但本地 KW/KSM 是内部任务适配，不是官方
  Isaac Sim/XPolicyLab evaluator；截至审计时公开提交页仍是 Coming Soon。一个月先做 6-task
  mechanism suite，官方 adapter/full sweep 是 stretch goal，不能把 internal score 称为榜单成绩。
- **信息边界修正**：`ALGORITHM_PLAN.md` 中原先允许从 asset 几何/物理先验拿 DoF 和力阈值的两处
  已删除。DoF 只能由演示/通用先验提出并经运行时感知验证；力阈值只能来自机器人通用安全上限和
  有界主动探测。

---

## 1. 实验总账

| 编号 | 目的 | 结果数字 | 结论 | 产物 | 核实 |
|---|---|---|---|---|---|
| **PREDICATE_V2_REGRESSION** | 判断 v2 是否已“够用”，避免继续调毫米 | 1 个 oracle positive 判 True；horizontal-in-rack、upright-but-uninserted、inverted-in-rack 3 个明显负例均判 False；`restore_ok=true` | v2 已满足当前评价需求：`inserted` 与显式 `orientation(+y,+z)` 能互相兜底。停止谓词调参；未改全局 evaluator、物理配置或项目代码 | `runs/predicate_v2_regression_20260726_102557/{REPORT.md,results.json}` | ✅ 4/4 |
| **M1 trial 1** | 首次单管抓取→提起→重定向 | tube1 `z_rise=116.72 mm`；lift 后长轴距竖直 2.8°；reorient 后变成 53.36°，未进入 align/insert。`is_gripping_sth` 与 `still_gripping` 均为字符串 `"False"`，却被 Python `bool()` 判真 | 该 trial 不能用于成功率。两条直接结论：① funnel 先做类型规范化，P1 必须要求真实 grasp retention；② reorient 必须重新观察**物体轴**后再决定是否/如何旋转，不能只让 wrist 到目标姿态并假设物体刚性随动 | `runs/m1_single_tube_20260726_095818/trial_1/`（含 `third_person.mp4`） | ❌ funnel 无效，P3 实际失败 |
| **M1 pre-flight** | 在真正 trial 前核对动作 frame、旋转闭环和 reach | `delta_move` 实测为 world frame；`local_delta_move` 为 EE frame；`local_rotation_move` 命令 17.19° 实到 13.51°。arm 0 的 tube0/1/2 grasp-height 误差分别 39.98/37.65/98.42 mm，均未达到 20 mm gate；tube1 pre-grasp 误差 4.89 mm，far_0 pre-insert 误差 16.69 mm | 32 原语动作面可用不等于当前 grasp pose 可达。已有 M1 graph 选择 tube0 的“xy 最近”理由被实测 reach 结果削弱；在冻结抓取 pose/arm 前不得跑整链。**没有有效 scored trial、没有成功率结论** | `runs/m1_single_tube_20260726_095818/{code,probes,logs}` | ⚠️ 仅 pre-flight |
| **M1 · 5 trials** | 单管「抓取+空中转+对准+下插尝试」全链 | t1 停 2（主动转向 2.8°→53.4°）、t2/t3 停 0（IK/抓取状态机）、t4 停 2（重感知给水平轴，8.05°→68.5°）、**t5 停 4**（不发转向指令：7.34° / 1.42 mm / depth_ratio 0，管底停顶板上方 11 mm）。全程未调 offset；子图关系 38.1% / 数值 61.9%，`release_tcp_z` 由关系算出 0.845 与母图一致 | 「抓取+空中转+对准」效果拿到，机制是**重力+抓点在质心上方**而非腕转；剩余瓶颈是下插（只走 33.8/100 mm，机器人/仿真侧）。失败分账：图/策略仅 1 项（误设刚性抓取）。**按 10:39 信息边界归类为特权诊断**：孔心用了父图特权坐标（rack 感知误差 25 mm > 孔半径 14.9 mm，solver 退化到 fallback）、极性兜底一次、gate 用特权 snapshot。t5 记录里 `adjudication=entered_hole` 是判据 bug，以 depth_ratio 0 为准 | `runs/m1_single_tube_20260726_095818/{REPORT.md,funnel_summary.json,trial_*/third_person.mp4}` | ✅ 盘上核实 5 trial + t5 数字 |
| **PREDICATE_AUDIT** | 原 task spec 的谓词能不能用 | 手摆成完美插好状态仍判 fail。`upright` 对横躺管 3.98–4.65°（PASS）、对竖直管 89.1–90.8°（FAIL）；`depth_ratio` 物理上限 0.53（架子 z 跨度 0.0637 / 管高 0.1207）；`robot_home` 恒 False | 原 spec 成功率**上限就是 0.0**，与策略无关。v2 spec：`orientation(+y,+z,15°)` + `min_depth_ratio 0.4`，删 `robot_home`，不加 `settled` | `PREDICATE_AUDIT.md`、`audit/`、`task_specs/insert_tubes_000_v2/task.yaml` | ✅ |
| **B4** | oracle 图 → 确定性编译 → 端到端执行 | **0/3**，三次逐位一致（方差 0），谓词 0/6；29 条 compile gap（blocking 10） | 图信息不足，不是调参问题。缺三类：物体初始位姿、实例可判别的感知 label、物体系抓取位姿。夹爪在横躺管上方 **7.0 cm** 闭合 | `runs/b4_oracle_20260726_013708/REPORT.md`、`compile_gaps.json` | ✅ |
| **B4 probe** | 「执行成功」是否等于「任务成功」 | 空断言变体 **70/70 步**无报错跑完，手臂正常回 home，**场景零变化** | 两者完全脱钩。不能用执行完整性代替任务成功 | `runs/b4_.../probe_vacuous_postcondition_assert/` | ✅ |
| **B5** | LLM 读图写代码 A/B 消融 | n=**3**/组。三槽位区分 A 0/3、B 0/3；图字段用上 1.0 vs 2.0；policy 2/3 vs 3/3 | 负面。查出混淆变量：「按 label 拿位姿」在原 harness 上**没有任何合法通路** | `runs/b5_llm_codegen_20260726_014304/REPORT.md` | ✅ |
| **B5.1** | 补掉混淆变量后重测 H1/H2 | n=8/组。三槽位：A 0/8、B 0/8、Aaug 0/8、**Baug 5/8**、**B′ 5/8**（Fisher p=0.0256）。Baug 平均图字段 1.57→4.5 | H1（散文→数值）、H2（能力缺口）均成立，**2×2 交互**：能力和图缺一不可。同时**撤回** B5 的「对象绑定 1/3→3/3」——n=8 下 3/8 vs 4/8，p=1.00，未复现 | `runs/b5_1_mechanism_20260726_020925/REPORT.md` | ✅ |
| **B7 · F1** | LLM 是不是只照搬格式对的数字 | ±0.06 → **±0.30 m**（架子半宽仅 0.108 m）。**0/8 拒绝、0 修正、0 提及**；条件照搬率 **3/3**。三槽位 5/8→3/8（p=0.6193，不显著）；**加物理闸门后 5/8→0/8（p=0.0256）** | F1 成立：数值零物理校验。三份照搬样本会把管松在离架子边缘 0.192 m 的空桌面上，其中一份还担心"偏移不够大" | `runs/b7_falsify_20260726_085823/REPORT.md`、`probes/gated_metric.json` | ✅ |
| **B7 · 主指标** | 指标本身可信吗 | `three_distinct_slots` 只问三个目标互不相同、不问是否在架子里 | **主指标是坏的**，会把「把管扔到架子外」判成功。物理闸门（偏移在架体内 + 间距 ≥ 管径）应成为默认指标 | 同上 | ✅ |
| **B7 · F2** | 一句自然语言够不够 | C（144 字符）**0/8**，但 8/8 都复述了要求、**3/8** 声明 `blocked_by_gap`；C′（316 字符 = 一句话+三个数+一句 frame 声明）**8/8**，物理闸门后仍 8/8 | F2 字面不成立（失败的是**可表达性**不是信息量），但强化版成立且更伤：**整张 10837 字符的图（5/8）输给 316 字符（8/8）**，边际价值为零、点估计为负 | 同上，`probes/c_prose.txt` | ✅ |
| **B7 · Baug** | 唯一正面线索 | 图只给规则不给数值 + harness 给能力，模型**自己推出 0.04/0.05/0.06**，全落在架体内，物理可行 **4/8** | 63 份产物里唯一一处「推导」而非「照搬」几何量。但只有 5 份样本，且依赖尚未进正式技能库的 `locate_by_label` | 同上 | ✅（样本量小） |
| **B7 · 更正 B5.1** | driver 记录有偏 | B5.1 报的 B 组 `declares_blocked_by_gap` = 1/8，从原始响应重算是 **3/8** | `max_attempts=1` 下 policy 违规会抛异常、不写 metadata，系统性漏掉失败最多的组。**只在此处更正，B5.1 run 目录原样不动** | `probes/roles7.txt` | ✅ |
| **B8** | 原语级消融（禁人写 pick&place 包装） | A/B 消融已取消。动作面已打通：32 个原语的 before/after 表——`local_rotation_move`、`follow_xquat_trajectory`、全部 8 个 info 原语原本**都不可调用**，现已全部可用；三个禁用包装已确认不可调 | A/B 部分无结果（结论已被 CaP-X 发表，见第 3 节）。**打断重设的原因要记住**：第一版原语清单残缺，漏了 `local_rotation_move`（就是空中转 90° 的那个原语）和整组 info 原语（`is_gripping_sth`/`get_ee_extforce`/`get_last_grasp_outcome`/`get_xquat`，闭环的唯一手段），照那个清单跑必然得出「LLM 写不出重定向/闭环」的伪结论。旧样本作废但保留标注 | `runs/b8_primitive_20260726_091658/probes/primitive_availability.txt` | ⏹️ |
| **B6** | 图 v2 + 端到端重跑 | 图 v2 与独立的 provenance 文件已产出（09:15），三个 trial 目录**空**，执行前被老板中止（09:43） | 无执行结果。图 v2 使用实测孔心 `far_0/far_2/far_4`，provenance 含 `asset` / `task_spec` / `harness` / `hand_tuned`，因此按现行信息边界只能作 **privileged oracle/诊断上界**；主方法最多复用 schema，不能复用字段值。M1 此前派生的单管图也必须在运行前做 provenance 清洗 | `runs/b6_graphv2_20260726_085724/insert_tubes_000.graph.v2.yaml` + `.provenance.yaml` | ⏳ |
| **slotgeom** | 量真实孔位几何 | 数据已出，当前无进程在跑：架子实为 **10 个孔、5×2 点阵、孔距 0.036 m**、孔径 0.02982 m、容差 0.94 mm。图里 `[0,-0.06,0]`/`[0,0,0]`/`[0,+0.06,0]` **三个全部脱靶**，最近孔差 16.7–18.0 mm = 容差的 17.8–19.1 倍；穷举 3601 yaw × 11 锚点证明**不是坐标系 bug**。演示实际用远侧行隔一个孔（等效间距 0.072 m） | **此前所有「三管进三孔」的成功数字，量的都只是「写出了三个互不相同的数」，没有一个会真插进孔** | `runs/slotgeom_20260726/probes/geometry.txt`、`frame_agnostic.txt`、`timeline.txt` | ✅ 数据可用 |

### 已钉死的事实（不要再重复查）

- **谓词**：试管长轴是物体系 **+y**（不是 +z）。`settled` 在 KSM 打分路径下是恒真空操作（`WebUIEntity.get_vel()` 硬编码返回 0；实测 1.6 m/s 自由落体仍判 settled），别用。
- **`is_gripping_sth` 恒假**（M1 实锤）：夹爪电流没被仿真（恒 ~4，判据要 >80），实际提起管子 50 mm 仍返回 false。与 `settled` 恒真是一对。抓取验证只能用「测试提升看物体跟不跟着走」。
- **夹持是枢轴不是刚性连接**（pose in hand）：纯竖直提升中管倾角自己变 ~80°，腕转不传递给管。但抓点在质心上方（0.65）时重力会让管自动垂成竖直、盖朝上——**转正的正确做法是不发转向指令**（母图 `derived_not_commanded` 被执行证实）。主动转向两次把 2.8°/8.05° 打坏成 53.4°/68.5°。
- **`qwen_xquat:dof` 结构上无法报告竖直物体**：PCA 主轴投影到水平面、帧 z 钉死为世界 −z，对已竖直的管返回水平轴。夹住后重感知姿态这条路对竖直物体不通。
- **顶抓姿态 IK 姿态误差 20–44°**，`xquat_move` 只查位置容差、照单执行妥协解。闭环时不要重发理想姿态，用 `delta_move` 保持已达姿态只补位置。
- **GraspNet**：wrapper 已迁入 `components/`，但服务尚未接当前 runtime；缺相机→基座变换、
  IK/collision filter 和 demo-conditioned candidate selection，不能把“能返回 proposals”写成任务效果。
- **坐标系与动作语义**：arm 0 的**定位服务输出帧**与世界系对齐（返回点距 tube1 中心 15 mm）；
  M1 probe 另行确认 `/ctrl/delta_move.delta_xyz` 是 world frame，
  `/ctrl/local_delta_move.delta_xyz` 是 EE frame。定位结论仍只覆盖 arm 0，arm 1 无数据；
  `local_rotation_move` 是 EE-local rotation，且存在实到角小于命令角的误差，需闭环读回。
- **感知层分不清三根同型试管**：查询 `tube0_prop:dof`，返回点距 tube0 **415 mm**、距 tube1 **15 mm**。约束图修不了。后果：所有槽位指标的准确名称只能是「代码是否表达了槽位区分的意图」。
- **harness 限制**：KSM `_is_private_reasoning_name` 用 `("qw"+"en") in value.lower()` 过滤，把**全部三个**「label→位姿」服务从 prompt 隐藏；另有 4 个定位服务**可见但不在契约里**（纯陷阱）。契约判定是「被稳定技能调用过才算数」，`follow_xquat_trajectory` 因此被拒。薄包装 `locate_by_label.yaml`（30 行，只转发 `qwen_xquat`）可解且不改变 `endpoint_arg_contracts`。
- **±0.06 的 provenance 曾经标错**：真实来源是 rack AABB（仿真特权几何）+ 图自己的避碰净空，却标成 `provenance: demo_video`。图 v2 已修。
- **架子碰撞体实际失效**（`has_collision_prims: false` + `convexify: true`），试管会穿过顶板。当前真正在 gate 的只有「xy 落在架子投影内 + 姿态竖直」，**几何精度在这个仿真里测不出来**。

---

## 2. 实验编号对照表

`ALGORITHM_PLAN.md` 的 B1–B4 是**四个对照组**（B1 只有指令 / B2 演示→纯文本 plan / B3 演示→约束图 / B4 人工 oracle 上界），今晚的 B4–B8 是**流水号**。只有 B4 碰巧重合。**不改矩阵定义，不重命名 run 目录，用这张表翻译。**

| 今晚流水号 | 矩阵位置 | 说明 |
|---|---|---|
| B4 | **B4**（oracle 上界） | 但用确定性编译器而非 LLM，比矩阵定义更严格 |
| B5 / B5.1 / B7 的 **A** 组 | **B1**（只有指令） | = wht 的 baseline |
| B5 / B5.1 / B7 的 **B** 组 | **B4** 的 LLM 版 | 注入的是人写 oracle 图，不是自动提取的图，所以**不是 B3** |
| B7 的 **C**、**C′** 组 | **B2**（演示→纯文本 plan）的极简版 | C = 一句话；C′ = 一句话 + 三个数 + 一句 frame 声明 |
| B5.1 的 **B′** / **Aaug** / **Baug** | 方法学检查 | 变量是「图的呈现形式」和「harness 能力」，不在矩阵的信息量轴上 |
| B7 的 **B″** | 方法学检查（伪证对照） | 故意给物理离谱的数值 |
| B8 | 方法学检查·**新轴**（动作抽象层级） | 矩阵没有这条轴；对应 CaP-X 的 S1–S4 |
| B6 | **B4** 重做 | 用修正几何后的图 v2 |
| PREDICATE_AUDIT / slotgeom | 方法学检查（基础设施与真值标定） | |

**矩阵 B3（自动提取的约束图）一次都没跑过。** 现在全部用人写 oracle 图，wht 的提取管线没接上。

---

## 3. 外部工作对照（CaP-X, arXiv 2603.22435, ICML 2026）

引证均已对着论文全文核对；括号里标出与口头转述不一致的地方。

- **B8 的结论已经被发表了。** §3.1 定义 S1–S4 四层抽象（Table 1）；Takeaway 2 原话 "Figure 3 shows a **monotonic increase in task success as primitive abstraction increases**"，摘要里配套那句是 "improve with human-crafted abstractions but degrade as these priors are removed, **exposing a dependence on designer scaffolding**"（这两句出自不同位置，不是同一句）。Figure 18 标题 "In-Context Examples Boost Performance by +20%"，即 S4→S3。规模：**抽象轴的消融跑的是 7 个受控任务 × 每格 100 trials**（187 是 CaP-Gym 完整套件 = 7 Robosuite + 130 LIBERO-PRO + 50 BEHAVIOR，不是抽象轴的样本量）；"12 个模型"我在全文里没核到确切数字，Figure 3 图例列出 9 个模型名。我们 B8 是 **1 任务 × 1 模型 × n=8**。**所以 B8 只能当 conditioning 变量，不能当 finding。**
- 顺带：B5.1 的 **Aaug** 组无意中做了半个 B8（补齐原语能力但不给图，三槽位区分 0/8），方向与 Takeaway 2 一致。
- **他们的 skill synthesis 没有覆盖我们。** 全文列出的九个合成函数（`rotation_matrix_to_quaternion`、`depth_to_point_cloud`、`transform_points`、`select_top_down_grasp` 等）八个是纯坐标数学、一个是通用几何启发式，论文自称 "9 verified, **task-agnostic** primitives"。它去的是样板代码的重，不产生任务结构。（附录编号：正文指向 **H.1**，不是 G.1；G 是各抽象层的 API 细节。）
- **"结构化"这个卖点撑不住。** Takeaway 3 显示原始 RGB（M2）反而比纯文本（M1）差、VDM 的自由文本（M3）最好；我们自己 B7 里 316 字符自由文本打赢 10837 字符结构化 YAML。**押注方向与我们自己的数据相反。**
- **唯一真正站得住的差异点是失败信用分配。** 案例研究原文："the model **retroactively implemented a fallback which only prevents the failure case it just encountered**"（附录编号：正文指向 **F.2.4**，不是 E.2.4）；Takeaway 3 里成功的 agent 靠自己插打印语句猜状态；他们的解法是并行集成（堆算力），而专门的 debug prompt 反而掉分（Table 5：68.29 → 65.43）。**但我们零证据**——B5 / B5.1 / B7 全是 `max_attempts=1` 单次采样，信用分配一次都没测过。
- **Future Works 一节把我们的核心卖点写成了他们的一句话计划**："incorporating **optimization-based control primitives that allow agents to specify task-level constraints** and account for collision avoidance during motion planning"。不构成 prior work，但将来写 intro 必须正面引。
- **对我们有利的两条**：同段承认 "remains brittle for **contact-rich** behaviors that require tight visual servoing and continuous feedback (e.g., **insertion** or pouring)"——插入是他们自认的软肋；附录自曝 "queries of 'alphabet soup can' to SAM 3 often results in segmentations of the 'tomato sauce can'"——和我们三根同型试管的实例判别失败是同一个病，他们也没解决。
- **另外要自省一条**：B4 的 oracle 图没有按 `ALGORITHM_PLAN.md` 自己的「typed hole」理念写。`world_z_offset_hint: 0.075` 是在"管竖直"隐含姿态下量的**世界系标量**，不是带类型和搜索域的洞；横躺场景下它直接导致夹爪在管上方 7 cm 闭合，0/3。

---

## 4. 待办与未解问题

1. **当前唯一主线 next todo（仅 1022）：** 在 `/mnt/data/wenqian/demo-graph-lab` 同步本仓改动后，
   对 1022 本地 pipeline 跑
   `python3 experiments/insert_tubes/run_m1.py --mode probe`，核对
   `tube_axis_found` / `tube_axis_source` 与 `holder_pose_error`。1024 NAS 只读可借、禁止写入。
   **grasp 真执行须用户再次明确允许**。失败归因只能落在 perception / reach / grasp，
   不得使用 scene pose、固定孔心或 evaluator fallback。holder 点云不足修好后，再接
   reorient/skip、align、insert。
2. **不扩写大而全 API/协议**：只补当前 M1 真正调用的 perception、info、ctrl 薄接口。
   代码以一个 graph、一个 Python runner 和一个 Knowin adapter 为主。
3. **D 组 / Baug″ / B6 已中止**（09:43），未出结果。若将来恢复：D 的判据是 ≥5/8 则约束图价值归零到负；B6 的图 v2（实测孔心版）只能用于隔离 oracle 上界，不能进入主方法。

4. **用真实孔位回溯重算 B5.1 / B7 的所有槽位指标**，闸门从「在架体内」收紧到「落进某个孔的 0.94 mm 容差内」。预期几乎全部归零，这个结果要如实写。
5. **补一个约束校验环节**。F1 证明 LLM 对注入数值 100% 照搬、零物理校验，所以自动提取一旦有几何误差，会被原样编译进代码且无人察觉——这把"提取质量"从工程问题升级成了安全性问题。三条可做的路各 8 次调用：提取侧对着场景 AABB 校验 / 注入侧把几何约束翻译成 `assert` / prompt 侧明确要求量纲范围检查。
6. **失败信用分配一次都没测过**，而它可能是相对 CaP-X 唯一站得住的差异点。现在全部实验是 `max_attempts=1`。
7. **矩阵 B3 从未跑过**，提取管线没接上。全部结论建立在 1 个任务、1 张人写图、1 个模型（gpt-5）上。

### 小的未解矛盾

- 管身半径两处测得不一致：0.01457（B6 provenance）vs 0.01397（slotgeom），对应单边间隙 0.34 mm vs 0.94 mm。
- 0.4 阈值余量两处不一致：`PREDICATE_AUDIT` 用仿真 AABB 算 15.5 mm，slotgeom 用资产几何算 8.2 mm。
- 演示里「哪根管进哪个孔」的逐管配对没逐帧核死，只核到「三根都在远侧行的第 0/2/4 列」。
- 具体 SSH、部署路径和端口只保存在 `configs/local/`，不进入公开仓。
- `gripper close_current_max = 160` 仍是 hand_tuned，视觉演示提不出力。
