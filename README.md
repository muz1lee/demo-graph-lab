# demo-graph-lab

从一段机器人示范中提取阶段和定性约束，再把它们编译成可执行、可独立检查的操作策略。

这个仓库是一个 constraint-guided Code as Policies（CaP）研究原型。主线是：从示范理解任务约束，把后续阶段对当前动作的要求反传回来，再让生成的 CaP 程序显式调用这些约束选择 API；因此每一阶段的抓取不是局部、随意地取一个可抓 pose。当前已完成三组代码级消融（`vanilla / local / backchain`）和 synthetic 反例；真实 candidate compatibility、真实 hard checker 和非特权完整执行仍未完成。`OracleRuntime` 只用于调试和上界，不代表方法效果。

## 代码架构

```text
CaP 主路径
video / trace → demo constraint graph → downstream backchain context
                                      → StageProgram JSON
                                      → generated policy.py explicitly calls
                                        begin_candidates / rank_by /
                                        require_future / choose

在线方法路径（当前 read-only perception）
graph hole anchor + head RGB-D
    → Qwen box → SAM3 mask → MODEL_PROPOSED binding + masked optical cloud
    ├─ grasp_candidate → object-only GraspNet raw proposals
    └─ part_center / part_axis → conservative rack-hole geometry (PASS / UNKNOWN)
    → [frame transform / candidate normalization 尚未完成]
    → typed-hole validation → hard filter
    → execute the selection dataflow written in policy.py → ExecutionDisabled

独立评测路径
动作前后观测 → predicates / gates → PASS / FAIL / UNKNOWN
```

核心代码全部在 `src/demo_graph_lab/`，每个目录只负责一个阶段：

| 目录 | 输入 | 输出 | 当前职责 |
|---|---|---|---|
| `demo/` | 视频、动作 trace | stages、keyframes、objects | 整理示范证据 |
| `graph/` | stages 与关键帧 | `graph.json`、报告、指标 | 提取和校验约束图 |
| `policy/` | graph、高层 API、失败 episode | `stage_program.json`、`policy.py`、`repairs/r<N>/` | 校验接线、确定性编译、fake dry-run、同门修复 |
| `perception/` | sensor artifact、graph anchor、模型回复 | typed observation、mask、object cloud、hole geometry | 窄只读 transport 与可信本地几何；不做控制 |
| `selection/` | graph holes、观测与候选 | binding/物理证书、排序 | fail-closed 校验和确定性偏好 |
| `execution/` | stage handlers、handles | 决策或动作日志 | planning-only runtime、runner 与受信任控制 |
| `evaluation/` | 动作前后观测、阶段约束 | `PASS / FAIL / UNKNOWN` | 独立检查阶段结果 |
| `common/` | — | 路径、实验产物、VLM 客户端 | 少量共享工具 |
| `prompts/` | graph/VLM 输入 | 结构化输出要求 | 代码实际读取的 prompt |

仓库其余目录：

```text
benchmarks/goldsets/   约束抽取标注草案，研究者复核待完成
docs/                  研究方案、API、TODO、里程碑
scripts/               启动 Oracle 仿真所需的小脚本
tests/                 纯逻辑测试和固定输入 fixture
```

一次实验的产物写到 `runs/<task>/<timestamp>/`。每次 backend 调用在 `model_calls/<tag>/` 保存脱敏请求、原始回复、解析/校验结果和成本；同一 run 中断后只在 request（包括图像指纹）完全相同时复用成功的 raw reply，再次显式调用会把旧记录放进该 tag 的 `history/`。graph、program、policy 和报告也留在同一目录。

## API 边界

生成的 policy 只能看到 `policy/api.py` 里的 `RuntimeAPI`：

```text
begin_candidates → rank_by → require_future → choose
solve
approach → grasp_at → lift → transport → align → lower_until → release → retreat
```

