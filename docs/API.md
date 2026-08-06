# API 分层

这个项目有三层 API。最重要的规则是：VLM 只看高层动作，不看底层数值控制；是否成功由独立 gate 判断。

这里的 backend model 专指通过 `common/llm.py` 调用的生成式 VLM/LLM。对象检测、分割、抓取 proposal 等模型属于非特权感知层，不等同于 backend model。

## Backend model 在 workflow 中的位置

当前 workflow 分成三条相互隔离的路径：

```text
离线语义路径：demo → backend model → graph → policy
在线方法路径：sensor → perception / candidates → selection → control
独立评测路径：observation → predicates / gates → verdict
```

### 当前已经实现的调用

| 步骤 | Backend model 的输入 | 输出 | 调用条件 |
|---|---|---|---|
| 阶段切分 | 全视频采样帧、任务指令 | `stages_proposed.json` | 只有上游 trace 缺失时调用 |
| 对象 registry | 全视频采样帧、trace 中的对象别名 | `objects.json` | 每个 demo 一次 |
| 约束抽取 | 单阶段关键帧、指令、对象 registry | `constraints / acceptance / holes` | 每阶段调用 `k` 次，再确定性合并 |
| Program 提议 | 已校验 graph、`RuntimeAPI` 源码 | `StageProgram`：primitive sequence + hole wiring | 每个 graph 一次；backend 不写 Python |
| PerceptionProgram 提议 | `StageProgram` 接线出的几何 hole 契约、从代码渲染的算子闭集与 resolver 绑定表 | `PerceptionProgram`：感知链组合 + hole 发布 | 每个 graph 一次；只在 `StageProgram` 发布后调用，无可发布 hole 时不调用 |
| StageProgram 修复 | 已校验 graph、当前 `StageProgram`、一份失败 episode 的确定性摘要 | 一句失败归因 + 修订版 `StageProgram` | 每个 run 目录最多 3 次；只在有失败 episode 时调用 |

表中这 **6 个调用点**是 backend model 在整个 workflow 中被允许出现的全部位置（此前是 5 个，`StageProgram` 修复是新增的第 6 个）。新调用点和其余五个受同一条边界约束：它输出的是受限 JSON，不是 Python、逐步参数或任何数值——model 改的只是自己那份 program 的动作序列与 hole 接线，边界细节见第 8 节。

视频读取、trace 解析、关键帧采样、graph 补全和校验都不调用模型。在线 hole 求解、候选排序、运动执行、predicate 和 gate 目前也没有 backend model 调用。`OracleRuntime` 只读取 simulator 状态，同样不调用模型。

推荐的完整离线顺序是：

```text
video + optional trace
  → deterministic ingest
  → trace stage split；无 trace 才由 VLM 提议
  → deterministic keyframes
  → VLM object registry
  → VLM per-stage graph extraction
  → deterministic merge / enrich / validate
  → backend StageProgram proposal
  → deterministic validation / Python compilation
  → AST check + fake dry-run
  → backend PerceptionProgram proposal
  → deterministic validation + fake perception dry-run
  → (episode failed) backend StageProgram repair
  → same deterministic validation / compilation / dry-run gate, published beside the original
```

Backend 输出始终是不可信 proposal。stage、registry、constraint sample、`StageProgram` 和 `PerceptionProgram` 都经过严格 schema；无效 sample 不参加投票，分母固定为请求次数。同名阶段的约束只有达到严格多数才传播。`StageProgram` 只决定高层 primitive sequence 和 hole/object 接线，validator 检查动作顺序、API 签名、hole 类型与 purpose、对象引用和数字字面量，可信 compiler 再生成 Python。`PerceptionProgram` 只决定哪条闭集算子链发布哪个几何 hole，validator 检查链的类型衔接、字段与 hole 类型、resolver 绑定、`(stage, hole)` 唯一性和数字字面量，fake runtime 再干跑一遍；它未通过时不发布，`StageProgram` 与 `policy.py` 的发布不受影响。每次调用的脱敏请求、raw reply、parsed result 和 validator 结论都保存在 `model_calls/<tag>/`；同 tag 的再次调用保留在 `history/`，不会把不同请求和回复混在一起。

### 当前建议的在线顺序

第一条非特权 baseline 不调用离线 backend model。Qwen 与 SAM3 只作为在线感知 proposal source，不能确认 graph identity、写三维几何或控制。当前有两条彼此独立的验证路径；它们尚未连接成完整 stage solve：

```text
单 anchor component record：
graph resolver + anchor + head RGB-D + fixed info reads
  → frozen optical-frame observation
  → Qwen single-box proposal → SAM3 binary mask
  → validated anchor request + MODEL_PROPOSED binding + mask-first cloud/lineage
  ├─ grasp_candidate → object-only raw GraspNet response
  ├─ principal_axis → local PCA
  └─ part_center / part_axis → local support-plane geometry PASS / UNKNOWN
  → STOP：尚无 frame transform、candidate 或 solve() 接线

独立 synthetic contract replay：
synthetic fixture candidates
  → strict candidate adapter
  → observation-bound typed-hole validation
  → reachability / collision / width hard filter
  → deterministic region / cone ranking
  → PlanningOnlyRuntime 写 decisions.jsonl
  → solve() 返回选中 bundle 的 opaque handles
  → 所有 control primitive 抛 ExecutionDisabled
```

