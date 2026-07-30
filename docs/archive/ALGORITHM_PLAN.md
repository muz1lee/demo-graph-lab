# 算法方案：演示约束图驱动的 code-as-policy

一句话：**演示视频不用来造数据，用来给 coding agent 写一份带洞的规格说明书（spec with typed holes）；洞由执行期感知填，约束同时编译成运动参数和运行时断言。**

关键设计立场：约束图的价值不只在"首次生成的代码更好"，更在**失败时的信用分配更准**——失败能定位到「哪个节点的哪条约束被违反、该约束的哪个参数还有搜索域」，这是它相对自由文本 plan 和相对 ASPIRE/RATs 全轨迹诊断的机制性优势。

---

## 四个阶段

### A. 演示 → 子任务 trace（已有，wht 的 robot-subtask-seg）
产物：分段 + 每段 actor_arm / eef_event / 操作物 / 目标物 / 是否需要对齐。14 个 RoboDojo 视频已跑通。
**不要在这一步投入更多**，它已经够用。

### B. trace → 约束图（新贡献，本方案的核心）

图 = 节点（子任务）+ 边（顺序 / 资源 / 碰撞），每个节点挂约束。schema 见 `../reference/constraint_graph_schema.md`。

关键点：**约束按"是否 2D 可提取"分层**，这是回避"视频 3D 提取效果差"的设计（详见下面"信息边界"）。

| 约束 | 2D 单目可提取？ | 提取方式 |
|---|---|---|
| 子任务分段/顺序 | ✅ | 已有 trace |
| 槽位分配（哪根管进哪个槽、顺序） | ✅ | CoTracker 终点相对 rack bbox 的横向序 |
| 抓取高度比例（管身上半段） | ✅ 尺度无关 | 夹爪落点相对物体 bbox 的高度分数 |
| 对齐需求（管轴 ∥ 槽轴） | ✅ 部分 | 主轴相对角；demo trace 已有 requires_alignment |
| 插入方向 | ✅ | 竖直向下在图像里可观测 |
| 自由 DoF（如绕管轴近似对称） | ⚠️ 可由视频/通用类别先验提出假设 | 执行期多视角感知或主动旋转验证；不能读 asset 几何 |
| grasp 位姿（6D） | ❌ | 执行期 GraspNet / qwen_xquat 现场解 |
| 插入深度（米） | ❌ | 执行期几何 + 有界搜索 |
| 力阈值 | ❌ 原理上不可见 | 机器人通用安全上限 + agent 有界探测；不能读 asset 物理参数 |

**"带洞的 spec"**：不可提取的量不写死，写成 typed hole——声明类型（位姿 / 长度 / 力）、求解器（哪个感知工具）、搜索域（合法区间）。图给的是"必须满足什么关系"，不是"移动到哪个坐标"。

### C. 约束图 → 代码（CaP 部分，成败关键）

三条设计规则：

1. **逐节点闭环编译，不生成整段长脚本**。GaP 论文实测单 LLM 直吐整段 Python 成功率崩为 0；zyh 的 RoboMEx 也是这个判断。每个节点：编译 → 执行 → 重新观察 → 验证 postcondition → 进下一节点。
2. **每条约束双向编译**：既生成运动参数（如 alignment → 预插入位姿的姿态项），又生成运行时断言（如 `pick_verifier`、谓词检查、力/行程门限）。这就是 "geometrically verifiable" 的落点，也是不让 agent 伪造成功的机制。
3. **失败信用分配走约束**：节点失败时，把「违反了哪条约束 + 该约束 hole 的搜索域」回传给 agent，agent 只在该域内改参数或换求解策略，而不是重写整段 workflow。有界重试次数写在节点的 `recovery` 字段里。

主方法的产物是受限 Python node policy。现有 WHT KW YAML generator 原样保留为 legacy
baseline；Python node 在 Knowin World 内部可以被 adapter 编译或包装成不可变临时 skill
运输，但 YAML 不再是方法层必须输出的格式。

Code Agent 只接收：约束图、演示证据、Method API 契约和可信 controller registry。它不能获得
EvalServer、scene/task metadata、文件系统或任意网络能力。每个 node handler 只能返回
`execute_controller / request_evidence / retry / complete / fail` 这类受限决定。

闭环分成三个时间尺度：

1. task graph：语义节点和恢复边，按事件或低频观察推进；
2. node state machine：填洞、候选、admission、执行、验证和有界恢复；
3. trusted servo：在 runtime 内以控制频率执行感知—修正—验证循环。

LLM 不进入 servo tick。Graph 只接收 controller 的 `converged / recoverable / abort` 摘要，
不能把每个控制周期展开成 graph node。

### D. 泛化测试（论文的 payoff）

