# 金标标注规则 v1（2026-07-29）

对 `graph.json` 里每条 constraint / acceptance 给四选一判定；漏提的写进 missing。

## 判定值

- **correct（核心约束）**：demo 可见支持，且**违反它任务大概率失败**。例：管插入时 `axis_parallel(tube↔hole)`；抓取 `region_grasp(tube, upper_body)`（决定重力转正可行性）。
- **incidental（真但非核心）**：demo 里确实如此，但只是**等价类中的一个样本**，换等价方式任务照样成功。**老板原则（2026-07-29）**：旋转对称物体（圆管/圆碗/圆孔）的**接近方位角是 symmetry-free DoF**——demo 教的是抓取"关系"（闭合轴⊥长轴、抓哪个区域、从上/从侧的仰角类别），不是每次必须复制的方位。典型 incidental：对称管的 `approach_direction(side vs top_down)` 之争、用哪只手、路径绕行方向。
- **wrong**：demo 不支持（视觉证据缺失/相反），或词表语义误用（如 `cone` 填了物体名）。
- **unsure**：关键帧看不清、遮挡导致无法判定。写明原因。

## missing（漏提）

站在"要把这个阶段执行成功，还缺哪条必要约束"的角度补。常见：order（先后依赖）、carry（搬运中保持竖直）、与已放置物体的 clearance、release 条件。

## 指标口径（metrics.py 实现同步）

- precision = correct / (correct + wrong)
- recall = correct / (correct + missing)
- incidental、unsure 单列计数，**不进 P/R 分母**。

## 流程

标注者逐阶段看关键帧（`frames/stageNN/`），judgment 必须引用帧证据；产物写
`harness/goldset/<task>_gold.json`（report.html exportGold 同构：
`stages{<idx>:{constraints:[{key,verdict,note}],acceptance:[...],missing:[...]}}`，
key = `name|args的JSON`）。本轮标注者 = Claude（bring-up 用）；**论文级金标须 PI 按本
规则抽查复核**，两者分歧本身记录为标注一致性数据。
