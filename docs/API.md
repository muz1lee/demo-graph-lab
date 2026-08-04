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

第一条非特权 baseline 不调用 backend model。当前已经实现 planning-only 部分：

```text
RGB-D / robot state
  → perception and grasp candidates
  → reachability / collision / width hard filter
  → deterministic region / cone ranking
  → PlanningOnlyRuntime 写 decisions.jsonl
  → solve() 返回选中 bundle 的 opaque handles
  → 所有 control primitive 抛 ExecutionDisabled
```

`ObservationPacket` 只接受 sensor artifact 引用、对象观测和显式 `Proprioception`；不接受任意 `robot_state` mapping。候选数据会冻结并检查为 finite、JSON-safe，缺失/异常/`UNKNOWN` 的硬检查全部 fail-closed。当前还没有真实 sensor/candidate adapter，也没有 candidate value 对 graph hole type/frame 的完整校验，所以不能连接控制。完整 episode 稳定后，才允许 backend model 对已经通过硬过滤的候选做可选排序；它不能生成新 pose、复活被过滤候选或决定 gate。显式 `compat` 和向后检查也属于可信 selection。

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

硬过滤和排序骨架已经存在，但真实的 reachability、collision、gripper-width checker adapter 尚未接入。跨阶段兼容性检查仍是后续功能。

当前 runner 失败后只重复同一个 handler。它不会 rollback、换候选或修改搜索范围。缺少 handler、hole 歧义和未知谓词都必须停止或返回 `UNKNOWN`，不能猜一个默认值继续执行。

当前 Oracle 从 simulator state 直接得到 world-frame 几何，因此数值 handle 明确标为 `frame="world"`，同时保留 graph 请求的 `requested_frame` 供检查。非特权 runtime 不能照搬这个捷径，必须使用相机与机器人标定完成真正的 frame transform。

`scalar` 和 `runtime_condition` 可以返回延迟描述子，由后续高层控制器结合当前状态求值；它们不是 VLM 猜出的数值。插入阶段由 deterministic enrich 补 `purpose=lower_stop` 的控制洞；StageProgram validator 只允许这类洞接到 `lower_until`。它目前只声明应读取非特权 contact/motion-plateau 信号，不生成阈值；把 descriptor 明确路由到 Oracle 的停止类型仍是执行前 TODO。

### 执行前门槛

以下四项未完成前，`PlanningOnlyRuntime.execution_enabled` 保持 `False`：

1. 真实 observation/candidate/check adapters 的完整调用图确认只读、无 `/state` 和 control side effect；
2. 每个 candidate hole value 按 graph type、frame、finite 数值和 calibration 做硬校验；
3. 固定 candidate replay 能复现过滤原因、ranking meta 和最终选择；
4. 一个非特权 stage 的 gate 输入和 abort 行为通过离线/仿真前检查。

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
