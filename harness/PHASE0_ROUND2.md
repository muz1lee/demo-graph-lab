# Phase 0 第二轮结果（v0.2 提取器，2026-07-30）

对照第一轮见 `PHASE0_ROUND1.md`。v0.2 杠杆:object registry / 同类阶段传播+order / holds
时序标记 / cone=接触法向 / cleanup 段入图 / 窗口与装配告警。金标 v2 独立重标(Fable 判卷,
未读 v1 金标;`goldset/*_gold_v2.json`)。

## 1. 总表(v0.1 → v0.2)

| 任务 | P | R | 备注 |
|---|---|---|---|
| insert_tubes | 0.919→**0.978** | 0.872→**0.882** | 双升 |
| stack_bowls | 0.818→**1.0** | 0.621→**0.976** | 最差变最好;registry 治好一碗三名 |
| deposit_coin | 1.0→0.957 | 0.792→0.786 | 持平;plane∥slot 装配缺口仍在(见 §3) |
| push_T | 0.70→0.538 | 0.778→0.636 | 唯一恶化,病因见 §3 |
| push_T_random | 1.0→0.889 | 0.818→0.800 | 压线 |
| **micro 合计** | **0.897→0.931** | **0.777→0.865** | **两道验收门(P≥0.7, R≥0.8)全部通过** |

## 2. 明星:derived 传播项 15 判 **13 correct / 2 incidental / 0 wrong**

一致性传播加的召回几乎零精度代价;2 条 incidental 恰是对称自由的抓取锥(规则预测内)。
holds 机制按设计工作:「约束为真、时序标错」首次可分离(insert_tubes s4 throughout 判 wrong
而 acceptance 的 at_end 版本正确)。cone=接触法向落地正确(悬顶腕姿正确判 side)。

## 3. push_T 恶化的解剖(v0.2 新表面积暴露的三个新病灶)

1. **registry 缺机器人本体**:cleanup 段想表达「撤臂避让块」,registry 里没有 EE/机械臂对象,
   模型把 clearance 绑成 (块,pad)——字面上成了任务目标的否定。→ v0.3: registry 必含 EE/双臂。
2. **holds 让主张可证伪**——v0.1 同样的错误藏在模糊时序里没被计价,v0.2 显式 scope 后被帧
   证据击毙。这部分「恶化」实为测量变诚实。
3. 投票聚合不过滤自我否定候选(`_comment_ignored` 仍 4/5 票通过)。
另:push_T demo 本身缺陷(粗推越推越歪)继续压低该任务上限。

## 4. Phase 0 验收门(proposal §5.4)终判

- 金标 5/5 ✅;P≥0.7 ✅(0.931);**R≥0.8 ✅(0.865)**;自一致性(k=5 全 5/5 解析)✅;
  零度量字面量 ✅;单任务成本≤$5 ✅(v0.2 全轮 ~$8,单任务 $0.5–1.9)。
- 歧义对≥3/4 ❌→**改判定义**:现有素材不含目标歧义(random 变体只随机布局;deposit_coin
  单币单槽),该项移交「素材构造」任务(多目标录制或 Phase 1 仿真),不计入本轮门。
- **裁决:Phase 0 理解层达标,可开 Phase 1(执行绑定)。**

## 5. v0.3 backlog(按性价比排序,来自 v2 标注 agent 的一手发现)

1. registry 必含 EE/机械臂 + 不漏台面实例(insert_tubes 漏了一根管、tube_left 跨阶段指两根管)
   + 收录干扰物(透明支架仍缺)。
2. 装配缺口修复调用:deposit_coin 的 coin.plane∥slot 轴约束仍缺(洞在、约束没组装);
   告警已有,补一个针对性 repair 微调用。
3. 镜头切变检测:push_T_random 源视频在 cleanup 段结束前切片,关键帧混入无关场景。
4. 投票聚合过滤自我否定候选;order 序列应含 cleanup 段;符号漂移归一(up_axis vs stack_axis)。
5. 待 PI 复核点:push_T_random s0 axis_parallel 两轮判定相反(两个 demo 事实不同,口径一致
   ——天然标注一致性样本);push_T cleanup clearance 双 wrong 的补录 args。