- `begin_candidates / rank_by / require_future / choose` 是 CaP 显式写出的候选选择数据流；runtime 执行它，不替 program 隐藏选择逻辑。
- `solve()` 只绑定普通 typed hole；`choose()` 返回选中 grasp pose 的不透明 handle。policy 只能传递 handle，不能读取坐标或阈值。
- StageProgram validator 只允许把 `purpose=lower_stop` 的运行时条件接到 `lower_until()`；不能拿 scalar depth 或 gate condition 冒充。真实 runtime 的停止信号路由仍是执行前 TODO。
- policy 不调用 `verify()`，也不能自行宣布阶段成功。
- StageProgram 的 `selection` 暴露给 backend model，并确定性编译成上面的四类 API 调用；runner 与 evaluation 仍不暴露给模型。
- `execution/robot_api.py` 和 `execution/pipeline.py` 是底层数值控制，只由 runtime 调用。
- `OracleRuntime` 会读取 simulator `/state`，只能用于集成调试。
- `retreat` 目前只有独立 opcode 和编译契约；可信 runtime solver 尚未实现，含该动作的 Oracle episode 会在 reset 和任何控制前拒绝启动。

当前 `PlanningOnlyRuntime` 的 runtime backend 固定关闭，所有九个控制原语都会抛出 `ExecutionDisabled`。计划中的运行时 VLM 只能做有限的离散选择、修正建议和视觉证据描述，不能输出连续控制量。完整接口见 [docs/API.md](docs/API.md)。

候选几何值使用一个闭合格式：

```json
{
  "value": [0.4, 0.1, 0.8, 0.0, 0.0, 0.0, 1.0],
  "frame": "robot_base",
  "calibration_ref": "calibration/head.json",
  "object_id": "tube_left"
}
```

它必须绑定到同一次 observation、同一标定和 graph 声明的精确 frame；V1 不做隐式 frame alias。`scalar` 和 `runtime_condition` 不能由 candidate provider 填写。校验失败或无法确认时，物理 checker 不运行，候选直接 fail-closed。

Cone 排序只读重力相对的 `approach_tilt_deg`，不接受没有 frame 的裸 `approach_dir`。未来的 GraspNet candidate normalization 必须同时验证米制 point-cloud manifest、独立 identity 接受记录和 grasp-frame→runtime-EEF 标定，并在 provenance 中保存变换数值、frame、单位、XYZW 约定和独立证据。当前 binding 只记录“这个 mask 是为哪个 anchor 请求的”，不证明模型找对了实例，也不篡改 GraspNet detector ID；当前仓库仍不发布 GraspNet→graph candidate converter。

当前 backend model 只在离线流程中参与：无 trace 时提议阶段切分、建立对象 registry、逐阶段提取约束，并生成包含动作接线与 `selection` 的结构化 `StageProgram`。可信 compiler 只负责把这份 CaP IR 降成 Python；候选选择的约束调用已经由 model 产物显式决定。episode 运行时不再调用 backend，运动执行和 gate 也不由它决定。

## 快速开始

只跑测试：

```bash
python3 -m pip install -e ".[dev]"
python3 -m pytest -q
```

运行完整示范处理流水线需要 OpenCV、OpenRouter 客户端和示范数据：

```bash
python3 -m pip install -e ".[dev,pipeline]"
export OPENROUTER_API_KEY=...
export DGL_VLM_MODEL=...
export DGL_DATA_ROOT=/path/to/robot-subtask-seg

dgl all --task insert_tubes
dgl compile --task insert_tubes
dgl compile --task insert_tubes --cap-mode backchain
dgl metrics --task insert_tubes \
  --gold benchmarks/goldsets/insert_tubes_gold.json
```

`all` 负责从示范到已校验约束图；`compile` 让 backend 生成 `StageProgram`，再确定性生成 policy，并做静态检查和 fake dry-run。`--cap-mode` 控制生成代码能使用的约束范围。未通过 `validation.json` 的 graph 不会调用 compiler backend。

固定同一 graph 做三组 CaP 代码生成消融：

```bash
dgl cap-ablate \
  --graph runs/insert_tubes/<timestamp>/graph.json \
  --output-dir /path/to/experiment \
  --repeats 5
```

