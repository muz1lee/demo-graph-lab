# OVERVIEW —— demo → graph → code → 执行 的逐跳细节视图

- 文档日期：2026-07-30
- 适用 checkout：`demo-graph-lab` @ `3f603d1`（工作区干净，45 个 commit）
- 定位：**细节视图**。想知道「这个方案是什么、为什么」看 `PROPOSAL.md`；想知道「进度和数字账本」看 `PROGRESS.md`；本文只回答「每一跳的输入/输出/由哪个文件哪一行实现，以及今天哪些地方是空的」。
- 行号可信度：本文所有 `file:line` 均在 2026-07-30 于上述 commit 上逐条核对。代码一改行号即失效，引用前请重新 `grep`。

---

## 0. TL;DR（先读这五条）

1. **链路是通的，但语义链路是断的。** demo → graph → policy.py → 执行 四跳的**管道**每一跳都有真实实现和真实产物；但图里的**约束内容**在运行期几乎不消费——`stage['constraints']` 整块不参与任何运行期判定，`solve()` 只对洞的**名字字符串**做子串匹配。
2. **最重要的单条缺口**：`region_grasp(tube_left, upper_body)` 今天不影响抓取。把图里 `region` 改成 `bottom` 或 `rim`，产生的抓取位姿**逐比特相同**。要改哪一行见 §8。
3. **唯一有 LLM 的是编译期**（Phase 0 + compile 共 32 次调用/任务量级）。运行期（Phase 1）零 LLM，两级 ReAct 的循环骨架写死在 `harness/fakerun.py:60` 的可信 runner 里，不在生成代码里。
4. **Phase 0 已达标且数字可复核**（micro P=0.931 / R=0.865，本次盘上重算一致）；**Phase 1 只有 ORACLE-M1A 上界模式跑通了软件链，零次真实抓取成功**——所有 Phase 1 数字的产物在 5090，本 checkout 里没有。
5. `harness/kwadapter.py` 是全仓最脆的文件：619 行、全仓 8 个 commit 改过它且全部集中在最近 20 个 commit 内、**零测试覆盖**。

---

## 1. 全景与文件地图

```text
Phase 0（mac / 5090，编译期，唯一有 LLM 的阶段）
  demo 视频 ─ingest─→ frames/ + meta.json
            ─stages─→ stages.json      (trace.json 优先；无 trace 才 vlm_split)
         ─keyframes─→ keyframes.json   (每阶段 5 帧)
           ─objects─→ objects.json     [LLM ×1, 12 帧]
           ─extract─→ graph.json       [LLM ×(stage 数 × k)，k=5 自一致性]
            ─enrich─→ graph.json'      (确定性传播，无 LLM)
          ─validate─→ validation.json
            ─report─→ report.html      (人工金标标注 UI)
           ─metrics─→ metrics.json     (对金标算 P/R)

  ── 分界：compile 不在 `all` 里，必须显式子命令 ──

           ─compile─→ policy.py + compile_report.json   [LLM ×1]

Phase 1（5090，运行期，零 LLM）
  policy.py + graph.json ─phase1.py─→ exec → STAGES{idx: handler}
      → fakerun.run_policy(可信 runner，两级 ReAct 骨架)
            每阶段：gates.snapshot → handler(rt) → gates.evaluate
      → KWRuntime(contract.Runtime 的实现)
            solve  → EvalServer GET /state   ← ORACLE-M1A 特权态
            ctrl   → pipeline :8000 /run     ← 4 个 ctrl + 2 个 info
            verify → 词表几何检查（10 个约束名只真判 5 个）
      → episode_*.json（落在 5090 的 ~/phase1/artifacts/<task>/）
```

| 目录 | 状态 | 说明 |
|---|---|---|
| `harness/` | **当前主线**（2026-07-29 起） | 本文全部内容 |
| `method/demo_graph/` | v1 遗留 | 仍有 25 个测试在跑；`adapters/__init__.py` 已于 `3f603d1` 改惰性导入，不再被 Phase 1 路径拖起 |
| `adapters/` | 部分在用 | Phase 1 只用 `adapters/knowin_world/pipeline.py` 的 `PipelineClient`（66 行纯 stdlib） |
| `experiments/insert_tubes/` | v1 遗留 | 仍被文档里的测试命令引用 |

四套目录并存是历史债，不是设计。删 `method/` 前要先把 `adapters/tests` 和 `experiments/` 的引用摘干净。

---

## 2. 逐跳：输入 / 输出 / 实现

### 2.1 Phase 0 理解流水线

| 跳 | 输入 | 输出 | 实现 | LLM |
|---|---|---|---|---|
| ingest | 任务名 → 视频 + refined trace | `frames/*.jpg`(24)、`meta.json`、`trace.json` | `harness/ingest.py:89` (`find_video:12` / `find_trace:20` / `sample_frames:38`) | 无 |
| stages | `trace.json` \| `meta.json` | `stages.json` | `harness/stages.py:57`；有 trace 走 `from_trace:15`，无 trace 走 `vlm_split:33`（产物叫 `stages_proposed.json`，**必须人审后改名**） | 兜底路径 ×1 |
| keyframes | `stages.json` + 视频 | `keyframes.json`（每阶段首/末+等分共 5 帧） | `harness/keyframes.py:8` | 无 |
| objects | 全视频 12 帧 + trace 物体提及 | `objects.json`（registry id） | `harness/registry.py:8` | **×1** |
| extract | 每阶段 5 关键帧 + registry | `graph.json`、`samples/stageNN.json` | `harness/extract.py:84`；k 采样循环 `:108`，多数票合并 `merge_samples:36`（`need = ceil(k/2)`，`:39`） | **×(阶段数 × k)** |
| enrich | `graph.json` | `graph.json`（就地覆写） | `harness/enrich.py:44` 同类阶段传播 + `:83` 全图 order | 无（确定性） |
| validate | `graph.json` | `validation.json` | `harness/validate.py:54`；词表校验 `check_item:33`，度量字面量扫描 `_is_metric_literal:12` + `_UNIT_RE:9` | 无 |
| report | 全部产物 | `report.html`（单文件，含金标标注 + `exportGold()` 导出，`:66`/`:75`） | `harness/report.py:98` | 无 |
| metrics | 人工金标 JSON | `metrics.json` | `harness/metrics.py:14`；`P=correct/(correct+wrong)`、`R=correct/(correct+missing)`（`:28-33`） | 无 |

