"""封闭约束词表 v0（代码即规范）。

提取器只允许输出本词表中的约束；词表变更走 git review，禁止运行时扩词。
设计依据见 RESEARCH_PROPOSAL_V2.md §4.1；schema 对齐 schema/constraint_graph_schema.md v0.2。
铁律：约束参数里不允许出现世界坐标度量字面量——一切数值走 typed hole。
"""

# 约束名 → 参数槽说明(对象/轴/区域均为符号引用,不是数值)
CONSTRAINT_VOCAB = {
    "axis_parallel":      {"args": ["axis_a", "axis_b"]},
    "axis_vertical":      {"args": ["axis"]},
    "center_align":       {"args": ["obj_a", "obj_b"]},
    "region_grasp":       {"args": ["obj", "region"]},
    "approach_direction": {"args": ["cone"]},
    "above":              {"args": ["obj_a", "obj_b"]},
    "inside":             {"args": ["obj_a", "obj_b"]},
    "order":              {"args": ["stage_sequence"]},
    "carry":              {"args": ["relation"]},
    "clearance":          {"args": ["obj_a", "obj_b"]},
}

GRASP_REGIONS = ["top", "upper_body", "middle", "bottom", "rim", "handle"]
APPROACH_CONES = ["top_down", "side", "oblique"]

HOLE_TYPES = ["pose_se3", "axis_3d", "point_3d", "scalar", "runtime_condition"]

# 阶段词表(关键事件切分的目标类别)
STAGE_VOCAB = [
    "approach", "grasp", "lift", "reorient", "transport",
    "pre_align", "insert", "place", "release", "retreat",
]

# 每条提取产物的必填字段(validate.V1 结构校验依据)
CONSTRAINT_REQUIRED_FIELDS = [
    "name", "args", "stage", "holes", "provenance",
    "evidence_frames", "confidence",
]
PROVENANCE_ALLOWED = ["demo_video", "task_instruction", "generic_prior", "derived"]
HOLDS_ALLOWED = ["throughout", "at_end"]
