# demo-graph-lab

**一段 demo 教的是「每个阶段什么必须成立」，不是「照抄哪条轨迹」。**

我们把演示视频编译成一份**带 typed holes 的约束程序**：阶段结构和几何关系从视频里读出来，
但**所有度量数值都留成洞**，执行时由现场感知填。同一条约束有两个消费者——既生成动作决策，
又当阶段验收判据。编译一次、代码冻结，换场景只有感知返回值变，代码一个字节不动。

权威方案 [`RESEARCH_PROPOSAL_V2.md`](RESEARCH_PROPOSAL_V2.md)（2026-07-29）· 投递目标 **RSS 2027**

---

## 架构

```text
                   ┌──────── 编译期（Phase 0，唯一有 LLM 的阶段）────────┐
demo 视频 + 一句指令
   ├─ ingest / stages / keyframes    抽帧、切阶段、选关键帧
   ├─ objects      物体登记表                    ◀── LLM ×1
   ├─ extract      阶段 × {约束, 验收, 洞}        ◀── LLM ×(阶段×k)，k=5 自一致性
   ├─ enrich       一致性传播（确定性）
   └─ validate     四层校验（含「零度量字面量」扫描）
   ▼  graph.json          ← 只有关系与洞，没有数值
   └─ compile      LLM 写 Python policy          ◀── LLM ×1
   ▼  policy.py
                   └────────────────────────────────────────────────────┘

                   ┌──────── 运行期（Phase 1，零 LLM）──────────────────┐
policy.py + graph.json
   └─ fakerun.run_policy        可信 runner，两级 ReAct 骨架
         每阶段： gates.snapshot → handler(rt) → gates.evaluate
   └─ kwadapter.KWRuntime       contract.Runtime 的实现
         rt.solve  → 感知填洞（当前走 ORACLE-M1A 特权态）
         rt.ctrl   → pipeline :8000
         rt.verify → 约束词表几何检查
   ▼  episode_report.json → 机器人动作
                   └────────────────────────────────────────────────────┘
```

两条纪律是**结构性保证**，不是约定：

| 纪律 | 怎么强制的 |
|---|---|
| policy 里**不可能**出现度量常数 | `rt.solve()` 返回不透明句柄，policy 能传不能读；编译后 AST 静态检查 |
| policy **不能自证成功** | 验收由 runner 拿图里的 acceptance 调 `rt.verify()`，不经过 policy |

细节（逐跳的输入/输出/实现文件、数据结构样例、契约的 11 个方法）见 [`docs/OVERVIEW.md`](docs/OVERVIEW.md)。

---

## 现在到哪

| Phase | 状态 | 一句话 |
|---|---|---|
| **Phase 0** 理解层 | ✅ 已达标 | micro P **0.931** / R **0.865**，两道门全过 |
| **Phase 1** 执行绑定 | 🔄 到 M1a oracle | 软件链全通、**零次真实抓取**，solve 全走特权 oracle |
| **Phase 2** 冻结协议 | ⏳ 未开 | — |

> ⚠️ **最重要的缺口**：demo 提取出的约束**目前还没有真正影响抓取**。
> `region_grasp` 在运行期被丢弃，抓取点来自 oracle 几何 + 硬编码偏移——
> 把图里的 region 改成任何值，输出逐比特相同。这是主张与实现之间最大的一条裂缝。

进展、阻塞、主张链健康度见 [`docs/STATUS.md`](docs/STATUS.md)；历史实验总账见 [`PROGRESS.md`](PROGRESS.md)。

---

## 快速开始

```bash
# Phase 0（mac 或 5090）
python3 -m harness.cli all      --task insert_tubes
python3 -m harness.cli compile  --task insert_tubes     # compile 不在 all 里，要显式跑

# 本地检查
python3 -m pytest -q method/demo_graph/tests adapters/tests experiments/insert_tubes tests
python3 scripts/public_release_check.py
```

前置：`.env` 里的 `OPENROUTER_API_KEY`、demo 素材（`HARNESS_DATA_ROOT`）、`opencv-python`——**都不在 git 里**。
**不要从仓根裸跑 `pytest`**（`components/` 需各自 rootdir）。

**Phase 1 只能在 5090**（需要仿真、EvalServer、pipeline），跑法与前置条件见
[`docs/OVERVIEW.md`](docs/OVERVIEW.md)。

---

## 文档

| 先读 | |
|---|---|
| [`RESEARCH_PROPOSAL_V2.md`](RESEARCH_PROPOSAL_V2.md) | **当前唯一权威方案**：主张、假设、方法、验收门 |
| [`docs/OVERVIEW.md`](docs/OVERVIEW.md) | 方法的细节视图 + 完整文档花名册 |
| [`docs/STATUS.md`](docs/STATUS.md) | 现在到哪、卡在哪、下一步 |
| [`docs/DECISIONS.md`](docs/DECISIONS.md) | 关键裁决与理由（**改动前先查，避免重开已裁决的问题**） |
| [`PROGRESS.md`](PROGRESS.md) | 实验总账，所有数字的出处与「⚠️ 待核」标记 |
| [`AGENTS.md`](AGENTS.md) | 工作边界 / 信息边界 / 代码边界 |

代码即规范：[`harness/contract.py`](harness/contract.py)（53 行，读完就知道生成代码能干什么）、
[`harness/vocab.py`](harness/vocab.py)（封闭约束词表）。

---

## 仓库结构

| 目录 | 定位 |
|---|---|
| **`harness/`** | ✅ **当前主线**：Phase 0 流水线 + Phase 1 适配器 `kwadapter.py` + 两级 gate `gates.py` |
| `method/` `adapters/` `experiments/` | v1 期代码，**不删**——仍被测试与 `adapters.m1_bindings` 引用 |
| `components/` | WHT 组件的字节级只读快照，来源与脱敏记录在 `SOURCE_MANIFEST.json` |
| `oracle/` `tools/` `schema/` `third_party/` | 手写资产。曾被误列进 `.gitignore`，代价是两份审计文档**已永久丢失** |
| `runs/` `harness/runs/` | 实验产物，不进 git（**新 checkout 是空的**） |

---

## 边界与远程

- **实验场地**：5090（仓库 checkout + `~/phase1`）。2026-07-29 起自 1022 迁出。
- **Knowin World / 仿真数据**：外部共享依赖，**只读借用**，不 vendoring 进本仓；禁止写入、部署、改配置或启停其服务。
- **主仓 = 内网 Gitea 私有仓**（remote `gitea`）；GitHub `origin` 自 2026-07-29 **停止维护**，其上内容视为已公开。
- 5090 用 `ssh -A` 拉取（agent forwarding 必需）。push 前跑门禁，详见 [`SECURITY.md`](SECURITY.md)。
