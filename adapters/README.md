# adapters：可信运行时边界

本目录只放**宿主侧**适配器，不把 Knowin World / GraspNet 源码或权重拷进仓库。

| 子包 | 职责 |
|---|---|
| `knowin_world/` | EvalServer `reset/skill/finalize`、开发态 pipeline `/run`、runtime doctor |
| `demo_bundle/` | 加载脱敏演示证据；遇特权标记 fail-closed |
| `grasp_proposals/` | GraspNet 外部服务薄客户端 + 候选转 `ActionCandidate` |
| `observability/` | Method API 审计落盘、拼装 `RunManifest` |

根模块还保留：

- `contracts.py`：方法可见证据契约
- `method_broker.py`：Allowlisted Method API 与 provenance 审计
- `m1_bindings.py`：把 Broker 接到 `PythonNodePolicy`

Oracle / evaluator 输出只能经 `KnowinWorldAdapter.finalize()` 进入隔离评测，**禁止**注册进 `MethodBroker`。
