# Claude 协作规则

先完整阅读 `AGENTS.md`，它是本项目的主要规则。本文件只强调最容易破坏研究边界的事项。

## 工作目标

这是一个研究型代码仓。优先保证方法假设可验证、代码路径清楚、结果口径诚实，不增加公司式发布治理或无用抽象。

## 代码放置

- 核心实现只放在 `src/demo_graph_lab/`。
- `demo / graph / policy / perception / selection / execution / evaluation` 各自管理对应阶段。
- 测试放 `tests/`，金标放 `benchmarks/goldsets/`，研究文档只放 `docs/`。
- 不恢复旧目录，不复制一套平行实现。

## 关键边界

- 生成 policy 只能看到 `policy/api.py::RuntimeAPI`。
- VLM 不输出世界坐标、关节角、速度、力或成功判定。
- `solve()` 的结果是不透明 handle，只能交给高层动作。
- `evaluation/` 独立做 gate；`execution/robot_api.py` 和 `pipeline.py` 只在可信底层调用。
- `OracleRuntime` 读取仿真精确状态，只用于调试；不得把 Oracle episode 写成主方法成功。
- candidate 必须绑定同一次 observation；typed-hole 校验必须早于所有物理 checker，几何值不做隐式 frame alias。
- scalar/runtime condition 不由 candidate provider 填写；每阶段 required holes 以已校验 StageProgram 的 wiring 为准。
- `planning-record` 的 raw live 记录不等于真实 candidate/replay 已接通；synthetic replay、raw record、真实 replay、compat 和 recovery 的状态必须如实区分。
- `planning-record` 必须保持显式分步：`plan` 零网络，`capture/predict` 各自要求 `--allow-live-read`，`ground/segment` 各自要求 `--allow-model-read`，`project` 只做本地计算；不能增加绕过检查点的一键入口。
- GraspNet raw detector ID 无论正负都不能直接映射到 graph object；Qwen/SAM3 anchor binding 保持 `MODEL_PROPOSED`，必须另有独立 identity 接受记录才能进入 candidate。
- cone 排序不接 frame-less `approach_dir`；GraspNet grasp pose 未经带独立 evidence artifact 的显式 tool transform 不能当 runtime EEF/TCP pose，变换数值语义必须保存在 candidate provenance。

## 改动方式

- 选择最小、最直接的实现，避免为了“以后可能用”增加层级。
- 改 API 时同步改 `docs/API.md` 和相关测试。
- 改架构或命令时同步改 `README.md`；具体后续工作写进 `docs/TODO.md` 或 `docs/MILESTONES.md`。
- 改离线阶段或产物格式时同步改 `docs/OFFLINE_WORKFLOW.md`。
- 每轮开发只在 `docs/DEVLOG.md` 留一条简短记录，不新增平行状态文档。
- 完成后至少运行 `python3 -m pytest -q`，并用 `PYTHONPATH=src` 检查两个 CLI 的 `--help`。