**边界事实**：`harness/cli.py:68-76` 的 `all` 子命令包含 ingest→report 八步，**不含 compile**。编译成 policy 必须显式 `python -m harness.cli compile --task X`（`cli.py:65-67`）。这是有意的分界：理解层的验收（金标 P/R）先过，才谈生成代码。

### 2.2 compile：图 → 代码

| 环节 | 事实 | 实现 |
|---|---|---|
| prompt 组装 | **三段拼接**：`prompts/compile_policy.md` 正文（`split("---",1)[1]`）+ `inspect.getsource(contract)` + 全量 `graph.json` | `harness/compilepolicy.py:81-85` |
| 模型调用 | OpenRouter，默认 `anthropic/claude-opus-4.8`，`T=0.1`，`max_tokens=4000`；**无 tools / 无 function calling**，纯 chat completion | `harness/compilepolicy.py:86-87` → `harness/llm.py:17`，模型默认值 `llm.py:22`，请求体 `llm.py:31-35` |
| 成本闸 | 每次调用记账进 `cost.jsonl`，累计超 `HARNESS_COST_CAP`（默认 $8）抛 `CostCapExceeded` | `harness/llm.py:39-47`、`harness/util.py:58`/`:69` |
| 代码落盘 | 剥 ```` ```python ```` 围栏后**先写盘，再静态检查** | `compilepolicy.py:88-90`（`extract_code:19`） |
| 静态检查 | 三条硬规则：禁 `import`；禁数字字面量（`STAGES` 赋值语句内豁免）；调用只准 `rt.*` 且方法名在 `contract.Runtime` 内 | `compilepolicy.py:29`（禁 import `:43`；数字字面量 `:45-47`；`rt.*` 白名单 `:48-56`，白名单来自 `_contract_methods:13` 的 `inspect.getmembers`） |
| 干跑 | `FakeRuntime` 跑两遍：正常一遍 + 首阶段注入一次 gate 失败测重试分支 | `compilepolicy.py:60-74` |

**单轮、无修复回路**：`static_check` 的结果只写进 `compile_report.json`，**不回喂模型重试**（`compilepolicy.py:90-97`）。若违规，`policy.py` 仍然留在盘上，只是 `dryrun` 字段缺失。

### 2.3 Phase 1 执行

| 环节 | 事实 | 实现 |
|---|---|---|
| 装载 | 读 `graph.json` / `objects.json` / `policy.py`，`exec` 到 `__builtins__={}` 的受限命名空间，取出 `STAGES` | `harness/phase1.py:22-29`（`exec` 在 `:28`） |
| runner | `run_policy(handlers, graph, rt, max_attempts=2)` | `harness/fakerun.py:60` |
| runtime | `KWRuntime(graph, objects, eval_url=:7480, pipe_url=:8000, arm_id=1)` | `harness/kwadapter.py:157` |
| 感知 | `EvalClient.state()` → `GET /state`，短 TTL(0.4s) 缓存 | `kwadapter.py:134` / `:175` |
| 控制 | `PipelineClient.call("ctrl"\|"info", ...)` → `GET :8000/run?action=…` | `adapters/knowin_world/pipeline.py:41` |
| 报告 | `episode_YYYYmmdd_HHMMSS.json`，带 `banner=ORACLE-M1A` 与逐条调用轨迹 | `phase1.py:55-62` |

**实际用到的下游原语只有 6 个**（`kwadapter.py` 内全部 `pipe.call` 点位）：

| 类型 | 名称 | 调用点 |
|---|---|---|
| ctrl | `go_home` | `:366`（作业前把闲置臂归位） |
| ctrl | `delta_move` | `:417`（step_to）、`:525`（lift）、`:548`（lower_until） |
| ctrl | `xquat_move` | `:472`（位姿闭环伺服） |
| ctrl | `set_gripper` | `:502`/`:514`（开/合）、`:578`（release） |
| info | `get_xquat` | `:338` |
| info | `get_ee_extforce` | `:552`（触底判据） |

`_ctrl` 的 docstring（`kwadapter.py:329-331`）记了一个重要的坑：**HTTP 返回值不可信**——ArmNode 日志 `result=FAILED` 时 `action=ctrl` 依然回 `{"ok":true}`。所以每个调用点都必须自己用 `_verify_moved()` / `_cur_xquat()` 回读确认。

---

## 3. 关键数据结构（真实样例，取自 `harness/runs/harness_insert_tubes_20260730_003434/graph.json`）

图的顶层：`{schema: "harness.constraint_graph.v0.2", task, instruction, model, k, stages[]}`，6 个阶段（`pick / insertion / pick / insertion / transport / insertion`），全部 `role=core`，`k_valid=5`、`parse_fail=0`。

### 3.1 constraint（动作侧约束）

```json
{
  "name": "region_grasp",
  "args": { "obj": "tube_left", "region": "upper_body" },
  "confidence": 0.72,
  "evidence_frames": [19, 28, 38],
  "votes": "5/5",
  "provenance": "demo_video"
}
```

derived（enrich 补出来的）长这样，字段固定 `confidence=0.4` / `votes="derived"`，并带 `derived_from`：

```json
{
  "name": "approach_direction",
  "args": { "cone": "side", "target": "tube_left" },
  "provenance": "derived",
  "confidence": 0.4,
  "votes": "derived",
  "derived_from": [2],
  "evidence_frames": []
}
```

| 字段 | 取值域 / 来源 | 谁读它 |
|---|---|---|
| `name` | `vocab.CONSTRAINT_VOCAB` 的 10 个键（`vocab.py:9-20`），封闭词表，禁运行时扩词 | `validate.check_item:36`、`kwadapter.verify:582` |
| `args` | **符号引用，铁律：不得含度量字面量**（`validate.py:49`） | `kwadapter.verify` 的五个分支 |
| `confidence` | `extract.py:52` 聚合：`mean(conf) × 命中数/k` | **唯一影响下游产物的读取点是 `extract.py:58` 的排序**；`report.py:45/54` 仅 HTML 展示；运行期零读取 |
| `evidence_frames` | 合并去重取前 8（`extract.py:53-54`） | `validate.py:69-72` 的时序错位告警 |
| `votes` | `"n/k"` 或 `"derived"` | 仅展示 |
| `provenance` | `demo_video` / `task_instruction` / `generic_prior` / `derived`（`vocab.py:38`） | `validate.py:45`、`enrich.py:59`（禁链式派生） |

### 3.2 acceptance（验收侧约束）

结构同 constraint，**多一个 `holds`**（`throughout` \| `at_end`，`vocab.py:39`）：

```json
{
  "name": "clearance",
  "args": { "obj_a": "tube_left", "obj_b": "table" },
  "holds": "at_end",
  "confidence": 0.74,
  "evidence_frames": [38],
  "votes": "5/5",
  "provenance": "demo_video"
}
```

**关键**：`holds` 字段今天**只被 `validate.py:66-67` 做合法性校验，运行期无人消费**——`gates.evaluate` 对 `at_end` 和 `throughout` 一视同仁，都在阶段结束时判一次（`gates.py:63-69`）。

### 3.3 typed hole

```json
{
  "name": "tube_left_grasp_pose",
  "type": "pose_se3",
  "solver_hint": "grasp region on tube_left upper body from segmentation",
  "frame": "world",
  "votes": "4/5"
}
```

| 字段 | 取值域 | 谁读它 |
|---|---|---|
| `name` | 自由字符串（模型生成） | **`kwadapter.solve:299` 只读这一个字段**，`.lower()` 后做子串匹配 |
| `type` | `vocab.HOLE_TYPES` 5 种：`pose_se3 / axis_3d / point_3d / scalar / runtime_condition`（`vocab.py:25`） | 仅 `validate.py:74-75` 校验合法性；**运行期零读取** |
| `solver_hint` | 自然语言 | **无任何程序消费点**；`.py` 源码里只有 `report.py:58-60` 把它渲染进 HTML 表格 |
| `frame` | `world` / `rack` / `gripper` 等 | **零读取**（连 validate 都不查） |
| `votes` | `"n/k"`（`extract.py:63`） | 仅展示 |

insert_tubes 全图 17 个不重名的洞，其中 6 个跨阶段重名（见 §7 缺口 8）。stage 0 的四个洞覆盖了四种 type：`lift_height`(scalar) / `tube_left_grasp_pose`(pose_se3) / `tube_left_long_axis`(axis_3d) / `grasp_closed_condition`(runtime_condition)。

---

## 4. 生成的 policy.py 长什么样

真实产物 `harness/runs/harness_insert_tubes_20260730_003434/policy.py`（73 行，6 个 handler，静态检查零违规）。stage 0 和 stage 5 原文：

```python
def stage_0(rt):
    # top_down approach for grasp; grasp region baked into grasp-pose hole
    grasp = rt.solve("tube_left_grasp_pose")
    long_axis = rt.solve("tube_left_long_axis")
    closed = rt.solve("grasp_closed_condition")
    height = rt.solve("lift_height")
    rt.approach("tube_left", cone={"cone": "top_down", "target": "tube_left"})
    rt.align("tube_left", "tube_left", axis=long_axis)  # keep long axis vertical
    rt.grasp_at(grasp)
    rt.lower_until(closed)
    rt.lift("tube_left")


