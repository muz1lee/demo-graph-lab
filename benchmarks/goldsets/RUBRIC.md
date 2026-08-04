# 金标标注规则

对 `graph.json` 里每条 constraint / acceptance 给四选一判定；漏提的写进 missing。

## 判定值

- **correct（核心约束）**：demo 可见支持，且**违反它任务大概率失败**。例：管插入时 `axis_parallel(tube↔hole)`；抓取 `region_grasp(tube, upper_body)`（决定重力转正可行性）。
- **incidental（真但非核心）**：demo 里确实如此，但只是**等价类中的一个样本**，换等价方式任务照样成功。对旋转对称物体（圆管、圆碗、圆孔），接近方位角通常是自由度；demo 教的是抓取关系，而不是每次复制同一方位。典型例子包括：对称管从哪一侧接近、用哪只手、从哪边绕行。
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
`benchmarks/goldsets/<task>_gold.json`（与 report.html 导出的结构相同：
`stages{<idx>:{constraints:[{key,verdict,note}],acceptance:[...],missing:[...]}}`，
key = `name|args的JSON`）。初始标注由 Claude 完成；**论文使用前须由研究者按本规则
抽查复核**，分歧记录为标注一致性数据。