`ObservationPacket` 只接受 sensor artifact 引用、对象观测和显式 `Proprioception`；不接受任意 `robot_state` mapping。EEF pose 另带 `end_effector_frame`，不能默认继承 camera frame。record adapter 拒绝 schema 漂移、非有限数值、数值位置的 bool 和空证据。Candidate 必须携带同一次 `observation_id`，并把会改变数值语义的规范化步骤写入结构化 `provenance`；几何 hole value 使用精确的 `{value, frame, calibration_ref, object_id}` envelope，pose 顺序固定为 `[x,y,z,qx,qy,qz,qw]`。frame 必须同时等于 observation、object observation 和 graph hole 的 frame；V1 没有隐式 transform/alias resolver。标定、对象、shape 或单位向量检查不通过时，不会调用 IK、碰撞或宽度 checker。

几何 graph hole 现在可以声明闭集 `resolver` 和结构化 `anchor={object_id, part, instance?, selection?}`。live contract 要求 anchor 引用 registry 对象、frame 为 `robot_base`，resolver 与 hole type 匹配。`StageProgram` 只保存 primitive 使用的 hole name；可信 helper 再用这些名字从 graph 查询 resolver/anchor contract。在线感知仍在 optical frame，因此这些字段只表达请求的语义归属，既不是已观测的实例身份，也不能代替相机外参或把 optical 数值直接改标签为 `robot_base`。

`StageProgram` 的 hole wiring 决定每阶段真正需要哪些值。`PlanningOnlyRuntime` 可以直接接收已经校验的 program 并据此收窄 required holes；没有 program 时保守要求该 stage 的全部几何 holes。`scalar` 和 `runtime_condition` 只能来自另一条可信 runtime resolver；candidate provider 提供这两类值会 `FAIL`，需要但尚无可信 resolver 时为 `UNKNOWN`。

`planning-record` 提供九个显式步骤：`plan / capture / ground / segment / project / predict / programs / identity-accept / project-base`。`plan` 零网络；`capture/predict` 分别要求 `--allow-live-read`；`ground/segment/programs` 分别要求 `--allow-model-read`（`programs` 会代表每个感知程序调用 Qwen 与 SAM3，属模型读）；`project / identity-accept / project-base` 只做本地计算，后两个见第 7 节。没有一键入口。`programs` 另外要求 `--perception-program` 指向已发布的 `perception_program.json`，把 manifest 从 `OBSERVATION_RECORDED` 推进到 `PROGRAMS_RECORDED`；它和 `ground/segment/project/predict` 互斥地消费同一次 capture，任何一条路径推进了状态，另一条就不再接受这个 record 目录。capture 的同步 render、camera cache 更新和 frame-id 增量写入 `sensor/call.json`。每次 Qwen/SAM3 请求、raw reply、校验结果和耗时分别保存在 `grounding/` 与 `segmentation/`，token 不写入 artifact。`project` 的 `object/result.json` 中 `status` 由几何证据派生：请求了几何但其 `opening_geometry_status` 不是 `PASS` 时为 `GEOMETRY_UNKNOWN`，几何 `PASS` 或本次未请求几何时才是 `ACCEPTED`；record 本身确实发生，因此 manifest 的 `OBJECT_CLOUD_RECORDED` 和退出码不受影响。

`ground/segment/project/predict` 这条 V1 链一次只处理一个 graph anchor，它不能在同一个 observation 下同时组装 tube grasp/axis 与 opening center/axis。`programs` 是那个「以 capture 为父 observation、以 anchor 为子任务」的结构：同一次冻结 capture 下按 `PerceptionProgram` 逐程序执行，每个程序有自己的 anchor、自己的 Qwen box、SAM3 mask 和几何产物，互不共享中间量。它发布的值停在 optical frame；接到 `PlanningOnlyRuntime.solve()` 的是第 7 节那两个本地步骤（`project-base` 做变换、`identity-accept` 开身份闸门）。没有它们时 `programs` 的产物只是 per-hole component artifact，不是 stage candidate。

真实点云保留在 OpenCV head optical frame：`+X` 右、`+Y` 下、`+Z` 前，单位米。可信代码先在 mask 上筛 depth，再同步生成 `Nx3` object cloud 与 `Nx2 (row,col)` lineage；`object_assignment.json` 记录 observation、被请求的 graph anchor、frame、calibration 和 Qwen/SAM3 evidence，但 identity 状态固定为 `MODEL_PROPOSED`。opening center/axis 由局部 RGB-D 对比与开口周围 ring 的局部支撑面重新计算，证据不足返回 `UNKNOWN`，不采用模型 pose。GraspNet 只消费 object cloud，raw detector ID 原值保留；仓库仍不发布 GraspNet→graph candidate converter。identity 接受与 lift-aware `camera_head_optical → robot_base` 变换见第 7 节；`graspnet_parallel_jaw → runtime_ee` 变换仍未做，因此 grasp 洞不走那条路径。

在线 selector 不接受没有 frame 的 `approach_dir`。上游必须先在有重力定义的 frame 中计算 `approach_tilt_deg ∈ [0,180]`；缺少某项排序特征时，对应 preference meta 是 `UNCHECKABLE`，只有部分候选有特征时为 `PARTIAL`，不能把 ID tie-break 误写成 demo ranking。固定 synthetic replay 已经验证一次 hard filter 后共享 accepted set 的 demo/no-demo 对照；这不是 live 感知或物理 checker 结果。当前 replay loader 只接受 `synthetic_contract_fixture`，在真实 provenance/manifest contract 完成前会拒绝任何 `recorded_real` 标签。完整 episode 稳定后，才允许 backend model 对已经通过硬过滤的候选做可选排序；它不能生成新 pose、复活被过滤候选或决定 gate。显式 `compat` 和向后检查也属于可信 selection。

实验需要区分两种模式：component mode 固定人工检查过的 graph 和 policy，只研究候选与执行；end-to-end mode 才重新从 demo 调用 backend 生成 graph 和 policy。选择算法对照必须共享同一 graph、policy、候选和执行预算。

