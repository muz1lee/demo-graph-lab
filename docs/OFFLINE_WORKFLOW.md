# 离线 Workflow 与产物

本文描述从 demonstration video 到 `policy.py` 的离线处理链，以及每一步实际写出的实验产物。这里的“阶段”指 workflow 的处理步骤，不是视频里的 grasp、lift、insert 等动作阶段。

当前边界是：离线 demo 理解、约束图、`StageProgram` 和 fake dry-run 已经接通；这些结果不代表 simulator 或机器人执行成功。

## 产物目录

每次实验默认写入一个独立目录：

```text
runs/<task>/<timestamp>/
```

完整流程分为两条命令：

```bash
dgl all --task <task>
dgl compile --task <task>
```

`dgl all` 负责从视频生成并验证约束图，`dgl compile` 负责生成 `StageProgram`、确定性编译 policy 和 fake dry-run，并在 policy 发布之后追加一段 `PerceptionProgram` 编译。

## 总体流程

```text
video + optional trace
  → ingest
  → stage split
  → keyframes
  → object registry
  → per-stage constraint extraction
  → deterministic enrich
  → graph validation + report
  → StageProgram proposal
  → deterministic Python compilation
  → static check + FakeRuntime dry-run
  → PerceptionProgram proposal
  → validation + FakePerceptionRuntime dry-run
```

| 步骤 | Backend model | 主要产物 |
|---|---|---|
| 视频导入 | 否 | `meta.json`、`trace.json`、`frames/` |
| 动作阶段切分 | 有 trace 时否；无 trace 时调用 | `stages.json`、可选 `stages_proposed.json` |
| 阶段关键帧 | 否 | `keyframes.json`、`frames/stageXX/` |
| 对象 registry | 调用 | `objects.json` |
| 逐阶段约束抽取 | 每阶段调用 `k` 次 | `samples/stageXX.json`、初始 `graph.json` |
| 确定性补全 | 否 | 更新后的 `graph.json` |
| 图验证与报告 | 否 | `validation.json`、`report.html`、可选 `metrics.json` |
| StageProgram 提议 | 调用 | `stage_program.json` |
| 确定性编译与 dry-run | 否 | `policy.py`、`compile_report.json`、编译快照 |
| PerceptionProgram 提议 | policy 发布后调用一次；无可发布 hole 时不调用 | `perception_program.json` |

## 1. 视频导入

入口：

```bash
dgl ingest --task <task> [--video <video>] [--trace <trace.json>]
```

输入包括 demonstration video，以及可选的上游 action trace。该步骤不调用 backend model。

产物：

```text
meta.json
trace.json          # 只有找到或显式传入 trace 时存在
frames/
```

`meta.json` 保存：

- 视频路径；
- fps、总帧数和时长；
- 全视频均匀采样帧及其 artifact 路径；
- trace 来源。

`frames/` 中的全视频采样帧供无 trace 的阶段切分和对象 registry 使用。

## 2. 动作阶段切分

入口：

```bash
dgl stages --task <task>
```

有 `trace.json` 时，代码确定性转换上游 segments；没有 trace 时，backend VLM 根据全视频均匀采样帧提出阶段边界。

最终产物 `stages.json` 的每个条目至少包含：

```json
{
  "index": 0,
  "name": "grasp",
  "label": "gripper closes on the tube",
  "start_sec": 0.0,
  "end_sec": 2.1
}
```

无 trace 时还会保留：

```text
stages_proposed.json
model_calls/stage_split/
```

阶段清单在抽取关键帧前检查：

- index 必须是唯一的非负整数；
- 时间窗口不得倒序或重叠，共享边界允许；
- `end_sec` 不得超过视频时长。

## 3. 阶段关键帧

入口：

```bash
dgl keyframes --task <task> --per-stage 5
```

该步骤按每个 stage 的时间窗口做确定性均匀采样，不调用 backend。

产物：

```text
keyframes.json
frames/stage00/
frames/stage01/
...
```

`keyframes.json` 记录每一帧的 `frame_idx`、时间和文件路径。后续 backend 输出的 `evidence_frames` 只能引用真正展示给它的这些帧。

