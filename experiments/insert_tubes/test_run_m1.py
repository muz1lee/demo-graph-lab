from __future__ import annotations

from pathlib import Path

from experiments.insert_tubes.perception import (
    derive_axis_from_xquat,
    find_axis_vector,
    parse_pick_response,
)
from experiments.insert_tubes.runtime import M1Runtime, build_policy
from method.demo_graph import ConstraintGraph


class FakePipeline:
    def __init__(
        self,
        *,
        place_found: bool = False,
        place_error: str | None = None,
        nested_axis: bool = False,
    ) -> None:
        self.pick_calls = 0
        self.place_found = place_found
        self.place_error = place_error
        self.nested_axis = nested_axis
        self.actions: list[str] = []
        self.place_kwargs: list[dict] = []

    def reasoning(self, name, **kwargs):
        if name == "qwen_dof_xquat":
            self.pick_calls += 1
            z = 0.78 if self.pick_calls == 1 else 0.86
            payload = {
                "xquats": [
                    [[0.52, 0.09, z, 0.0, 0.0, 0.0, 1.0, 0.0]],
                    [None],
                ],
                "grasp_angles": [[60.0], [None]],
                "results": [{"run_id": "perception-run"}],
            }
            if self.nested_axis:
                payload["results"][0]["object_axis_world"] = [0.0, 1.0, 0.0]
            return payload
        if name == "qwen_dof_xquat_place":
            self.place_kwargs.append(dict(kwargs))
            if self.place_error:
                return {"xquats": [[None], [None]], "error": self.place_error}
            row = [0.68, 0.07, 0.82, 0.0, 0.0, 0.0, 1.0]
            return {"xquats": [[row], [None]] if self.place_found else [[None], [None]]}
        raise AssertionError(name)

    def ctrl(self, name, **kwargs):
        del kwargs
        self.actions.append(name)

    def info(self, name, **kwargs):
        del kwargs
        assert name == "get_qpos"
        return [0.0] * 7


def _runtime(fake):
    return M1Runtime(
        fake,
        arm_id=0,
        pick_prompt="tube:dof",
        place_prompt="empty opening",
        settle_poll_s=0.0,
        settle_samples=1,
    )


def test_parse_nested_axis_and_derive_fallback():
    nested = {
        "xquats": [[[0.1, 0.2, 0.3, 0.0, 0.0, 0.0, 1.0]]],
        "grasp_angles": [[10.0]],
        "results": [{"run_id": "r1", "object_axis_world": [0.0, 1.0, 0.0]}],
    }
    parsed = parse_pick_response(nested, arm_id=0)
    assert parsed.axis == [0.0, 1.0, 0.0]
    assert parsed.axis_source and "object_axis_world" in parsed.axis_source

    axis, path = find_axis_vector({"results": [{"long_axis": [1, 0, 0]}]})
    assert axis == [1.0, 0.0, 0.0]
    assert path is not None

    derived = derive_axis_from_xquat([0, 0, 0, 0, 0, 0, 1])
    assert derived is not None
    assert abs(derived[2]) < 1e-9


def test_probe_derives_tube_axis_and_reports_holder_error():
    fake = FakePipeline(place_error="point cloud insufficient")
    result = _runtime(fake).probe()
    assert result["grasp_candidate_found"] is True
    assert result["tube_axis_found"] is True
    assert result["tube_axis_source"] == "derived:grasp_xquat_horizontal"
    assert result["holder_pose_found"] is False
    assert result["holder_pose_error"] == "point cloud insufficient"
    assert result["perceptual_holes"] == ["holder_pose"]
    assert fake.place_kwargs  # 至少尝试过 place


def test_probe_uses_nested_response_axis():
    result = _runtime(FakePipeline(nested_axis=True, place_found=True)).probe()
    assert result["tube_axis_found"] is True
    assert "object_axis_world" in (result["tube_axis_source"] or "")
    assert result["holder_pose_found"] is True
    assert result["perceptual_holes"] == []


def test_grasp_succeeds_then_full_graph_fails_closed_without_reorient_controller():
    graph = ConstraintGraph.load_json(Path(__file__).with_name("m1_graph.json"))
    fake = FakePipeline()
    policy, _broker = build_policy(_runtime(fake), graph)
    result = policy.run()
    assert result.succeeded is False
    assert result.nodes[0].node_id == "pick"
    assert result.nodes[0].succeeded is True
    assert result.nodes[1].node_id == "reorient"
    # 轴已由 xquat 推导出，但当前无可信 reorient 控制器，且目标未竖直
    assert "perceptual hole unresolved" in result.nodes[1].reason
    assert fake.actions == [
        "set_gripper",
        "xquat_move",
        "set_gripper",
        "delta_move",
    ]
