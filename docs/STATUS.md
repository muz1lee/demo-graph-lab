# STATUS — 当前进展与阻塞快照

> **与 `PROGRESS.md` 的分工**
> `PROGRESS.md` 是**历史总账**：跑了什么、结果是什么、什么时候拍的板，**只增不改**，是可追溯的流水。
> **本文件是「现在」的视图**：当前站在哪、卡在哪、下一步做什么，**会被反复覆写**，不保留历史。
> 冲突时以 `PROGRESS.md` 的历史记录为准；本文件只对「此刻状态」负责。
>
> 标注约定：**[已核实]** = 本次在 HEAD 上直接读代码/产物确认；**[文档声称]** = 仓内文档写着、本次未独立复核；**[未核实]** = 明确没验证过。

---

## 1. 一句话状态

Phase 0（demo 理解层）已过验收门并有盘上产物可复核；Phase 1（执行绑定）软件链在 5090 上端到端可跑但**全部走 oracle 特权态**、物理侧仍被姿态路径与夹爪通道挡住；**真正的研究断点不在 infra，而在「约束 → 检验函数」这一环今天零代码**——因此即使 Phase 1 明天跑出成功率，也无法归因给 demo 约束。

---

## 2. 三个 Phase 状态表

| Phase | 定义（`RESEARCH_PROPOSAL_V2.md:151` / `:205-206`） | 状态 | 判据 | 证据路径 |
|---|---|---|---|---|
| **Phase 0** 理解层 | demo 视频 → 带 typed holes 的约束程序，不碰仿真 | **已达标** | H2 门：P≥0.7、R≥0.8、金标 5/5、自一致性 k=5 全解析、零度量字面量（`RESEARCH_PROPOSAL_V2.md:55`） | micro **P=0.931 / R=0.865** [已核实]，逐任务见 §2.1；`harness/PHASE0_ROUND2.md` |
| **Phase 1** 执行绑定 | 最小执行层对接 knowin-world；三层漏斗实装；两级 ReAct；反事实法庭 | **进行中（软件通、物理未通、感知全 oracle）** | 需：非 oracle 感知 + 真实抓取成功 + 约束真正参与决策 | 软件链见 `harness/phase1.py`、`harness/fakerun.py:60`、`harness/kwadapter.py`；episode 产物**只在 5090** `~/phase1/artifacts/<task>/episode_*.json`，**本 checkout 无任何 `episode_*.json`** [已核实] |
| **Phase 2** 冻结协议 | D/E seed 协议、六组对照 + no-demo frontier / per-episode VLM 两条新基线 | **未开** | 冻结后 held-out seed 成功率与泛化 gap | 无代码、无产物 [已核实：仓内无 seed 协议实现] |

### 2.1 Phase 0 逐任务成绩 [已核实，逐个读 `metrics.json`]

| 任务 | P | R | metrics.json 路径（仓根相对） |
|---|---|---|---|
| insert_tubes | 0.978 | 0.882 | `harness/runs/harness_insert_tubes_20260730_003434/metrics.json` |
| stack_bowls | 1.0 | 0.976 | `harness/runs/harness_stack_bowls_20260730_004159/metrics.json` |
| deposit_coin | 0.957 | 0.786 | `harness/runs/harness_deposit_coin_20260730_005022/metrics.json` |
| push_T | 0.538 | 0.636 | `harness/runs/harness_push_T_20260730_005609/metrics.json` |
| push_T_random | 0.889 | 0.800 | `harness/runs/harness_push_T_random_20260730_005924/metrics.json` |
| **micro 合计** | **0.931** | **0.865** | 见 `harness/PHASE0_ROUND2.md` §1 |

- 成本：上述五个 v0.2 run 的 `cost.jsonl` 合计 **$5.7853** [已核实，按 `cost` 字段求和]。`harness/PHASE0_ROUND2.md` §4 记「~$8」，差异已在 `PROGRESS.md` 标为待核。
- **歧义对门 ❌ → 改判**：现有素材不含目标歧义，移交「素材构造」，不计入本轮（`harness/PHASE0_ROUND2.md` §4）[文档声称]。
- **口径风险 [未核实]**：`harness/PHASE0_ROUND2.md` 开头写「金标 v2 独立重标（Fable 判卷，未读 v1 金标）」，即 v2 金标由独立 agent 产出。人工复核覆盖多少条、是否等价于 proposal 说的「人工金标」，**未核实**。这条直接影响环 1 的证据强度，投稿前必须落实。