## 4. 对象 Registry

入口：

```bash
dgl objects --task <task>
```

Backend VLM 根据全视频采样帧和 trace aliases 建立稳定对象 ID。

产物：

```text
objects.json
model_calls/registry/
```

对象条目示例：

```json
{
  "id": "tube_left",
  "category": "tube",
  "distinguishers": "left tube at the beginning",
  "trace_aliases": ["tube0"],
  "first_seen_frame": 12
}
```

后续 graph、`StageProgram` 和 runtime observation 必须引用 registry ID，不能临时发明对象名。

## 5. 逐阶段约束抽取

入口：

```bash
dgl extract --task <task> --k 5
```

每个 stage 调用 backend VLM `k` 次。每次回复提出：

- `stage_objects`：当前 manipulated object 和 target；
- `constraints`：动作期间或结束时必须保持的关系；
- `acceptance`：阶段结果的可检查条件；
- `holes`：运行时需要从新场景求解的数值缺口；几何 hole 还可声明闭集 `resolver` 和结构化 object/part `anchor`。

原始样本产物：

```text
samples/stage00.json
samples/stage01.json
...

model_calls/extract_s0_k0/
model_calls/extract_s0_k1/
...
```

聚合产物：

```text
graph.json
```

`graph.json` 保存：

- 请求次数 `k`；
- 每阶段有效回复数 `k_valid`；
- 每阶段洞级丢弃统计 `hole_drops`（`count` 丢了几个、`reasons` 错误类型分布、`dropped` 逐条 hole 名与错误）；
- 多数票保留的 constraints、acceptance 和 holes；
- `confidence`、`evidence_frames`、`provenance`；
- `throughout` 或 `at_end` 的时间语义；
- hole 的 name、type、frame、solver hint、可选 purpose，以及几何 hole 的 resolver/anchor。

无效回复不参加投票，但多数票分母仍使用请求次数 `k`。因此不能通过丢弃失败回复降低通过门槛。

样本校验分两级粒度：**约束级**错误否决整个样本，该回复不计入 `k_valid`，也不投任何票；**洞级**错误只丢那一个洞（同名洞按分不清对错处理，同名的都丢），样本的其余部分照常参与投票。被丢的洞不参加洞投票，所以在多数样本里都写坏的洞自然到不了 `k//2+1` 阈值，不会进入最终图；`hole_drops` 让洞级错误率单独可见，不再藏在 `schema_fail` 里。进入 `graph.json` 的洞仍由 `dgl validate` 全严校验，采样阶段放宽的只是粒度，不是最终契约。

## 6. 确定性图补全

入口：

```bash
dgl enrich --task <task>
```

该步骤不调用模型，直接更新 `graph.json`。当前只做确定性的保守补全：

- 从多个同类阶段的严格多数模式传播缺失关系；
- 添加阶段顺序约束；
- 为需要下放停止条件的阶段补 `purpose=lower_stop` 的 runtime-condition hole；
- 修复可以从来源明确确定的 derived metadata。

Derived 项不会继续作为下一轮传播来源，避免链式放大模型猜测。

## 7. 图验证、报告与指标

入口：

```bash
dgl validate --task <task>
dgl report --task <task>
dgl metrics --task <task> --gold <gold.json>
```

主要产物：

```text
validation.json
report.html
metrics.json         # 只有运行 metrics 时生成
```

`validation.json` 示例：

```json
{
  "task": "insert_tubes",
  "items_checked": 45,
  "violations": [],
  "warnings": [],
  "passed": true
}
```

验证范围包括：

- graph 与完整 stage manifest 精确对齐；
- `k` 和每阶段 `k_valid` 达到严格多数；
- constraint、acceptance 和 hole 使用闭集词表；
- object 参数引用 registry；
- evidence frame 位于视频内；
- hole name、type、frame、purpose、resolver 和 anchor 合法；
- graph 中不包含世界坐标、距离、角度等度量字面量。

验证失败时 CLI 返回非零，并删除可能误导下游的旧 program、policy 和编译报告。

