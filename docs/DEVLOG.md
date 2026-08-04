# 开发日志

只记录最近的工程动作、可复查产物和停点。稳定设计写进 README/API，后续工作写进 TODO/MILESTONES。

## 2026-08-04：单 anchor object perception chain

- graph 几何 hole 增加闭集 resolver 与 object/part anchor；reviewed `insert_tubes` fixture 固定 `tube_mid/right/left → center/right/left hole`，抓取和 tube axis 复用 whole-object anchor，upper-body 只保留为排序约束；
- 新增 Qwen single-box 与 SAM3 binary-mask 只读 client；每次调用保存 request、raw、result 和 call，零框、多框、bbox 映射错误、非二值/全帧/越框 mask 都 fail-closed；
- 新增 mask-first RGB-D 投影、逐点 pixel lineage、`MODEL_PROPOSED` anchor binding、object cloud manifest，以及基于 RGB-D contrast 和 rack ring plane 的 hole center/axis `PASS/UNKNOWN`；
- `planning-record` 现在显式分成 `plan / capture / ground / segment / project / predict`；只有 `grasp_candidate` 能把 object-only cloud 交给 GraspNet，raw detector ID 原样保存且不生成 candidate；
- 每个 live/model step 都重验 graph、objects、embedded stage 与 perception request；project 重新绑定 frozen BGR/JPEG、Qwen box、SAM3 PNG/bool mask，predict 从 frozen depth+mask 重算 cloud/pixel lineage 后才允许调用 GraspNet；part geometry 不写成 whole-object observation；
- 静态核对 5090 现有接口：Qwen 是 OpenAI-compatible chat completion，SAM3 的 JSON `/segment` 接受 base64 image + box prompts，和新 client 契约一致；CLI 识别现有 Qwen/SAM3 环境变量，但非交互 shell 需要先显式加载实验环境；
- 本地与 5090 均为 `376 passed`，主 CLI 与 `planning-record --help` 通过，`git diff --check` 通过；本轮没有调用 Qwen、SAM3、camera、GraspNet、simulator、planner 或 control。

当前停点：V1 一个 record 只处理一个 anchor，尚不能在同一 observation 下组装一个 stage 的多个 holes；identity 仍是 `MODEL_PROPOSED`，camera/tool frame 标定和 candidate conversion 未接。下一步先在正确 `insert_tubes` scene 上做只读 component 验证，再设计父 observation + 多 anchor 子任务；执行继续关闭。

## 2026-08-04：首个只读 head → raw GraspNet record

### 完成的代码

- 新增 `perception/live_sources.py`：一次性 head capture、米制 depth 反投影、固定四次 `get_qpos/get_xquat` 读取，以及仅允许 loopback 的 GraspNet health/predict client；没有通用 action、planner 或 control 接口；
- 新增 `execution/planning_record.py` 与 `dgl planning-record`，显式分成 `plan / capture / predict`。默认 plan 零网络，两个 live step 都要单独给 `--allow-live-read`，没有一键跨过检查点的入口；
- observation 保存左右 BGR、float32 米制 depth、OpenCV head optical-frame 点云、完整 projection manifest、严格 binding manifest、内参和本体状态；
- raw GraspNet validator 保留原始 detector ID；删除了没有 assignment evidence 的旧 mapping converter，当前不发布 GraspNet→graph candidate 路径；
- `HTTP 200 + ok=false`、fixture backend、未就绪 health、schema/backend/input echo 漂移全部 fail-closed，并保留原始 payload 和调用状态。

### 5090 实跑产物

Record：`/home/knowin-sim/demo-graph-lab-workflow/runs/planning_records/20260804_181047`

- 状态：`RAW_GRASPNET_RECORDED`；`backend_model_enabled=false`，`execution_enabled=false`；
- observation：`head-134-700896236992`，720×1280 depth，921,600 个 finite 且 `z>0` 的米制 optical-frame 点；
- capture：一次同步 head render + 两臂 qpos/xquat，0.45 s；传感副作用完整写入 `sensor/call.json`；
- GraspNet：真实 baseline health ready，一次 predict 0.51 s，返回 20 个通过 raw schema/17D 一致性检查的 proposal；20/20 `object_id=-1`；
- 没有生成 `candidates.json`、hard-check certificate、replay、motion plan 或 action；
- 当前 simulator 运行的是 `scenes/smoke/stand_up_bottle.scene.yaml`，而 plan 引用 `insert_tubes` graph。主方法没有读取 `/state` 核验场景，因此 manifest 明确保留 `scene_identity_unverified`；这份 record 是 infra smoke，不是 insert-tubes 效果数据。