图冻结不变，扰动场景重新编译执行：换 rack 位置、换试管初始布局、改试管数量（2/4 根）、换同类物体。
图编码的是关系不是坐标，所以应当仍然成立——**这正是 LIBERO-Pro 暴露的 VLA 死穴，也是 CaP 的天然强项**。

---

## 实验矩阵（回答"图到底有没有用"）

四组，同一个 LLM、同一套 harness、同一批任务：

| 组 | 输入 | 对位的已有工作 | 要证明什么 |
|---|---|---|---|
| B1 | 只有任务指令 | CaP-Agent0 | 无演示基线 |
| B2 | 演示 → 纯文本 plan | SeeDo | **拆掉"图/约束"只留文本，是否就掉下来**（最关键的消融） |
| B3 | 演示 → 约束图 | 本方案 | 主结果 |
| B4 | 人工 oracle 图 | 上界 | **把"提取质量"和"代码生成质量"分开** |

B4 与 B3 的差距 = 提取管线的损失；B3 与 B2 的差距 = 约束图本身的价值。有了 B4，即使 wht 的视频提取暂时弱，也能先证明方法的天花板值不值得追。

三个指标，第二个最有说服力：
1. **任务成功率**（insert_tubes + 2–3 个 Precision/Long-Horizon 任务）
2. **达到成功所需的环境交互次数 / agent 迭代轮数** —— 我们主张"单条演示、zero-interaction"，直接对位 ASPIRE/RATs 需要大量 play/trace 探索
3. **扰动下的成功率（图冻结）** —— 泛化

消融：分别去掉 grasp 约束边 / 槽位资源边 / 碰撞边，看各掉多少。（`insert_tubes` 里槽位资源边是 wht 的 v7 workflow 真实缺失的信息——三根管共用同一个 rack label 和同一个 preinsert offset，第 2、3 根有撞上已插入试管的风险。如果这条边被证明是成败关键，它就是"约束图 > 文本 plan"最漂亮的单点证据。）

---

## 信息边界（这是方法论上的一个正面论证，不是缺陷）

wht 说"视频提取效果不好"是准确的，我在他的 `demonstration_bundle.json` 里看到实锤：坐标系 `video_image_pixels 640×480`，5 条 `evidence_gaps`（无 metric depth / 无相机标定 / 无机器人状态同步 / 无 6D pose / mask 仅采样帧），白色试管 CoTracker 可靠帧率仅 0.59（抓取和插入时夹爪必然遮挡）。

单目无标定视频恢复度量 3D 是病态问题。**所以中间表示必须是约束图而不是轨迹**：
- 轨迹复现路线（Do As I Do / Qwen-RobotManip）要求从视频恢复度量级 3D，难且不泛化；
- 约束图只要求视频提供拓扑和关系（2D 可稳定提取），把度量落地推迟到执行期感知。

这条"演示给关系、执行期给度量"的分工，可以直接写进方法节，也顺带解释了 GraspNet 的正确位置：**在执行期解算 grasp 约束**，不是从视频里提抓取。

---

## 风险与止损点

| 风险 | 怎么先证伪 |
|---|---|
| **R1 图是多余的**：agent 光看指令也能写出同样的代码 | B1/B2 对照。若 B2 就接近 B3，方法没价值 → 换任务（选更依赖演示信息的：多槽位分配、有顺序约束的长程任务） |
| **R2 洞填不上**：hole 需要的 3D 感知在这套 runtime 里不可靠 | 先跑 B4 oracle 图。oracle 都失败 → 是执行/感知瓶颈，方法层再好也无用，止损 |
| **R3 约束不可运行时检查**：如无力反馈，guarded motion 退化 | 逐条标注"本 runtime 可检查性"，不可检查的降级为开环参数并在论文中说明 |
| **R4 仿真物理不稳**（抓取电流、夹爪收敛） | **不由方法层承担**，见下方 infra 待办；实验用固定 seed + 多 trial，把物理噪声作为方差报告 |

**第一个该做的实验（最强证伪）**：按
`experiments/insert_tubes/m1_contract.json` 跑非特权的单试管
`demo evidence → graph → Python policy`。先取得抓取、转正和对准的 method-visible stage
success，再由隔离 evaluator 给终局 verdict。Oracle 图只作诊断上界，不能进入主方法。

---

## 执行后端边界

Knowin World 只提供 runtime、传感器、控制和隔离评测。生成 policy 不直接持有 EvalServer URL；
trusted parent 负责 reset、skill lifecycle 和 finalize，并删除响应中的 simulator state 后才形成
method-visible result。

插入等接触任务必须由受审计的 controller/skill plugin 承担高频闭环。若 runtime 当前只有预计算
轨迹流，则先把 servo 接口和假 plant 测试做完整，再单独实现 plugin；不得用 LLM 生成的 Python
通过 HTTP 逐 tick 模拟高频控制。
