"""Validate a model-proposed PerceptionProgram; nothing here runs perception.

The backend model chooses only which closed-set operator chain publishes which
graph hole.  It writes no query text, no per-step parameters and no numbers:
每条链的根是被 provide 的 hole 已有的 anchor,查询由可信实现从 anchor 渲染,
几何数值只在运行时产生。本模块是感知侧程序的单一真相源,与 ``policy/program.py``
对 StageProgram 的角色同构;PerceptionProgram 是独立编译产物,graph schema 不变。
"""

from __future__ import annotations

import re

from ..graph import vocab


SCHEMA = "demo_graph_lab.perception_program.v1"

TOP_LEVEL_KEYS = frozenset({"schema", "task", "programs"})
PROGRAM_KEYS = frozenset({"stage", "chain", "provides"})
PROVIDE_KEYS = frozenset({"field", "hole"})

# 链上流动的中间产物类型。它不是 graph hole 类型,只决定两个算子能否首尾相接。
# ANCHOR 是链的根:没有算子产出它,它由被 provide 的 hole 的 anchor 推导。
ANCHOR = "ANCHOR"
GEOMETRY = "GEOMETRY"
ROOT_OPERATOR = "localize"

# 感知算子闭集(v1 全集,不多不少)。``fields`` 是该算子发布的几何字段名 →
# graph hole 类型;只有产出 GEOMETRY 的算子有字段,中间算子为空。类型表构成一个
# 无环链,所以链里不可能出现回路,不需要额外的重复算子规则。
# 注释给出每个算子背后的现有实现位置;本轮只固定契约,不接线。
OPERATORS = {
    # 背后实现:`perception/semantic_sources.py` 的 single-box grounding client
    "localize": {"consumes": ANCHOR, "produces": "BBOX", "fields": {}},
    # 背后实现:`perception/semantic_sources.py` 的 binary-mask segmentation client
    "segment": {"consumes": "BBOX", "produces": "MASK", "fields": {}},
    # 背后实现:`perception/object_pipeline.py::estimate_planar_opening_geometry`
    "fit_opening": {"consumes": "MASK", "produces": GEOMETRY,
                    "fields": {"center": "point_3d", "axis": "axis_3d"}},
    # 背后实现:`perception/object_pipeline.py::project_masked_depth`
    "crop_points": {"consumes": "MASK", "produces": "POINTS", "fields": {}},
    # 背后实现:`perception/operators.py::fit_principal_axis`
    "fit_axis": {"consumes": "POINTS", "produces": GEOMETRY,
                 "fields": {"axis": "axis_3d"}},
}

# v1 只发布被观测到的对象几何。``grasp_candidate`` 走候选身份与排序机制,
# ``motion_derived`` 的值来自执行状态而不是观测,两者都不是本 DSL 的产物。
PROVIDABLE_RESOLVERS = vocab.PERCEPTION_RESOLVERS - {"grasp_candidate"}

_UNIT_RE = re.compile(r"\d+\.?\d*\s*(mm|cm|m\b|deg|°|rad)", re.I)


def _is_numeric_literal(value) -> bool:
    """Reject metrics while allowing identifiers such as ``opening_a``.

    与 `policy/program.py` 的同名判据逐条同规:两侧共用同一条禁令,但各自持有实现,
    由测试对齐两者;出现第三个调用方时再上提到 `common/`。
    """
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


def _numeric_literal_errors(node, path: str, exempt: set[str]) -> list[str]:
    """Scan the whole document for smuggled metrics, one message per leaf path.

    stage 索引是指向 graph 的结构性引用,不是度量,因此按路径豁免;其余任何位置
    (包括未知字段)都要扫,这是纵深防御,不依赖 key 白名单先拦住。
    """
    if path in exempt:
        return []
    if isinstance(node, dict):
        return [
            error
            for key in sorted(node, key=repr)
            for error in _numeric_literal_errors(node[key], f"{path}.{key}", exempt)
        ]
    if isinstance(node, list):
        return [
            error
            for offset, item in enumerate(node)
            for error in _numeric_literal_errors(item, f"{path}[{offset}]", exempt)
        ]
    if _is_numeric_literal(node):
        return [f"{path}: 禁止数值字面量 {node!r};感知程序只组合闭集算子,数值来自可信实现"]
    return []


