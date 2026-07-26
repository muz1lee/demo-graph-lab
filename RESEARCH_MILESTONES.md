# Research Milestones：从单次演示到跨 Seed 泛化的完整研究路线

- 日期：2026-07-26
- 配套文档：`RESEARCH_PROPOSAL.md`（方法与实验设计，本文引用其章节号）、`AGENTS.md`（边界）、`PROGRESS.md`（实验总账，唯一动态状态源）
- 北极星：**一个 demo 视频 → agent 生成的策略代码冻结后，在 held-out seed/layout 上保持成功率**
- 约定：每个里程碑有「入口条件 / TODO / 验收（可测）/ 失败分支」。验收只认隔离 evaluator 判定 + 盘上产物；执行完整 ≠ 任务成功。

---

## 总时间轴

```text
M0 基础设施与证据基线 ────────────── ✅ 已完成（2026-07-26）
M1 非特权单管端到端 + 冻结验收 ───── W1–W4（至 ~08-23）
M2 机制验证与消融（H1 主实验）────── W5–W8（至 ~09-20）
M3 泛化阶梯与鲁棒性 ──────────────── W9–W12（至 ~10-18）
M4 论文与复现包 ─────────────────── W10–W16（与 M3 重叠，至 ~11-15）
```

投稿目标：主目标 RSS 2027（预计 2027-01/02 截稿）；若 M2 提前完成且数字强，ICRA 2027（约 2026-09 中截稿）作为 stretch，仅投 M1+M2 范围的收窄版本。

---

## M0：基础设施与证据基线（✅ 已完成，锚定用）

已到位，后续里程碑直接引用，不再重复验证：

- [x] 公开仓 `muz1lee/demo-graph-lab` 建立，WHT 组件按 allowlist 导入（`wht-import-20260726` tag）
- [x] 模块化：`method/demo_graph` + `adapters/{knowin_world,demo_bundle,grasp_proposals,observability}` + `experiments/insert_tubes`（54 新测 + WHT 90+2+7 通过）
- [x] 工作边界钉死：仅 1022；1024 NAS 只读；文档中文化并推送
- [x] 仿真物理对齐效果侧：knowin-world `bf714099` + `priority=1`
- [x] 反模式证据链：B7（度量零校验）、slotgeom（假成功）、B4 probe（执行≠成功）、B5.1（关系×能力交互）、谓词 v2 回归
- [x] 非特权 probe 通过：grasp / `tube_axis` / `holder_pose`，`perceptual_holes=[]`（`runs/m1_probe_20260726_132419`）

---

## M1：非特权单管端到端 + 冻结验收（W1–W4）

**目标**：拿到第一次非特权端到端成功；视频→图管线产出真图；在 20 个 held-out seed 上通过 M1 稳定验收。这是全研究的存亡里程碑——M1 不成，后面全部顺延或降级。

### M1.a 带控制 trial 与 H3 闸门裁决（本周）

- 入口条件：probe 已通过 ✅；**用户批准发控制**（唯一外部依赖）
- TODO：
  - [ ] `--mode grasp`：单抓取 + 附着验证（测试提升 gate ≥ 40 mm）
  - [ ] `--mode full`：全链五阶段，per-trial 视频 + 记录
  - [ ] 按 proposal §5.4 归因插入结果 → **裁决 H3 闸门**
  - [ ] 更新 `PROGRESS.md` 与 proposal §6
- 验收：≥1 次非特权端到端成功（v2 谓词判定），或明确的分阶段失败归因
- 失败分支：
  - 对准合格、下插停滞 → H3 开启，伺服层进 M2
  - 插入直接通过 → 归因旧物理，伺服降级为备选
  - 对准不合格 → 触发「感知精度闸门」（见闸门总表），先修感知再回来

### M1.b 视频→图管线（Codex T1/T2，并行进行中）

