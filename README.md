# demo-graph-lab

从一段机器人示范中提取阶段和定性约束，再把它们编译成可执行、可独立检查的操作策略。

这个仓库是研究原型。基于录制示范的解析、约束图、policy 编译和阶段执行已经接通；在线执行目前只有读取仿真精确状态的 `OracleRuntime`，用于调试和上界，不代表主方法已经完成。非特权感知、真实抓取候选和下游可行性选择仍在开发中。

## 代码架构

```text
demo video / trace
        │
        ▼
demo          切阶段、取关键帧、统一对象 ID
        │
        ▼
graph         提取 constraints / acceptance / typed holes（待求解的带类型参数）
        │
        ▼
policy        编译只调用高层 API 的 Python handlers
        │
        ▼
execution     按 stage 执行动作
     │                 │
     ▼                 ▼
selection           evaluation
填洞与偏好函数       独立 gate 判定
```

核心代码全部在 `src/demo_graph_lab/`，每个目录只负责一个阶段：

| 目录 | 输入 | 输出 | 当前职责 |
|---|---|---|---|
| `demo/` | 视频、动作 trace | stages、keyframes、objects | 整理示范证据 |
| `graph/` | stages 与关键帧 | `graph.json`、报告、指标 | 提取和校验约束图 |
| `policy/` | graph 与高层 API | `policy.py`、编译报告 | 生成、静态检查、fake dry-run |
| `selection/` | typed holes、约束、候选 | 不透明 handle 或排序 | 填洞与任务无关偏好 |
| `execution/` | stage handlers、handles | 动作日志与 episode 报告 | runner、运动规划、机器人调用 |
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

一次实验的产物写到 `runs/<task>/<timestamp>/`。示范处理、graph、policy 和执行报告都留在同一个 run 目录，便于从结果反查输入。

## API 边界

生成的 policy 只能看到 `policy/api.py` 里的 `RuntimeAPI`：

```text
solve
approach → grasp_at → lift → transport → align → lower_until → release
```

- `solve()` 返回不透明 handle；policy 只能传递，不能读取坐标或阈值。
- policy 不调用 `verify()`，也不能自行宣布阶段成功。
- `selection`、`runner` 和 `evaluation` 属于可信运行层，不暴露给 VLM。
- `execution/robot_api.py` 和 `execution/pipeline.py` 是底层数值控制，只由 runtime 调用。
- `OracleRuntime` 会读取 simulator `/state`，只能用于集成调试。

计划中的运行时 VLM 只能做有限的离散选择、修正建议和视觉证据描述，不能输出连续控制量。完整接口见 [docs/API.md](docs/API.md)。

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

`all` 负责从示范到约束图；`compile` 单独把最新 graph 编译成 policy。不使用命令行入口时，可以从仓库根目录运行 `PYTHONPATH=src python3 -m demo_graph_lab ...`。

Oracle 集成入口：

```bash
dgl-oracle smoke --task-id robodojo_insert_tubes_000
dgl-oracle episode \
  --run-dir runs/insert_tubes/<timestamp> \
  --task-id robodojo_insert_tubes_000
```

这两个命令都不是非特权方法评测。

## 当前研究重点

下一条真实主线是：接入 grasp candidates → 做物理硬过滤和示范排序 → 实现独立的非特权 runtime → 打通一个完整 episode。只有这条链稳定后，才加入跨阶段兼容性（`compat`）检查和恢复。

详细内容：

- [docs/PROPOSAL.md](docs/PROPOSAL.md)：研究问题与实验方法；
- [docs/API.md](docs/API.md)：VLM、高层 runtime、可信 gate 和底层控制的边界；
- [docs/TODO.md](docs/TODO.md)：当前待办；
- [docs/MILESTONES.md](docs/MILESTONES.md)：阶段目标和验收条件；
- [AGENTS.md](AGENTS.md)、[CLAUDE.md](CLAUDE.md)：仓库协作规则。