def _stage_holes(stage: dict, path: str, errors: list[str]) -> dict[str, dict]:
    """Index one graph stage's holes by name; malformed entries are reported."""
    holes: dict[str, dict] = {}
    raw_holes = stage.get("holes", [])
    if not isinstance(raw_holes, list):
        errors.append(f"{path}: graph holes 必须是列表")
        return holes
    for offset, hole in enumerate(raw_holes):
        if not isinstance(hole, dict):
            errors.append(f"{path}.holes[{offset}]: 必须是对象")
            continue
        name = hole.get("name")
        if not isinstance(name, str) or not name:
            errors.append(f"{path}.holes[{offset}]: 缺少合法 name")
            continue
        if name in holes:
            errors.append(f"{path}: graph 含重复 hole {name!r}")
        holes[name] = hole
    return holes


def _chain_terminal(chain, path: str, errors: list[str]) -> str | None:
    """Validate the linear chain and return its terminal operator, or ``None``.

    只报第一处断裂:后续步骤的类型都建立在它之上,继续走只会产生派生噪声。
    """
    if not isinstance(chain, list) or not chain:
        errors.append(f"{path}: 必须是非空算子列表")
        return None
    produced = ANCHOR
    for offset, op in enumerate(chain):
        step = f"{path}[{offset}]"
        if not isinstance(op, str) or op not in OPERATORS:
            errors.append(f"{step}: 未支持算子 {op!r},允许 {sorted(OPERATORS)}")
            return None
        if offset == 0 and op != ROOT_OPERATOR:
            errors.append(f"{step}: 链必须以 {ROOT_OPERATOR!r} 开头,根是 anchor")
            return None
        if OPERATORS[op]["consumes"] != produced:
            errors.append(
                f"{step}: 算子 {op!r} 消费 {OPERATORS[op]['consumes']},"
                f"上一步产出 {produced}"
            )
            return None
        produced = OPERATORS[op]["produces"]
    terminal = chain[-1]
    if OPERATORS[terminal]["produces"] != GEOMETRY:
        errors.append(
            f"{path}: 终点算子 {terminal!r} 产出 {OPERATORS[terminal]['produces']},"
            f"必须产出 {GEOMETRY} 字段"
        )
        return None
    return terminal


