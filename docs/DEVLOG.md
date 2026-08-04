# 开发日志

只记录最近的工程动作、可复查产物和停点。稳定设计写进 README/API，后续工作写进 TODO/MILESTONES。

## 2026-08-04：离线 workflow 与在线脚手架

### 远端保护

- 5090 原目录：`/home/knowin-sim/demo-graph-lab`；
- 完整归档：`/home/knowin-sim/archives/demo-graph-lab-pre-workflow-2026-08-04.tar.gz`；
- 归档 98 MB、权限 600，通过 gzip 和目录清单检查；原目录未移动或删除；
- 本轮代码和离线实验放在旁路目录 `/home/knowin-sim/demo-graph-lab-workflow`。

### 完成的代码

- 新增 `docs/OFFLINE_WORKFLOW.md`，集中说明从视频导入到 policy 编译的阶段、产物和 backend 调用记录；
- 四类离线 backend call 统一记录脱敏 request、input refs、raw、parsed、validator、实际模型、耗时和成本；
- stage split 全视频均匀采样；registry、constraint sample、hole 和 object ref 严格校验；
- 同一 run 在脱敏 request 完全相同时复用已完成 raw reply；prompt、model 或参数变化会重新调用；
- 图像内容进入脱敏指纹；cache 只复用 `status=ok` 的完整调用，同 tag 重调会保留旧记录；
- backend 只生成 `StageProgram`，可信代码校验 primitive sequence / hole wiring 并确定性生成 Python；
- final graph 必须完整对齐 stages manifest，holds/frame/evidence 投票与校验 fail-closed；compiler 会重验当前 graph，dry-run 成功后才发布 policy；
- stage manifest 会在抽帧前拒绝重复编号、重叠窗口和视频越界；final graph 强制记录请求数 `k` 和每阶段有效数 `k_valid`，不能跳过多数票；
- gate 对 `acceptance` 和 `constraints` 都做三值合取；`throughout` 与 `at_end` 独立记账，前者必须通过入口和出口检查；
- 新增 `purpose=lower_stop` 控制契约和独立的 `retreat(target)` opcode；validator 会拒绝错误接线。可信 retreat pose solver 尚未实现，Oracle 在运动前明确拒绝；停止信号的明确路由也仍列为执行前 TODO；
- 新增 planning-only 在线路径：typed observation/proprioception、immutable candidates、fail-closed hard filter、确定性排序、decision JSONL 和 opaque handles；
- 所有在线控制原语默认抛 `ExecutionDisabled`，runtime backend 固定关闭。

### `insert_tubes` 离线实跑

最终 run：`/home/knowin-sim/demo-graph-lab-workflow/runs/insert_tubes/20260804_122155`

- 6 stages、30 stage keyframes、5 个 registry objects；
- constraint extraction：每阶段 5/5 有效，30/30 raw replies 通过 schema；
- graph：45 个 constraint/acceptance items，0 violations，0 warnings；
- backend calls：1 registry + 30 extraction + 2 program proposals；第一次 program proposal 被 type/order validator 拒绝，修正 API 契约后第二次通过；
- `cost.jsonl` 保留 33 次调用；当前 `model_calls/` 有 32 份最终 call artifact，因为第一次无效 program 发生在 history 保留机制接入前并被同 tag 重调覆盖。后续重调不会再覆盖；
- 累计 backend 成本：约 USD 1.65；
- deterministic compile：program/static violations 均为 0；
- FakeRuntime：normal path 与 fail-once retry path 都通过，16 个 holes 被求解；
- `compile_report.json` 仍显式列出 unwired scalar、重复 stage holes 和 gate-only conditions，未把它们伪装成已消费。

实跑中修复了三类真实问题：dot-style hole 名与 snake_case validator 不一致；`carry.relation` 的 registry-id 边界误判；两阶段分组使用半数阈值会传播互相矛盾的 approach cone，现改为严格多数。

### 验证与停点

- 本地：`217 passed`，两个 CLI `--help` 通过，`git diff --check` 通过；
- 5090：`217 passed`，两个 CLI、归档和权限检查通过；对既有 run 做只读复核，30/30 raw samples、manifest、registry、45 个 graph items、StageProgram、当前确定性 policy 和两条 FakeRuntime dry-run 全部通过；
- 既有 run 的 `compile_report.json` 早于“报告精确绑定完整 StageProgram”规则。新 Oracle loader 会拒绝直接执行它；没有手工升级旧报告，执行前须在明确确认后重新通过 compile gate；
- 本轮没有启动 simulator，没有读取 `/state`，没有调用 pipeline/control，也没有下发机器人动作。

执行前 blocker：真实 sensor/candidate/check adapters、candidate hole type/frame/calibration 校验、固定 replay、单 stage gate/abort 检查。完成并评审这些项目后，才能把 planning-only runtime 接到控制层。