---

## 3. 当前阻塞

| # | 是什么 | 根因 | 证据 | 谁能解 |
|---|---|---|---|---|
| **B1** | **「约束 → 检验函数」零代码**（研究断点，见 §4 环 2） | 从未实现编译步骤：`verify()` 只对约束的 `name` 做 5 分支硬编码几何判断，`args` 之外的 `holes/type/solver_hint/frame` 全丢 | `harness/kwadapter.py:582-615`；词表 10 个约束名（`harness/vocab.py:9-20`）中只真判 `axis_vertical / axis_parallel / above / inside / center_align` **5 个**，其余落 `else: detail="unchecked"`（`harness/kwadapter.py:610-611`） | **我方，纯本仓 Python，mac 即可做，零 infra 依赖** |
| **B2** | **demo 约束今天不影响抓取**（归因不可能） | 编译 prompt 明确告诉模型「grasp region is already baked into the grasp-pose hole」，但 `solve()` 不兑现——只对 hole **名字字符串**做子串匹配 | prompt：`harness/prompts/compile_policy.md:20`；实现：`harness/kwadapter.py:301-305`，抓取点 = oracle 质心 xy + AABB 顶 − 硬编码 `0.03`。把图里 `region_grasp` 的 region 从 `upper_body` 改成 `bottom` 或 `rim`，产生的抓取位姿**逐比特相同** | 我方，同 B1 |
| **B3** | **`stage['constraints']` 整个字段无运行期读者** | gate 只读 `acceptance` | `harness/gates.py:51`、`:63`、`:65` 三处全部 `stage.get("acceptance")`，全仓无运行期读 `stage["constraints"]` 的点 | 我方，同 B1 |
| **B4** | **姿态路径不可行**（物理阻塞） | 手写 servo 贪心逼近，姿态不闭环 | `rot_error` 沿路点 **16°→52° 发散**、`collision_free=true`（`harness/PHASE1_M1A_STATUS.md` 顶部块）[文档声称，产物在 5090]；代码自陈：`harness/kwadapter.py:402` 注释「姿态交给 IK 自然漂移」[已核实] | 我方 + 5090；**用户已放行 motion planning 路线**（上游成功先例全走 KSM 运动规划，raw IK 直达在本环境零先例） |
| **B5** | **夹爪通道不通** | v3 控制器每臂只出 7 DoF | `harness/DESIGN_GRASP_AND_LOOP.md:86`、`harness/kwadapter.py:510`（实测注释）[已核实] | infra 侧 / knowin-world；捏取类抓取在此之前**不可能成功** |
| **B6** | **Phase 1 感知全部走 oracle** | `solve()` 直接 `EvalServer GET /state` 拿特权态；`PHASE1_API_PLAN.md` 规划的 12 个非特权感知 API **零实现** | `harness/kwadapter.py:295-321`；`harness/PHASE1_API_PLAN.md:32-43` 列 12 条；全仓 grep `def get_observation / segment_text / mask_to_world_points / compute_obb / sample_grasps / query_yes_no` **零命中** [已核实] | 我方 + 5090（GraspNet 从 1022 移植、SAM/GDINO 权重现状 [未核实]） |
| **B7** | **`push` 硬 stub 被 dry-run 吞成绿灯** | `KWRuntime.push` 直接 `raise`，而 `FakeRuntime.__getattr__` 把 `push` 列入「统一记日志」白名单 → dry-run 不炸、只在真机炸 | `harness/kwadapter.py:574-575`（`raise NotImplementedError("push 任务挂起(老板指示),M1 不实现")`）；`harness/fakerun.py:49-55`；**4 个生成的 policy 共 8 处调用 `rt.push(`** [已核实：`grep -c` on `harness/runs/*/policy.py`] | 我方，本仓 |
| **B8** | **`residual` 是软 stub** | 只 `_log`，无感知无数值 | `harness/kwadapter.py:323-325` | 我方，本仓 |
| **B9** | **`kwadapter.py` 619 行、churn 高、零测试** | 一夜 debug 堆积 | 619 行 [已核实]；全仓唯一引用者是 `harness/phase1.py`，**无任何测试文件提及 kwadapter** [已核实]；对比 `gates` 有覆盖（`tests/test_harness_units.py:86`）。**churn 口径更正**：本 HEAD 复核为「最近 15 个 commit 中 **4** 个触及」（`c870a69/197e11d/921bd82/6c39680`），该文件历史总计 8 个 commit；上轮审计记的「15 占 8」在当前 HEAD **未复现** | 我方，本仓 |
| **B10** | 歧义对验收门无素材 | 现有 demo 不含目标歧义（random 变体只随机布局；deposit_coin 单币单槽） | `harness/PHASE0_ROUND2.md` §4 [文档声称] | 我方：多目标录制或 Phase 1 仿真造对 |

