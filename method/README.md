# method/ — v1 协议与不变量层

> **代次**：v1（1022 工作面，2026-07-26 定型） · **主线**：**否**
> **角色**：协议与不变量层——出**纪律**（不变量、冻结、审计链），不出数字
> **与主线的依赖边**：**零**。`grep -rn "method\." harness/*.py` 退出码 1，harness 从不 import 本目录

## 为什么它还在（不是技术债，是未来依赖）

常见误解：「v2 是最新方案，v1 的代码就该删」。**这个判断对普通工程仓成立，对本仓不成立**——
因为 `method/` 实现的不是 v1 的方案，而是 **v2 的核心假设 H1 所依赖、但 `harness/` 还没写的部分**。

对照 `../docs/PROPOSAL.md`（2026-07-29，当前权威方案）：

| v2 的要求 | 出处 | harness/ 实现了吗 |
|---|---|---|
| 「策略代码**冻结后**，在 held-out seed/layout 上的成功率与泛化 gap」 | v2 §0 北极星 | ❌ 零实现 |
| **H1**：「非度量约束程序 + 运行时绑定，**冻结后** held-out 成功率显著优于…」 | v2 §2 | ❌ 零实现 |
| 「一次编译**冻结**复用」（与 ReKep/CoPa 的差异点之一） | v2 §1.1 | ❌ 零实现 |
| 「代码中零场景度量字面量」 | v2 §0 效果层 | 部分（`harness/validate.py` 有扫描，无冻结） |

而 v2 §0 第 6 行自己写明：「执行与**冻结协议实验后置到 Phase 1/2**」——是**后置，不是取消**。

实测：`grep 'freeze|frozen|digest|sha256|manifest' harness/*.py` 命中全是字符串字段，
冻结协议在 harness 侧**零实现**；本目录的 `manifest.py` / `metric_scan.py` / `provenance.py` 是现成实现。

## 本目录承载的能力（harness/ 均无等价物）

| 能力 | 位置 | v2 里的用途 |
|---|---|---|
| typed `ConstraintGraph`（`TypedHole` 八元组） | `demo_graph/models.py` | 图 schema 的类型层 |
| `RunManifest` + `freeze_policy` + `assert_frozen_policy_unchanged` | `demo_graph/manifest.py` | **H1 的冻结协议** |
| 递归 oracle 防火墙 `assert_method_safe` | `demo_graph/provenance.py` | GT 防火墙的程序化强制 |
| 度量字面量静态扫描 | `demo_graph/metric_scan.py` | 「零场景度量字面量」验收 |
| 子进程隔离沙箱 | `demo_graph/isolation.py` | 生成代码的执行边界（当前 `harness/phase1.py` 用裸 `exec`，更弱） |
| insert_tubes 确定性提取（零 LLM） | `demo_graph/rule_extractor` 类模块 | **提取层的地板线对照臂**，不是 `harness/extract.py` 的竞争者 |

## 退役条件

**逐模块退役，不整树删除。** 单个模块可退役，当且仅当：

1. 对应能力已在 `harness/` 侧重建，**且有测试覆盖**；或
2. `../docs/PROPOSAL.md` 的假设/消融/验收门中**没有任何一条**需要它。

判断规则（可自查）：**「v2 proposal 或实验矩阵里，有没有哪条假设/消融/验收门需要它？」**
有 → 留着，哪怕当前无人 import（研究基础设施本来就是先写后用）；没有 → 删。

裁决与逐模块理由见 `../docs/DECISIONS.md`。

## 与 harness/ 的撞名对照（**没有一对是重复实现，有两对方向相反**）

| v2 `harness/` | v1 本目录 / `adapters/` | 实际关系 |
|---|---|---|
| `extract.py`（LLM 视觉提取） | `demo_graph/extractor.py`（零 LLM 规则） | 输入输出 schema 不兼容，是对照臂 |
| `keyframes.py`（抽帧存 JPEG） | `demo_graph/keyframe_relations.py`（PCA 主轴 + 接触侧几何） | 完全不同的东西 |
| `contract.py`（可调用面白名单） | `adapters/contracts.py`（数据溯源契约） | 同名不同域 |
| `fakerun.py`（假的是 runtime API） | `demo_graph/examples/m1_fake.py`（假的是世界状态） | 假的东西不同 |
| `kwadapter.py`（读 `GET /state` oracle） | `adapters/knowin_world/adapter.py`（**拒绝** `/state`） | **方向相反** |