def stage_5(rt):
    hole_center = rt.solve("rack_hole_center")
    hole_axis = rt.solve("rack_hole_axis")
    depth = rt.solve("insertion_depth")
    release = rt.solve("release_condition")
    long_axis = rt.solve("tube_long_axis")
    retract = rt.solve("retract_pose")
    rt.approach("rack", cone={"cone": "top_down", "target": "rack"})
    rt.transport("tube_left", hole_center)
    rt.align("tube_left", "rack", axis=hole_axis)  # parallel + center align before insert
    rt.lower_until(depth)
    rt.release()
    rt.transport("tube_left", retract)  # retract clear of rack


STAGES = {0: stage_0, 1: stage_1, 2: stage_2, 3: stage_3, 4: stage_4, 5: stage_5}
```

值得注意的三点：

1. **句柄不透明性在结构上成立**：`grasp`、`long_axis` 只被传递，从不被读出数字——`static_check` 的「禁数字字面量」（`compilepolicy.py:45-47`）让硬编码度量在语法上不可表达。这是本方案最扎实的一条。
2. **solve 出来的句柄有相当一部分被绑了变量就再没用过**：stage_0 的 `height`、stage_1 的 `depth`/`long_axis`、stage_5 的 `release`/`long_axis`/`retract`（`retract` 后面用了）。prompt `compile_policy.md:22` 要求「每个洞用前都要 solve」，但没要求「solve 了必须用」，于是模型倾向于把该阶段全部洞先 solve 一遍。
3. **命名不一致直接漏进产物**：stage_2 用 `rt.solve("tube_right.grasp_pose")`（点号），stage_3 用 `rt.solve("tube_right_grasp_pose")`（下划线）——因为图里这两个洞确实分别声明在 stage 2 和 stage 3，名字就不一样。`FakeRuntime.solve:29-31` 只校验「名字在全图洞集合里」，不校验「在本阶段洞集合里」，所以这不算违规。

**compile_report.json 实测（已核实，产物时间 2026-07-30 01:19）**：`static_violations: []`；干跑 `n_calls=70`、`gates_checked=17`、`holes_solved` 17 个；normal 六阶段全 `passed`；注入失败后 stage 0 变 `passed_retry1`、其余不变。

> **必须警惕**：这一份 `compile_report.json` 的 dryrun 里**没有 `vacuous_pass_total` 字段**——它产生于 `gates.py` 引入（commit `e826e67`）之前。即：这份「全绿」是**旧 gate 语义下的全绿**，不能当作现行两级 gate 的证据。

---

## 5. `contract.Runtime` 的 11 个方法：职责 / 实现 / 参数丢弃情况

单一真源是 `harness/contract.py`（53 行），编译 prompt 直接把它的源码贴进去（`compilepolicy.py:83`）。

| # | 方法 | 契约职责（`contract.py`） | `FakeRuntime`（Phase 0 干跑） | `KWRuntime`（Phase 1，ORACLE-M1A） | 被丢弃的参数 |
|---|---|---|---|---|---|
| 1 | `solve(hole_name)` | 返回**不透明句柄**（`:19`） | 校验名字在全图洞集合内，返回 `Handle`（`fakerun.py:29`） | `kwadapter.py:295` 名字子串匹配 → 五条分支 | 洞的 `type`/`solver_hint`/`frame` 全丢；局部变量 `hole`（`:296`）绑了就没再用 |
| 2 | `residual(constraint)` | 返回残差句柄供阶段内修正（`:23`） | 记日志返回 `Handle`（`:35`） | **软 stub**：只 `_log`，返回 `{"kind":"residual", "constraint": name}`，无感知、无数值（`:323-325`） | — |
| 3 | `approach(target, cone)` | 按离散锥标签接近（`:27`） | no-op 日志（`__getattr__`，`:49-57`） | 闲置臂归位 → 纯位置伺服到物体正上方 `PREGRASP_DZ+CLAW_TIP_DZ`（`:488`） | **`cone` 完全未使用** |
| 4 | `grasp_at(grasp_pose)` | 按位姿句柄合爪（`:30`） | no-op 日志 | 张爪 → 预抓取 → 锁腕姿下探 → 合爪（`:498`） | `grasp_pose["quat"]` 恒为 `None`（`solve:305`），姿态实际取「离当前腕姿最近的竖直姿态」 |
| 5 | `lift(obj)` | 提起（`:33`） | no-op 日志 | 6 步 ×0.02 m 增量抬升 + 逐步回读物体 z（`:517`） | — |
| 6 | `transport(obj, target)` | 携带到目标附近（`:36`） | no-op 日志 | 只把 EEF 移到 `target` 上方（`:532`） | **`obj` 未使用** |
| 7 | `align(obj, target, axis)` | 按 axis 句柄对齐（`:39`） | no-op 日志 | 只把 EEF 移到 `target` 上方 `ALIGN_DZ`（`:537`） | **`obj`、`axis` 均未使用**——「对齐」实际只是换了个高度的 transport |
| 8 | `lower_until(stop_condition)` | 下放直到条件触发（`:42`） | no-op 日志 | 逐步下探，停止判据 = 接触力 >20 N \| `root_in_bbox && axis_aligned` 谓词 \| 高度不再降 \| 12 步预算（`:542`） | **`stop_condition` 完全未使用**——policy 传进来的 `insertion_depth` 句柄被整个丢弃 |
| 9 | `push(obj, contact, toward)` | 非抓取推动（`:45`） | no-op 日志 | **硬 stub**：`raise NotImplementedError`（`:574-575`） | 三个参数全部未使用 |
| 10 | `release()` | 张爪（`:48`） | no-op 日志 | `set_gripper(GRIP_OPEN)` + 1.2 s 等待（`:577`） | — |
| 11 | `verify(constraint)` | 与 gate 同源的判定（`:52`） | 恒 `True`，可注入一次失败（`:39-47`） | 词表几何检查（`:582`） | — |

### 5.1 `KWRuntime.solve` 的五条分支（`kwadapter.py:299-321`）

| 命中条件（对 `hole_name.lower()`） | 返回 | 数值来源 |
|---|---|---|
| 含 `grasp` **且** 含 `pose` | `{kind:"pose", xyz:[质心x, 质心y, AABB顶−0.03], quat:None}` | oracle 实体态 + **硬编码 0.03** |
| 含 `axis` | `{kind:"axis", vec: 物体 quat 的局部 +z}`，解析失败退化为 `[0,0,1]` | oracle |
| 含 `hole`/`slot`/`place`/`target`/`insert_point`/`center` | `{kind:"point", xyz:[目标质心xy, AABB顶]}` | oracle |
| 含 `depth`/`height`/`clearance`/`distance` | `{kind:"scalar", value:0.05}` | **纯硬编码常数，与场景无关** |
| 其余（含 `runtime_condition`） | `{kind:"condition", target, manip}` | 描述子；下游 `lower_until` 并不消费 |

### 5.2 `KWRuntime.verify` 的词表覆盖（`kwadapter.py:582-615`）

| 约束名 | 是否真判 | 判据 |
|---|---|---|
| `axis_vertical` | ✅ | 物体 +z 与世界 z 夹角 <20°（`:584-588`） |
| `axis_parallel` | ✅（简化） | **把孔轴按竖直近似**，实为退化的 axis_vertical，阈值 25°（`:589-593`） |
| `above` | ✅ | z 大小比较（`:597`） |
| `inside` | ✅ | 质心 xy 落在目标 AABB ±0.02 m 内（`:599-603`） |
| `center_align` | ✅ | xy 距离 <0.05 m（`:604-609`） |
| `region_grasp` | ❌ | 落 `else` → `detail="unchecked"`，返回 `True`（`:610-611`） |
| `approach_direction` | ❌ | 同上 |
| `carry` | ❌ | 同上 |
| `clearance` | ❌ | 同上 |
| `order` | ❌ | 同上 |

外加一条：`except` 分支把任何异常也吞成 `ok=True`（`:612-613`，注释写明「oracle 检查失败不误杀」）。**净效果：10 个约束名有 5 个恒真，任何 verify 内部异常也恒真。** 这直接决定了 §6 的空洞性检查有多少真实约束力。

---

## 6. 两级 ReAct 是怎么落地的

**设计原则（`contract.py:6-8`）**：tick 级控制、重试、回退属于**可信 runner**，LLM 不写；验收与动作同源，policy 无法伪造成功。

### 6.1 外层循环 —— `fakerun.run_policy`（`harness/fakerun.py:60-92`）

```text
for st in graph["stages"]:                       # :68
    for attempt in range(max_attempts):          # :76   默认 2
        entry   = gates.snapshot(rt, st)         # :77   ← 阶段入口快照
        handler(rt)                              # :78   ← LLM 生成的那一段
        verdict = gates.evaluate(rt, st, entry)  # :79   ← 阶段出口判定
        if verdict["passed"]: break              # :80
    else: result["ok"] = False                   # :83-84
    if status == "failed":                       # :87
        result["rollback_at"] = idx; break       # :88-89
