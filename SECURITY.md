# 安全与发布策略

**仓库形态（2026-07-29 起）**：主仓是**内网 Gitea 私有仓**（remote `gitea`）。GitHub `origin`
已停止维护，**其上已推送的内容一律视为已公开**。

分两档要求：

| | 私有主仓（当前） | 对外公开（将来投稿/开源时） |
|---|---|---|
| 凭证、令牌、私钥 | **禁止** | 禁止 |
| 模型权重、数据集、runs 原始产物、>10 MiB 文件 | **禁止** | 禁止 |
| 许可证禁止再分发的第三方源码 | **禁止** | 禁止 |
| 内部主机 / 端口 / NAS 路径 | **允许**（文档要能直接用） | 必须脱敏 |
| 手写研究资产（`oracle/`、`tools/`、`*_AUDIT.md`） | **应当纳入版本控制** | 按需筛选 |

门禁：`python3 scripts/public_release_check.py`（默认 `--profile private`，内部 endpoint 只报
WARN）；**对外发布前必须跑 `--profile public` 且清零**。

> 2026-07-30 记：`oracle/`、`tools/`、`*_AUDIT.md` 此前被误列进 `.gitignore` 的「可再生成产物」
> 段，导致 `PREDICATE_AUDIT.md` 与 `PROVENANCE_CORRECTION.md` 永久丢失、`PRIMITIVE_API_AUDIT.md`
> 在实验机上取不到。已移出并纳管。**把手写资产排除出版本控制不是安全措施，是数据丢失。**

## GT 防火墙

生成策略只能使用：

- 任务指令与演示证据；
- 带 provenance 的传感器感知结果；
- 机器人可观测状态与动作反馈；
- allowlisted、任务无关先验。

禁止读取 scene/asset 库、仿真实体状态、精确位姿/尺寸、评测谓词、目标绑定，或由上述来源派生的字段。Oracle 评测在隔离进程中运行，其结果不得回流到策略生成、选择、恢复或执行。

> **防火墙约束的是运行时数据流，不是版本控制。** `oracle/`（人工手写的上界基准图）纳入 git
> 不违反本节——只要方法代码不在运行期读它。同理，Phase 1 的 `ORACLE-M1A` 模式产物可以留档，
> 但**不得报为方法结果**（`harness/kwadapter.py:19` 的标签即为此）。

## 运行时边界

- 实验场地：**5090 服务器**（仓库 checkout + `~/phase1` 运行目录），2026-07-29 起自 1022 迁出。
  对外名是 `demo-graph-lab`，不是 ksm。
- 外部共享依赖（Knowin World / 仿真数据 / NAS 基础仓）：可只读借用数据与 venv；不得写入、
  部署、改配置或启停其服务。历史上误部署到 NAS 树下的工作副本已作废。

## 发布前检查

1. 只按 allowlist 暂存；永远不要 `git add .`
2. 扫描凭证与内部 endpoint
3. 拒绝权重、数据集、runs 输出与 >10 MiB 文件
4. 核验导入组件 SOURCE_MANIFEST
5. 跑通单测、集成测与 GT 防火墙测

敏感运行时配置放在被忽略的 `configs/local/`；已提交配置只能是带占位符的示例。