```text
离线：backend VLM/LLM → graph + StageProgram
                              │
                              ▼
                       deterministic compiler → policy.py

在线：生成的 policy.py
          │
          ▼
      高层 Runtime API
          │
          ▼
    可信 runner 与填洞
          │
          ▼
   运动规划与底层 pipeline
          │
          ▼
        机器人

独立：动作前后观测 → predicates / gates → verdict
```

## 1. 给生成 policy 的高层 API

高层接口定义在 `src/demo_graph_lab/policy/api.py`。编译器会把这个文件的源码放进 VLM prompt。未出现在该类中的方法一律不能由生成 policy 调用。

### 感知入口

| 方法 | 输入 | 输出 | 失败语义 |
|---|---|---|---|
| `solve(hole_name)` | 当前阶段声明的 hole 名 | 不透明 handle | hole 未声明、类型未知或必需的对象观测缺失时直接报错 |

Handle 只允许传给后续高层动作。生成 policy 不能读取其中的坐标、角度或阈值，也不能自己修改它。

编译检查会拒绝 handle 下标、属性读取以及 `rt` 的非公开属性。例如 `handle["xyz"]`、`handle.xyz` 和 `rt.pipe` 都不合法。这个检查用于约束本项目生成的代码，不是执行任意不可信 Python 的安全沙箱。

graph 为未来的可信 resolver 层固定以下映射，生成 policy 不直接选择或调用这些模型。当前 component recorder 已能生成表中的局部 artifact，但尚未接入 `PlanningOnlyRuntime.solve()`；多 anchor assembly、identity 接受、frame transform 和 candidate normalization 完成后才能形成真正的 solve 路径：

| Graph resolver | 可信层数据路径 | 当前状态 |
|---|---|---|
| `grasp_candidate` | Qwen box → SAM3 mask → object cloud → GraspNet | raw proposal 已接，candidate conversion 未接 |
| `principal_axis` | mask-first object cloud → local PCA | record artifact 与 `programs` 执行器已接，robot-base transform 未接 |
| `part_center` / `part_axis` | opening ROI + local RGB-D contrast + support-plane fit | `PASS/UNKNOWN` artifact 与 `programs` 执行器已接，robot-base transform 未接 |
| `motion_derived` | 当前 EEF、持握状态和受检运动结果 | 未实现；不能退回视觉模型猜测 |

表中前三行的数据路径现在有了显式契约，见「6. PerceptionProgram v1」。

### 高层动作

| 方法 | 含义 |
|---|---|
| `approach(target, cone=None)` | 按离散接近方向靠近对象或目标 handle |
| `grasp_at(grasp_pose, axis=None)` | 在已求解的抓取位姿闭合夹爪 |
| `lift(obj)` | 抬起对象 |
| `transport(obj, target)` | 携带对象移动到目标附近 |
| `align(obj, target, axis=None)` | 在下放前完成对象与目标对齐 |
| `lower_until(stop_condition)` | 下放到运行时停止条件触发 |
| `release()` | 释放夹爪 |
| `retreat(target)` | 释放后的退离动作；当前只有 opcode/接线契约，Oracle 在可信 pose solver 完成前拒绝执行 |

`push` 当前没有可靠实现，因此不属于可用 API。需要支持推动任务时，应先实现和测试底层动作，再把它加入高层契约。

`retreat` 的 graph hole 目前只能证明 backend 显式选择了 retract/retreat 语义，不能证明数值 pose 安全。真正接控制前，可信 runtime 必须基于当前 EEF、接近路径和碰撞检查生成退离候选；不得回退到对象质心。当前 Oracle loader 会在 reset 和任何控制前拒绝含 `retreat` 的 episode；方法自身也保留 `NotImplementedError` 作为第二道硬停。

生成 policy 不能调用 `verify()` 或自行返回成功。阶段是否通过只由 runner 和 gate 决定。

这里的“独立”指 backend 不输出 `PASS / FAIL`，不表示验收规格完全与 backend 无关：当前 constraint extractor 会提议 `acceptance`，确定性 gate 再执行这些检查。正式任务成功必须另外使用固定的人工或 benchmark evaluator，不能只用模型自己提议的 acceptance，否则过弱的验收条件会让结果虚高。

## 2. 可信运行层

这一层不暴露给 VLM。当前包括：

- `begin_stage(stage)`：把 hole 解析限定到当前阶段；
- `runner.run_policy(...)`：按阶段顺序执行；
- typed-hole 求解和任务无关的 region/cone 偏好函数；
- `evaluation.gates` 和 `evaluation.predicates`：读取独立观测并给出 `PASS / FAIL / UNKNOWN`。

typed-hole 校验、硬过滤、排序和 fixed replay 已经存在，但真实的 reachability、collision、gripper-width checker adapter 尚未接入。跨阶段兼容性检查仍是后续功能。

当前 runner 失败后只重复同一个 handler。它不会 rollback、换候选或修改搜索范围。缺少 handler、hole 歧义和未知谓词都必须停止或返回 `UNKNOWN`，不能猜一个默认值继续执行。

gate 的三值合取里，只有 `predicates.UNCHECKABLE_IN_RUNTIME`（`carry` / `order`，本 runtime 结构上永远查不出的跨阶段状态量）这一类 `UNKNOWN` 被排除在 hold 合取之外，并记进 `excluded_uncheckable_keys`（同时保留在 `unknown_keys` 里）；它们全部被排除时合取里没有任何证据，acceptance 仍然不是 `True`。其他任何 `UNKNOWN`（谓词异常、缺少 `grasp_point` / `approach_dir` 这类输入、参照实体解析不到、词表外的名字）照旧阻塞判定。白名单之外不得新增豁免项：往里加名字等于让那条约束再也拦不住任何 stage。`region_grasp` 与 `approach_direction` 的输入由 runner 从 runtime 本阶段的执行记录取，经 `gates.snapshot / evaluate` 的 `ctx` 传给谓词；runtime 没有记录这些值时两条谓词维持 `UNKNOWN`。

