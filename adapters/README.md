# adapters：可信运行时边界

> **代次**：v1（2026-07-26 定型），但**含唯一一条通往 v2 主线的活边** · **主线**：部分
> **与主线的依赖边**：`harness/kwadapter.py:17 → adapters/knowin_world/pipeline.py`
> （66 行纯 stdlib HTTP 客户端）。这是 harness 与本目录之间的**全部**代码通路。
> **导入策略**：`__init__.py` 已于 2026-07-30 改惰性（PEP 562），`import harness.kwadapter`
> 不再拖起 `method.*` 的 13 个模块。

本目录只放**宿主侧**适配器，不把 Knowin World / GraspNet 源码或权重拷进仓库。

| 子包 | 职责与边界 | 状态 |
|---|---|---|
| `knowin_world/` | EvalServer `reset/skill/finalize`、开发态 pipeline `/run`、runtime doctor | `pipeline.py` **活**（Phase 1 在用）；`adapter.py` 的 `execute_skill` 范式已过时，但 `reset`/`finalize`/失败分类学无替代 |
| `demo_bundle/` | 加载脱敏演示证据 JSON。内容含 `privileged_oracle` / `exact_pose` / `entity_id` 等标记时**直接拒绝**，避免特权字段混入主方法 | 被 harness 代码显式点名为「将来接入点」 |
| `grasp_proposals/` | GraspNet 的方法侧薄封装。真实 `graspnet-baseline` 源码与权重**不得**进入本仓；未配置外部 endpoint 时 **fail-closed**，不用 GT 位姿顶替 | 当前无 importer，但正对准在飞的 GraspNet 移植（见 `../harness/DESIGN_GRASP_AND_LOOP.md`） |
| `observability/` | Method Broker 审计落盘与 `RunManifest` 汇总。只写脱敏摘要，不把原始 runs/视频/大包观测提交进 Git | 服务 H1 冻结协议，harness 侧零等价物 |

> ⚠️ `adapter.py`（本目录）与 `harness/kwadapter.py` **方向相反**：前者是 GT 防火墙客户端、
> **拒绝** `GET /state`；后者是 ORACLE-M1A 模式下的 `/state` 读取器。名字像，用途相反。

退役条件与逐模块理由见 `../method/README.md` 的通用规则与 `../docs/DECISIONS.md`。

根模块还保留：

- `contracts.py`：方法可见证据契约
- `method_broker.py`：Allowlisted Method API 与 provenance 审计
- `m1_bindings.py`：把 Broker 接到 `PythonNodePolicy`

Oracle / evaluator 输出只能经 `KnowinWorldAdapter.finalize()` 进入隔离评测，**禁止**注册进 `MethodBroker`。