- 入口条件：无（已可开工）
- TODO：
  - [ ] T1 视频→约束图提取器：`insert_tubes` 从 6 段粗 trace 补齐 grasp region/DoF、reorientation、axis/clearance、postcondition、recovery，全部 `provenance=demo_video`
  - [ ] T2 关键帧夹爪-物体相对关系提取器（区域/轴粒度，复用 CoTracker）
  - [ ] 提取出的图接入 Code Agent 编译，替换人工图
- 验收：schema 校验通过；零度量字面量（T3 扫描器）；用提取图（非人工图）编译出的 policy 在开发 seed 上跑通 probe→执行链
- 失败分支：W3 末仍不能产真图 → H1 起点降级为人工图（proposal 风险 #2 预案），视频→图移入 M3，论文主张相应收窄

### M1.c 冻结协议与 20-seed 验收（W4 前半）

- 入口条件：M1.a 至少一次端到端成功 + M1.b 或其降级预案就位；T3/T4（扫描器、seed harness）验收通过
- TODO：
  - [ ] 开发集 D（3 seed）上生成并调试 policy → 冻结（code digest 入 RunManifest）
  - [ ] held-out 20 seed 批量运行 + 五阶段 funnel 报告
- 验收（沿用 PLAN.md）：**≥16/20 完成抓取+转正+对准；≥12/20 达成 inserted+upright**
- 失败分支：12/20 未达 → 按 funnel 归因决定回修哪一层（感知/绑定/控制），M2 顺延一周，只许回修一轮

---

## M2：机制验证与消融——H1 主实验（W5–W8）

**目标**：把「非度量图 + 运行时绑定跨 seed 泛化优于度量代码」从设计变成数字；两个证伪开关出结果。

### M2.a 六组对照主实验（proposal §5.2）

- 入口条件：M1.c 通过
- TODO：
  - [ ] 100-layout 上跑组 1–6（相同模型/预算/runtime/seeds）
  - [ ] 主检验 H1：组 1/2（允许硬编码）对组 3 的 held-out gap
  - [ ] 组 3 vs 4：候选机制增量；组 4 vs 5：闭环增量（视 H3）
  - [ ] 分阶段 funnel、恢复次数、API/LLM 成本随成功率一并报告
- 验收：H1 方向性结论 + 显著性检验；每组失败按（感知/绑定/控制/物理）归因入账
- 失败分支：组 3 对组 1/2 无 held-out 优势 → 核心主张证伪，研究转向失败分析论文（诚实路径，提前与老板对齐）

### M2.b GraspNet 两级漏斗 + 消融 B（proposal §4.3/5.3）

- TODO：
  - [ ] GraspNet 接真实链路：相机帧 → 候选 → T2 几何过滤 → VLM tie-break
  - [ ] 消融 B：几何条件 vs VLM 相似性 vs 两级漏斗
- 验收：三组候选选择在相同 trial 集上的对照数字
- 失败分支：几何过滤无增量 → §4.3 两级漏斗表述降级，保留 VLM 单级

### M2.c 伺服层 + 消融 A（仅当 H3 开启）

- TODO：
  - [ ] `ServoSpec` + 可信伺服 runtime 插件（KW 侧受审计 skill）
  - [ ] 消融 A：推导 Spec vs 手调常数 Spec（同一控制律实现）
- 验收：消融 A 有差异 → 「推导」主张成立；无差异 → 伺服退为工程组件，§4.4 论述从主张降为实现细节
- H3 关闭时：本节整体跳过，工时转 M2.b 与 M3

### M2.d 插入约束反推 grasp DoF（机制 3，proposal §2.4）

- TODO：
  - [ ] 在图中实现放置/插入约束对抓取候选过滤的反向边
  - [ ] 对照：有/无反推的抓取成功率与后续插入成功率
- 验收：反推对 funnel 后段（对准/插入）通过率的增量数字

---

## M3：泛化阶梯与鲁棒性（W9–W12）

**目标**：把结论从「insert_tubes 单任务」推到可辩护的泛化边界；这是论文 scope 从 workshop 级到主会级的分水岭。