当前 Oracle 从 simulator state 直接得到 world-frame 几何，因此数值 handle 明确标为 `frame="world"`，同时保留 graph 请求的 `requested_frame` 供检查。非特权 runtime 不能照搬这个捷径，必须使用相机与机器人标定完成真正的 frame transform。

Oracle 的 `scalar` 和 `runtime_condition` 可以返回延迟描述子；非特权 planning runtime 尚未实现对应 resolver，因此当前会 fail-closed，而不是让 candidate 或 VLM 猜值。插入阶段由 deterministic enrich 补 `purpose=lower_stop` 的控制洞；StageProgram validator 只允许这类洞接到 `lower_until`。它目前只声明应读取非特权 contact/motion-plateau 信号，不生成阈值；明确的停止信号路由仍是执行前 TODO。

### 只读真实记录接口

head RGB-D、Qwen/SAM3、object cloud、opening geometry 和 GraspNet `/predict` 已有同一条显式 read-only record 入口，但还不能报告为完整候选链：

- snapshot 的 depth 是 float32 米制左目 render depth；反投影后只保留 finite 且 `z>0` 的 optical-frame 点；
- Qwen 必须只返回一个合法 box，SAM3 必须返回与冻结图像同尺寸的非空二值 mask；零框、多框、全帧 mask 和 schema 漂移都 fail-closed；
- `bbox_1000 → bbox_pixel` 是覆盖式换算——min 边 `floor`、max 边 `ceil`，像素框完整覆盖 1000 制连续框；client 与 record 校验用同一份约定，不一致的像素框直接拒绝。mask 越出该像素框只容许每边 1px 量化抖动（分割器在量化边界上的像素级差，不是放宽分割质量），越出更多仍按越框 fail-closed；
- graph identity 只来自已校验 anchor，模型回复只作为 evidence；assignment 与 object cloud 由本地代码发布，GraspNet 实际只消费该 object-only `point_cloud_path` 与 `extra.max_grasps`；
- baseline 的 `pred_decode()` 把所有 `object_id` 固定为 `-1`，raw 记录可以成功，candidate normalization 必须 fail-closed；
- head camera 挂在可升降 link 上；改标签仍然禁止，真正的变换是第 7 节那个独立步骤，且必须带上与该次 observation 同时刻的 `q_lift`，拿不到就记 `UNKNOWN`；
- recorded reply 的 point-cloud ref、frame ID 和 coordinate frame 必须与当前 observation 精确一致，structured grasp fields 还要与原始 17D array 一致；
- point-cloud binding manifest、完整 projection manifest、pixel lineage 和 assignment 分开保存；grasp center 不能直接当 TCP pose；
- GraspNet 不做碰撞过滤，也不直接给 `approach_tilt_deg` 或 `height_fraction`；
- 现有 IK 会先 clip 越界目标，reachability checker 必须检查原目标和最终残差；
- 当前 motion-planning wrapper 丢失 planner success，不能签发可信 `PASS`；
- candidate width 是米，而 K1 只有 motor angle，未做 opening-width 标定前 width checker 必须返回 `UNKNOWN`。

因此当前状态 `OBJECT_CLOUD_RECORDED` 或 `OBJECT_RAW_GRASPNET_RECORDED` 只证明逐对象 evidence 可追溯；它还没有 camera/tool frame transform、graph candidate 或 hard-check certificate。不能用模型“被调用”或 geometry `PASS` 替代 grasp 可执行和任务成功。

### 执行前门槛

以下项目全部完成前，`PlanningOnlyRuntime.execution_enabled` 保持 `False`：

1. live observation/candidate/check adapters 的完整调用图确认只读、无 `/state` 和 control side effect；
2. 第一份真实 replay 能复现 typed binding、三个 hard-check 原因、ranking meta 和最终选择；
3. reachability 不接受 clipped target，collision 参数与 K1 对齐，width 有物理标定；
4. 一个非特权 stage 的 gate 输入和 abort 行为通过离线/仿真前检查。

候选 type/frame/calibration 校验和 synthetic fixed replay 已完成，只是上述门槛的合约验证，不等于真实链验收。

特权 Oracle 也不会只凭一个旧 `policy.py` 启动：加载器要求当前 validation 与 compile report 通过，graph 和 object registry 与编译快照一致，StageProgram 与 compile report 中实际 dry-run 的内容一致，并重新校验 program、确定性生成 policy 后做逐字比对。episode 中任一 stage 失败会返回非零。`retreat` 的可信 pose solver 未完成，因此即使其余产物通过，含该动作的 episode 也会在 reset 和任何控制前硬停。

## 3. 底层控制 API

底层实现位于 `src/demo_graph_lab/execution/`，只由受信任的 runtime 调用。

### `PipelineClient`

`pipeline.py` 封装 Knowin World `/run`：

- `info`：读取机器人状态；
- `reasoning`：调用运动规划等已有能力；
- `ctrl`：发送底层控制命令。

HTTP 接受请求不代表机器人到位。控制结果必须通过关节、末端位姿、夹爪或力反馈重新确认。

### `robot_api.py`

| 接口 | 输入约定 | 输出 |
|---|---|---|
| `plan_joint_path(...)` | 关节为弧度；Cartesian pose 为 `[x,y,z,qx,qy,qz,qw]`，位置单位米，frame 为 robot base | `PlanResult`，包含 7-DoF 航点 |
| `execute_path(...)` | `N × 7` 关节航点 | `ExecResult`，包含是否到达和终点残差 |

底层控制不接收任务名、对象语义或自然语言。它只处理明确的数值、坐标系和机器人状态。这能防止任务策略悄悄进入控制层。