result["vacuous_pass_total"] = Σ vacuous_pass    # :90-91
```

- **内层**是 `KWRuntime` 各原语里的伺服闭环：`_move`（`:433`）和 `_step_to`（`:401`）都是「下发限幅子目标 → settle → 回读真实位姿 → 重解」的 tick 级循环，带进展判据和卡死检测（`SERVO_*` 常数，`:45-54`）。
- **两级 ReAct 因此是**：外层「快照—动作—验收—重试/回退」由 runner 拿图里的验收约束驱动；内层「指令—回读—修正」由适配器驱动。生成代码位于两级之间，只负责「这个阶段做什么」。
- **回退今天只是标记**：`rollback_at` 写进报告，没有任何实际的状态回滚动作。

### 6.2 `gates.snapshot`（`harness/gates.py:48-57`）

对 `stage['acceptance']` 每一条打上 `_probe="pre"` 标记后调 `rt.verify`，记下**阶段入口就已为真**的集合；同时用 `object_positions(rt)`（`:32-45`）读一份物体位置。`_probe` 标记的作用见 `fakerun.py:43`——入口探针不消耗注入的失败。

### 6.3 `gates.evaluate`（`harness/gates.py:60-117`）两道检查

| 检查 | 判据 | 行 |
|---|---|---|
| **空洞性（vacuity）** | 阶段结束为真 **且** 入口已为真 → 计入 `vacuous_pass`；为真且入口为假 → `informative_pass` | `:72-73` |
| **效果（effect）** | 阶段名命中 `EFFECTFUL_STAGES`（`:19-22`，含 pick/grasp/lift/place/stack/insert/transport/push 等）时，被操作物体位移必须 ≥ `MIN_DISPLACEMENT_M = 0.005`（`:23`） | `:82-94` |

最终 `passed = constraints_hold and (effect_ok or not strict)`（`:110`），`STRICT_DEFAULT=True`（`:24`）。

**动机是实测教训**，写在模块 docstring（`gates.py:3-6`）：stack_bowls 的 0/1/2 阶段判 passed，而三个碗位移全是 0.0000——满足的是 reset 时就已成立的谓词。

**三个必须知道的语义细节**：

1. **空洞性只统计、不否决**。`vacuous_pass` 只是诊断字段，`passed` 的计算里不出现它（`:110`）。一个阶段的验收条目全部空洞通过，`constraints_hold` 依然为 `True`。真正能拦下来的是效果检查。
2. **观测不到就不判效果**。`observable = bool(post)`（`:93`），fake 干跑时 `object_positions` 返回 `{}` → `effect_ok` 恒 `True`（`:94`）。**所以「dry-run 全绿」对物理效果零信息量。**
3. **`stage['constraints']` 一次都没被读**。`snapshot:51` 和 `evaluate:63` 都只取 `stage.get("acceptance")`。

---

## 7. 信息边界（GT 防火墙）在代码里的位置

方案的对照卖点写在 `harness/PHASE1_API_PLAN.md` §4：GaP 把 `sim.check_success` 直接暴露给图、CaP-X 的 privileged API 不与视觉判据类型隔离；本方案要求 oracle 只进 evaluator 与上界，方法路径的 `runtime_condition` 只能来自非特权感知。

### 7.1 标签与边界在代码里的落点

| 位置 | 内容 |
|---|---|
| `harness/kwadapter.py:19` | `ORACLE_BANNER = "ORACLE-M1A"`，注释：「本模式产出一律带此标签，**不得报为方法结果**」 |
| `harness/kwadapter.py:1-7` | 模块 docstring 声明 `solve` 在 M1a 用特权实体态，M1b 换非特权求解器、本类接口不变 |
| `harness/phase1.py:19` | 从 kwadapter 导入 `ORACLE_BANNER` |
| `harness/phase1.py:56` | 每份 `episode_*.json` 的第一个字段就是 `"banner": ORACLE_BANNER` |
| `harness/phase1.py:63` | 终端输出也带 `[ORACLE-M1A]` 前缀 |
| `harness/gates.py:33` | `object_positions` docstring：「**仅 evaluator 侧特权数据，不进方法路径**」 |

### 7.2 禁止进方法路径的量

| 量 | 来源 | 现状 |
|---|---|---|
| 实体位姿 / AABB（`GET /state` 的 `entities`） | EvalServer 特权态 | **今天进了方法路径**——`solve()` 的四条数值分支全部由它供数（`kwadapter.py:302-315`）。这是 M1a 的**已知、已标注**的临时状态，不是隐蔽违规 |
| 官方谓词 `probes` | EvalServer | 主要做旁路记录（`phase1.py:52`/`:57` 前后各拍一次）；**但 `lower_until:560-563` 用 `root_in_bbox && axis_aligned` 当停止判据，这是特权量进方法路径的一条实质漏口** |
| 物体位移 | `gates.object_positions` → `rt._entities` | 只在 evaluator 侧（gates）使用，未进 policy 可见面。这条边界目前是干净的 |

**判读规则**：任何来自 `EvalClient`（`kwadapter.py:117-135`）的数据，进 `gates.py` 是合规的（evaluator 侧），进 `solve()`/`lower_until()` 是 M1a 的临时特权，必须带 `ORACLE-M1A` 标签报告，**不得写成方法能力**。M1b 的任务定义就是把 `solve` 的数据源从 `EvalClient` 换成非特权感知服务，而 `contract.Runtime` 接口不变。

---

## 8. 诚实的缺口清单

按「阻碍研究主张成立」的严重度排序。1–7 是前几轮审计的结论，8–11 是本轮核对时新查实的。

### 缺口 1（最严重）：demo 约束今天不影响抓取

- **主张**：图里的 `region_grasp(tube_left, upper_body)` 应当决定抓在管子哪一段。
- **prompt 明确告诉模型这条已被兑现**：`harness/prompts/compile_policy.md:20` 写着「grasp region is already baked into the grasp-pose hole」，生成的 `policy.py:2` 也照抄了这句注释。
- **实际没有兑现**：`kwadapter.solve:299-305` 只对 `hole_name.lower()` 做子串匹配（`"grasp" in n and "pose" in n`），抓取点 = oracle 质心 xy + AABB 顶 **− 硬编码 0.03**。洞的 `type`/`solver_hint`/`frame` 全部丢弃，所属阶段的 `constraints` 一条不读。
- **可证伪的后果**：把 `graph.json` 里 `region` 改成 `"bottom"` 或 `"rim"`，产生的抓取位姿**逐比特相同**。
- **verify 也接不住**：`region_grasp` 落在 `kwadapter.verify:610-611` 的 `else` 分支，返回 `True` + `detail="unchecked"`。

### 缺口 2：`stage['constraints']` 整块不参与任何运行期判定

`gates.snapshot:51` 与 `gates.evaluate:63` 都只读 `stage['acceptance']`。也就是说图里一半的内容（动作侧约束）在 Phase 1 是纯装饰。

### 缺口 3：`confidence` / `solver_hint` 基本是死字段

- `confidence`：唯一影响下游产物的读取点是 `extract.py:58` 的排序；`report.py:45/54` 只做 HTML 展示；`kwadapter`/`gates`/`fakerun` 零读取。
- `solver_hint`：**无任何程序消费点**；`.py` 源码里只有 `report.py:58-60` 把它渲染进 HTML 表格。
- `frame`：连 `validate.py` 都不校验，纯装饰。

### 缺口 4：`push` 是硬 stub，但 dry-run 看不出来

- `kwadapter.push:574-575` → `raise NotImplementedError("push 任务挂起(老板指示),M1 不实现")`。
- 但 **4 个生成的 policy 一共调用它 8 次**（已核实：`harness_push_T_20260729_235655`、`harness_push_T_20260730_005609`、`harness_push_T_random_20260729_234435`、`harness_push_T_random_20260730_005924`，各 2 次）。
- `fakerun.FakeRuntime.__getattr__:49-57` 的白名单里含 `"push"`，把它吞成 no-op 日志 → **dry-run 全绿，只在真机炸**。这是 fake/real 语义分叉的典型案例。

### 缺口 5：`residual` 是软 stub

`kwadapter.residual:323-325` 只 `_log` 后返回一个描述字典，无感知、无数值。「阶段内用残差修正」这条设计（`contract.py:23`）今天完全没有实现。所幸目前生成的 policy 也没调用它。

### 缺口 6：非特权感知 API 零实现

`harness/PHASE1_API_PLAN.md` §2 规划了 12 个 method-visible 感知 API（`get_observation` / `segment_text` / `detect` / `mask_to_world_points` / `filter_noise` / `compute_obb` / `get_object_pose` / `sample_grasps` / `select_top_down_grasp` / `point_prompt` / `transform_points` / `query_yes_no`）。**已核实：`harness/perception_service/` 目录不存在，这 12 个名字在全仓 `.py` 里零命中。** Phase 1 全部 `solve` 走 oracle。

### 缺口 7：`kwadapter.py` 零测试覆盖

- 619 行，全仓最长的单文件。
- 45 个 commit 里有 8 个改过它，且**全部集中在最近 20 个 commit 内**（`ff0a961`、`9dd8191`、`bc0a1eb`、`6c39680`、`3614383`、`921bd82`、`197e11d`、`c870a69`）。
- `tests/test_harness_units.py` 覆盖 `extract` / `stages` / `validate` / `enrich` / `gates` / `compilepolicy`（10 个测试），**没有一个 `kwadapter` 的测试**；`adapters/tests/` 也不覆盖它。

### 缺口 8（本轮新查实）：跨阶段同名洞被后写覆盖

`kwadapter.py:166-167` 的 `_hole_index = {h["name"]: (st, h) for st in stages for h in st["holes"]}` 是字典推导，**同名洞后写覆盖**。insert_tubes 图里有 6 个洞跨阶段重名：

| 洞名 | 声明于阶段 | `_hole_index` 实际保留 |
|---|---|---|
| `lift_height` | 0, 2 | stage 2 |
| `tube_left_grasp_pose` | 0, 1, 4 | stage 4 |
| `tube_left_long_axis` | 0, 1, 4 | stage 4 |
| `rack_hole_axis` | 1, 3, 5 | stage 5 |
| `insertion_depth` | 1, 3, 5 | stage 5 |
| `release_condition` | 3, 5 | stage 5 |

后果：stage 0 里 `solve("tube_left_grasp_pose")` 拿到的是 **stage 4 的 `stage_objects`**，不是当前阶段的。这次因为 stage 0 和 stage 4 的 `manipulated` 恰好都是 `tube_left` 而没出错，但这是巧合不是保证。**兜底路径也是死的**：`self._current_stage` 在 `:168` 初始化为 `None` 后全仓再无赋值点，`solve:296` 的 default 分支永远拿到 `None`。

### 缺口 9（本轮新查实）：一半的控制原语参数被丢弃

见 §5 的表格末列。除 `push` 外还有四处：`approach` 丢 `cone`、`transport` 丢 `obj`、`align` 丢 `obj` 和 `axis`、`lower_until` 丢 `stop_condition`。其中 **`align` 丢 `axis`** 和 **`lower_until` 丢 `stop_condition`** 与缺口 1 同性质：图里辛苦提取出的 `axis_parallel` 关系和 `insertion_depth` 洞，到执行端被整个扔掉。`align` 今天的实际行为是「移到目标上方 `ALIGN_DZ`」，语义上是第二个 `transport`。

### 缺口 10（本轮新查实）：`holds` 字段运行期无人消费

`at_end` 与 `throughout` 在 `gates.evaluate` 里待遇完全相同，都在阶段结束时判一次（`gates.py:63-69`）。而 `PHASE0_ROUND2.md` §2 把 `holds` 机制当作 v0.2 的一项成果（「约束为真、时序标错」首次可分离）——那是**标注侧**的成果，执行侧还没接上。

### 缺口 11（本轮新查实）：编译无修复回路

`compilepolicy.run:88-97` 的顺序是「写盘 → 静态检查 → 记报告」。违规不回喂模型，单轮生成、无重试。今天五个任务恰好都零违规，所以这条还没被触发，但它是一个没有兜底的单点。

---

## 9. 「要让 `region_grasp` 真正影响抓取，该改哪一行」

**答案是三处，缺一不可。**

### 改点 1（必需，最小改动）：`harness/kwadapter.py:301-305`

```python
if "grasp" in n and "pose" in n:
    e = self._ent(manip or n.split("_grasp")[0])
    top = e["aabb"]["max"][2] if isinstance(e.get("aabb"), dict) else e["aabb"][1][2]
    val.update(kind="pose", xyz=[e["pos"][0], e["pos"][1],
                                 top - 0.03], quat=None)  # 上部区域:顶下 3cm