每个 repeat 只让 backend 生成一次共享的 primitive sequence 与 hole wiring；可信代码再从这同一份骨架派生三组 policy。`vanilla` 不引用示范约束，`local` 只写当前阶段约束，`backchain` 额外把涉及同一 manipulated object 的下游约束写入 `require_future(...)`。因此三组共享 graph、动作骨架、候选和模型调用，唯一实验变量是生成 CaP 中的 selection dataflow。

只跑 planning-only 固定 replay：

```bash
dgl planning-replay \
  --graph tests/fixtures/planning/grasp_graph.json \
  --replay tests/fixtures/planning/grasp_replay.json \
  --output /tmp/dgl-planning-comparison.json
```

该 fixture 是合约测试用的 synthetic data，不是机器人或仿真结果。命令不加载 backend 环境、不调用模型或控制，只在同一组通过过滤的候选上比较固定 ID baseline 与 demo region/cone 排序。

只读真实记录使用显式分步命令：

```bash
python3 -m pip install -e ".[dev,live]"

dgl planning-record \
  --record-dir /path/to/record \
  --graph /path/to/graph.json \
  --objects /path/to/objects.json \
  --stage 0 \
  --intrinsics /path/to/intrinsics.json \
  --qwen-url https://qwen.example/v1/chat/completions \
  --qwen-model qwen-vl \
  --sam3-url https://sam3.example/segment

dgl planning-record --record-dir /path/to/record \
  --step capture --allow-live-read

dgl planning-record --record-dir /path/to/record \
  --step ground --allow-model-read

dgl planning-record --record-dir /path/to/record \
  --step segment --allow-model-read

dgl planning-record --record-dir /path/to/record \
  --step project

dgl planning-record --record-dir /path/to/record \
  --step predict --allow-live-read
```

已发布 `perception_program.json` 时，同一次 capture 还可以改走多程序执行：

```bash
dgl planning-record --record-dir /path/to/record \
  --step programs --allow-model-read \
  --perception-program runs/<task>/<timestamp>/perception_program.json
```

`plan` 校验 graph 的 resolver/anchor 和 objects registry，只写本地调用计划；token 不进入 `plan.json`。`capture` 冻结一次 head snapshot 与固定的 `get_qpos/get_xquat`；`ground` 和 `segment` 分别调用 Qwen 与 SAM3；`project` 在本地做 mask-first 反投影、anchor binding 和几何估计；只有 `resolver=grasp_candidate` 才允许 `predict` 调用 GraspNet。5090 现有的 `QWEN_BASE_URL / QWEN_DEFAULT_MODEL / QWEN_AUTH_TOKEN / SAM3_SERVER_URL` 可直接作为默认配置，SAM3 使用其 `/segment`；命令行参数优先，Qwen 也兼容 `QWEN_API_KEY`，SAM3 如需认证则读取 `SAM3_API_KEY`。

每一步有独立授权和状态，没有一键跨阶段命令。Qwen/SAM3 在这里属于非特权感知模型，不是负责离线 graph/program 的 backend model。hole 几何证据不足时保存 `UNKNOWN`；GraspNet 完成后状态为 `OBJECT_RAW_GRASPNET_RECORDED`，仍停在 candidate normalization、运动规划和控制之前。

`ground/segment/project/predict` 这条链一个 `record-dir` 只处理一个 `--hole`/anchor；省略 `--hole` 时只允许 stage 恰好有一个 `grasp_candidate`。它是 component record，不是完整 stage solve。不能把多个不同 capture 的单-anchor record 拼成“同一 observation”的 candidate bundle。

`programs` 是同一次 capture 下的多 anchor 路径：以 capture 为父 observation，按 `perception_program.json` 逐程序执行，每个程序有自己的 anchor 和自己的 Qwen/SAM3/几何产物，结果写进 `programs/` 与 `program_results.json`。它和上面那条链互斥——任一条推进了 manifest 状态，另一条就不再接受这个 record 目录。发布的几何值停在 `camera_head_optical`，identity 是 `MODEL_PROPOSED`。