## 4. 计划中的运行时 VLM 接口

以下接口尚未实现。它们会放在独立模块中，不加入生成 policy 的 `RuntimeAPI`。

它们不是当前 baseline 的依赖。加入顺序是：先打通无运行时模型的完整 episode，再做候选排序，最后才考虑有界修正。

### `rank_candidates`

输入：阶段约束、示范关键帧、带 ID 的候选卡片和可追溯观测证据。

输出：候选 ID 的排序、引用的约束、引用的证据 ID。不能输出新坐标或自造候选。

### `suggest_correction`

输入：失败约束、残差类别、允许调整的离散参数和执行证据。

输出：参数名、`-1 / 0 / +1` 方向、引用的残差。不能越过允许范围，也不能修改 policy 结构。

### `describe_change`

输入：动作前后图像与一个明确问题。

输出：可核对的文字证据，例如“对象仍在夹爪下方”或“目标口被遮挡”。它只提供证据，不能输出 `passed=true`。

## 5. Oracle 边界

`OracleRuntime` 会读取 simulator 的精确 `/state`，只用于集成调试和上界。它不能作为主方法 runtime，也不能为运行时 VLM 生成候选卡片或证据。

主方法允许的信息只有相机、点云、感知模型输出、机器人本体状态、力反馈和由这些信息得到的规划结果。精确对象 pose、AABB、instance ID 和官方 task probe 只能留在隔离评测侧。

## 6. PerceptionProgram v1

`PerceptionProgram` 是与 `StageProgram` 平行的第二个 backend model 产物：`StageProgram` 决定动作怎么接线，`PerceptionProgram` 决定几何 hole 由哪条感知链发布。它是独立编译产物（落盘 `perception_program.json`），不是 hole 的字段，graph schema 不变。契约实现在 `src/demo_graph_lab/perception/program.py`，干跑实现在 `perception/fake_runtime.py`，编译入口是 `dgl compile` 的第二段，真实执行器是 `execution/program_record.py`（`planning-record --step programs`）。

### 编译与发布门

覆盖目标不是 graph 里的全部几何 hole，而是 `StageProgram` 真正接线、且 `resolver` 落在可发布集里的那些；没被接线的 hole 这一轮不需要值。发布门是「零违规 + fake 干跑通过」，两者都过才写 `perception_program.json`。任何一步失败都只把 violations 写进 `compile_report.json` 的 `perception_program` 段，raw reply 与校验结论照常留在 `model_calls/compile_perception/`；未发布时那些 hole 继续走 graph resolver 老路，因此感知程序是纯增量，不改变 `StageProgram` 的发布结果或 CLI 退出状态。wired 几何 hole 里没有任何可发布目标时 `status=skipped`，不调用 backend。

### 文档形状

```json
{"schema": "demo_graph_lab.perception_program.v1",
 "task": "<task>",
 "programs": [
   {"stage": 0,
    "chain": ["localize", "segment", "fit_opening"],
    "provides": [{"field": "center", "hole": "<hole_a>"},
                 {"field": "axis", "hole": "<hole_b>"}]}]}
```

顶层与条目的 key 都是闭集，任何多余字段都是违规。程序没有 `name`：身份就是文档内索引，日志和报告里派生成 `p<stage>_<index>`。

### 算子闭集与类型表

v1 只有线性链，算子闭集如下，`consumes/produces` 是链上流动的中间产物类型，不是 graph hole 类型：

| 算子 | 消费 | 产出 | 发布字段 | 背后实现 |
|---|---|---|---|---|
| `localize` | `ANCHOR` | `BBOX` | — | single-box grounding client |
| `segment` | `BBOX` | `MASK` | — | binary-mask segmentation client |
| `fit_opening` | `MASK` | `GEOMETRY` | `center: point_3d`、`axis: axis_3d` | `estimate_planar_opening_geometry` |
| `crop_points` | `MASK` | `POINTS` | — | `project_masked_depth` |
| `fit_axis` | `POINTS` | `GEOMETRY` | `axis: axis_3d` | `operators.fit_principal_axis` |

链必须以 `localize` 开头（根是 `ANCHOR`）、逐步类型衔接、终点必须产出 `GEOMETRY` 字段。类型表本身是无环的，所以链里不可能出现回路。

### 执行

`planning-record --step programs` 在一次已冻结的 capture 上逐程序执行已发布的文档，按 `(stage, 文档索引)` 顺序。算子绑定与上表逐条对应：`localize` 走 Qwen single-box client，`segment` 走 SAM3 binary-mask client，`crop_points` 走 `project_masked_depth`（经 `build_object_point_cloud`，同时产出 `MODEL_PROPOSED` assignment 与 cloud manifest），`fit_opening` 走 `estimate_planar_opening_geometry`，`fit_axis` 走 `operators.fit_principal_axis`。客户端与几何实现都是注入参数，离线测试注假实现，生产注真实 client；执行器的算子实现集合由测试钉死等于 `OPERATORS` 的闭集，契约加算子而执行器没跟上会直接失败。

`localize` 的查询由与单 anchor record 相同的可信渲染器从 hole 的 graph anchor 渲染，model 依旧写不了任何文本。每个程序的 request、raw reply 和校验结果分别落在 `programs/p<stage>_<index>/{grounding,segmentation,geometry}/`，父 observation 的 JPEG 只冻结一份放在 `programs/observation_input.jpg`；token 不写入任何 artifact。

每个被 `provide` 的 `(stage, hole)` 在 `program_results.json` 里得到一条 envelope：