```

**`top - 0.03` 这一行（`:305`）就是「demo 说了什么都不影响抓取」的物理位置。** 要兑现 region，需要：

1. 从 `st`（`:296` 已经拿到的所属 stage）的 `constraints` 里找 `name == "region_grasp"` 且 `args["obj"]` 与 `manip` 匹配的那一条，取 `args["region"]`。
2. 把 `region` 映射成沿物体主轴的比例（`vocab.GRASP_REGIONS` 的 6 个值：`top / upper_body / middle / bottom / rim / handle`，见 `harness/vocab.py:22`），用 AABB 的 `min[2]`/`max[2]` 插值出 z，而不是从 `max[2]` 减一个常数。
3. **同时必须修 `:166-167` 的 `_hole_index`**（缺口 8），否则第 1 步拿到的 `st` 是最后一个声明该洞的阶段，不是当前阶段——那样 stage 0 会读到 stage 4 的约束。最小修法：把 `_hole_index` 的 value 改成 list，`run_policy` 每阶段开始时给 `rt._current_stage` 赋值（这个字段已经存在，只是从没被写过）。

### 改点 2（否则改不出可验证的差异）：`harness/kwadapter.py:610-611`

```python
else:  # region_grasp/carry/order/clearance 等 M1a 不可几何判 → 记录不拦截
    detail = "unchecked"
