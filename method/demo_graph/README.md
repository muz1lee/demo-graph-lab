# method/demo_graph：v1 协议与不变量层

> ⚠️ **2026-07-30 更正**：下文原写「本目录是可执行的方法核心」——那是 v1 语境下的表述，
> 现已不准确。当前主线是 `harness/`（v2 实证流水线）。本目录的定位、为什么保留、
> 以及逐模块的退役条件，见 [`../README.md`](../README.md)。
>
> 一句话：**v1 有纪律没数字，v2 有数字没纪律**——本目录承载 `harness/` 尚无等价物的
> 冻结协议、provenance 防火墙、RunManifest 与隔离沙箱，是 v2 的 H1 假设的未来依赖。

本目录是可执行的方法核心，不是通用机器人框架。

| 模块 | 职责 |
|---|---|
| `models.py` | `ConstraintGraph` / 节点 / typed hole |
| `code_agent.py` | 图 → 受限 Python node handlers；只绑定可信 controller registry |
| `runner.py` | `PythonNodePolicy` 逐节点观察—执行—验证 |
| `state_machine.py` | `READY → … → SUCCEEDED/RECOVERABLE/FAILED` |
| `candidates.py` | 不可变 `ActionCandidate` 与选择器 |
| `backends.py` | 主方法 Python 后端；YAML 仅 baseline |
| `servo.py` | 可信高频伺服，只回传有界结果 |
| `manifest.py` | `RunManifest` 可复现元数据 |
| `isolation.py` | 策略隔离：禁网 / 禁特权 API |
| `provenance.py` | 递归拒绝 `privileged_oracle` |

## 本地烟雾

```bash
python3 -m method.demo_graph.examples.m1_fake
python3 -m unittest discover -s method/demo_graph/tests -v
```

插入伺服的高频循环在可信 controller 内完成；生成策略只看到 `ControllerResult` / `ServoOutcome`。
