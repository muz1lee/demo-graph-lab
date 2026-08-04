"""Validate a model-proposed StageProgram and compile it deterministically.

The backend model chooses only the high-level primitive sequence and argument wiring.
It never writes Python.  Numeric values remain in graph holes and are solved by the
runtime when the generated handler runs.
"""

from __future__ import annotations

from copy import deepcopy
import inspect
import re

from ..graph import vocab
from .api import RuntimeAPI


PRIMITIVES = (
    "approach",
    "grasp_at",
    "lift",
    "transport",
    "align",
    "lower_until",
    "release",
    "retreat",
)

# Each argument accepts exactly one or more of: a typed hole, a stage object, or a
# closed string vocabulary.  This is deliberately small and mirrors RuntimeAPI.
ARGUMENT_SPECS = {
    "approach": {
        "target": {"holes": {"pose_se3", "point_3d"}, "object": True},
        "cone": {"values": set(vocab.APPROACH_CONES)},
    },
    "grasp_at": {
        "grasp_pose": {"holes": {"pose_se3"}},
        "axis": {"holes": {"axis_3d"}},
    },
    "lift": {
        "obj": {"object": True},
    },
    "transport": {
        "obj": {"object": True},
        "target": {"holes": {"pose_se3", "point_3d"}, "object": True},
    },
    "align": {
        "obj": {"object": True},
        "target": {"holes": {"pose_se3", "point_3d"}, "object": True},
        "axis": {"holes": {"axis_3d"}},
    },
    "lower_until": {
        "stop_condition": {"holes": {"runtime_condition"}, "purpose": "lower_stop"},
    },
    "release": {},
    "retreat": {
        "target": {"holes": {"pose_se3", "point_3d"},
                   "semantic": "retreat_target"},
    },
}

_UNIT_RE = re.compile(r"\d+\.?\d*\s*(mm|cm|m\b|deg|°|rad)", re.I)


def _is_numeric_literal(value) -> bool:
    """Reject metrics while allowing identifiers such as ``tube0``."""
    if isinstance(value, bool):
        return False
    if isinstance(value, (int, float)):
        return True
    if isinstance(value, str):
        try:
            float(value.strip())
            return True
        except ValueError:
            return bool(_UNIT_RE.search(value))
    if isinstance(value, list):
        return any(_is_numeric_literal(item) for item in value)
    if isinstance(value, dict):
        return any(_is_numeric_literal(item) for item in value.values())
    return False


def _primitive_parameters(op: str) -> tuple[set[str], set[str], list[str]]:
    """Return required, allowed, and declaration-order RuntimeAPI parameters."""
    signature = inspect.signature(getattr(RuntimeAPI, op))
    params = [param for name, param in signature.parameters.items() if name != "self"]
    required = {
        param.name for param in params if param.default is inspect.Parameter.empty
    }
    return required, {param.name for param in params}, [param.name for param in params]


def _stage_object_refs(stage: dict) -> set[str]:
    objects = stage.get("stage_objects", {})
    if not isinstance(objects, dict):
        return set()
    return {value for value in objects.values() if isinstance(value, str) and value}


def _hole_map(stage: dict, path: str, errors: list[str]) -> dict[str, dict]:
    holes: dict[str, dict] = {}
    raw_holes = stage.get("holes", [])
    if not isinstance(raw_holes, list):
        errors.append(f"{path}: graph holes 必须是列表")
        return holes
    for offset, hole in enumerate(raw_holes):
        if not isinstance(hole, dict):
            errors.append(f"{path}.holes[{offset}]: 必须是对象")
            continue
        name, hole_type = hole.get("name"), hole.get("type")
        if not isinstance(name, str) or not name:
            errors.append(f"{path}.holes[{offset}]: 缺少合法 name")
            continue
        if name in holes:
            errors.append(f"{path}: graph 含重复 hole {name!r}")
        holes[name] = hole
    return holes


def _validate_argument(
    value,
    spec: dict,
    holes: dict[str, dict],
    objects: set[str],
    path: str,
) -> list[str]:
    errors: list[str] = []
    if _is_numeric_literal(value):
        errors.append(f"{path}: 禁止数值字面量 {value!r}；数值必须来自 typed hole")
        return errors

    if isinstance(value, str) and "values" in spec:
        if value not in spec["values"]:
            errors.append(f"{path}: 未知离散值 {value!r}")
        return errors

    if not isinstance(value, dict) or len(value) != 1:
        errors.append(
            f"{path}: 参数必须是 {{\"hole\": name}}、{{\"object\": name}}"
            " 或该参数允许的离散字符串"
        )
        return errors

    if "hole" in value:
        name = value["hole"]
        if not isinstance(name, str) or not name:
            errors.append(f"{path}: hole 引用必须是非空字符串")
        elif name not in holes:
            errors.append(f"{path}: 引用了未声明 hole {name!r}")
        elif holes[name].get("type") not in spec.get("holes", set()):
            expected = sorted(spec.get("holes", set()))
            errors.append(
                f"{path}: hole {name!r} 类型 {holes[name].get('type')!r} "
                f"不兼容，允许 {expected}"
            )
        else:
            if spec.get("purpose") and holes[name].get("purpose") != spec["purpose"]:
                errors.append(
                    f"{path}: hole {name!r} 缺少 purpose={spec['purpose']!r}"
                )
            if spec.get("semantic") == "retreat_target":
                text = f"{name} {holes[name].get('solver_hint', '')}".lower()
                if "retreat" not in text and "retract" not in text:
                    errors.append(
                        f"{path}: hole {name!r} 未明确声明 retract/retreat 语义"
                    )
        return errors

    if "object" in value:
        name = value["object"]
        if not spec.get("object"):
            errors.append(f"{path}: 此参数不接受 object 引用")
        elif not isinstance(name, str) or not name:
            errors.append(f"{path}: object 引用必须是非空字符串")
        elif name not in objects:
            errors.append(f"{path}: 未知 stage object {name!r}，允许 {sorted(objects)}")
        return errors

    errors.append(f"{path}: 未知引用格式 {value!r}")
    return errors