```

`region_grasp` 落在这里恒返回 `True`。不加一条真判据（比如「爪尖 z 落在物体 AABB 的 region 对应区间内」），改点 1 的效果在 gate 上看不出来——**改了也无法证明改对了**。

### 改点 3（否则动作侧约束仍然进不了 gate）：`harness/gates.py:51` 与 `:63`

两处都只遍历 `stage.get("acceptance")`。若要让动作侧约束参与运行期判定（缺口 2），需要在这里加上 `stage.get("constraints")` 的 `holds == "throughout"` 子集。注意这会连带触发缺口 10——`holds` 目前没人读。

### 改完怎么验证（建议的最小验证协议）

1. 写第一个 `kwadapter` 单测（缺口 7），用假的 `EvalClient`/`PipelineClient` 打桩，断言：同一张图，`region` 从 `upper_body` 改成 `bottom` 时 `solve()` 返回的 `xyz[2]` **必须不同**。这条测试今天必然失败——它就是缺口 1 的可执行定义。
2. 再断言 stage 0 与 stage 4 solve 同名洞时读到的是各自阶段的约束（缺口 8）。
3. 真机验证前先确认 §10 的两条阻塞是否已解——否则拿不到有意义的抓取结果。

---

## 10. Phase 1 当前阻塞（截至 2026-07-30）

> ⚠️ 本节全部为**文档声称**，一手产物在 5090 的 `~/phase1/`，本 checkout 不含 episode 报告，无法在 mac 上复核。

| 项 | 状态 | 出处 |
|---|---|---|
| **reach 墙** | ✅ 已解决。真因是 v3/v4 **机器人代次错配**——C++ IK 加载 v4 碰撞模型而 Genesis 跑 v3 机器人，产生与目标点无关的恒定幽灵自碰 `pair_id=263`。零污染 v3 override 后右臂前伸 0.24→0.678 m 零拒绝 | `harness/PHASE1_M1A_STATUS.md` 顶部更新块 |
| **姿态路径不可行** | ❌ 阻塞。`rot_error` 沿路点 16°→52° 发散而 `collision_free=true`；根因是手写 servo 贪心逼近，`_step_to` 的 docstring（`harness/kwadapter.py:402-403`）自陈「姿态交给 IK 自然漂移」 | 同上 + 代码自陈 |
| **夹爪通道不通** | ❌ 阻塞。v3 控制器每臂只出 7 DoF，无夹爪通道；`set_gripper` 任意角度都不改变 `/state` 的爪子自由度，而 MotorNode 仍秒回 SUCCESS。**在通道接好前捏取类抓取不可能成功** | `harness/kwadapter.py:508-513` 的实测注释 |
| **路线裁决** | 用户已放行 motion planning 路线。上游所有成功先例都走 KSM 运动规划，raw IK 直达在本环境零先例 | `PHASE1_M1A_STATUS.md` 选项 B |

**因此：任何 Phase 1 的「passed」都不得报为机器人效果。** `PHASE1_M1A_STATUS.md` 自己记着 stack_bowls stage 0-2 的 passed 是平凡真检查放行（物体没动）——这正是 `gates.py` 后来被引入的原因。

---

## 11. 数字账本与可信度分级

### 11.1 已核实（2026-07-30 本 checkout 盘上重算）

| 数字 | 值 | 出处 |
|---|---|---|
| Phase 0 v0.2 micro P | **0.931** | 五个 run 的 `metrics.json` 重算：correct=122, wrong=9 |
| Phase 0 v0.2 micro R | **0.865** | 同上：correct=122, missing=19 |
| insert_tubes P/R | 0.978 / 0.882 | `harness/runs/harness_insert_tubes_20260730_003434/metrics.json` |
| stack_bowls P/R | 1.0 / 0.976 | `harness/runs/harness_stack_bowls_20260730_004159/metrics.json` |
| deposit_coin P/R | 0.957 / 0.786 | `harness/runs/harness_deposit_coin_20260730_005022/metrics.json` |
| push_T P/R | 0.538 / 0.636 | `harness/runs/harness_push_T_20260730_005609/metrics.json`（唯一恶化） |
| push_T_random P/R | 0.889 / 0.800 | `harness/runs/harness_push_T_random_20260730_005924/metrics.json` |
| 五个 v0.2 run 的 LLM 成本合计 | **$5.79**（1.5425+1.6207+1.1828+0.6610+0.7783） | 五份 `cost.jsonl` 求和 |
| insert_tubes 单 run 调用数 | 32 次 | 该 run 的 `cost.jsonl` 行数 |
| insert_tubes 校验 | 51 items / 0 violations / 0 warnings | `.../validation.json` |
| 测试 | **88 passed**（1.11 s） | `python3 -m pytest -q method/demo_graph/tests adapters/tests experiments/insert_tubes tests` |

两道 Phase 0 验收门（P≥0.7 / R≥0.8）均通过。歧义对门为 ❌ 改判：现有素材不含目标歧义，移交素材构造，不计入本轮（`harness/PHASE0_ROUND2.md` §4）。

### 11.2 文档声称（未在本 checkout 复核）

- v0.1 基线 P 0.897 / R 0.777（`PHASE0_ROUND2.md` §1；v0.1 的 run 目录未逐条核对）。
- derived 传播项 15 判 13 correct / 2 incidental / 0 wrong（`PHASE0_ROUND2.md` §2）。
- Phase 1 的全部 episode 结果、rot_error 16°→52°、前伸 0.24→0.678 m（产物在 5090）。

### 11.3 已知文档漂移（供后续清理）

| 位置 | 声称 | 实测 |
|---|---|---|
| `PHASE0_ROUND2.md` §4 | v0.2 全轮成本 ~$8 | cost.jsonl 合计 $5.79（`PROGRESS.md` 已标为待核） |
| `README.md`「本地检查」 | 88 tests / **87 passed**，1 例已知失败 | 本轮实测 **88 passed** |
| `README.md`「仓库结构」 | `method/` 仍被 `adapters/__init__.py` 的 eager import 拉起 | 已于 `3f603d1` 改为 PEP 562 惰性导入 |

---

## 12. 复现命令

```bash
# Phase 0 全链（不含 compile）
python3 -m harness.cli all      --task insert_tubes --k 5
python3 -m harness.cli metrics  --task insert_tubes --gold harness/goldset/insert_tubes_gold_v2.json

