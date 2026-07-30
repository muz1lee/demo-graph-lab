# experiments/ — v1 期实验入口（移植源，非活入口）

> **代次**：v1（1022 工作面，2026-07-26 定型） · **主线**：**否**
> **角色**：v1 期的非特权 M1 入口与契约。**当前不作为运行入口**，但仍被测试命令引用

## 现状

`experiments/insert_tubes/` 绑的是 1022 工作面（pipeline `:8000`、`sys.path` bootstrap），
Phase 1 已转到 5090 的 `arm_node + services/common`，所以**它作为活入口已经过时**。

但**不能删**，两个原因：

1. **仓根的测试命令引用它**：
   ```bash
   python3 -m pytest -q method/demo_graph/tests adapters/tests experiments/insert_tubes tests
   ```
   移走会让这条命令 abort，连带一批测试静默失灵。

2. **它是移植源**：`run_m1.py:152-160` 的冻结断言是**全仓唯一**把「冻结」做成可失败断言的代码。
   在把它搬进 `harness/phase1.py` 之前，不要动它。
   同理 `candidate_chain.py` 里三层漏斗每层的幸存者计数，是**消融 B 的原始数据入口**
   （见 `../docs/PROPOSAL.md` §4.2 三层漏斗）。

## 退役条件

- `run_m1.py`：冻结断言搬进 `harness/phase1.py` 并有测试覆盖后，可退役
- `candidate_chain.py`：其正则绑 1022 `pipeline.log` 文本格式，已过时；但**幸存者计数的语义**
  要先在 harness 侧重建（消融 B 需要），再退役
- 其余：按 `../method/README.md` 的通用判断规则

裁决记录见 `../docs/DECISIONS.md`。