## 8. StageProgram 提议

入口：

```bash
dgl compile --task <task>
```

只有当前 `validation.json` 通过，且 compiler 再次验证当前 graph 后，才会调用 backend。

Backend 不写 Python，只提出结构化 `StageProgram`：

```json
{
  "stages": [
    {
      "index": 0,
      "name": "grasp",
      "actions": [
        {
          "op": "grasp_at",
          "args": {
            "grasp_pose": {"hole": "tube_grasp_pose"}
          }
        }
      ]
    }
  ]
}
```

它只决定：

- primitive sequence：调用哪些高层动作及其顺序；
- hole wiring：动作参数连接哪个 object、typed hole 或离散枚举值。

产物：

```text
stage_program.json
model_calls/compile/
```

Validator 会检查 stage 对齐、primitive 白名单、动作顺序、API 参数、object 引用、hole 类型、`lower_stop` purpose、release/retreat 语义和数值字面量。

## 9. 确定性 Policy 编译与 Fake Dry-run

`StageProgram` 通过后，可信 compiler 才生成：

```text
policy.py
compile_report.json
compiled_graph.json
compiled_objects.json
```

这里才是当前 code-as-policy 路径真正写出 Python 的时刻。Backend model 负责提出 program，Python 文本由固定 compiler 生成，因此同一份 graph 和 `StageProgram` 会得到同一份 `policy.py`。

发布 `policy.py` 前会依次执行：

1. `StageProgram` schema 和语义检查；
2. 确定性 Python 生成；
3. AST 静态检查；
4. FakeRuntime normal dry-run；
5. FakeRuntime fail-once retry dry-run；
6. 确认 graph 和 objects 在 backend/dry-run 期间没有变化。

`compile_report.json` 保存：

- graph validation 状态；
- program 和静态检查 violations；
- unwired holes；
- 两条 dry-run 的结果和调用记录；
- 实际通过 dry-run 的完整 `StageProgram`；
- `perception_program` 段（只有 policy 发布后才存在，见第 10 节）。

Oracle loader 会将当前 graph、objects、StageProgram 和确定性生成的 policy 与这些编译证据精确比较。任何不一致都会拒绝执行。

## 10. PerceptionProgram 提议

`policy.py` 发布之后，`dgl compile` 追加第二段编译：backend 提出 `PerceptionProgram`，决定几何 hole 由哪条闭集感知链发布。契约见 `docs/API.md` 第 6 节。

覆盖目标不是 graph 里的全部几何 hole，而是 `StageProgram` 真正接线、类型为几何且 `resolver ∈ {part_center, part_axis, principal_axis}` 的那些。prompt 里的算子表与 resolver 绑定表由代码从 `perception/program.py` 渲染，不留第二份副本。

产物：

```text
perception_program.json
model_calls/compile_perception/
```

发布门依次是：

1. `validate_perception_program` 零违规；
2. `FakePerceptionRuntime` 干跑通过。

两者都过才写 `perception_program.json`。任何一步失败都不发布感知程序，violations 和原始回复照常落在 `compile_report.json` 与 `model_calls/compile_perception/`。感知程序是纯增量产物：未发布时相关 hole 继续走 graph resolver 老路，`stage_program.json`、`policy.py` 和 CLI 退出状态都不受影响。

`compile_report.json` 的 `perception_program` 段：

```json
{
  "status": "published",
  "ref": "perception_program.json",
  "violations": [],
  "coverage": [
    {"stage": 0, "covered": ["opening_axis"], "uncovered": ["peg_grasp_pose"]}
  ]
}
```

`status` 为 `published / failed / skipped`。被接线的几何 hole 里没有任何可发布目标（全是 grasp 或 motion 类）时是 `skipped`，此时不调用 backend；`coverage` 只在 `published` 时非空，来自 `coverage_by_stage`，是记录而不是准入判据——未覆盖的 hole 不是违规。

运行时消费者是 `planning-record --step programs`（`execution/program_record.py`）。它不是离线步骤，也不写进 run 目录：它在一个已 `plan + capture` 的 record 目录上执行已发布的文档，产物落在那个 record 目录里。