- 入口条件：M2.a 完成且 H1 成立
- TODO：
  - [ ] **泛化阶梯**：同任务跨 seed（已有）→ 跨 layout 扰动幅度扫描（物体初始位姿分布逐级放大）→ 跨实例（不同管径/架型，若资产允许）→ 跨任务
  - [ ] **6-task mechanism suite**（内部审计既定方向）：从 RoboDojo 选 5 个新任务，每个只做「demo → 图 → code → 冻结 → held-out」最小闭环，不追单任务成功率上限
  - [ ] 失败模式分类学：全部失败按阶段×根因交叉表，作为论文分析章
  - [ ] 感知精度对照容差的系统报告（proposal §5.4 的扩展）
- 验收：≥3 个新任务上完成最小闭环并有 held-out 数字（不设成功率下限，但须可归因）；泛化阶梯曲线成图
- 失败分支：新任务的视频→图提取不动 → 论文 scope 收窄为「insert_tubes 深度研究 + 机制消融」，M3 剩余工时转 M4

---

## M4：论文与复现包（W10–W16，与 M3 重叠）

- 入口条件：M2.a 出数字即可启动写作（不等 M3）
- TODO：
  - [ ] W10：story 固化（以 proposal 摘要 + §1.1 反模式证据开场；claims 严格对齐已挣到的数字）
  - [ ] W11–12：主图表——① offset 反模式证据图（B7+slotgeom）；② 泛化 gap 主结果（组 1/2 vs 3）；③ funnel 分阶段归因；④ 消融 A/B；⑤ 泛化阶梯（若 M3 成）
  - [ ] W13：related work 全面扫描更新（Demo2Code/GaP/AgentChord/2026 人类视频技能线各自最新版本，逐篇写差异句）
  - [ ] W14：内审——找 2 人分别扮演「这不就是分层控制」和「为什么不端到端 VLA」的审稿人，逐条回击写入 discussion
  - [ ] W15–16：复现包（fresh clone 可跑 fake-backend 全流程 + 冻结 policy + RunManifest 全审计链）、投稿
- 验收：投稿；复现包在干净机器上一次跑通
- 写作纪律：每个 claim 旁标注证据编号（附录 A 索引沿用）；未挣到的一律进 future work，不进 claims

---

## 决策闸门总表

| 闸门 | 触发时点 | 判据 | 开启走向 | 关闭走向 |
|---|---|---|---|---|
| **H3（伺服）** | M1.a full trial | 对准合格而下插停滞 | 伺服进 M2.c，成核心机制 4 的延伸 | 伺服降为备选；工时转 M2.b/M3 |
| **感知精度** | M1.a 或 M1.c | 非特权孔位误差 vs 容差量级（历史教训 25 mm > 14.9 mm） | 达标：继续 | 不达标：多视角/主动感知进 M1 回修；只许一轮 |
| **视频→图** | W3 末 | T1 产出真图且过验收 | 用提取图做 H1 | 降级人工图，主张收窄，提取移 M3 |
| **消融 A** | M2.c | 推导 vs 手调有无差异 | 「推导」主张入论文 | §4.4 降为实现细节 |
| **消融 B** | M2.b | 几何过滤有无增量 | 两级漏斗入论文 | 保留 VLM 单级 |
| **H1（生死线）** | M2.a | 组 3 对组 1/2 的 held-out gap | 主论文照常 | 转失败分析论文，与老板对齐 |

---

## 持续纪律（贯穿所有里程碑）

1. 每个有效实验后 24h 内更新 `PROGRESS.md`（数字 + 产物路径 + 核实状态）；本文件只在里程碑边界更新
2. 仅 1022；1024 NAS 只读；GT 防火墙常开；发控制必经用户批准
3. 冻结协议：进入任何 held-out 评测后，policy/模型/配置/runtime 全部禁改；违反即该批数字作废
4. runs/ 原始产物不入 Git，只提交脱敏汇总；push 前过 `public_release_check.py`
5. runtime 洁净度：正式 benchmark（M2.a 起）前，把当前 KW dirty 文件（`k1s_v3_w_claw_sim_v0.sim.yaml` 覆盖）固化为可追溯 commit，拒绝 dirty dependency 进 golden run
