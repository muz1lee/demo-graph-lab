"""Closed constraint vocabulary used by extraction and execution.

提取器只允许输出本词表中的约束；词表变更走 git review，禁止运行时扩词。
铁律：约束参数里不允许出现世界坐标度量字面量——一切数值走 typed hole。
"""

# 约束名 → 参数槽说明(对象/轴/区域均为符号引用,不是数值)
CONSTRAINT_VOCAB = {
    "axis_parallel":      {"args": ["axis_a", "axis_b"]},
    "axis_vertical":      {"args": ["axis"]},
    "center_align":       {"args": ["obj_a", "obj_b"]},
    "region_grasp":       {"args": ["obj", "region"]},
    "approach_direction": {"args": ["cone"], "optional": ["target"]},
    "above":              {"args": ["obj_a", "obj_b"]},
    "inside":             {"args": ["obj_a", "obj_b"]},
    "order":              {"args": ["stage_sequence"]},
    "carry":              {"args": ["relation"]},
    "clearance":          {"args": ["obj_a", "obj_b"]},
}

GRASP_REGIONS = ["top", "upper_body", "middle", "bottom", "rim", "handle"]
APPROACH_CONES = ["top_down", "side", "oblique"]

HOLE_TYPES = ["pose_se3", "axis_3d", "point_3d", "scalar", "runtime_condition"]

GEOMETRIC_HOLE_TYPES = frozenset({"pose_se3", "axis_3d", "point_3d"})

# Runtime geometry resolvers are deliberately narrower than solver_hint prose.
# The value declares how a provider obtains the geometry; anchor identifies the
# object part whose geometry is being published.
HOLE_RESOLVER_TYPES = {
    "grasp_candidate": frozenset({"pose_se3"}),
    "principal_axis": frozenset({"axis_3d"}),
    "part_center": frozenset({"point_3d"}),
    "part_axis": frozenset({"axis_3d"}),
    "motion_derived": GEOMETRIC_HOLE_TYPES,
}

# 阶段词表(关键事件切分的目标类别)
STAGE_VOCAB = [
    "approach", "grasp", "lift", "reorient", "transport",
    "pre_align", "insert", "place", "release", "retreat",
]

PROVENANCE_ALLOWED = ["demo_video", "task_instruction", "generic_prior", "derived"]
HOLDS_ALLOWED = ["throughout", "at_end"]
