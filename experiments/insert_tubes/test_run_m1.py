from __future__ import annotations

from experiments.insert_tubes.run_m1 import M1Runtime, build_policy
from method.demo_graph import ConstraintGraph


class FakePipeline:
    def __init__(self, *, place_found: bool = False) -> None:
        self.pick_calls = 0
        self.place_found = place_found
        self.actions: list[str] = []

    def reasoning(self, name, **kwargs):
        del kwargs
        if name == "qwen_dof_xquat":
            self.pick_calls += 1
            z = 0.78 if self.pick_calls == 1 else 0.86
            return {
                "xquats": [
                    [[0.52, 0.09, z, 0.0, 0.0, 0.0, 1.0, 0.0]],
                    [None],
                ],
                "grasp_angles": [[60.0], [None]],
                "results": [{"run_id": "perception-run"}],
            }
        if name == "qwen_dof_xquat_place":
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


def test_probe_names_unresolved_perceptual_holes():
    result = _runtime(FakePipeline()).probe()
    assert result["grasp_candidate_found"] is True
    assert result["perceptual_holes"] == ["tube_axis", "holder_pose"]


def test_grasp_succeeds_then_full_graph_fails_closed_on_axis():
    graph = ConstraintGraph.load_json(
        __import__("pathlib").Path(__file__).with_name("m1_graph.json")
    )
    fake = FakePipeline()
    policy, _broker = build_policy(_runtime(fake), graph)
    result = policy.run()
    assert result.succeeded is False
    assert result.nodes[0].node_id == "pick"
    assert result.nodes[0].succeeded is True
    assert result.nodes[1].node_id == "reorient"
    assert result.nodes[1].reason == (
        "perceptual hole unresolved: tube_axis; no ground-truth fallback"
    )
    assert fake.actions == [
        "set_gripper",
        "xquat_move",
        "set_gripper",
        "delta_move",
    ]
