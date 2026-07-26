# demo-graph-lab 项目方案

本文件只定义项目长期稳定的目标、方法边界和协作约定，不记录当前进度、实验结论或 next todo。

开始任何工作前必须依次读取：

1. `AGENTS.md`：理解项目方案与边界；
2. `ALGORITHM_PLAN.md`：理解完整方法假设和实验设计；
3. `PROGRESS.md`：确认最新事实、当前里程碑、正在运行的任务和唯一下一步。

用户当前明确指令优先于上述文档。动态状态一律维护在 `PROGRESS.md`，不要写回本文件。

## 1. 项目定位

本项目属于“研究先行、未来迁移”工作线：先研究 coding agent 如何从演示中获得可执行的
manipulation 约束，以后再迁移到自家机器人基础设施。它不是公司工程部署项目，也不与
k1-scene 等项目共用目标。

项目的核心问题是：

> 能否把演示视频转成带约束的子任务图，再由 coding agent 结合执行期感知，把图逐节点编译为
> 可执行、可验证、失败后可局部恢复的机器人策略？

演示不是用来复现逐帧轨迹，而是用来提供任务结构和关系；执行期感知负责把关系落到当前场景的
度量位姿。

## 2. 方法总览

完整管线分为五层：

```text
演示视频
  → 关键事件与子任务 trace
  → 带 typed holes 的约束图
  → 执行期感知/规划器填洞
  → coding agent 逐节点编译、执行、验证和局部恢复
```

### 2.1 演示证据层

从视频中提取：

- 子任务边界与执行顺序；
- actor、操作对象和目标对象；
- 抓取发生在物体的哪个相对区域；
- 抓取前后的 approach 与重定向关系；
- 放置/插入的目标关系、轴、槽位和资源顺序；
- 支撑上述判断的关键帧、时间范围、置信度和 provenance。

均匀抽帧只是原始证据，不等于关键帧。关键帧应围绕状态跃迁选择，例如接触、闭爪、离面、
重定向、预对准、插入和释放。

### 2.2 约束图层

图由子任务节点和跨节点边组成。每个操作节点至少要显式表达四类核心约束：

1. 抓取的自由/锁定 DoF；
2. 放置或插入的自由/锁定 DoF；
3. 抓取区域或抓取位置；
4. 放置点、目标区域或插入轴。

边表达顺序、资源互斥、对象依赖、carry constraint 和 collision avoidance。图应描述
“什么关系必须成立”，而不是把一次场景中的世界坐标伪装成可泛化策略。

每条约束必须带来源和依赖链。主方法允许的 provenance 包括：

`demo_video / task_instruction / runtime_perception / generic_prior / derived`

其中 `derived` 必须列出 `derived_from`，并继承上游信息的权限等级。`privileged_oracle` 可以作为
上界或诊断标签存在，但含有该标签或依赖它的字段不得进入主方法生成的图、agent prompt 或执行决策。

### 2.3 Typed hole

视频不能可靠提供的度量量应保留为 typed hole，而不是猜一个常数。typed hole 至少包含：

- 类型、shape、单位和坐标系；
- 合法搜索域；
- 候选求解器；
- 求解所需输入；
- 运行时验证方式；
- 失败后的有界恢复策略。

典型 hole 包括 6D grasp pose、精确目标 pose、插入深度和接触阈值。

### 2.4 图编译与执行

coding agent 不一次生成整段不可观察的长脚本，而是逐节点闭环：

```text
读取节点约束
  → 填充当前节点的 holes
  → 生成/选择动作
  → 执行
  → 检查 postcondition
  → 成功后进入下一节点，失败则只在允许域内恢复
```

每条约束应尽量双向编译：

- 正向成为动作参数或规划约束；
- 反向成为 postcondition、verifier 或运行时断言。

恢复反馈必须指出“哪个节点的哪条约束失败、哪些参数仍可调整”，避免无边界地重写整个 workflow。

## 3. 信息边界

单目、无标定或遮挡严重的视频适合提供拓扑与相对关系，不适合直接承诺度量级 3D 真值。
仿真只是执行与评测后端，不能成为 graph generator 的答案库。项目采用与真机一致的
**observability contract**：一个量是否可用取决于它通过什么观测路径获得，而不只取决于它的数值。