```text
<record-dir>/
├── programs/
│   ├── observation_input.jpg      父 observation 冻结的唯一一份 JPEG
│   ├── call.json                  本步的授权、服务地址、程序数与耗时
│   └── p<stage>_<index>/          每个程序一个 anchor 子任务
│       ├── grounding/             request.json / raw.json / result.json
│       ├── segmentation/          request.json / raw.json / mask.png / mask.npy /
│       │                          mask_record.json / result.json
│       ├── geometry/              request.json / result.json，外加终点算子的载荷：
│       │                          crop_points → pointcloud.npz / pixels_rc.npy /
│       │                          assignment.json / cloud_manifest.json；
│       │                          fit_opening → opening_geometry.json；
│       │                          fit_axis → principal_axis.json
│       └── call.json              本程序的状态、reason 与 failed_step
└── program_results.json           每个被 provide 的 (stage, hole) 一条 envelope
```

`program_results.json` 的 envelope 形状、`camera_head_optical` 的诚实 frame、`MODEL_PROPOSED` identity 和 all-or-nothing 失败语义见 `docs/API.md` 第 6 节的「执行」小节。

这些 optical 值再经两个**本地**步骤才成为 base 系候选绑定（同样在 record 目录里，零网络）：

```bash
dgl planning-record --record-dir <dir> --step identity-accept \
  --program p1_1 --object-id rack --accepted-by <name> --basis <evidence>

dgl planning-record --record-dir <dir> --step project-base \
  --extrinsics <camera_extrinsics.json>
```

```text
<record-dir>/
├── identity_acceptance.json    人对 (program, object_id) 的显式接受,含 by/basis/框/证据目录
└── base_frame_values.json      每个 (stage, hole) 一条 robot_base envelope,
                                含 status/reason、identity_accepted、
                                source_frame/source_value 与外参 ref
```

`project-base` 需要一份 `demo_graph_lab.camera_extrinsics.v1` 记录（不在 record 目录里，是独立的标定产物），并从该次 observation 自己的 `proprioception.json` 读 `lift_position_m`：拿不到同时刻的升降读数时 `point_3d` 洞记 `UNKNOWN`，`axis_3d` 洞不受影响（方向只吃 `R`）。manifest 推进到 `BASE_VALUES_PROJECTED`，重跑允许——多接受一个身份就该能多出一个候选值，不必重采。schema、拒绝规则、质心禁令与身份闸门见 `docs/API.md` 第 7 节。

## Backend 调用公共产物

每次 backend 调用都记录在：

```text
model_calls/<tag>/
├── request.json    脱敏后的完整请求和输入引用
├── raw.txt         原始回复
├── result.json     JSON 解析和 schema 校验结果
├── call.json       模型、耗时、token、成本和状态
└── history/        同 tag 再次显式调用的历史
```

`request.json` 不保存内嵌图像字节，只保存稳定指纹和实际输入 artifact 引用。只有 request 完全一致、`call.json` 为成功且 parsed result 未失败时，raw reply 才可复用。

同一 run 的总成本记录在：

```text
cost.jsonl
```

`cost.jsonl` 是追加式调用账本；每行对应一次已付费 backend 调用。

## 完整目录示意

```text
runs/<task>/<timestamp>/
├── meta.json
├── trace.json
├── frames/
├── stages.json
├── stages_proposed.json
├── keyframes.json
├── objects.json
├── samples/
│   ├── stage00.json
│   └── ...
├── graph.json
├── validation.json
├── report.html
├── metrics.json
├── stage_program.json
├── policy.py
├── perception_program.json
├── compile_report.json
├── compiled_graph.json
├── compiled_objects.json
├── model_calls/
└── cost.jsonl
```

其中 `trace.json`、`stages_proposed.json`、`metrics.json` 和 `perception_program.json` 是条件产物，不保证每个 run 都存在。编译失败时也不会发布 `policy.py` 和编译快照；感知编译失败或被跳过时不会发布 `perception_program.json`。