```json
{"value": [0.0, 1.0, 0.0], "frame": "camera_head_optical",
 "calibration_ref": "<...>/calibration/bundle.json", "object_id": "tube_mid",
 "identity_status": "MODEL_PROPOSED", "status": "PASS",
 "reason": "pca_dominant_axis", "failed_step": null,
 "evidence_refs": ["<...>"], "program": "p0_0", "collides_with": []}
```

`frame` 如实写测量所在的相机光学系。执行器**不做**任何 frame 变换，所以把它的 envelope 直接喂给 typed-hole 校验仍然会因 frame 不一致被拒——这是设计意图，不是缺陷。真正的变换是第 7 节那个独立的本地步骤，它需要额外输入（外参记录 + 与该次 observation 同时刻的 `q_lift`）；把 optical 数值改标签成 `robot_base` 依然是错误。identity 一律 `MODEL_PROPOSED`，执行器不做任何自动接受。

失败时 `status=UNKNOWN`、`value=null`，`reason` 是机器可读码（客户端拒绝如 `grounding_reference_count_not_one`，几何估不出来时直接沿用估计器自己的 reason 如 `insufficient_depth_contrast`），`failed_step` 指出链上断在哪个算子，`evidence_refs` 保留已经产出的证据。all-or-nothing 在这里是硬约束：一个程序的全部 `provides` 要么都有值，要么都是 `UNKNOWN`。一个程序失败不影响同一次 capture 下的其它程序。

### 跨程序身份守卫

上面那些守卫都在一个程序内部生效，因此拦不住这一类污染：同一次 observation 内，两个 `object_id` 不同的程序接受了**逐元素相同**的 `bbox_pixel`。此时这个框没有识别出其中任何一个对象（一个框不可能同时是两个 graph object），落在它上面的几何值来自哪个物体无从判断，所以同框的程序**全部**记 `UNKNOWN`、`value=null`、`reason=grounding_identity_collision`、`failed_step=localize`（被判定的正是 `localize` 交出的那个框），并在 envelope 与 program 摘要的 `collides_with` 里互相点名。判定精确相等、无参数：不用 IoU、不设阈值，只处理「两个身份压在同一份证据上」这一种情形。同一个 `object_id` 被多个程序各查一次而命中同一个框是**合法**的——一个 anchor 本来就可以被多条链观测——不受影响。

判定在全部程序执行完、写 `program_results.json` 之前做一次，所以「先发布后被撞」与「先被撞后发布」两个方向对称，与文档里的程序顺序无关。已经因为自己链上失败记了 `UNKNOWN` 的程序保留它自己的 reason（更具体的事实），冲突只由 `collides_with` 记账。`programs/p<stage>_<index>/` 下的产物**照原样保留**：链确实跑完了，那份记录正是这条判定的证据，降级只发生在 envelope 与 program 摘要上。program 摘要另外记下被接受的 `bbox_pixel`，让判定与它依据的证据出现在同一份文件里。

### 信息边界

- backend model 只做一件事：把闭集算子组合成线性链，并声明这条链的哪个字段发布哪个 hole。它不写查询文本、不写逐步参数、不写数值；
- 链的根是被 `provide` 的 hole 已经声明的 `anchor`，程序里不重复声明 anchor，也无法改写它。真正的 `localize` 查询由可信代码从 anchor 渲染，model 不提供自由文本；
- 同一个程序 `provide` 的所有 hole 必须共享逐字段相同的 anchor：一个程序只观测一个 anchor；
- 整个文档禁止数值字面量，除 `stage` 索引（指向 graph 的结构性引用）外任何位置出现数字或带单位的字符串都是违规。这条与 `StageProgram` 同规，是纵深防御，不依赖 key 白名单先拦住；
- hole 身份是 stage 内唯一的 `(stage, hole)`：同名 hole 可以出现在多个 stage，但同一个 stage 的同一个 hole 只能由一个程序发布；
- v1 只发布被观测到的对象几何，`resolver` 限于 `part_center / part_axis / principal_axis`。`grasp_candidate` 走候选身份与排序机制，`motion_derived` 的值来自执行状态而不是观测，两者出现在 `provides` 里都是违规；
- 声明了 `resolver` 的 hole 还必须由绑定的那条链发布：`part_center → fit_opening.center`、`part_axis → fit_opening.axis`、`principal_axis → fit_axis.axis`。类型相同不等于语义相同——开口平面法向与点云 PCA 主轴都是 `axis_3d`，只比类型就能互换，而它们测的不是同一个量。绑定表 `perception/program.py::RESOLVER_BINDINGS` 是这条语义的单一真相源，键恰好是可发布 resolver 的全集；没有声明 `resolver` 的 hole 维持类型匹配即可；
- 失败语义是 all-or-nothing：链在任何一步失败，该程序的 `provides` 一个都不产出。部分成功会让上层以为 hole 已填，是 bug 不是可接受的降级；
- 身份判定跨程序生效：一个 pixel box 只能属于一个 graph object，两个 `object_id` 不同的程序接受同一个框时双双 `UNKNOWN`；同一个 `object_id` 的重复查询不算冲突。graph anchor 的 distinguisher 必须在单帧里可判——`localize` 只看得到一张冻结图，写成时序或历史描述（「第三个被插入的」）时模型只能退化成某种空间描述，退化的终点就是这条守卫；
- 未被任何程序覆盖的几何 hole 不是违规，它们继续走 graph resolver 老路。`coverage_by_stage` 只产出 per stage 的 covered/uncovered 名单供记录，不做准入判断。

## 7. camera → robot_base 变换与 base 系候选

第 6 节的 envelope 停在 `camera_head_optical`，而 graph 的几何 hole 请求 `robot_base`。本节是这两者之间那一步：它是**本地计算**（零网络），输入是一份已测量的外参记录、一次已冻结的 record 目录，以及一份独立的 identity 接受记录。实现在 `perception/frames.py`（变换与标定 schema）与 `execution/program_projection.py`（逐洞投影、身份闸门、runtime provider）。