def validate_perception_program(doc: dict, graph: dict) -> list[str]:
    """Return all PerceptionProgram contract violations without executing anything."""
    errors: list[str] = []
    if not isinstance(doc, dict):
        return ["perception_program: 顶层必须是对象"]
    missing_top = sorted(TOP_LEVEL_KEYS - set(doc))
    extra_top = sorted(set(doc) - TOP_LEVEL_KEYS, key=repr)
    if missing_top:
        errors.append(f"perception_program: 缺少字段 {missing_top}")
    if extra_top:
        errors.append(f"perception_program: 未知字段 {extra_top}")
    if doc.get("schema") != SCHEMA:
        errors.append(
            f"perception_program.schema: 应为 {SCHEMA!r},实际为 {doc.get('schema')!r}"
        )

    task, graph_task = doc.get("task"), graph.get("task")
    if not isinstance(task, str) or not task:
        errors.append("perception_program.task: 必须是非空字符串")
    elif isinstance(graph_task, str) and task != graph_task:
        errors.append(
            f"perception_program.task: 应为 {graph_task!r},实际为 {task!r}"
        )

    programs = doc.get("programs")
    if not isinstance(programs, list):
        return errors + ["perception_program.programs: 必须是列表"]
    if not programs:
        errors.append("perception_program.programs: 至少需要一个程序")
    errors.extend(_numeric_literal_errors(
        doc,
        "perception_program",
        {f"perception_program.programs[{offset}].stage"
         for offset in range(len(programs))},
    ))

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
    holes_by_stage = {
        index: _stage_holes(stage, f"graph.s{index}", errors)
        for index, stage in graph_by_index.items()
    }

    # 洞身份是 stage 内唯一的:同名洞可以出现在多个 stage,所以归属键是 (stage, hole)。
    owner_by_hole: dict[tuple[int, str], int] = {}
    for offset, program in enumerate(programs):
        path = f"perception_program.programs[{offset}]"
        if not isinstance(program, dict):
            errors.append(f"{path}: 必须是对象")
            continue
        missing = sorted(PROGRAM_KEYS - set(program))
        extra = sorted(set(program) - PROGRAM_KEYS, key=repr)
        if missing:
            errors.append(f"{path}: 缺少字段 {missing}")
        if extra:
            errors.append(f"{path}: 未知字段 {extra}")

        stage_index = program.get("stage")
        if (isinstance(stage_index, bool) or not isinstance(stage_index, int)
                or stage_index < 0):
            errors.append(f"{path}.stage: 必须是非负整数")
            continue
        if stage_index not in graph_by_index:
            errors.append(f"{path}.stage: graph 中不存在 stage {stage_index}")
            continue

        terminal = _chain_terminal(program.get("chain"), f"{path}.chain", errors)
        provides = program.get("provides")
        if not isinstance(provides, list) or not provides:
            errors.append(f"{path}.provides: 必须是非空列表")
            continue
        if terminal is None:
            continue

        fields = OPERATORS[terminal]["fields"]
        stage_holes = holes_by_stage[stage_index]
        seen_pairs: set[tuple[str, str]] = set()
        anchors: list[tuple[str, dict]] = []
        for entry_offset, entry in enumerate(provides):
            entry_path = f"{path}.provides[{entry_offset}]"
            if not isinstance(entry, dict):
                errors.append(f"{entry_path}: 必须是对象")
                continue
            missing_entry = sorted(PROVIDE_KEYS - set(entry))
            extra_entry = sorted(set(entry) - PROVIDE_KEYS, key=repr)
            if missing_entry:
                errors.append(f"{entry_path}: 缺少字段 {missing_entry}")
            if extra_entry:
                errors.append(f"{entry_path}: 未知字段 {extra_entry}")

            field, hole_name = entry.get("field"), entry.get("hole")
            if field not in fields:
                errors.append(
                    f"{entry_path}.field: {field!r} 不是终点算子 {terminal!r} 的产出字段,"
                    f"允许 {sorted(fields)}"
                )
                continue
            if not isinstance(hole_name, str) or not hole_name:
                errors.append(f"{entry_path}.hole: 必须是非空字符串")
                continue
            hole = stage_holes.get(hole_name)
            if hole is None:
                errors.append(
                    f"{entry_path}.hole: stage {stage_index} 未声明 hole {hole_name!r}"
                )
                continue
            if hole.get("type") != fields[field]:
                errors.append(
                    f"{entry_path}: hole {hole_name!r} 类型 {hole.get('type')!r} "
                    f"与字段 {field!r} 的类型 {fields[field]!r} 不一致"
                )

            if (field, hole_name) in seen_pairs:
                errors.append(
                    f"{entry_path}: 同一程序重复 provide ({field!r}, {hole_name!r})"
                )
            seen_pairs.add((field, hole_name))
            owner = owner_by_hole.get((stage_index, hole_name))
            if owner is None:
                owner_by_hole[(stage_index, hole_name)] = offset
            else:
                errors.append(
                    f"{entry_path}: stage {stage_index} 的 hole {hole_name!r} "
                    f"已由 programs[{owner}] provide,一个洞只能有一个程序"
                )

            resolver = hole.get("resolver")
            if isinstance(resolver, str) and resolver not in PROVIDABLE_RESOLVERS:
                errors.append(
                    f"{entry_path}: hole {hole_name!r} 的 resolver {resolver!r} "
                    f"不由感知程序发布,允许 {sorted(PROVIDABLE_RESOLVERS)}"
                )
            anchor = hole.get("anchor")
            if not isinstance(anchor, dict):
                errors.append(
                    f"{entry_path}: hole {hole_name!r} 缺少 anchor;链的根由它渲染"
                )
            else:
                anchors.append((hole_name, anchor))

        if anchors:
            first_name, first_anchor = anchors[0]
            for hole_name, anchor in anchors[1:]:
                if anchor != first_anchor:
                    errors.append(
                        f"{path}: hole {hole_name!r} 的 anchor 与 {first_name!r} 不一致;"
                        f"一个程序只观测一个 anchor"
                    )
    return errors


def program_id(stage: int, index: int) -> str:
    """Derive a program's identity; the document carries no name field."""
    return f"p{stage}_{index}"


def coverage_by_stage(doc: dict, graph: dict) -> list[dict]:
    """Report, per stage, which geometric holes this document publishes.

    未覆盖不是违规:那些洞继续走 graph resolver 老路。上层记录这份报告,用来说明
    感知程序当前覆盖到哪一步,不用它做准入判断。
    """
    violations = validate_perception_program(doc, graph)
    if violations:
        raise ValueError(f"PerceptionProgram validation failed: {violations[:3]}")

    published: dict[int, set[str]] = {}
    for program in doc["programs"]:
        published.setdefault(program["stage"], set()).update(
            entry["hole"] for entry in program["provides"]
        )

    report: list[dict] = []
    for stage in graph["stages"]:
        names = published.get(stage["index"], set())
        geometric = [
            hole["name"] for hole in stage.get("holes", [])
            if hole.get("type") in vocab.GEOMETRIC_HOLE_TYPES
        ]
        report.append({
            "stage": stage["index"],
            "covered": sorted(name for name in geometric if name in names),
            "uncovered": sorted(name for name in geometric if name not in names),
        })
    return report
