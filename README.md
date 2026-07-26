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

| 允许 | 禁止 |
|---|---|
| 1022：`/mnt/data/wenqian/demo-graph-lab` | 1024：`/mnt/nas/knowin_sim/sim_workspace/` |
| 本仓内模块化技能迭代与只读 probe | 向 `services/ksm` 部署 / 改配置 / 启停基础仓服务 |

Knowin World 是**外部**运行时依赖，不 vendoring 进本仓。

## 仓库结构

- `components/`：字节保留的 WHT 组件快照
- `method/demo_graph/`：约束图、状态机、候选、后端、伺服、隔离、RunManifest
- `adapters/`：`knowin_world` / `demo_bundle` / `grasp_proposals` / `observability`
- `experiments/insert_tubes/`：非特权 M1 入口与契约
- `AGENTS.md` / `ALGORITHM_PLAN.md` / `PROGRESS.md`：原则、方法、进度

## 本地检查

```bash
python3 -m pytest -q method/demo_graph/tests adapters/tests experiments/insert_tubes tests/integration
python3 scripts/public_release_check.py
```

远程仓库：`https://github.com/muz1lee/demo-graph-lab`（已由原 `ksm` 重命名）。  
首轮暂不添加开源 LICENSE。详见 [SECURITY.md](SECURITY.md)。