### 标定记录 schema

`demo_graph_lab.camera_extrinsics.v1` 是闭集记录，字段全部必填：

```json
{"schema": "demo_graph_lab.camera_extrinsics.v1",
 "frame_from": "camera_head_optical", "frame_to": "robot_base",
 "axis_convention": {"x": "right", "y": "down", "z": "forward"},
 "rotation": [[...], [...], [...]],
 "translation": [0.097078, 0.037055, 1.161351], "translation_unit": "meter",
 "lift_dependency": {"link": "lifting_link", "joint_type": "prismatic",
   "axis_base": [0, 0, 1], "limits_m": [-0.35, 0.0],
   "q_lift_assumed": 0.0, "correction": "translate_base_origin"},
 "method": "...", "provenance": {"calibrated_at": "...", "operator": "...",
   "source_refs": ["..."]},
 "validation": {"table_normal_angle_deg": 0.055,
   "table_height_residual_m": 0.00069, "evidence_refs": ["..."]}}
```

`rotation` 与 `quaternion_xyzw` 只能给一个（同一件事的两种写法，两个都给就要回答哪个是真的）。校验包含 `SO(3)` 成员资格与 `det ≈ +1`：镜像矩阵会把每个轴悄悄翻过来，绝不能进 hole 值。`axis_convention` 只接受 OpenCV 光学系——`R` 的数值就是在这个约定下解出来的，换约定它直接失效，所以不做任何别名。`provenance` 与 `validation` 必填，是因为一份没有来历、没有残差的变换事后无法审计。

### 两个变换与升降依赖

- `point_to_base(p_cam, extrinsics, q_lift)`：`t_eff = t + axis * (q_lift - q_lift_assumed)`。相机挂在棱柱 `lifting_link` 上，一份静态 `(R, t)` 只在一个升降位置成立；
- `direction_to_base(d_cam, extrinsics)`：**只吃 `R`，永不加 `t`**，结果重新归一。方向没有原点，加 `t` 会把单位轴变成一个随相机位置漂移的向量。因此 axis hole 与升降位置无关，`q_lift` 缺失时它照常可用，而 point hole 必须拒绝。

拒绝规则（返回带 reason 的 `UNKNOWN`，不抛裸异常，一个洞失败不影响同次观测的其它洞）：

| reason | 触发 |
|---|---|
| `q_lift_unavailable` | 拿不到与该次 observation 同时刻的升降读数 |
| `q_lift_out_of_limits` | 读数超出记录声明的关节限位，它不是一个升降位置 |
| `q_lift_correction_unavailable` | 记录声明 `correction=none`，而实际位移超过 2mm |

2mm 是 8/6 标定残差的量级（修正后 +0.69mm），再大就不是噪声而是没被记账的升降位移。**绝不发布静默偏移的位姿**：默认按标定姿态处理会让误差等于升降行程，而下游看不到任何异常。

`q_lift` 的来源是该次 observation 自己的 proprioception 记录（`observation.robot_state.evidence_ref` 指向的那份，`lift_position_m` + `lift_source`，schema `demo_graph_lab.readonly_proprioception.v2`）。当前只读 proprio 通道只有两条手臂的 `get_qpos`，没有升降关节来源，因此 capture 如实写 `null`，consuming 端据此拒绝 point 类洞——这是当前真实状态，不是暂时的占位。

### 投影产物

`planning-record --step project-base --extrinsics <record>` 逐洞投影 `program_results.json`，写 `base_frame_values.json`（`demo_graph_lab.base_frame_hole_values.v1`）：

```json
{"value": [0.6, 0.0, 0.75069], "frame": "robot_base",
 "calibration_ref": "<...>/head_extrinsics.json", "object_id": "rack",
 "identity_status": "MODEL_PROPOSED", "identity_accepted": true,
 "status": "PASS", "reason": "transformed_with_lift_corrected_translation",
 "hole_type": "point_3d", "resolver": "part_center", "program": "p1_1",
 "source_frame": "camera_head_optical", "source_value": [...],
 "source_calibration_ref": "<...>/calibration/bundle.json",
 "evidence_refs": ["<...>"]}
```

前四个键与 `selection/binding.py::_CANDIDATE_VALUE_FIELDS` 逐字对齐，候选只携带它们；其余字段留在记录里。变换后这个数值的有效性由**外参**决定，所以 `calibration_ref` 换成外参记录，产生相机系数值的内参记录留在 `source_calibration_ref` 与证据里。上游已经是 `UNKNOWN` 的洞原样保留它自己的 reason（`grounding_identity_collision` 比「无法变换」具体得多）。

派生的 base 系 observation 保留原 `observation_id`（同一次 capture，只是换了表述），`frame` 与 `calibration_ref` 换成 base 与外参，因此 typed-hole 校验的 frame/calibration/observation 三项比较是同类相比，不存在隐式 alias。

### 质心禁令

只有绑定到**拟合几何中心**的 resolver 可以填 `point_3d`（当前只有 `part_center → fit_opening.center`）。`crop_points` 的点云质心是「可见表面的重心」而不是部件中心：8/6 实测它比实体中心偏向相机约一个半径（共模 `x ≈ −10.7mm`、`z ≈ +12.9mm`），接到 `point_3d` 等于把这个偏置直接变成插入点误差。v1 里 `fit_axis` 只发布 `axis`，所以这条路径目前不存在；名单写在 `program_projection.py::_POINT_RESOLVERS`，将来有人接 POINTS 质心时必须先显式改这份名单，而不是靠「类型都是 `point_3d`」混进来。投影同时要求程序链的终点算子与 hole 的 resolver 绑定一致，否则记 `chain_terminal_does_not_match_resolver`。

