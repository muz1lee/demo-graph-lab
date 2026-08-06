"""图对象名 → ``/state`` 实体键的解析不得塌缩。

8/6 ep1 两次复现的实测:objects.json 里三根管的 ``trace_aliases`` 都是 ``["tube"]``,
旧 ``_resolve`` 的别名分支命中多个实体时取第一个,于是
``tube_left / tube_right / tube_third`` **全部**解析到 ``tube0_prop``,写好的空间
双射 ``_family_bijection`` 成了死代码。

本文件钉死修复后的语义:多命中降级到空间双射;双射定不下来就拒绝(``UnsolvedHole``,
reason=``ambiguous_object_reference``),而"实体表里压根没有"仍是 ``KeyError``——
两种失败语义不同,不合并。

风格对齐 tests/test_contract_params.py:纯逻辑、离线、不触 sim/网络/LLM,
用真 ``OracleRuntime`` 走真代码路径,只注入实体缓存。
"""

import time

import pytest

from demo_graph_lab.execution.oracle_runtime import OracleRuntime
from demo_graph_lab.selection import binding


def _entity(x=0.4, y=0.1, z=0.8, half=0.06):
    return {"pos": [x, y, z], "quat": [1.0, 0.0, 0.0, 0.0],
            "aabb": {"min": [x - half, y - half, z - 0.08],
                     "max": [x + half, y + half, z + 0.08]}}


def _registry(*ids, aliases=("tube",)):
    """ep1 的 objects.json 形态:同类物体共用同一组 trace_aliases。"""
    return [{"id": i, "category": "tube", "distinguishers": [],
             "trace_aliases": list(aliases), "first_seen_frame": 0} for i in ids]


def _rt(graph_names, entities, registry=None):
    """按 stage_objects 声明图对象、注入实体缓存的离线 OracleRuntime。"""
    stages = [{"index": i, "name": "insert", "holes": [],
               "stage_objects": {"manipulated": name, "target": "rack"}}
              for i, name in enumerate(graph_names)]
    rt = OracleRuntime({"stages": stages}, objects=registry)
    rt._ents_cache = (time.time() + 1e6, entities)
    return rt


# 三根管:y 从左(+y)到右(-y)。实体键刻意与图名无字面关系(ep1 就是这样)。
def _three_tubes(ys=(0.20, 0.00, -0.20)):
    return {f"tube{i}_prop": _entity(y=y) for i, y in enumerate(ys)}


_THREE_NAMES = ("tube_left", "tube_third", "tube_right")


# ==========================================================================
# 三同别名图对象 + 三同类实体 → 解析互不相同,且与空间双射一致。
# ==========================================================================
def test_three_same_alias_tubes_resolve_to_distinct_entities():
    ents = _three_tubes()
    rt = _rt(_THREE_NAMES, ents, _registry(*_THREE_NAMES))

    resolved = {name: rt._resolve(name) for name in _THREE_NAMES}

    assert len(set(resolved.values())) == 3, (
        f"三个图对象必须解析到三个不同实体,实得 {resolved}")
    # 空间双射:名字里的空间词决定左右,实体按 y 排(+y 为左)。
    assert resolved["tube_left"] == "tube0_prop"     # y=+0.20,最左
    assert resolved["tube_third"] == "tube1_prop"    # 无空间词 → 由左右两端消去法定下
    assert resolved["tube_right"] == "tube2_prop"    # y=-0.20,最右


def test_resolution_is_consistent_with_entity_geometry_not_key_order():
    """双射看的是 y 坐标,不是实体键的字典序:把 y 翻过来,对应关系也翻过来。"""
    ents = _three_tubes(ys=(-0.20, 0.00, 0.20))
    rt = _rt(_THREE_NAMES, ents, _registry(*_THREE_NAMES))

    assert rt._resolve("tube_left") == "tube2_prop"
    assert rt._resolve("tube_right") == "tube0_prop"


# ==========================================================================
# 双射定不下来 → 拒绝(不静默取第一个)。
# ==========================================================================
def test_tied_y_coordinates_refuse_instead_of_collapsing():
    """三根管挤在同一个 y 上 → 左右分不开 → 拒绝。"""
    ents = _three_tubes(ys=(0.10, 0.10, 0.10))
    rt = _rt(_THREE_NAMES, ents, _registry(*_THREE_NAMES))

    with pytest.raises(binding.UnsolvedHole) as error:
        rt._resolve("tube_left")
    assert error.value.reason == "ambiguous_object_reference"


def test_more_graph_names_than_entities_refuse_instead_of_sharing_one():
    """图名 3 个、场景实体 2 个 → 不存在双射。旧实现会把多出来的名字塌到最后一个实体。"""
    ents = {f"tube{i}_prop": _entity(y=y) for i, y in enumerate((0.20, -0.20))}
    rt = _rt(_THREE_NAMES, ents, _registry(*_THREE_NAMES))

    with pytest.raises(binding.UnsolvedHole) as error:
        rt._resolve("tube_third")
    assert error.value.reason == "ambiguous_object_reference"


def test_tied_spatial_scores_refuse_instead_of_guessing_alphabetically():
    """两个图名都没有空间词 → 次序只能靠字典序,那是猜 → 拒绝。"""
    names = ("tube_left", "tube_alpha", "tube_beta")
    rt = _rt(names, _three_tubes(), _registry(*names))

    with pytest.raises(binding.UnsolvedHole) as error:
        rt._resolve("tube_alpha")
    assert error.value.reason == "ambiguous_object_reference"


# ==========================================================================
# 单实体场景与精确匹配:别名直取仍工作。
# ==========================================================================
def test_single_entity_scene_alias_still_resolves_directly():
    """场景只有一根管时别名唯一命中,直取,不需要双射。"""
    rt = _rt(("tube_left",), {"tube0_prop": _entity()}, _registry("tube_left"))
    assert rt._resolve("tube_left") == "tube0_prop"


def test_exact_entity_key_is_taken_directly_even_with_a_family():
    """精确匹配唯一命中仍直取,不进歧义分支。"""
    rt = _rt(_THREE_NAMES, _three_tubes(), _registry(*_THREE_NAMES))
    assert rt._resolve("tube1_prop") == "tube1_prop"


def test_absent_object_is_key_error_not_ambiguity():
    """实体表里没有 ≠ 分不清:前者仍是 KeyError,两种失败语义不合并。"""
    rt = _rt(("bowl",), {"tube0_prop": _entity()}, None)
    with pytest.raises(KeyError):
        rt._resolve("nonexistent_widget_xyz")


def test_ambiguity_is_reported_through_consume_obj_not_silently_resolved():
    """原语侧:分不清的 obj 走 UNSUPPORTED 记账,不会静默拿到某一根管。"""
    rt = _rt(_THREE_NAMES, _three_tubes(ys=(0.1, 0.1, 0.1)),
             _registry(*_THREE_NAMES))
    assert rt._consume_obj("tube_left", op="transport") is None
    unsupported = [c for c in rt.calls if c["op"] == "unsupported_param"]
    assert unsupported and unsupported[0]["reason"] == "unresolved:UnsolvedHole"
