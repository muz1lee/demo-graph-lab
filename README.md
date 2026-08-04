# demo-graph-lab

从一段机器人示范中提取阶段和定性约束，再把它们编译成可执行、可独立检查的操作策略。

这个仓库是研究原型。离线的 demo 理解、约束图、结构化 `StageProgram` 和确定性 policy 编译已经接通。在线主方法目前只到 read-only planning：record adapter、head RGB-D 记录入口、raw GraspNet 记录入口、typed-hole 校验、候选硬过滤、确定性排序、决策日志和 synthetic replay 可用；真实 object assignment、三个 hard checker 和控制仍未接入。`OracleRuntime` 只用于调试和上界，不代表主方法已经完成。

## 代码架构

```text
离线语义路径
video / trace → demo → graph → StageProgram JSON → deterministic compiler → policy.py
                    ↑              ↑
              backend VLM    backend LLM 只提议动作序列与接线

在线方法路径（当前 read-only planning）
head RGB-D + typed proprioception → frozen observation → raw GraspNet reply
    → [object assignment / frame transform 尚未完成]
    → candidates → typed-hole validation → hard filter → ranking
    → opaque handles → ExecutionDisabled

独立评测路径
动作前后观测 → predicates / gates → PASS / FAIL / UNKNOWN
```

核心代码全部在 `src/demo_graph_lab/`，每个目录只负责一个阶段：

| 目录 | 输入 | 输出 | 当前职责 |
|---|---|---|---|
| `demo/` | 视频、动作 trace | stages、keyframes、objects | 整理示范证据 |
| `graph/` | stages 与关键帧 | `graph.json`、报告、指标 | 提取和校验约束图 |
| `policy/` | graph 与高层 API | `stage_program.json`、`policy.py` | 校验接线、确定性编译、fake dry-run |
| `perception/` | sensor artifact、本体状态、recorded GraspNet reply | typed observation 与 raw/candidate records | 严格 adapter 与窄只读 transport；不做控制 |
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
solve
approach → grasp_at → lift → transport → align → lower_until → release → retreat
```

- `solve()` 返回不透明 handle；policy 只能传递，不能读取坐标或阈值。
- StageProgram validator 只允许把 `purpose=lower_stop` 的运行时条件接到 `lower_until()`；不能拿 scalar depth 或 gate condition 冒充。真实 runtime 的停止信号路由仍是执行前 TODO。
- policy 不调用 `verify()`，也不能自行宣布阶段成功。
- `selection`、`runner` 和 `evaluation` 属于可信运行层，不暴露给 VLM。
- `execution/robot_api.py` 和 `execution/pipeline.py` 是底层数值控制，只由 runtime 调用。
- `OracleRuntime` 会读取 simulator `/state`，只能用于集成调试。
- `retreat` 目前只有独立 opcode 和编译契约；可信 runtime solver 尚未实现，含该动作的 Oracle episode 会在 reset 和任何控制前拒绝启动。

当前 `PlanningOnlyRuntime` 的 runtime backend 固定关闭，所有八个控制原语都会抛出 `ExecutionDisabled`。计划中的运行时 VLM 只能做有限的离散选择、修正建议和视觉证据描述，不能输出连续控制量。完整接口见 [docs/API.md](docs/API.md)。

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

Cone 排序只读重力相对的 `approach_tilt_deg`，不接受没有 frame 的裸 `approach_dir`。未来的 GraspNet candidate normalization 必须同时验证米制 point-cloud manifest、可信 object assignment 和 grasp-frame→runtime-EEF 标定，并在 provenance 中保存变换数值、frame、单位、XYZW 约定和独立证据。当前仓库只发布 raw validator，没有 GraspNet→graph candidate converter；baseline 的 `object_id=-1` 只作为原始证据留存。

当前 backend model 只在离线流程中参与：无 trace 时提议阶段切分、建立全视频对象 registry、逐阶段提取约束，以及提议结构化 `StageProgram`。Python policy 由可信代码确定性生成；在线候选选择、运动执行和 gate 都不调用 backend。这里的 backend model 指通过 `common/llm.py` 调用的生成式 VLM/LLM；抓取检测器等感知模型属于非特权感知层，不在这个定义中。

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
dgl metrics --task insert_tubes \
  --gold benchmarks/goldsets/insert_tubes_gold.json
```

`all` 负责从示范到已校验约束图；`compile` 先取得 `StageProgram`，再确定性生成 policy，并做静态检查和 fake dry-run。未通过 `validation.json` 的 graph 不会调用 compiler backend。

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
  --stage 0 \
  --intrinsics /path/to/intrinsics.json

dgl planning-record --record-dir /path/to/record \
  --step capture --allow-live-read

dgl planning-record --record-dir /path/to/record \
  --step predict --allow-live-read
```

默认 `plan` 只写本地调用计划，不加载 backend 环境，也不访问相机或网络。`capture` 只允许一次 head snapshot 和固定的 `get_qpos/get_xquat` 查询；`predict` 只读取已经冻结的点云并调用 GraspNet health/predict。两步都必须显式给 `--allow-live-read`，没有一键跨阶段命令。最终状态 `RAW_GRASPNET_RECORDED` 仍然停在 object assignment、候选选择、运动规划和控制之前。

`predict` 要求 GraspNet 已由实验环境单独启动，并且只接受 loopback URL。仓库里的 client 不负责安装模型、启动常驻服务或接收局域网请求。

第一版 record 使用绝对 artifact 路径，采集后不要移动目录；point-cloud binding 与当前 `record-dir` 不一致时，会在任何模型调用前失败。

编译入口会再次校验当前 graph；只有 program、静态检查和两条 fake dry-run 都通过才发布 `policy.py`。编译报告保存它实际 dry-run 的完整 StageProgram；Oracle 加载时还会比对 graph、objects、StageProgram 与确定性生成的 policy，任何产物被改动都会拒绝执行。episode 中任一 stage 失败时命令返回非零。

Oracle 集成入口：

```bash
dgl-oracle smoke --task-id robodojo_insert_tubes_000
dgl-oracle episode \
  --run-dir runs/insert_tubes/<timestamp> \
  --task-id robodojo_insert_tubes_000
```

这两个命令都不是非特权方法评测。

## 当前研究重点

下一条真实主线是：从只读 record 中得到可信 object mask → 为每个 graph object 生成独立 optical point cloud 和 assignment artifact → 补齐 lift-aware camera→robot-base 与 grasp→runtime-EEF 标定 → 实现可达、碰撞和夹爪宽度证书 → 建立第一份真实固定 replay。现有 raw record 只证明感知调用可追溯，synthetic replay 只证明契约和对照逻辑可重放。完成真实 replay 和单 stage gate/abort 审查之前不连接控制；完整 episode 稳定后才加入跨阶段 `compat` 和向后检查。

详细内容：

- [docs/OFFLINE_WORKFLOW.md](docs/OFFLINE_WORKFLOW.md)：离线处理阶段与每一步实验产物；
- [docs/PROPOSAL.md](docs/PROPOSAL.md)：研究问题与实验方法；
- [docs/API.md](docs/API.md)：VLM、高层 runtime、可信 gate 和底层控制的边界；
- [docs/TODO.md](docs/TODO.md)：当前待办；
- [docs/MILESTONES.md](docs/MILESTONES.md)：阶段目标和验收条件；
- [docs/DEVLOG.md](docs/DEVLOG.md)：最近开发、离线实跑产物和当前停点；
- [AGENTS.md](AGENTS.md)、[CLAUDE.md](CLAUDE.md)：仓库协作规则。
