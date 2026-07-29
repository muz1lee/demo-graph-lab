# Phase 0 第一轮结果（2026-07-30 凌晨）

5 任务 × k=5 提取 + 金标标注 + 指标。标注者 = Claude（bring-up 级，按 `goldset/RUBRIC.md`；
**论文级金标须 PI 抽查复核**，待复核点见 §4）。全部产物在 `harness/runs/`、`harness/goldset/`。

## 1. 总表

| 任务 | P | R | correct/incid/wrong/missing | 备注 |
|---|---|---|---|---|
| insert_tubes | **0.919** | **0.872** | 34/2/3/5 | 双过线;重力转正机制被帧证据自证 |
| stack_bowls | 0.818 | 0.621 | 18/2/4/11 | R 未过线:stage6 第二摞几乎全漏 |
| deposit_coin | **1.0** | 0.792 | 19/2/0/5 | 零 wrong;漏的集中在平面对齐+时序 |
| push_T | 0.70 | 0.778 | 7/0/3/2 | demo 自身粗推越推越歪,提取照抄了假设 |
| push_T_random | **1.0** | **0.818** | 9/0/0/2 | 零污染(无抓取类幻觉) |
| **micro 合计** | **0.897** | **0.777** | 87/6/10/25 | **P 门(≥0.7)✅ 过线有余;R 门(≥0.8)差 0.023 近失** |

成本:全天 151 次 Opus 调用共 **$6.49**(单任务全量 $0.4–1.3)。k=5 全部 5/5 解析;
validator 最终全任务 0 违例(度量字面量零泄漏)。

## 2. 系统性错误分类（wrong/missing 的病因谱,附改进杠杆）

- **A. 阶段边界/时序错位**(最大 wrong 来源,4 任务出现):把下一阶段的稳态倒灌进当前窗口
  (pick 阶段声称 axis_vertical,而管还在摆动;pick 声称离桌,而碗还贴桌)。
  → 杠杆:自动校验 evidence_frames ∈ 阶段窗口;约束区分「事件式(末态达成)vs 不变量式(全程保持)」。
- **B. 不对称提取/跨阶段不一致**:同类阶段有的约束另一处漏(s1 插入缺 inside/center_align
  而 s3/s5 都有;carry 忽有忽无)。→ 杠杆:同类型阶段一致性检查 pass(便宜,规则即可)。
- **C. 跨阶段共指缺失**(stack_bowls 一只碗三个名字→order 依赖结构性不可见,全图 order=0)。
  → 杠杆:先全视频建一次 object registry,各阶段强制引用 registry id。
- **D. 装配失败**(deposit_coin:coin_normal_axis/slot_axis 两个洞都挖了,却没组装成
  axis_parallel 约束——感知有、词表有、组装丢)。→ 杠杆:洞→约束 linker 检查。
- **E. 语义混淆**:approach cone 被按「腕姿」而非「接触法向」理解(push_T 头顶腕姿误标
  top_down,实际侧向推)。→ 杠杆:prompt 里用接触法向定义 cone + 一个反例。
- **F. 词表缺口**(记录于各 rationale,汇总):无 on/contact 谓词;axis_parallel 无向
  (180° 翻转的 T 也满足→需有向对齐或 footprint containment);推压的接触侧/推向无一等
  表达(现靠 hole 兜底);cleanup 段被跳过→release 时序与撤臂 clearance 无处挂。

## 3. 两个方法学收获

- **对称性原则被 demo 自证**(老板 07-29 提出,RUBRIC 落地):stack_bowls 同一 demo 里
  s0 顶抓、s2 侧抓、同样 rim grasp——等价类的两个样本自己出现在数据里;而放置/插入侧的
  cone 判 core(碗只能沿栈轴入、币必须 edge-on 入槽)。「抓取侧方位=自由,放置侧方位=承重」
  这条分界线五个任务全部成立。
- **歧义对必须构造**:push_T vs push_T_random 指令逐字相同但只随机了布局,目标相同
  (副产品:跨布局提取到语义一致的约束集=稳定性证据);deposit_coin 单币单槽无选择歧义。
  现有素材没有目标歧义,需专门录制(多币多槽/多目标位)或 Phase 1 仿真构造。

## 4. 待 PI 复核的标注争议点（标注 agent 主动上交的）

1. push_T_random s0 acceptance `axis_parallel`:按粗容差判 correct(交接残差 20–30°),
   严口径可降 incidental——两卷 push 对照判定相反但口径一致,是天然标注一致性样本。
2. push_T s1 `approach_direction{top_down}` 双记账:错值计 wrong+正确值 side 计 missing,
   只认一账则删 missing(P/R 影响已标注)。
3. insert_tubes pick 阶段 `axis_vertical` 判 wrong(非 unsure):证据帧全程 30–45° 摆动。
4. deposit_coin 抓取约束的条件性:demo 里币被透明支架斜撑(非平放),平放场景不可迁移。

## 5. 下一步

- **v0.2 提取器**(按 §2 杠杆逐条):object registry → 窗口校验 → 一致性 pass →
  洞-约束 linker → cone 语义修 → 词表 v0.2(on/contact、有向对齐、release 槽位)。
  预期主攻 R(0.777→0.8+),P 已有余量。
- 歧义对素材构造方案(录制 or 仿真)另定。
- 5090 侧 extract 待办公室代理恢复后补验;9 个未标任务视需要扩量。