def validate_program(program: dict, graph: dict) -> list[str]:
    """Return all StageProgram contract violations without executing anything."""
    errors: list[str] = []
    if not isinstance(program, dict):
        return ["program: 顶层必须是对象"]
    extra_top = sorted(set(program) - {"stages"})
    if extra_top:
        errors.append(f"program: 未知字段 {extra_top}")
    program_stages = program.get("stages")
    if not isinstance(program_stages, list):
        return errors + ["program.stages: 必须是列表"]

    graph_stages = graph.get("stages")
    if not isinstance(graph_stages, list):
        return errors + ["graph.stages: 必须是列表"]
    graph_by_index: dict[int, dict] = {}
    for offset, stage in enumerate(graph_stages):
        if not isinstance(stage, dict):
            errors.append(f"graph.stages[{offset}]: 必须是对象")
            continue
        index = stage.get("index")
        if isinstance(index, bool) or not isinstance(index, int) or index < 0:
            errors.append(f"graph.stages[{offset}]: index 必须是非负整数")
        elif index in graph_by_index:
            errors.append(f"graph: 含重复 stage index {index}")
        else:
            graph_by_index[index] = stage

    expected_order = list(graph_by_index)
    actual_order = [
        stage.get("index") for stage in program_stages if isinstance(stage, dict)
    ]
    if actual_order != expected_order:
        errors.append(
            f"program: stage 顺序必须与 graph 一致，expected={expected_order} "
            f"got={actual_order}"
        )

    seen: set[int] = set()
    release_seen = False
    for offset, stage_program in enumerate(program_stages):
        path = f"program.stages[{offset}]"
        if not isinstance(stage_program, dict):
            errors.append(f"{path}: 必须是对象")
            continue
        extra = sorted(set(stage_program) - {"index", "name", "actions"})
        missing = sorted({"index", "name", "actions"} - set(stage_program))
        if missing:
            errors.append(f"{path}: 缺少字段 {missing}")
        if extra:
            errors.append(f"{path}: 未知字段 {extra}")

        index = stage_program.get("index")
        if isinstance(index, bool) or not isinstance(index, int) or index < 0:
            errors.append(f"{path}.index: 必须是非负整数")
            continue
        if index in seen:
            errors.append(f"{path}: 重复 stage index {index}")
            continue
        seen.add(index)
        graph_stage = graph_by_index.get(index)
        if graph_stage is None:
            errors.append(f"{path}: graph 中不存在 stage {index}")
            continue
        if stage_program.get("name") != graph_stage.get("name"):
            errors.append(
                f"{path}.name: 应为 {graph_stage.get('name')!r}，"
                f"实际为 {stage_program.get('name')!r}"
            )

        holes = _hole_map(graph_stage, f"graph.s{index}", errors)
        objects = _stage_object_refs(graph_stage)
        actions = stage_program.get("actions")
        if not isinstance(actions, list):
            errors.append(f"{path}.actions: 必须是列表")
            continue
        if not actions:
            errors.append(f"{path}.actions: 每个 stage 至少需要一个 primitive")

        previous_rank = -1
        seen_ops: set[str] = set()
        for action_offset, action in enumerate(actions):
            action_path = f"{path}.actions[{action_offset}]"
            if not isinstance(action, dict):
                errors.append(f"{action_path}: 必须是对象")
                continue
            extra_action = sorted(set(action) - {"op", "args"})
            missing_action = sorted({"op", "args"} - set(action))
            if missing_action:
                errors.append(f"{action_path}: 缺少字段 {missing_action}")
            if extra_action:
                errors.append(f"{action_path}: 未知字段 {extra_action}")
            op, args = action.get("op"), action.get("args")
            if op not in PRIMITIVES:
                errors.append(f"{action_path}.op: 未支持 primitive {op!r}")
                continue
            if op in seen_ops:
                errors.append(f"{action_path}.op: 同一 stage 不允许重复 primitive {op!r}")
            seen_ops.add(op)
            rank = PRIMITIVES.index(op)
            if rank < previous_rank:
                errors.append(
                    f"{action_path}.op: primitive 顺序倒退到 {op!r}；"
                    f"必须遵循 {' -> '.join(PRIMITIVES)} 的子序列"
                )
            previous_rank = max(previous_rank, rank)
            if op == "retreat" and not release_seen:
                errors.append(f"{action_path}.op: retreat 必须位于 release 之后")
            if op == "release":
                release_seen = True
            if not isinstance(args, dict):
                errors.append(f"{action_path}.args: 必须是对象")
                continue

            required, allowed, _ = _primitive_parameters(op)
            missing_args = sorted(required - set(args))
            extra_args = sorted(set(args) - allowed)
            if missing_args:
                errors.append(f"{action_path}: {op} 缺少参数 {missing_args}")
            if extra_args:
                errors.append(f"{action_path}: {op} 含未知参数 {extra_args}")
            for arg_name in sorted(set(args) & allowed):
                spec = ARGUMENT_SPECS[op][arg_name]
                errors.extend(_validate_argument(
                    args[arg_name], spec, holes, objects,
                    f"{action_path}.args.{arg_name}",
                ))

    missing_stages = sorted(set(graph_by_index) - seen)
    if missing_stages:
        errors.append(f"program: 缺少 graph stages {missing_stages}")
    return errors


