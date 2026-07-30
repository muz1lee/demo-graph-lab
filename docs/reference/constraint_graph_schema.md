# 演示约束图 Schema v0.2

约束图是演示证据与 Python 节点策略之间的可执行规格：描述必须成立的关系与尚未解析的运行时量，**不**包含仿真答案或运动路点。

可执行示例：`method/demo_graph/examples/m1_graph.json`  
加载器：`method.demo_graph.ConstraintGraph`

## 最小形态

```json
{
  "schema_version": "0.2",
  "graph_id": "m1_single_tube",
  "entry_node": "pick",
  "provenance": {
    "source": "task_instruction",
    "reference": "pick, align and insert one tube"
  },
  "nodes": [
    {
      "node_id": "pick",
      "action": "pick",
      "goal": "tube_attached",
      "controller_ref": "trusted.pick",
      "max_attempts": 2,
      "next_node": "align",
      "preconditions": [],
      "postconditions": ["tube_attached"],
      "invariants": [],
      "provenance": {
        "source": "demo_video",
        "reference": "grasp keyframe"
      },
      "holes": [
        {
          "hole_id": "grasp_pose",
          "value_type": "pose_se3",
          "solver": "runtime_grasp_proposals",
          "search_domain": {"region": "tube_middle"},
          "provenance": {
            "source": "runtime_perception",
            "reference": "current RGB-D"
          }
        }
      ],
      "constraints": [
        {
          "constraint_id": "grasp_region",
          "description": "grasp on the upper half of the tube body",
          "hole_ids": ["grasp_pose"],
          "provenance": {
            "source": "demo_video",
            "reference": "relative grasp height"
          }
        }
      ]
    }
  ]
}
```

## 规则

1. 每个节点至少一条 constraint，并声明 `goal` / `controller_ref`。
2. provenance 允许：`demo_video` / `task_instruction` / `runtime_perception` / `robot_state` / `generic_prior` / `derived`。
3. 含 `privileged_oracle`（含 derived 祖先）的图不得进入主方法。
4. typed hole 必须带类型、solver 与 search_domain。
5. 节点状态机：`READY → RESOLVING_HOLES → CANDIDATES_READY → ADMITTED → EXECUTING → VERIFYING → SUCCEEDED|RECOVERABLE|FAILED`。
