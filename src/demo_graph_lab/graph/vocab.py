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

# motion_derived 的值来自执行状态,不是被观测到的对象几何;感知记录路径必须
# 显式拒绝它,不能退回视觉估计。这里是感知侧闭集的唯一来源。
MOTION_DERIVED_RESOLVER = "motion_derived"
PERCEPTION_RESOLVERS = frozenset(HOLE_RESOLVER_TYPES) - {MOTION_DERIVED_RESOLVER}


def anchor_rule_errors(
    part,
    *,
    has_instance: bool,
    has_selection: bool,
    resolver=None,
) -> list[str]:
    """Closed anchor rules shared by graph validation and the perception record.

    ``has_instance`` / ``has_selection`` are presence flags, not values: the
    graph layer counts a declared-but-malformed qualifier as present, while the
    record layer normalizes an absent qualifier to ``None``.  Messages carry no
    location prefix; each caller adds its own and decides whether to accumulate
    them or raise the first one.
    """

    errors: list[str] = []
    qualifier_count = int(has_instance) + int(has_selection)
    if has_instance and has_selection:
        errors.append("anchor cannot contain both instance and selection")
    if part == "whole" and qualifier_count:
        errors.append("whole-object anchor cannot contain instance or selection")
    if part == "hole" and qualifier_count != 1:
        errors.append("hole anchor requires exactly one of instance or selection")
    if not isinstance(resolver, str):
        return errors
    if resolver == "grasp_candidate" and (part != "whole" or qualifier_count):
        errors.append(
            "grasp_candidate must use a whole-object anchor; "
            "grasp-region preference belongs in constraints"
        )
    if resolver in {"part_center", "part_axis"} and part != "hole":
        errors.append(f"{resolver} must use a hole anchor")
    if resolver == "principal_axis" and (part != "whole" or qualifier_count):
        errors.append(
            "principal_axis must use a whole-object anchor; "
            "use part_axis for a physical part"
        )
    return errors

# 阶段词表(关键事件切分的目标类别)
STAGE_VOCAB = [
    "approach", "grasp", "lift", "reorient", "transport",
    "pre_align", "insert", "place", "release", "retreat",
]

PROVENANCE_ALLOWED = ["demo_video", "task_instruction", "generic_prior", "derived"]
HOLDS_ALLOWED = ["throughout", "at_end"]