**已解除**：reach 墙。真因是 **v3/v4 机器人代次错配**（IK 加载 v4 碰撞模型、Genesis 跑 v3），产生与目标点无关的恒定幽灵自碰 `pair_id=263`；零污染 v3 override 后右臂前伸 0.24→0.678 零拒绝（`harness/PHASE1_M1A_STATUS.md` 顶部块）[文档声称，实验在 5090]。

---

## 4. 主张链的健康度（核心）

主张（`RESEARCH_PROPOSAL_V2.md:13`）：「一段 demo 教的是**每个阶段什么必须成立**，不是照抄哪条轨迹。」
把它拆成 5 个**可证伪**环节；链条强度 = 最弱环强度。

| 环 | 命题 | 状态 | 证据 / 缺什么 |
|---|---|---|---|
| **环 1** | demo 能提取约束 | ✅ **已证** | micro **P=0.931 / R=0.865**，5 任务 `metrics.json` 盘上可核（§2.1）。风险：金标由独立 agent 标注，人工复核范围**未核实** |
| **环 2** | 约束能编译成**检验函数** | ❌ **零代码 —— 断点在这里** | 无「约束 → 可执行判据」的编译步骤。现状是 10 选 5 的硬编码几何 switch（`harness/kwadapter.py:582-615`），hole 的 `type/solver_hint/frame` 无消费者（`solver_hint` 无任何程序消费点，`.py` 源码里只有渲染：`harness/report.py:58-60`；`confidence` 唯一程序读取点是排序：`harness/extract.py:58`）。`stage['constraints']` 整个字段零运行期读者（`harness/gates.py:51/63/65`） |
| **环 3** | 检验函数能**筛掉坏候选** | ❌ **未开始** | 既无候选生成器（`sample_grasps` = `PHASE1_API_PLAN.md:39` 第 8 条，零实现），也无筛选器；`RESEARCH_PROPOSAL_V2.md:103`（§4.2）的三层漏斗未实装 |
| **环 4** | 筛出的候选**成功率更高** | ⚠️ **路径存在，但走 oracle** | `harness/fakerun.py:60` 两级 ReAct runner + `harness/phase1.py` 端到端可跑；但 ① 所有 `solve` 读 EvalServer 特权态（**ORACLE-M1A**），② 抓取点与 demo 约束解耦（B2）。**即使拿到成功率也无法归因给 demo 约束** |
| **环 5** | 冻结后**跨 seed 泛化** | ❌ **未开始** | Phase 2 的 D/E seed 协议无代码无产物 |

**结论：断点在环 2，且环 2 不依赖任何 infra 阻塞。**
B4（姿态发散）/ B5（夹爪 7 DoF）/ B6（感知 oracle）挡的是**环 4 的执行力**，全部在 5090 与 knowin-world 侧；而环 2 是本仓纯 Python，mac 上就能写、能测、能出反事实证据。换句话说：**5090 全停机的日子，环 2 照样能推进**——今天没推进不是被挡住，是没排上。

**环 2 的最小证伪实验**（不需要机器人、不需要 LLM）：把 `graph.json` 里 `region_grasp(tube_left, upper_body)` 改成 `bottom`，跑同一条 policy，比对 `solve("*_grasp_pose")` 的输出。**当前预期：逐比特相同 = 环 2 被证伪**。这条断言本身是 §3-B2 的直接推论，本次**未实跑**，标记 [未核实]，建议作为第一个落地测试。

---

## 5. 下一步候选动作与依赖

