# demo-graph-lab

**一段 demo 教的是「每个阶段什么必须成立」，不是「照抄哪条轨迹」。**

我们把一段演示视频编译成一份**带 typed holes 的约束程序**：阶段结构和物体间的几何关系从视频里读出来，
但**所有度量数值都留成洞**，执行时由现场感知填。同一条约束有两个消费者——既生成动作决策，又当阶段验收判据。
编译一次、代码冻结，换场景只有感知返回值变，代码一个字节不动。

- 权威方案：[`RESEARCH_PROPOSAL_V2.md`](RESEARCH_PROPOSAL_V2.md)（2026-07-29，§0 主张原文在 `RESEARCH_PROPOSAL_V2.md:13`）
- 投递目标：**RSS 2027**
- 与相邻路线的差异（ReKep/CoPa 的每集 VLM 在环、CaP 的 LLM 写死常数、VLA 的端到端训练）见 `RESEARCH_PROPOSAL_V2.md:26-32`

---

## 1. 端到端是怎么流的

```text
                   ┌──────────── 编译期（Phase 0，唯一有 LLM 的阶段）────────────┐
demo 视频 + 一句指令
   │
   ├─ ingest      抽帧 + 定位 refined trace                    harness/ingest.py
   ├─ stages      切阶段（trace.json 优先，VLM 兜底）           harness/stages.py
   ├─ keyframes   每阶段选关键帧                               harness/keyframes.py
   ├─ objects     物体登记表                    ◀── LLM ×1（12 帧）harness/registry.py:8
   ├─ extract     阶段 × {约束, 验收, 洞}       ◀── LLM ×(阶段数×k)，k=5 自一致性
   │                                                多数票 need=ceil(k/2)  harness/extract.py:39
   ├─ enrich      一致性传播（确定性，无 LLM）                  harness/enrich.py
   ├─ validate    四层校验（含「零世界坐标度量字面量」扫描）     harness/validate.py
   └─ report      report.html 供人审 / 打金标                   harness/report.py
   │
   ▼  graph.json      ← 图里只有关系与洞，没有数值
   │
   └─ compile     LLM 写 Python policy          ◀── LLM ×1   harness/compilepolicy.py
          prompt = prompts/compile_policy.md 正文
                 + inspect.getsource(harness/contract.py)   ← rt.* API 单一真源
                 + 全量 graph.json                          （harness/compilepolicy.py:81-85）
   ▼  policy.py
                   └──────────────────────────────────────────────────────────┘

                   ┌──────────── 运行期（Phase 1，零 LLM）─────────────────────┐
policy.py + graph.json
   └─ harness/phase1.py  exec 出 STAGES
        └─ harness/fakerun.py run_policy   ← 可信 runner，两级 ReAct 骨架
              每阶段： gates.snapshot → handler(rt) → gates.evaluate
                                                   （空洞性 + 世界变化双检查）
        └─ harness/kwadapter.py KWRuntime  ← contract.Runtime 的实现
              rt.solve  → EvalServer GET /state    ★ ORACLE-M1A 特权态
              rt.ctrl   → pipeline :8000 /run（xquat_move / delta_move / go_home / set_gripper）
              rt.verify → 词表几何检查（见 §3 缺口 2）
   ▼  episode_report.json → 机器人动作
                   └──────────────────────────────────────────────────────────┘
```

两条纪律，都是结构性保证不是约定：

| 纪律 | 怎么强制的 |
|---|---|
| policy 代码里**不可能**出现度量常数 | `rt.solve()` 返回**不透明句柄**，policy 能传不能读（`harness/contract.py:4-5`、`:19-21`）；编译后 AST 静态检查 |
| policy **不能自证成功** | 验收由 runner 拿图里的 acceptance 调 `rt.verify()`，不走 policy（`harness/contract.py:8`、`harness/fakerun.py:77-79`） |

---

## 2. 现在做到哪

| Phase | 状态 | 硬结论 | 权威出处 |
|---|---|---|---|
| **Phase 0** 理解层 | ✅ **已达标**（2026-07-30） | micro P **0.931** / R **0.865**，两道门（P≥0.7、R≥0.8）全过 | `harness/PHASE0_ROUND2.md:16,35-39` |
| **Phase 1** 执行绑定 | 🔄 **到 M1a oracle**（进行中） | 软件链在 5090 上全通、**零次真实抓取**；所有 solve 走特权 oracle | `harness/PHASE1_M1A_STATUS.md` |
| **Phase 2** 冻结协议 / 跨 seed 泛化 | ⏳ **未开** | H1 主实验尚未启动 | `RESEARCH_PROPOSAL_V2.md` §2 |

