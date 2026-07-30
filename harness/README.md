# harness/ — v2 主线（Phase 0 理解层 + Phase 1 执行层）

> **代次**：v2（5090 工作面，2026-07-29 起） · **主线**：**是**
> **角色**：实证流水线——出数字（P/R、episode 报告）
> **状态**：Phase 0 已达标（micro P=0.931 / R=0.865，2026-07-30）；Phase 1 到 M1a·ORACLE

一句话：`demo 视频 → 阶段 × {约束, 验收, 洞} → 校验 → 编译成 policy.py → 5090 执行`。

## 归属图

每个模块的 docstring 第 1 行带阶段标签，`head -1 harness/*.py` 即完整归属图。

| 标签 | 模块 | 职责 |
|---|---|---|
| `[entry]` | `cli.py` | 唯一入口，11 个子命令 |
| `[phase0 1/9]` | `ingest.py` | 定位任务视频与 refined trace，抽帧 |
| `[phase0 2/9]` | `stages.py` | 切阶段（trace.json 优先，VLM 兜底） |
| `[phase0 3/9]` | `keyframes.py` | 每阶段按时间窗取 K 帧 |
| `[phase0 4/9]` | `registry.py` | 物体注册表（**CLI 子命令名是 `objects`**） |
| `[phase0 5/9]` | `extract.py` | 逐阶段调 Opus 提取 {约束, 验收, 洞}，k=5 自一致性 |
| `[phase0 6/9]` | `enrich.py` | 确定性补全 pass（无 LLM） |
| `[phase0 7/9]` | `validate.py` | 结构/词表校验 + 度量字面量扫描 |
| `[phase0 8/9]` | `report.py` | 单文件 report.html，供人审与打金标 |
| `[phase0 9/9]` | `metrics.py` | 对金标算 P/R（**不在 `all` 里**，需 `--gold`） |
| `[phase0 · 词表]` | `vocab.py` | 封闭约束词表 v0，代码即规范 |
| `[compile]` | `compilepolicy.py` | graph.json + 契约 → `policy.py`（**不在 `all` 里**） |
| `[runtime]` | `contract.py` | `rt.*` API 契约。**豁免标签、禁改内容**，见下 |
| `[runtime]` | `fakerun.py` | Fake 运行时 + 可信 runner（两级 ReAct 骨架） |
| `[runtime]` | `gates.py` | 阶段 gate：空洞性 + 世界变化双检查 |
| `[phase1]` | `kwadapter.py` | `KWRuntime` — 契约的 knowin-world 实现 |
| `[phase1 · entry]` | `phase1.py` | Phase 1 CLI（`smoke` / `episode`，只在 5090 跑） |
| `[common]` | `llm.py` | OpenRouter 客户端，仅编译期 |
| `[common · 路径锚点]` | `util.py` | `.env` 加载、run 目录、**`HARNESS_ROOT`** |

## 依赖方向（无环，全部指向上游或平级）

```
cli → phase0 九步, compilepolicy, util
phase0 → util, llm, vocab
compilepolicy → util, llm, contract, fakerun     ← 编译期要 dry-run，故依赖 runtime
phase1 → contract, fakerun, gates, kwadapter, adapters.knowin_world.pipeline
gates → (叶子)
```

## 两条不能碰的东西

1. **`contract.py` 是版本化的 prompt 资产，不只是代码。**
   `compilepolicy.py:83` 用 `inspect.getsource(contract)` 把**整个文件（含模块 docstring）**
   拼进编译提示词的 `## CONTRACT SOURCE` 段。改文件名安全（反射走 `module.__file__`），
   但**改内容会静默改变 LLM 的输入**，可能改变生成的 policy——没有任何工具会报这个错。
   所以它豁免了阶段标签。改它等同于改提示词，要按改提示词的纪律走。

2. **`util.py` 是全 harness 唯一的 `__file__` 路径锚点**（`util.py:11` 的 `HARNESS_ROOT`）。
   `prompts/`、`runs/`、`.env` 三类资产全部由它派生。把它移进子目录会**静默**把产物写到新位置，
   旧 `runs/` 变孤儿且不报错。另注：`.env` 不存在时 `util.py:18` 静默跳过——本地已 `export`
   key 就一切照跑，到 5090 或干净 shell 才炸。

**这也是本目录不建 `phase0/` `phase1/` 子包的原因**：收益是「一眼看出归属」，而这个
docstring 标签方案已经 100% 达成；建子包却要付上面两个静默失败风险 + 23 行 import
+ 72 条文档行锚重写。裁决记录见 `../docs/DECISIONS.md`。

## 数据资产（都不进 git 或另有纪律）

| 目录 | 说明 |
|---|---|
| `prompts/` | 版本化提示词。VLM = Claude Opus，仅编译期，两个合法工位，禁数值输出 |
| `goldset/` | 人工金标 + rationale。经 `report.html` 标注产生，**入库** |
| `runs/` | 运行产物，`.gitignore` 排除。**新 checkout 是空的** |

## 相关文档

Phase 0 的设计与验收门以 `../docs/archive/PROPOSAL_v2.md` §5（§5.3 校验四层、§5.4 验收门）为唯一权威——v3 刻意未收录这一块。
当前方法主张与框架见 `../docs/PROPOSAL.md`（v3），实验与 TODO 见 `../docs/EXECUTION.md`。
本目录另有阶段性现场文档：`PHASE0_ROUND1.md` / `PHASE0_ROUND2.md`（Phase 0 两轮结果）、
`PHASE1_API_PLAN.md`（感知 API 计划，**是计划不是现状**）、`PHASE1_M1A_STATUS.md`（现场状态）、
`DESIGN_GRASP_AND_LOOP.md`（抓取与闭环的设计裁定）。
