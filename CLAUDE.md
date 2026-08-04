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
- observation/candidate 契约不等于真实 adapter 已接通；真实感知、candidate generation、typed binding、compat 和 recovery 的状态必须如实区分。

## 改动方式

- 选择最小、最直接的实现，避免为了“以后可能用”增加层级。
- 改 API 时同步改 `docs/API.md` 和相关测试。
- 改架构或命令时同步改 `README.md`；具体后续工作写进 `docs/TODO.md` 或 `docs/MILESTONES.md`。
- 每轮开发只在 `docs/DEVLOG.md` 留一条简短记录，不新增平行状态文档。
- 完成后至少运行 `python3 -m pytest -q`，并用 `PYTHONPATH=src` 检查两个 CLI 的 `--help`。