### Phase 0 逐任务（v0.1 → v0.2 提取器）

| 任务 | P | R | 备注 |
|---|---|---|---|
| insert_tubes | 0.919→**0.978** | 0.872→**0.882** | 双升 |
| stack_bowls | 0.818→**1.0** | 0.621→**0.976** | 最差变最好（object registry 治好「一碗三名」）|
| deposit_coin | 1.0→**0.957** | 0.792→**0.786** | 持平；装配缺口仍在 |
| push_T | 0.70→**0.538** | 0.778→**0.636** | **唯一恶化**，病因见 ROUND2 §3 |
| push_T_random | 1.0→**0.889** | 0.818→**0.800** | 压线 |
| **micro 合计** | **0.897→0.931** | **0.777→0.865** | 两道门全过 |

- **歧义对门 ❌ → 改判**：现有素材不含目标歧义（random 变体只随机布局、deposit_coin 单币单槽），
  该项移交「素材构造」任务，不计入本轮（`harness/PHASE0_ROUND2.md:37-38`）。这是本轮最大的方法论欠账——
  歧义对实验正是 `RESEARCH_PROPOSAL_V2.md:46` 里对抗「常识就够了」这条攻击的主防线。
- **成本口径待核**：盘上五个 v0.2 run 的 `cost.jsonl` 合计 **$5.79**；`harness/PHASE0_ROUND2.md:36` 记「全轮 ~$8」。
  差异已在 `PROGRESS.md:122` 标为待核（疑文档合并了第一轮重跑）。

### Phase 1（5090 现场，本 checkout 无产物）

> ⚠️ 下列 episode 数字**只存在于 5090** `~/phase1/artifacts/`，本仓 `runs/` 被 `.gitignore` 排除。
> 这里转述的是 `harness/PHASE1_M1A_STATUS.md` 的**文档声称**，未在本机复核。

| 项 | 状态 |
|---|---|
| 端到端链路（sim → EvalServer → oracle 适配器 → Opus 编译的 policy → 两级 gate → episode 报告） | 文档称可重复运行 |
| episode 报告 | 文档称 insert_tubes ×3、stack_bowls ×1 |
| 真实抓取成功 | **0 次** |
| solve 数值来源 | **全部 ORACLE-M1A 特权态**（`GET /state` 直读实体位姿），不是自家感知 |

**当前三条阻塞**

| # | 阻塞 | 现状 |
|---|---|---|
| 1 | 姿态路径不可行 | `rot_error` 沿路点 **16°→52° 发散**、`collision_free=true`；根因是手写 servo 贪心逼近，`step_to` 注释自陈「姿态交给 IK 自然漂移」 |
| 2 | 夹爪通道不通 | v3 控制器每臂只出 7 DoF，**捏取类抓取当前不可能成功** |
| 3 | ~~reach 墙~~ | ✅ **已解决**（2026-07-30 上午）。真因是 **v3/v4 机器人代次错配**：IK 加载 v4 碰撞模型、Genesis 跑 v3，产生与目标点无关的恒定幽灵自碰 `pair_id=263`。零污染 v3 override 后右臂前伸 **0.24→0.678、零拒绝**（`harness/PHASE1_M1A_STATUS.md:1-8`）|

路线裁决：用户已放行 **motion planning 路线**——上游所有成功先例都走 KSM 运动规划，raw IK 直达在本环境**零先例**。

---

## 3. 最重要的缺口：demo 约束今天还没有真正影响抓取

这是当前研究主张与实现之间**最大的一条裂缝**，写在最显眼处以免被忘掉。

**1）`region_grasp` 不兑现——把图里的 region 改成任何值，抓取位姿逐比特相同。**

编译提示词明确告诉模型「grasp region 已经烘进 grasp-pose 洞了」（`harness/prompts/compile_policy.md:20`），
于是 policy 理直气壮地不管 region。但 `solve()` 并没有兑现这个承诺——它只对 **hole 的名字字符串**做子串匹配，
hole 的 `type` / `solver_hint` / `frame` 全部丢弃：

