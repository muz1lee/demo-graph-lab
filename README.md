# demo-graph-lab：演示约束图 → Python Policy

本仓库研究 coding agent 如何从演示中得到可执行的操作约束，再编译为可验证、可局部恢复的 Python 节点策略。

```text
演示视频
  → 时序 / 关键帧证据
  → 带 typed holes 的约束图
  → 生成 Python node policy
  → 运行时感知填洞
  → 反应式执行与可信伺服
```

## 工作边界（硬约束）

- **实验场地**：5090 服务器（仓库 checkout + `~/phase1` 运行目录）。2026-07-29 起自 1022 迁出；
  1022/1024 时期的历史边界见 `AGENTS.md` §9 与 `PROGRESS.md`「硬边界更正」小节（**已不是当前规则**）。
- **对外名**：`demo-graph-lab`（不是 ksm）。
- **Knowin World / 仿真数据**：外部共享依赖，只读借用；**禁止**写入、部署、改配置或启停其服务。

Knowin World 是**外部**运行时依赖，不 vendoring 进本仓。

## 仓库结构

- `harness/`：**当前主线**（2026-07-29 起）——Phase 0 演示理解流水线（`ingest`→`stages`→`keyframes`→
  `objects`→`extract`→`enrich`→`validate`→`report`→`metrics`，另有 `compile`）与 Phase 1 执行适配器
  `kwadapter.py`、两级 gate `gates.py`
- `components/`：字节保留的 WHT 组件快照（含历史 `knowin-skill-manager` 包名）
- `method/demo_graph/`：约束图、状态机、候选、后端、伺服、隔离、RunManifest（v1 期；仍被
  `adapters/__init__.py` 的 eager import 拉起，勿直接删）
- `adapters/`：`knowin_world` / `demo_bundle` / `grasp_proposals` / `observability`
- `experiments/insert_tubes/`：v1 期非特权 M1 入口与契约（仍被下方测试命令引用）
- `RESEARCH_PROPOSAL_V2.md`：**当前权威方案**；`AGENTS.md` / `PROGRESS.md`：边界与进度

## 本地检查

```bash
python3 -m pytest -q method/demo_graph/tests adapters/tests experiments/insert_tubes tests
```

预期 **88 tests / 87 passed**；已知 1 例失败为 `components/SOURCE_MANIFEST.json` 与盘上文件漂移
（`test_repository_source_manifest_is_consistent`）。**不要从仓根裸跑 `pytest`**——`components/` 下的
包需各自的 rootdir，会在收集阶段直接报错。

远程仓库：**主仓为内网 Gitea 私有仓**（remote 名 `gitea`，本地 `main` 跟踪 `gitea/main`）；
5090 用 `ssh -A` 拉取。GitHub `origin` 自 2026-07-29 起**停止维护**，其上内容视为已公开。
首轮暂不添加开源 LICENSE。详见 [SECURITY.md](SECURITY.md)。