| 动作 | 内容 | 依赖 | 解锁哪一环 / 阻塞 |
|---|---|---|---|
| **A** | 写下 §4 的反事实测试：改 region → 抓取位姿必须变。先让它**红** | 无（本仓，mac） | 把环 2 的断点变成可执行判据 |
| **B** | `solve()` 消费 hole 的 `type/solver_hint/frame` 与 `region_grasp` 的 region，令 A 转绿 | A | **环 2 断点** + B2 |
| **C** | 让 `stage['constraints']` 进入运行期（gate 或 within-stage residual），并让 `verify` 覆盖 `region_grasp/clearance/carry/order` | 无 | 环 2 + B3 |
| **D** | `kwadapter.py` 补测试（619 行零覆盖），至少覆盖实体解析、`solve` 分支、`verify` 分支 | 无 | B9；同时是 B/C 的安全网 |
| **E** | `push`：要么实现，要么让 `FakeRuntime` 对硬 stub 也炸（dry-run 不许假绿） | 无 | B7 |
| **F** | 接 motion planning 替代手写 servo（用户已放行） | 5090 + agent forwarding | B4 → 环 4 |
| **G** | 落地 12 个非特权感知 API，摘掉 ORACLE-M1A | F（或至少 sim 能动）+ GraspNet 移植 | B6 → 环 4 归因 |
| **H** | 歧义素材构造（多目标录制 / 仿真造对） | 无（但要录制或 sim） | B10 |
| **I** | Phase 0 v0.3 backlog（registry 含 EE、装配缺口 repair、镜头切变检测、投票过滤自我否定） | 无（有 LLM 成本，约 $1–2/任务） | 抬环 1 上限，非关键路径 |

**建议顺序：A → B → C → D/E → F → G。**
理由：A/B/C 直接补断点，且产出的是**论文主张的可证伪核心**；F/G 是效果层——没有 A/B/C，F/G 跑通后拿到的成功率只能说明「oracle + 手写启发式能抓起来」，说明不了「demo 约束有用」，对 RSS 2027 的主张零贡献。D/E 是低成本止血，建议与 B 同批做。

---

## 6. 环境与复核入口

| 项 | 值 |
|---|---|
| 主仓 | 内网 Gitea 私有仓，remote `gitea` = `git@192.168.20.77:muz1lee/demo-graph-lab.git` [已核实]；GitHub `origin` 自 2026-07-29 起停止维护 |
| 实验机 | 5090，用 `ssh -A` 拉（agent forwarding 必需）[文档声称] |
| 测试 | `python3 -m pytest -q method/demo_graph/tests adapters/tests experiments/insert_tubes tests` → 上轮 88 passed [文档声称，本次**未复跑**（只读约束）]。**不可从仓根裸跑 pytest**（`components/` 需各自 rootdir） |
| 门禁 | `python3 scripts/public_release_check.py`（默认 `--profile private`，内部端点只报 WARN） |
| 编译期 LLM | OpenRouter → `anthropic/claude-opus-4.8`（`harness/llm.py:22`）；compile 用 `max_tokens=4000, temperature=0.1`（`harness/compilepolicy.py:87`）；**无 tools / 无 function calling**；`static_check` 在写盘**之后**（`harness/compilepolicy.py:89-90`），违规不回喂模型，单轮无修复回路 |
| 管线分界 | `compile` **不在 `all` 里**，需显式子命令（`harness/cli.py:24/27/68`） |
| 自一致性 | k=5，`need = ceil(k/2) = 3`（`harness/extract.py:39`） |
| 目录并存 | `harness/`（当前主线）、`method/demo_graph/`（v1）、`adapters/`、`experiments/`（v1）。`adapters/__init__.py` 已改惰性导入，`method.*` 不再被 Phase 1 路径拖起 |

---

## 7. 最后更新

- **更新时间**：2026-07-30
- **对应 HEAD**：`3f603d1d621ddf82fb4b4e02f43840adfce73574`（`3f603d1`，2026-07-30 16:37:06 +0800，`refactor(adapters): 包初始化改惰性导入,解开 method/ 与 Phase 1 主链路的焊死`）
- **工作树**：clean，分支 `main` [已核实]
- **本次核对方式**：mac 本地 checkout 只读；所有 file:line 与 `metrics.json` / `cost.jsonl` 数字为本次直接读取；标 [文档声称] 的条目对应产物在 5090，本 checkout 无副本。