```python
# harness/kwadapter.py:299-305
n = hole_name.lower()
if "grasp" in n and "pose" in n:
    e = self._ent(manip or n.split("_grasp")[0])
    top = e["aabb"]["max"][2] ...
    val.update(kind="pose", xyz=[e["pos"][0], e["pos"][1],
                                 top - 0.03], quat=None)  # 上部区域:顶下 3cm
```

抓取点 = oracle 质心 xy + AABB 顶 − **硬编码 0.03**。把 `region_grasp(tube_left, upper_body)` 改成 `bottom` 或 `rim`，
输出不变。**「约束来自 demo」这条主张在抓取这一环目前是空转的。**

**2）整个 `stage['constraints']` 不参与任何运行期判定。** gate 只读 `stage['acceptance']`（`harness/gates.py:51,63`）。

**3）`verify` 的词表覆盖率 5/10。** 只有 `axis_vertical` / `axis_parallel` / `above` / `inside` / `center_align`
做真几何判定；`region_grasp` / `approach_direction` / `order` / `carry` / `clearance` 一律返回 `True` + `"unchecked"`
（`harness/kwadapter.py:610-611`）。异常也返回 `True`（不误杀，但也不拦截）。

**4）`push` 是硬 stub，dry-run 全绿、只在真机炸。** `harness/kwadapter.py:574-575` 直接 `raise NotImplementedError`，
但 4 份生成的 policy 一共调它 8 次；`fakerun.FakeRuntime.__getattr__`（`harness/fakerun.py:49`）把它吞成 no-op。

**5）`residual` 是软 stub。** 只 log，无感知无数值（`harness/kwadapter.py:323-325`）——两级 ReAct 的「阶段内修正」这一级实际是空的。

**6）非特权感知零实现。** `harness/PHASE1_API_PLAN.md:28` 规划的 12 个感知 API 一个都没写，
`harness/perception_service/` 目录不存在。Phase 1 全部 solve 走 oracle。

**7）`kwadapter.py` 619 行、churn 高度集中、零测试覆盖**——该文件**全部 8 个历史 commit 都发生在 2026-07-30 当天**
（01:51–11:37；全部落在全仓最近 20 个 commit 内，最近 15 个中占 4 个）；
`tests/`、`adapters/tests/`、`method/demo_graph/tests/` 中 `kwadapter` 零出现。

**8）图里的元数据是装饰性的。** `confidence` 只被 `harness/extract.py:58` 用于排序、被 `report.py` 渲染进 HTML；
`solver_hint` 只在 `harness/report.py:58-60` 被渲染，**零运行期读取**。

**9）编译无修复回路。** `static_check` 在 `policy.py` 写盘**之后**才跑（`harness/compilepolicy.py:89-97`），
违规不回喂模型，单轮出图。

---

## 4. 怎么跑起来

### Phase 0（mac 或 5090，一条命令）

```bash
python3 -m harness.cli all --task insert_tubes
python3 -m harness.cli metrics --task insert_tubes --gold harness/goldset/insert_tubes_gold_v2.json
```

前置条件（缺一不可，**都不在 git 里**）：

| 依赖 | 说明 |
|---|---|
| `.env` 里的 `OPENROUTER_API_KEY` | 编译期 VLM = `anthropic/claude-opus-4.8`，经 OpenRouter（默认值在 `harness/llm.py:22`）|
| demo 素材 | `HARNESS_DATA_ROOT`，默认 `~/data/upstream/robot-subtask-seg`（`harness/util.py:30-31`）；视频与 refined trace 只读复用、不进 git |
| `opencv-python` | `ingest` 抽帧时 lazy import |

注意两点：

- **`compile` 不在 `all` 里**，要显式跑：`python3 -m harness.cli compile --task insert_tubes`（`harness/cli.py:68-76`）。
- 产物落 `harness/runs/<kind>_<task>_<ts>/`，该目录被 `.gitignore` 的 `runs/` 排除——**新 checkout 是空的**。
  金标 `harness/goldset/*` 则是入库的手写资产。

### Phase 1（**只能在 5090**）

Phase 1 需要仿真、EvalServer、pipeline，本地 mac 跑不了。

