# API 分层

这个项目有三层 API。最重要的规则是：VLM 只看高层动作，不看底层数值控制；是否成功由独立 gate 判断。

```text
VLM / 生成的 policy
        │
        ▼
高层 Runtime API
        │
        ▼
可信 runner、填洞与 gate
        │
        ▼
运动规划与底层 pipeline
        │
        ▼
机器人
```

## 1. 给生成 policy 的高层 API

高层接口定义在 `src/demo_graph_lab/policy/api.py`。编译器会把这个文件的源码放进 VLM prompt。未出现在该类中的方法一律不能由生成 policy 调用。

### 感知入口

| 方法 | 输入 | 输出 | 失败语义 |
|---|---|---|---|
| `solve(hole_name)` | 当前阶段声明的 hole 名 | 不透明 handle | hole 未声明、类型未知或必需的对象观测缺失时直接报错 |

Handle 只允许传给后续高层动作。生成 policy 不能读取其中的坐标、角度或阈值，也不能自己修改它。

编译检查会拒绝 handle 下标、属性读取以及 `rt` 的非公开属性。例如 `handle["xyz"]`、`handle.xyz` 和 `rt.pipe` 都不合法。这个检查用于约束本项目生成的代码，不是执行任意不可信 Python 的安全沙箱。

### 高层动作

| 方法 | 含义 |
|---|---|
| `approach(target, cone=None)` | 按离散接近方向靠近对象或目标 handle |
| `grasp_at(grasp_pose, axis=None)` | 在已求解的抓取位姿闭合夹爪 |
| `lift(obj)` | 抬起对象 |
| `transport(obj, target)` | 携带对象移动到目标附近 |
| `align(obj, target, axis=None)` | 在下放前完成对象与目标对齐 |
| `lower_until(stop_condition)` | 下放到运行时停止条件触发 |
| `release()` | 释放夹爪 |

`push` 当前没有可靠实现，因此不属于可用 API。需要支持推动任务时，应先实现和测试底层动作，再把它加入高层契约。

生成 policy 不能调用 `verify()` 或自行返回成功。阶段是否通过只由 runner 和 gate 决定。

## 2. 可信运行层

这一层不暴露给 VLM。当前包括：

- `begin_stage(stage)`：把 hole 解析限定到当前阶段；
- `runner.run_policy(...)`：按阶段顺序执行；
- typed-hole 求解和任务无关的 region/cone 偏好函数；
- `evaluation.gates` 和 `evaluation.predicates`：读取独立观测并给出 `PASS / FAIL / UNKNOWN`。

真实候选的可达、碰撞和夹爪宽度硬过滤，以及跨阶段兼容性检查，仍是计划中的功能。

当前 runner 失败后只重复同一个 handler。它不会 rollback、换候选或修改搜索范围。缺少 handler、hole 歧义和未知谓词都必须停止或返回 `UNKNOWN`，不能猜一个默认值继续执行。

当前 Oracle 从 simulator state 直接得到 world-frame 几何，因此数值 handle 明确标为 `frame="world"`，同时保留 graph 请求的 `requested_frame` 供检查。非特权 runtime 不能照搬这个捷径，必须使用相机与机器人标定完成真正的 frame transform。

`scalar` 和 `runtime_condition` 可以返回延迟描述子，由后续高层控制器结合当前状态求值；它们不是 VLM 猜出的数值。

## 3. 底层控制 API

底层实现位于 `src/demo_graph_lab/execution/`，只由受信任的 runtime 调用。

### `PipelineClient`

`pipeline.py` 封装 Knowin World `/run`：

- `info`：读取机器人状态；
- `reasoning`：调用运动规划等已有能力；
- `ctrl`：发送底层控制命令。

HTTP 接受请求不代表机器人到位。控制结果必须通过关节、末端位姿、夹爪或力反馈重新确认。

### `robot_api.py`

| 接口 | 输入约定 | 输出 |
|---|---|---|
| `plan_joint_path(...)` | 关节为弧度；Cartesian pose 为 `[x,y,z,qx,qy,qz,qw]`，位置单位米，frame 为 robot base | `PlanResult`，包含 7-DoF 航点 |
| `execute_path(...)` | `N × 7` 关节航点 | `ExecResult`，包含是否到达和终点残差 |

底层控制不接收任务名、对象语义或自然语言。它只处理明确的数值、坐标系和机器人状态。这能防止任务策略悄悄进入控制层。

## 4. 计划中的运行时 VLM 接口

以下接口尚未实现。它们会放在独立模块中，不加入生成 policy 的 `RuntimeAPI`。

### `rank_candidates`

输入：阶段约束、示范关键帧、带 ID 的候选卡片和可追溯观测证据。

输出：候选 ID 的排序、引用的约束、引用的证据 ID。不能输出新坐标或自造候选。

### `suggest_correction`

输入：失败约束、残差类别、允许调整的离散参数和执行证据。

输出：参数名、`-1 / 0 / +1` 方向、引用的残差。不能越过允许范围，也不能修改 policy 结构。

### `describe_change`

输入：动作前后图像与一个明确问题。

输出：可核对的文字证据，例如“对象仍在夹爪下方”或“目标口被遮挡”。它只提供证据，不能输出 `passed=true`。

## 5. Oracle 边界

`OracleRuntime` 会读取 simulator 的精确 `/state`，只用于集成调试和上界。它不能作为主方法 runtime，也不能为运行时 VLM 生成候选卡片或证据。

主方法允许的信息只有相机、点云、感知模型输出、机器人本体状态、力反馈和由这些信息得到的规划结果。精确对象 pose、AABB、instance ID 和官方 task probe 只能留在隔离评测侧。
