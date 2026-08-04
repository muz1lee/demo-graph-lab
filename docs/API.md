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
```

Backend 输出始终是不可信 proposal。stage、registry、constraint sample 和 `StageProgram` 都经过严格 schema；无效 sample 不参加投票，分母固定为请求次数。同名阶段的约束只有达到严格多数才传播。`StageProgram` 只决定高层 primitive sequence 和 hole/object 接线，validator 检查动作顺序、API 签名、hole 类型与 purpose、对象引用和数字字面量，可信 compiler 再生成 Python。每次调用的脱敏请求、raw reply、parsed result 和 validator 结论都保存在 `model_calls/<tag>/`；同 tag 的再次调用保留在 `history/`，不会把不同请求和回复混在一起。

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

`planning-record` 提供六个显式步骤：`plan / capture / ground / segment / project / predict`。`plan` 零网络；`capture/predict` 分别要求 `--allow-live-read`；`ground/segment` 分别要求 `--allow-model-read`；`project` 只做本地计算。没有一键入口。capture 的同步 render、camera cache 更新和 frame-id 增量写入 `sensor/call.json`。每次 Qwen/SAM3 请求、raw reply、校验结果和耗时分别保存在 `grounding/` 与 `segmentation/`，token 不写入 artifact。`project` 的 `object/result.json` 中 `status` 由几何证据派生：请求了几何但其 `opening_geometry_status` 不是 `PASS` 时为 `GEOMETRY_UNKNOWN`，几何 `PASS` 或本次未请求几何时才是 `ACCEPTED`；record 本身确实发生，因此 manifest 的 `OBJECT_CLOUD_RECORDED` 和退出码不受影响。

V1 record 一次只处理一个 graph anchor。它不能在同一个 observation 下同时组装 tube grasp/axis 与 opening center/axis，因此还没有接到 `PlanningOnlyRuntime.solve()`。后续结构必须以 capture 为父 observation、以 anchor 为子任务，并让同一 tube cloud 和同一 opening geometry 分别复用；在此之前只报告 component artifact，不报告完整 stage candidate。

真实点云保留在 OpenCV head optical frame：`+X` 右、`+Y` 下、`+Z` 前，单位米。可信代码先在 mask 上筛 depth，再同步生成 `Nx3` object cloud 与 `Nx2 (row,col)` lineage；`object_assignment.json` 记录 observation、被请求的 graph anchor、frame、calibration 和 Qwen/SAM3 evidence，但 identity 状态固定为 `MODEL_PROPOSED`。opening center/axis 由局部 RGB-D 对比与开口周围 ring 的局部支撑面重新计算，证据不足返回 `UNKNOWN`，不采用模型 pose。GraspNet 只消费 object cloud，raw detector ID 原值保留；仓库仍不发布 GraspNet→graph candidate converter。之后必须补 identity 接受、lift-aware `camera_head_optical → robot_base` 与 `graspnet_parallel_jaw → runtime_ee` 变换。

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
| `principal_axis` | mask-first object cloud → local PCA | record artifact 已接，robot-base transform 未接 |
| `part_center` / `part_axis` | opening ROI + local RGB-D contrast + support-plane fit | `PASS/UNKNOWN` artifact 已接，robot-base transform 未接 |
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

当前 Oracle 从 simulator state 直接得到 world-frame 几何，因此数值 handle 明确标为 `frame="world"`，同时保留 graph 请求的 `requested_frame` 供检查。非特权 runtime 不能照搬这个捷径，必须使用相机与机器人标定完成真正的 frame transform。

Oracle 的 `scalar` 和 `runtime_condition` 可以返回延迟描述子；非特权 planning runtime 尚未实现对应 resolver，因此当前会 fail-closed，而不是让 candidate 或 VLM 猜值。插入阶段由 deterministic enrich 补 `purpose=lower_stop` 的控制洞；StageProgram validator 只允许这类洞接到 `lower_until`。它目前只声明应读取非特权 contact/motion-plateau 信号，不生成阈值；明确的停止信号路由仍是执行前 TODO。

### 只读真实记录接口

head RGB-D、Qwen/SAM3、object cloud、opening geometry 和 GraspNet `/predict` 已有同一条显式 read-only record 入口，但还不能报告为完整候选链：

- snapshot 的 depth 是 float32 米制左目 render depth；反投影后只保留 finite 且 `z>0` 的 optical-frame 点；
- Qwen 必须只返回一个合法 box，SAM3 必须返回与冻结图像同尺寸的非空二值 mask；零框、多框、全帧 mask 和 schema 漂移都 fail-closed；
- graph identity 只来自已校验 anchor，模型回复只作为 evidence；assignment 与 object cloud 由本地代码发布，GraspNet 实际只消费该 object-only `point_cloud_path` 与 `extra.max_grasps`；
- baseline 的 `pred_decode()` 把所有 `object_id` 固定为 `-1`，raw 记录可以成功，candidate normalization 必须 fail-closed；
- head camera 挂在可升降 link 上；静态 extrinsics 没有实时 lift 修正，不能把 optical cloud 改标签成 robot base；
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

`PerceptionProgram` 是与 `StageProgram` 平行的第二个 backend model 产物：`StageProgram` 决定动作怎么接线，`PerceptionProgram` 决定几何 hole 由哪条感知链发布。它是独立编译产物（将来落盘 `perception_program.json`），不是 hole 的字段，graph schema 不变。契约实现在 `src/demo_graph_lab/perception/program.py`，干跑实现在 `perception/fake_runtime.py`。当前只有校验器和 fake 干跑，链上的算子尚未接真实模型或几何实现。

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

### 信息边界

- backend model 只做一件事：把闭集算子组合成线性链，并声明这条链的哪个字段发布哪个 hole。它不写查询文本、不写逐步参数、不写数值；
- 链的根是被 `provide` 的 hole 已经声明的 `anchor`，程序里不重复声明 anchor，也无法改写它。真正的 `localize` 查询由可信代码从 anchor 渲染，model 不提供自由文本；
- 同一个程序 `provide` 的所有 hole 必须共享逐字段相同的 anchor：一个程序只观测一个 anchor；
- 整个文档禁止数值字面量，除 `stage` 索引（指向 graph 的结构性引用）外任何位置出现数字或带单位的字符串都是违规。这条与 `StageProgram` 同规，是纵深防御，不依赖 key 白名单先拦住；
- hole 身份是 stage 内唯一的 `(stage, hole)`：同名 hole 可以出现在多个 stage，但同一个 stage 的同一个 hole 只能由一个程序发布；
- v1 只发布被观测到的对象几何，`resolver` 限于 `part_center / part_axis / principal_axis`。`grasp_candidate` 走候选身份与排序机制，`motion_derived` 的值来自执行状态而不是观测，两者出现在 `provides` 里都是违规；
- 声明了 `resolver` 的 hole 还必须由绑定的那条链发布：`part_center → fit_opening.center`、`part_axis → fit_opening.axis`、`principal_axis → fit_axis.axis`。类型相同不等于语义相同——开口平面法向与点云 PCA 主轴都是 `axis_3d`，只比类型就能互换，而它们测的不是同一个量。绑定表 `perception/program.py::RESOLVER_BINDINGS` 是这条语义的单一真相源，键恰好是可发布 resolver 的全集；没有声明 `resolver` 的 hole 维持类型匹配即可；
- 失败语义是 all-or-nothing：链在任何一步失败，该程序的 `provides` 一个都不产出。部分成功会让上层以为 hole 已填，是 bug 不是可接受的降级；
- 未被任何程序覆盖的几何 hole 不是违规，它们继续走 graph resolver 老路。`coverage_by_stage` 只产出 per stage 的 covered/uncovered 名单供记录，不做准入判断。