主方法可见的信息只有：

- 演示视频中的关系、顺序、相对抓取区域和任务偏好；
- 任务指令中的目标语义，但不包括仿真为该实例预填的精确答案；
- 运行时传感器与感知 API 的输出，例如 RGB-D、点云、检测/分割、尺寸估计、当前 6D pose/DoF
  估计、轴向估计和 grasp proposals；
- 机器人自身状态、动作反馈以及从上述允许信息推导出的结果；
- 与具体仿真实例无关、明确披露的通用先验。

主方法禁止直接或间接读取：

- scene/asset library、USD/URDF/MJCF、mesh、碰撞体、预存 affordance 或资产标注；
- simulator 中对象的精确 pose/DoF、AABB、尺寸、body/instance ID、ground-truth mask、接触状态；
- task/evaluator 内预存的孔位、目标坐标、成功状态或其他答案；
- 由上述 privileged 数据计算后换名包装的“感知结果”。给 ground truth 套一个 perception API
  名称仍然属于泄露。

因此，图可以表达“夹持试管中段”“试管长轴对齐孔轴”以及对应 typed hole，但不能预填
scene library 中的试管长度、精确世界位姿、物体系抓取矩阵或孔心坐标。这些量必须由执行期感知
和规划器估计、生成并验证。

oracle 信息只允许用于三类隔离用途：评测、基础设施 sanity check、方法上界/故障归因。其输入、
产物和指标必须标为 `privileged_oracle`，与主方法的 graph/prompt/run 分目录保存，不能回流到
候选生成、候选排序、动作选择或恢复。自动校验应拒绝任何 provenance 依赖链中出现
`privileged_oracle` 的主方法图。

## 4. API 与抽象层

机器人 API 是执行基座，不是论文贡献本身。接口设计可以参考 CaP-X，但不整套照搬。

稳定的 API 原则：

- 底层能力、通用几何工具和任务专属 skill 必须分层；
- 每个接口明确参数 shape、dtype、单位、frame、四元数顺序和失败语义；
- 坐标系转换是独立、可测试的纯函数；
- 允许用薄适配器暴露已有能力，但薄适配器不得偷偷加入任务策略；
- perception API 必须返回观测值、置信度和可追溯的 sensor evidence；不得用普通接口名包装
  simulator exact state。为了 bring-up 暂时使用的 oracle API 必须显式命名并从主方法 allowlist 移除；
- agent 必须能看到完整契约、可用范围和失败信息；
- 高层 pick/place 包装可用于基础设施 sanity check，但主方法不能依赖它隐藏约束推理。

引入一个 API 前必须回答：

1. 它属于感知、几何、控制、验证还是任务 skill？
2. 它填充约束图中的哪个 typed hole？
3. 它引入了什么人工先验？
4. 失败能否被观察并归因？

## 5. Grasp candidate 的位置

GraspNet 或其他 grasp planner 是 grasp-pose hole 的候选求解器，不直接决定最终任务策略。

推荐保持三层分离：

1. `generate_candidates`：从传感器深度、相机参数和感知模型产生的实例 mask 生成多个 6D grasp
   与原始分数，主方法不得使用 simulator instance ID 或 ground-truth mask；
2. `filter_candidates`：按可达性、碰撞、夹爪宽度和图中的硬约束过滤；
3. `rank_from_demo`：结合演示抓取关键帧，对剩余候选的抓取区域、approach 和 DoF 相似度排序。

必须保留原始候选、坐标变换、过滤原因和最终选择依据。若必要输入缺失，应把 hole 标为未解，
不能静默退化成手写 pose。

## 6. 验证与失败信用分配

项目必须区分以下五个层面的成败：

1. executor/机器人基础设施是否具备动作能力；
2. 约束图是否包含完成任务所需的信息；
3. coding agent 是否正确编译并执行了图；
4. 自动视频提取是否恢复了正确图；
5. task predicate/verifier 是否正确评价真实结果。

workflow 无报错执行完不等于任务成功。评价应同时保留：

- 动作阶段结果；
- 关键状态的视觉证据；
- 可用的几何/接触 verifier；
- 最终 task predicate；
- 第一失败节点和违反的约束。

