# demo-graph-lab 协作规则

本项目属于“研究先行、未来迁移”：先验证 demo 条件化约束是否能改善 manipulation 的选择、执行和失败归因，再考虑迁移到真实系统。保持仓库小、代码可读、实验结论诚实。

开始工作前阅读：

1. `README.md`：代码架构和入口；
2. `docs/PROPOSAL.md`：研究假设；
3. `docs/API.md`：VLM、高层动作和底层控制边界；
4. `docs/TODO.md` 与 `docs/MILESTONES.md`：当前工作顺序。

文档与代码冲突时，以可运行代码和测试为准，并同步修正文档。

## 目录职责

所有核心实现放在 `src/demo_graph_lab/`：

- `demo/`：示范切阶段、关键帧和对象注册；
- `graph/`：约束图的提取、补全、校验、报告和指标；
- `policy/`：给 VLM 的 API、policy 编译和 fake runtime；
- `selection/`：typed-hole 求解和任务无关的偏好排序；真实候选硬过滤尚未实现；
- `execution/`：阶段 runner、runtime、运动规划和 pipeline；
- `evaluation/`：独立 gate 与谓词；
- `common/`：仅放确实被多个阶段共用的小工具。

不要重新建立第二套实现目录，也不要把核心逻辑放回脚本或测试。任务专属信息通过 graph 或 runtime observation 进入，不要硬编码进通用模块。

## API 与信息边界

生成 policy 只能调用 `src/demo_graph_lab/policy/api.py::RuntimeAPI`。新增高层动作时，要同时更新 API 文档、静态检查相关测试和至少一个 dry-run 测试。

必须保持以下边界：

- demo 提供阶段顺序、对象关系和离散偏好，不提供新场景的精确坐标；
- 世界坐标、轴、距离和停止条件保留为 typed holes，由运行时求解；
- policy 只能传递 `solve()` 返回的 handle，不能读取或计算内部数值；
- policy 不调用 gate，阶段成功只由 `evaluation/` 独立判断；
- VLM 不直接调用 `PipelineClient`、`robot_api` 或任何连续控制接口；
- 检查不了的谓词返回 `UNKNOWN`，不能当作通过。

`execution/oracle_runtime.py` 是明确的特权调试实现。它可以读取 simulator `/state` 来填洞和验收，但结果不能报告为主方法性能。非特权 runtime 必须是独立实现，只读取相机、点云、感知结果、机器人状态和力反馈。

## 实现原则

- 优先写最小、直接的代码；没有两个真实调用方时，不要先造通用框架。
- 每个模块保持单一职责；跨阶段数据用清楚的普通 dict/类型传递。
- 不吞掉错误：未知 hole、缺 stage handler、未知谓词和规划失败都要显式暴露。
- runner 目前只会重试同一个 handler。没有实现 rollback、换候选或自适应恢复时，不要这样描述。
- AST 检查只是生成代码的语法约束，不是安全沙箱。
- prompts 是运行资产。修改 prompt 或 `RuntimeAPI` 后，要跑 policy 编译相关测试。
- 保护工作区里与当前任务无关的改动，不顺手格式化或重写相邻代码。

## 测试与结果口径

默认验证：

```bash
python3 -m pytest -q
PYTHONPATH=src python3 -m demo_graph_lab --help
PYTHONPATH=src python3 -m demo_graph_lab.execution.cli --help
```

测试放在 `tests/`，固定输入放在 `tests/fixtures/`。测试应离线运行；需要网络、仿真或机器人时，写成单独的显式集成入口。

报告结果时区分：

1. 静态检查或 fake dry-run；
2. 离线 fixture/单元测试；
3. privileged Oracle 调试；
4. 非特权方法执行；
5. 完整任务成功。

抽取 precision/recall、某个 stage 通过、夹爪闭合和完整任务成功不是同一个指标，不能混报。

## 文档规则

只维护以下说明：

- `README.md` 写代码架构和运行入口；
- `docs/PROPOSAL.md` 写研究方案；
- `docs/API.md` 写接口和信息边界；
- `docs/TODO.md` 写具体待办；
- `docs/MILESTONES.md` 写阶段验收。

过期内容直接删除或合并，不维护历史状态链。