```bash
# 在 5090 上
bash scripts/phase1_sim.sh tasks/robodojo/insert_tubes/insert_tubes_000.suite.yaml
python3 -m harness.phase1 smoke   --task-id robodojo_insert_tubes_000
python3 -m harness.phase1 episode --task insert_tubes --task-id robodojo_insert_tubes_000
```

`smoke` 只做 health → reset → state 摘要 → get_xquat 回读，**不动机器人**；`episode` 跑全链并产出
`~/phase1/artifacts/<task>/episode_*.json`（`harness/phase1.py:1-8`）。5090 用 `ssh -A` 拉代码（agent forwarding 必需）。

### 本地检查

```bash
python3 -m pytest -q method/demo_graph/tests adapters/tests experiments/insert_tubes tests   # → 88 passed
python3 scripts/public_release_check.py                                                      # 发布门禁
```

- **不要从仓根裸跑 `pytest`**——`components/` 下的包需各自的 rootdir，会在收集阶段直接报错。
- `public_release_check.py` 默认 `--profile private`，内部主机/端口只报 WARN；对外发布前跑 `--profile public` 清零（`scripts/public_release_check.py:73-74,317`）。

---

## 5. 从哪读起

**新窗口 / 新人的最短路径**：本 README → `RESEARCH_PROPOSAL_V2.md` §0-§4 → `harness/PHASE0_ROUND2.md` →
`harness/PHASE1_M1A_STATUS.md` → `harness/contract.py`（53 行，读完就知道生成代码能干什么）→ `harness/kwadapter.py`。

| # | 文档 | 权威范围 | 注意 |
|---|---|---|---|
| 1 | [`RESEARCH_PROPOSAL_V2.md`](RESEARCH_PROPOSAL_V2.md) | **当前唯一权威方案**：主张、假设 H1/H2/H3'、方法、验收门 | 2026-07-29；取代 v1 的执行策略 |
| 2 | [`harness/PHASE0_ROUND2.md`](harness/PHASE0_ROUND2.md) | Phase 0 第二轮结果与终判 | 2026-07-30；P/R 数字以此为准 |
| 3 | [`harness/PHASE1_M1A_STATUS.md`](harness/PHASE1_M1A_STATUS.md) | Phase 1 现场状态与阻塞 | 顶部有 2026-07-30 上午的 reach 墙更新，先读顶部再读正文 |
| 4 | [`harness/PHASE1_API_PLAN.md`](harness/PHASE1_API_PLAN.md) | Phase 1 感知 API v1 设计 | **是计划不是现状**，12 个 API 零实现 |
| 5 | [`PROGRESS.md`](PROGRESS.md) | **实验总账**，所有数字的出处与「⚠️ 待核」标记 | 与其他文档冲突时，先看这里有没有标待核 |
| 6 | [`AGENTS.md`](AGENTS.md) | 工作边界 / 信息边界 / 代码边界 | §9 含 1022/1024 时期的历史环境条款，**已不是当前规则** |
| 7 | [`harness/contract.py`](harness/contract.py) | `rt.*` API 单一真源（编译提示词直接引用本源码） | 代码即规范 |
| 8 | [`harness/vocab.py`](harness/vocab.py) | 封闭约束词表 v0（10 条）+ 阶段词表 | 代码即规范，改词表走 git review |
| 9 | [`schema/constraint_graph_schema.md`](schema/constraint_graph_schema.md) | 图 schema v0.2 | — |
| 10 | [`harness/DESIGN_GRASP_AND_LOOP.md`](harness/DESIGN_GRASP_AND_LOOP.md) | 抓取姿态 / pose-in-hand / 闭环由谁来闭的设计裁定 | 2026-07-30；改的是方法设计不只是实现 |
| 11 | [`harness/README.md`](harness/README.md) | harness 目录说明 | ⚠️ 状态行停在 2026-07-29「脚手架」，**已过时** |
| 12 | [`SECURITY.md`](SECURITY.md) | 发布策略与两档要求 | push 前必读 |
| 13 | [`RESEARCH_MILESTONES.md`](RESEARCH_MILESTONES.md) | ⚠️ **SUPERSEDED**；仅**止损判据与验收阈值**仍有效（含唯一成文的 20-seed 阈值） | 顶部有逐条 SUPERSEDED 标注，正文行号是加注前的 |
| 14 | [`RESEARCH_PROPOSAL.md`](RESEARCH_PROPOSAL.md) | v1（2026-07-26），执行策略已作废 | 只作历史参考 |
| 15 | [`DIRECTION_AUDIT_20260726.md`](DIRECTION_AUDIT_20260726.md) / [`PRIMITIVE_API_AUDIT.md`](PRIMITIVE_API_AUDIT.md) | 竞品占位审计 / 控制原语审计 | 手写资产，曾被误列进 `.gitignore`，现已入库 |
| 16 | [`ALGORITHM_PLAN.md`](ALGORITHM_PLAN.md) / [`PLAN.md`](PLAN.md) | v1 期规划 | 已被 v2 取代 |