GraspNet 缺失的 `pointnet2._ext` 和 `knn_pytorch` 已在现有 venv 中按 RTX 5090 编译并通过模型 import；没有修改上游源码。构建日志位于 `/home/knowin-sim/dgl-perception/logs/`。服务只临时绑定 `127.0.0.1:8092`，record 完成后已关闭。

### 验证与停点

- 本地：`326 passed`，两个 CLI help、`planning-record --help` 和 `git diff --check` 通过；
- 5090：同一套 `326 passed`；record 的 observation、point-cloud binding、health、request、raw response、validation 和 call artifacts 均可复查；
- 本轮没有调用 backend model、simulator `/state`、reset、官方 task probe、motion planner 或 control；
- 当前停在 raw response。下一步必须先做任务匹配的 object mask/assignment，以及 lift-aware camera→robot-base 和 grasp→runtime-EEF 标定；在此之前不生成真实 candidate，更不进入执行。

## 2026-08-04：planning-only 候选契约与固定 replay

### 完成的代码

- 增加严格的 observation/candidate record adapter；多余或缺失字段、NaN/Inf、数值位置的 bool、空或重复证据都会失败；
- 增加 recorded GraspNet `/predict` raw validator：校验真实 schema、17D raw array、米制 point-cloud manifest、frame 与 observation identity；没有可信 object assignment 时不输出 graph candidate；
- Candidate 绑定 `observation_id`，几何 hole 统一使用 `{value, frame, calibration_ref, object_id}`；pose quaternion 和 axis 必须单位化；
- typed-hole 校验位于所有物理 checker 之前。类型、shape、frame、标定或对象不合法时，reachability/collision/width 不运行并留下 `UNKNOWN not_run` 证书；
- `PlanningOnlyRuntime.solve()` 做第二次 binding 校验；validated StageProgram 的 hole wiring 可直接决定每阶段 required holes；candidate provider 不能填写 scalar/runtime condition；
- 增加 synthetic fixed replay 和 `dgl planning-replay`：三个 hard-check certificate 只过滤一次，demo 与 candidate-ID baseline 共用 accepted candidates，输出一个 comparison JSON；
- replay fixture 明确标记 `synthetic_contract_fixture`，不作为真实效果或执行结果。

### 5090 只读接口盘点

- head RGB-D 与米制 depth 的读取路径存在；hand camera 的实时 EEF frame transform 尚不完整，暂不接；
- 实际 GraspNet 服务使用 `/predict` + point-cloud path，旧 `/propose` client 不兼容；输出不含 graph object ID、`approach_tilt_deg`、`height_fraction` 或碰撞结果；
- IK 会先 clip 越界 target，现成 planner wrapper 又丢失 success；两者当前都不能直接签发可信 reachability `PASS`；
- K1 gripper 只有 motor angle，缺米制 opening-width 标定，width checker 当前必须为 `UNKNOWN`；
- 没有找到同时含 RGB、depth、calibration、proprioception、candidates 和三个 certificates 的真实 replay，需要新采集。

### 验证与停点

- 本地 `305 passed`；两个 CLI help、planning replay CLI 和 `git diff --check` 通过；
- synthetic replay 接受 `c00/c01/c02`，固定 ID baseline 选 `c00`，demo region/cone 选 `c01`；这里只验证对照逻辑能改变 top-1，不报告方法效果；
- 本轮只静态读取远端代码，没有调用 HTTP/API、capture bridge、`/state`、reset、backend、pipeline 或 control，也没有启动 simulator；
- 当前停在 frozen planning replay。下一步是只读采集第一份真实 observation；在真实 checker 与 gate/abort 审查完成前继续保持 `ExecutionDisabled`。

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
