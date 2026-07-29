# harness/ — Demo 理解 Harness(Phase 0,无仿真)

一句话:`demo 视频 → 阶段 × {约束, 验收, 洞} → 四层校验 → report.html 人审/金标 → 对金标打分`。

- 设计与验收门:`../RESEARCH_PROPOSAL_V2.md` §5(唯一权威,此处不重复)
- 词表:`vocab.py`(代码即规范,改词表走 git review)
- 提示词:`prompts/`(版本化;VLM=Claude Opus,仅编译期,两个合法工位,禁数值输出)
- 金标:`goldset/<task>.json`(经 report.html 标注产生)
- 素材:只读复用 1022 `robot-subtask-seg` 的视频与 refined trace,经 source manifest 登记,
  落 5090 `data/upstream/`(不进 git)
- 状态(2026-07-29):脚手架。cli 五个子命令已定型,实现按 §8 TODO-1 推进。