---

## 6. 仓库结构：四套目录并存的现状

这个仓经历过 v1 → v2 的路线重排，目录没有一次性清理干净。**哪个是主线要说清楚**：

| 目录 | 定位 | 能不能删 |
|---|---|---|
| **`harness/`** | ✅ **当前主线**（2026-07-29 起）。Phase 0 流水线（`ingest`→`stages`→`keyframes`→`objects`→`extract`→`enrich`→`validate`→`report`→`metrics`，另有 `compile`）+ Phase 1 执行适配器 `kwadapter.py` + 两级 gate `gates.py` | 主线 |
| `method/demo_graph/` | v1 期方法树：约束图、状态机、候选、后端、伺服、隔离、RunManifest | **不删**。`adapters/__init__.py` 已于 2026-07-30 改惰性导入（PEP 562），Phase 1 主链路不再拖起这 13 个模块；但 `adapters.m1_bindings` 仍依赖它 |
| `adapters/` | `knowin_world`（EvalServer / pipeline / runtime doctor）、`demo_bundle`、`grasp_proposals`、`observability` | Phase 1 用其中的 `PipelineClient` |
| `experiments/insert_tubes/` | v1 期非特权 M1 入口与契约 | **不删**，仍被上面的测试命令引用 |
| `components/` | 字节保留的 WHT 组件快照（含历史 `knowin-skill-manager` 包名） | 只读快照，`SOURCE_MANIFEST.json` 记录来源与脱敏 |
| `oracle/` `tools/` `configs/` `schema/` `third_party/` | 手写资产 | **不删**。它们曾被误列进 `.gitignore` 的「可再生成产物」段，代价是 `PREDICATE_AUDIT.md` 与 `PROVENANCE_CORRECTION.md` **已永久丢失**；2026-07-30 已移出并纳入版本控制 |
| `runs/` `harness/runs/` | 实验产物 | `.gitignore` 排除，不进 git |

---

## 7. 工作边界（硬约束）

- **实验场地**：5090 服务器（仓库 checkout + `~/phase1` 运行目录）。2026-07-29 起自 1022 迁出；
  1022/1024 时期的历史边界见 `AGENTS.md` §9 与 `PROGRESS.md`「硬边界更正」小节（**已不是当前规则**）。
- **对外名**：`demo-graph-lab`（不是 ksm）。
- **Knowin World / 仿真数据**：外部共享依赖，**只读借用**；禁止写入、部署、改配置或启停其服务。
  Knowin World 是**外部**运行时依赖，不 vendoring 进本仓。Phase 1 的 v3 override 走的正是零污染方案
  （渲染我方 `~/phase1/cfg/sim_cfg.v3.yaml` + env 重启，**他们仓库零改动**）。
- **底座规则**：可用 = arm_node 级控制 + `knowin_reasoner/services/common`；禁人手 skill yaml 与非 common reasoner；
  ctrl 新增先斟酌（`harness/PHASE1_API_PLAN.md:3-6`）。

## 8. 远程仓库与发布

- **主仓 = 内网 Gitea 私有仓**（remote `gitea`，本地 `main` 跟踪 `gitea/main`）。
- GitHub `origin` 自 2026-07-29 起**停止维护**，其上已推送内容**一律视为已公开**。
- 5090 用 `ssh -A` 拉取（agent forwarding 必需；IdentitiesOnly 钉身份）。
- push 前跑 `python3 scripts/public_release_check.py`。首轮暂不添加开源 LICENSE。详见 [SECURITY.md](SECURITY.md)。