### identity-accept

Qwen/SAM3 给的是框和掩码，不是「这个框就是 anchor 指名的那个 graph object」的证明，所以执行器发布的 identity 一律 `MODEL_PROPOSED`。投影产物**保留**这个状态，并且默认**不可**进 candidate。唯一的出口是一条独立记录：

```bash
dgl planning-record --record-dir <dir> --step identity-accept \
  --program p1_1 --object-id rack --accepted-by <name> --basis <evidence>
```

写 `identity_acceptance.json`（`demo_graph_lab.identity_acceptance.v1`），每条含 `program / object_id / accepted_by / basis / accepted_at / bbox_pixel / evidence_dir`。四个字段都必须显式给：这是人的判断，不是任何模型输出的推论。接受**只能加**不能减——它不能接受一个 anchor 对不上的 `object_id`，也不能接受一个自身 `UNKNOWN` 的程序（否则「人工接受」就成了推翻同框守卫的后门）。

闸门有两道，方向不同：候选只携带 `status=PASS` **且** `identity_accepted=true` 的洞；派生 observation 也只把被接受的对象列为「已观测」。因此即使有人手工拼一个候选，typed-hole 校验也会在 `object_not_observed` 上拦住。

### 现在能说什么

`base_frame_values.json` 加上一条 acceptance 记录后，`execution/program_projection.py::base_frame_sources` 直接充当 `PlanningOnlyRuntime` 的 observation/candidate provider，离线测试可以从冻结记录一路走到 `solve()` 并拿到 base 系 `point_3d/axis_3d`。这证明的是**合约打通**：frame、calibration、observation 绑定、identity 闸门和 typed-hole 校验在一条真实数值上自洽。它**不**证明真实链已经跑通——第 2 节「执行前门槛」的四项一条都没变，reachability/collision/gripper-width 仍是未接入的 checker，`execution_enabled` 保持 `False`；`pose_se3`（grasp）洞不走本节路径，仍要独立的 tool transform 与 evidence artifact。

## 8. StageProgram 修复回路

第 6 个调用点。`dgl repair --run-dir <dir> --episode <episode_*.json>` 把一份失败 episode 交回给提出该 program 的 backend model，让它改**自己写的那份 program**。实现在 `src/demo_graph_lab/policy/repair.py`，prompt 是 `prompts/repair_policy.md`。

### 信息边界：模型能改什么

| 对象 | 修复时的地位 |
|---|---|
| `StageProgram` 的动作序列与 hole/object 接线 | **可改**，且只能在闭集原语与该 stage 已声明的 hole/object 内改 |
| graph：stage、名字、holes、stage objects | 不可改，也不在输出 schema 里 |
| stage 的 `constraints` 与 `acceptance` | 不可改。它们是示范的证词，不是可以改写的代码 |
| gate 判据、`evaluation/` 的实现、成功口径 | 不可改。修的是程序，不是判它的人 |
| 可信层：`compile_program`、`static_check`、fake dry-run、`policy/api.py` | 不可改 |
| 数值 | 不可写。坐标、距离、角度、阈值一律禁止，`index` 是唯一允许的数字 |

模型的回复只有两个字段：`attribution`（一句失败归因，进 `model_calls/` 与 `repairs/r<N>/attribution.txt` 留档，**不进**任何被执行的产物）和 `program`（完整 StageProgram）。graph 不在这个 schema 里，所以「改约束」在结构上就写不出来；真写了，`validate_program` 的顶层未知字段检查会拒。

### 发布门与产物隔离

修订版走**与 `dgl compile` 完全相同**的发布门：`validate_program` 零违规 → 确定性重编译 → AST 静态检查 → `FakeRuntime` 正常与注入失败两条干跑。全过才发布，判据是同一个 `compiler.report_ready`。接线变了意味着可发布几何 hole 集合可能变，因此发布之后照 `dgl compile` 的规矩追加一段 `PerceptionProgram` 编译，产物同样落在修复目录里。

原发布产物**不覆盖**：修订版写进 `repairs/r<N>/`（`stage_program.json`、`policy.py`、编译快照、`compile_report.json`、`attribution.txt`、可选 `perception_program.json`）。要执行修订版必须显式 `dgl-oracle episode --run-dir <run> --program-dir <run>/repairs/r1`；`--run-dir` 的语义不变，执行前的 8 道一致性门（validation、compile report ready、graph/objects 快照、StageProgram 与 report 一致、program 合约、retreat 硬停、policy 与 program 逐字节一致）对修复目录同样全跑。

### 记账与上限

`repairs/repair_ledger.json` 每次尝试一条：序号、来源程序（`.` 或 `repairs/r<N>`）、episode 文件名与规范化指纹、`banner`、归因、是否发布、违规列表、感知段状态。**每个 run 目录上限 3 次**，计的是尝试数——被拒的修订同样占一格，超限直接拒绝并如实报错。模型调用走 `common/llm.py` 的既有成本上限与请求指纹缓存，tag 是独立的 `repair_r<N>` / `repair_perception_r<N>`，原 compile 的调用记录不被覆盖。

### 口径

episode 报告当前来自 `OracleRuntime`（第 5 节的特权调试路径）。由这种 episode 驱动的修复继承**第 3 档「privileged Oracle 调试」**，不构成任何阶段或任务成功率；`banner` 因此一路带进摘要与台账。摘要本身是确定性提炼（第一失败 stage、gate 判据结论、每 stage verdict、探针前后、调用流水尾部），丢掉墙钟字段，不含世界坐标或位姿数值——整份 episode 报告不会灌进 prompt。

修复回路只作用在离线产物上：它不改 runtime、不改 gate、不重试真实执行，也没有实现自动重跑。跑不跑修订版、认不认它的结果，都是显式的下一条命令。