# 编译成 policy（必须显式调用）
python3 -m harness.cli compile  --task insert_tubes

# 测试（不可从仓根裸跑 pytest——components/ 下各包需各自 rootdir）
python3 -m pytest -q method/demo_graph/tests adapters/tests experiments/insert_tubes tests

# 发布门禁（默认 --profile private，内部端点只报 WARN）
python3 scripts/public_release_check.py

# Phase 1（仅 5090；产物不会出现在 mac checkout）
python3 -m harness.phase1 smoke   --task-id robodojo_insert_tubes_000
python3 -m harness.phase1 episode --task insert_tubes --task-id robodojo_insert_tubes_000
```

环境：`OPENROUTER_API_KEY` 从仓根 `.env` 读（`harness/util.py:15-25`，`setdefault` 语义——已存在的环境变量不被覆盖）；成本上限 `HARNESS_COST_CAP` 默认 `8.0`（`util.py:69-70`）；数据根 `HARNESS_DATA_ROOT` 默认 `~/data/upstream/robot-subtask-seg`（`util.py:28-31`）。

仓库：主仓为**内网 Gitea 私有仓**（remote `gitea`），5090 用 `ssh -A` 拉取；GitHub `origin` 自 2026-07-29 起停止维护。

---

## 附录：文档花名册与权威范围

（从 README 下沉至此，2026-07-30。README 只保留最短阅读路径。）

| # | 文档 | 权威范围 | 注意 |
|---|---|---|---|
| 1 | [`PROPOSAL.md`](PROPOSAL.md) | **当前唯一权威方案**：主张、假设 H1/H2/H3'、方法、验收门 | 2026-07-29；取代 v1 的执行策略 |
| 2 | [`harness/PHASE0_ROUND2.md`](../harness/PHASE0_ROUND2.md) | Phase 0 第二轮结果与终判 | 2026-07-30；P/R 数字以此为准 |
| 3 | [`harness/PHASE1_M1A_STATUS.md`](../harness/PHASE1_M1A_STATUS.md) | Phase 1 现场状态与阻塞 | 顶部有 2026-07-30 上午的 reach 墙更新，先读顶部再读正文 |
| 4 | [`harness/PHASE1_API_PLAN.md`](../harness/PHASE1_API_PLAN.md) | Phase 1 感知 API v1 设计 | **是计划不是现状**，12 个 API 零实现 |
| 5 | [`PROGRESS.md`](PROGRESS.md) | **实验总账**，所有数字的出处与「⚠️ 待核」标记 | 与其他文档冲突时，先看这里有没有标待核 |
| 6 | [`AGENTS.md`](../AGENTS.md) | 工作边界 / 信息边界 / 代码边界 | §9 含 1022/1024 时期的历史环境条款，**已不是当前规则** |
| 7 | [`harness/contract.py`](../harness/contract.py) | `rt.*` API 单一真源（编译提示词直接引用本源码） | 代码即规范 |
| 8 | [`harness/vocab.py`](../harness/vocab.py) | 封闭约束词表 v0（10 条）+ 阶段词表 | 代码即规范，改词表走 git review |
| 9 | [`reference/constraint_graph_schema.md`](reference/constraint_graph_schema.md) | 图 schema v0.2 | — |
| 10 | [`harness/DESIGN_GRASP_AND_LOOP.md`](../harness/DESIGN_GRASP_AND_LOOP.md) | 抓取姿态 / pose-in-hand / 闭环由谁来闭的设计裁定 | 2026-07-30；改的是方法设计不只是实现 |
| 11 | [`harness/README.md`](../harness/README.md) | harness 目录说明 | ⚠️ 状态行停在 2026-07-29「脚手架」，**已过时** |
| 12 | [`SECURITY.md`](SECURITY.md) | 发布策略与两档要求 | push 前必读 |
| 13 | [`archive/ARCHIVE.md`](archive/ARCHIVE.md) | v1 期 5 份文档（里程碑 / v1 提案 / 算法方案 / 首月计划 / 方向审计）的**合并归档本**：§1 止损判据与验收阈值、§2 实验矩阵与对照组、§3 方法规格与信息边界、§4 证据索引、§5 竞品与相关工作、§6 已作废方案及其原因 | ⚠️ **执行策略整体作废**；仅 **§1** 仍有效（含唯一成文的 20-seed 阈值）。文中「第 N 行」一律指各原文件的历史行号，不是本文件行号 |
| 14 | [`reference/PRIMITIVE_API.md`](reference/PRIMITIVE_API.md) | 控制原语审计 | 手写资产，曾被误列进 `.gitignore`，现已入库 |