要让这些值成为 `solve()` 能用的 base 系候选绑定，还要两个本地步骤（零网络）：

```bash
dgl planning-record --record-dir /path/to/record \
  --step identity-accept --program p1_1 --object-id rack \
  --accepted-by <name> --basis "<核对了什么>"

dgl planning-record --record-dir /path/to/record \
  --step project-base --extrinsics /path/to/camera_extrinsics.json
```

`identity-accept` 是人对某个 `(program, object_id)` 的显式接受，写独立的 `identity_acceptance.json`；没有它的洞不进 candidate。`project-base` 用一份已测量的相机外参把 optical 值变换进 `robot_base`（point 带升降修正，axis 只吃 `R`），写 `base_frame_values.json`。两者都不调用任何模型或网络。详见 `docs/API.md` 第 7 节。

`predict` 要求 GraspNet 已由实验环境单独启动，并且只接受 loopback URL。仓库里的 client 不负责安装模型、启动常驻服务或接收局域网请求。

第一版 record 使用绝对 artifact 路径，采集后不要移动目录；image、mask、point cloud、assignment 或 calibration 与当前 `record-dir` 不一致时会 fail-closed。

编译入口会再次校验当前 graph；只有 program、静态检查和两条 fake dry-run 都通过才发布 `policy.py`。编译报告保存它实际 dry-run 的完整 StageProgram；Oracle 加载时还会比对 graph、objects、StageProgram 与确定性生成的 policy，任何产物被改动都会拒绝执行。episode 中任一 stage 失败时命令返回非零。

Oracle 集成入口：

```bash
dgl-oracle smoke --task-id robodojo_insert_tubes_000
dgl-oracle episode \
  --run-dir runs/insert_tubes/<timestamp> \
  --task-id robodojo_insert_tubes_000
```

这两个命令都不是非特权方法评测。

episode 失败之后，可以把那份失败轨迹交回给提出 program 的 backend model，让它改自己写的那份 program：

```bash
dgl repair \
  --run-dir runs/insert_tubes/<timestamp> \
  --episode runs/insert_tubes/<timestamp>/episode_<ts>.json

dgl-oracle episode \
  --run-dir runs/insert_tubes/<timestamp> \
  --program-dir runs/insert_tubes/<timestamp>/repairs/r1 \
  --task-id robodojo_insert_tubes_000
```

模型只能在闭集原语与已声明 hole 内改动作序列和接线：graph、约束、验收条件和 gate 判据都不在它的输出 schema 里。修订版走与 `compile` 完全相同的发布门，产物落在 `repairs/r<N>/`，原发布产物不覆盖，每个 run 目录上限 3 次。因为 episode 来自 `OracleRuntime`，这条回路的产出同样属于特权调试口径。详见 `docs/API.md` 第 8 节与 `docs/OFFLINE_WORKFLOW.md` 第 11 步。

## 当前研究重点

当前先验证方法论本身：在同一 graph 上跑 `vanilla / local / backchain` 的 CaP 代码生成有效率与代码差异；随后把 `require_future` 依赖的 synthetic `future_constraints` 换成真实几何/规划 compatibility，并比较同候选集上的长程成功率。现有测试已经证明接口因果路径和一个“局部最优、下游必死”的反例，不证明真实 grasp 或完整任务成功。

详细内容：

- [docs/OFFLINE_WORKFLOW.md](docs/OFFLINE_WORKFLOW.md)：离线处理阶段与每一步实验产物；
- [docs/PROPOSAL.md](docs/PROPOSAL.md)：研究问题与实验方法；
- [docs/API.md](docs/API.md)：VLM、高层 runtime、可信 gate 和底层控制的边界；
- [docs/TODO.md](docs/TODO.md)：当前待办；
- [docs/MILESTONES.md](docs/MILESTONES.md)：阶段目标和验收条件；
- [docs/DEVLOG.md](docs/DEVLOG.md)：最近开发、离线实跑产物和当前停点；
- [AGENTS.md](AGENTS.md)、[CLAUDE.md](CLAUDE.md)：仓库协作规则。