谓词和 verifier 应先用明显正例与明显负例做回归，证明能区分目标状态，再用于评价策略。
不能为了让某次 trial 通过而事后移动阈值。

## 7. 研究评价原则

实验设计要把不同误差源拆开：

- oracle 图用于测执行与编译上界；
- 自动提取图与 oracle 图的差距衡量提取损失；
- 文本 plan 与约束图的差距衡量结构化约束的边际价值；
- 固定图后的场景扰动衡量关系表示能否泛化；
- 失败后的局部恢复效率衡量约束图能否改进信用分配。

每次实验必须先写可证伪问题和验收标准。负面结果保留原始产物，不覆盖、不事后改指标。
若发现指标错误，应发布更正并保留旧目录。

Demo2Code、CaP-X 及相关工作构成强基线，因此“演示到代码”或“结构化中间表示”本身不能直接
作为新颖性结论。潜在贡献必须由机制和实验支持，例如几何 typed holes、约束双向编译、
跨节点资源约束或失败信用分配。新颖性判断与最新文献结论记录在 `ALGORITHM_PLAN.md` 或
专门的研究笔记中，不写在本文件。

## 8. 产物与上下文管理

三个顶层文档职责固定：

- `AGENTS.md`：长期稳定的项目方案和边界；
- `ALGORITHM_PLAN.md`：可演化的方法细节、研究假设和实验矩阵；
- `PROGRESS.md`：唯一动态总账，包括当前状态、已完成工作、证据路径、阻塞项和 next todo。

每个实验 run 应保存冻结后的 task spec、graph、workflow、API contract、代码版本/dirty 状态、
seed、日志、视频或关键帧、指标和简短报告。详细 trial 数据放在 run 目录，不堆进顶层文档。

每个里程碑结束后由主 agent 更新 `PROGRESS.md`，至少写清：

- 更新时间和一句话状态；
- 本轮动作与结果；
- 证据路径；
- 被支持或推翻的假设；
- 当前唯一下一步；
- 是否仍有任务在运行。

subagent 不直接编辑 `PROGRESS.md`，只向主 agent 返回证据，避免并发覆盖。

## 9. 项目环境与改动安全

本仓是公开、净化后的 source-of-truth。技能迭代、感知 probe、抓取试验与模块化改造的**唯一
工作树**是 1022 上的 `/mnt/data/wenqian/demo-graph-lab`（及其同步镜像）。  
**禁止**对 1024 `/mnt/nas/knowin_sim/sim_workspace/`（含误部署的 `services/ksm`）写入、部署、
改配置或启停服务——那是基础仓，不是本项目迭代场地。

具体主机、路径、端口和密钥只写入被忽略的 `configs/local/`，不写进公开文档或 example
config。若存在独立 runtime checkout，必须与本仓使用同一 Git commit，且仍不得落在上述 1024
基础仓路径内。

Knowin World 是外部、共享且可能 dirty 的依赖，不作为本仓子目录、submodule 或 vendored
源码。开发 run 可以记录依赖的 dirty diff digest，但必须标为 non-golden；正式评测只允许
clean、pinned 的 runtime 和 data revision。

未知改动一律视为用户或其他人的工作，不覆盖、不回滚、不顺手清理。敏感配置和密钥不得打印、
复制到报告或提交。迁移只允许显式 allowlist，禁止 `git add .`。各 agent 不得并发编辑同一文件
或覆盖同一 run 目录，subagent 不直接更新 `PROGRESS.md`。

## 10. 代码边界

`components/` 保存 WHT 已有实现的字节级快照；首次导入不得混入算法修改。新方法只写在
`method/demo_graph/`，外部系统接入只写在 `adapters/`。若后续确实需要修改 WHT 组件，必须在
独立 commit 中说明原因、保留 upstream digest，并先证明 adapter 无法满足需求。

主方法生成 Python node policy。已有 KW YAML generator 作为 legacy baseline 原样保留；YAML
可以作为 Knowin World 内部运输格式，但不是方法要求的输出。生成代码运行在无网络、无仿真
数据挂载的隔离进程，只能通过 allowlisted broker 调用受信任的 perception/control API。