def _render_value(value, hole_vars: dict[str, str]) -> str:
    if isinstance(value, str):
        return repr(value)
    if "hole" in value:
        return hole_vars[value["hole"]]
    return repr(value["object"])


def wired_holes_by_stage(program: dict) -> dict[int, tuple[str, ...]]:
    """Return hole names in first-use order from an already validated program."""

    result = {}
    for stage in program["stages"]:
        names = []
        for action in stage["actions"]:
            _, _, argument_order = _primitive_parameters(action["op"])
            for argument_name in argument_order:
                value = action["args"].get(argument_name)
                if (isinstance(value, dict) and "hole" in value
                        and value["hole"] not in names):
                    names.append(value["hole"])
        result[stage["index"]] = tuple(names)
    return result


def wired_hole_contracts_by_stage(
    program: dict,
    graph: dict,
) -> dict[int, tuple[dict, ...]]:
    """Return full graph hole contracts in deterministic first-use order."""
    violations = validate_program(program, graph)
    if violations:
        raise ValueError(f"StageProgram validation failed: {violations[:3]}")

    graph_stages = {stage["index"]: stage for stage in graph["stages"]}
    contracts: dict[int, tuple[dict, ...]] = {}
    for index, names in wired_holes_by_stage(program).items():
        holes = {
            hole["name"]: hole
            for hole in graph_stages[index].get("holes", [])
            if isinstance(hole, dict) and isinstance(hole.get("name"), str)
        }
        contracts[index] = tuple(deepcopy(holes[name]) for name in names)
    return contracts


def unwired_holes(program: dict, graph: dict) -> list[dict]:
    """List declared holes omitted from wiring; some, such as scalar, are expected."""
    wired = wired_holes_by_stage(program)
    report = []
    for graph_stage in graph["stages"]:
        used = set(wired[graph_stage["index"]])
        declared = {
            hole["name"] for hole in graph_stage.get("holes", [])
            if isinstance(hole, dict) and isinstance(hole.get("name"), str)
        }
        missing = sorted(declared - used)
        if missing:
            report.append({"stage": graph_stage["index"], "holes": missing})
    return report


def compile_program(program: dict, graph: dict) -> str:
    """Compile a validated StageProgram into stable, model-free Python handlers."""
    violations = validate_program(program, graph)
    if violations:
        raise ValueError(f"StageProgram validation failed: {violations[:3]}")

    programs_by_index = {stage["index"]: stage for stage in program["stages"]}
    lines: list[str] = []
    for graph_stage in graph["stages"]:
        index = graph_stage["index"]
        stage_program = programs_by_index[index]
        actions = stage_program["actions"]
        hole_vars: dict[str, str] = {}
        for action in actions:
            _, _, arg_order = _primitive_parameters(action["op"])
            for arg_name in arg_order:
                value = action["args"].get(arg_name)
                if isinstance(value, dict) and "hole" in value:
                    hole_name = value["hole"]
                    if hole_name not in hole_vars:
                        hole_vars[hole_name] = f"h{len(hole_vars)}"

        lines.append(f"def stage_{index}(rt):")
        for hole_name, variable in hole_vars.items():
            lines.append(f"    {variable} = rt.solve({hole_name!r})")
        for action in actions:
            op, args = action["op"], action["args"]
            _, _, arg_order = _primitive_parameters(op)
            rendered = [
                f"{name}={_render_value(args[name], hole_vars)}"
                for name in arg_order if name in args
            ]
            lines.append(f"    rt.{op}({', '.join(rendered)})")
        lines.append("")

    lines.append("STAGES = {")
    for graph_stage in graph["stages"]:
        index = graph_stage["index"]
        lines.append(f"    {index}: stage_{index},")
    lines.append("}")
    lines.append("")
    return "\n".join(lines)
