from __future__ import annotations

import unittest
from dataclasses import replace
from pathlib import Path

from ksm.registry import SkillSummary, ToolRegistry
from ksm.robodojo_auto import (
    RobodojoPoolItem,
    _choose_grounded_pick_label,
    _grounding_candidate_labels,
    _pool_items_for_stack_blocks,
    _scene_alignment_from_process_args,
    _select_pool_item,
    _validate_generated_label_arg,
    _tier4_binding_execution_bonus,
    _with_binding_label_overrides,
)


def _item(task_id: str, primary_pick_label: str = "blue and white bottle:dof") -> RobodojoPoolItem:
    return RobodojoPoolItem(
        task_id=task_id,
        task_class="put_bottles_into_dustbin",
        prompt="put one bottle into the dustbin",
        tags=["robodojo"],
        suite_path="/tmp/suite.yaml",
        scene_path="/tmp/scene.yaml",
        target_asset={"id": "bottle0_prop", "category": "bottle"},
        target_import={},
        success={"all_of": [{"type": "inside", "object": "bottle0_prop", "container": "dustbin_prop"}]},
        admission={"accepted": True},
        binding={
            "primary_pick_label": primary_pick_label,
            "candidate_pick_labels": [primary_pick_label, "bottle:dof"],
            "primary_place_label": "dustbin",
            "candidate_place_labels": ["dustbin", "trash bin"],
            "asset_visual_hints": {"source": "asset_texture", "preferred_labels": [primary_pick_label]},
        },
        score=1.0,
        tier=4,
        place_asset={"id": "dustbin_prop", "category": "dustbin"},
        place_import={},
        subtask={"source_object": "bottle0_prop", "target_container": "dustbin_prop"},
    )


def _registry() -> ToolRegistry:
    return ToolRegistry(
        k1_dir="/tmp/k1",
        test_skill_dir="knowin_skills/knowin_skill_manager_tests",
        ctrl=["go_home"],
        info=[],
        reasoning=[],
        namespaces=["head"],
        skills=[],
    )


def replace_placeholder_skill(path: str) -> SkillSummary:
    return SkillSummary(path=path, description="", args={}, actions=[])


class RobodojoAutoSelectionTests(unittest.TestCase):
    def test_preferred_task_id_selects_exact_pool_item(self) -> None:
        pool = [_item("robodojo_a_bottle0"), _item("robodojo_b_bottle1")]

        selected = _select_pool_item(pool, preferred_task_id="robodojo_b_bottle1")

        self.assertEqual(selected.task_id, "robodojo_b_bottle1")

    def test_binding_label_override_keeps_task_identity(self) -> None:
        selected = _with_binding_label_overrides(
            _item("robodojo_a_bottle0"),
            primary_pick_label="yellow bottle:dof",
            primary_place_label="trash bin",
        )

        self.assertEqual(selected.target_asset["id"], "bottle0_prop")
        self.assertEqual(selected.success["all_of"][0]["object"], "bottle0_prop")
        self.assertEqual(selected.binding["primary_pick_label"], "yellow bottle:dof")
        self.assertEqual(selected.binding["candidate_pick_labels"][0], "yellow bottle:dof")
        self.assertEqual(selected.binding["primary_place_label"], "trash bin")
        self.assertEqual(selected.binding["candidate_place_labels"][0], "trash bin")
        self.assertEqual(selected.binding["asset_visual_hints"]["source"], "operator_or_visual_probe")

    def test_tier4_bonus_does_not_hardcode_blue_white_preference(self) -> None:
        blue = _tier4_binding_execution_bonus(
            binding={"asset_visual_hints": {"preferred_labels": ["blue and white bottle:dof"]}, "primary_pick_label": "blue and white bottle:dof"},
            source_import={},
            place_import={},
        )
        yellow = _tier4_binding_execution_bonus(
            binding={"asset_visual_hints": {"preferred_labels": ["yellow bottle:dof"]}, "primary_pick_label": "yellow bottle:dof"},
            source_import={},
            place_import={},
        )

        self.assertEqual(blue, yellow)

    def test_grounding_choice_prefers_success_nearest_target_pose(self) -> None:
        selected = _choose_grounded_pick_label(
            [
                {"label": "generic bottle:dof", "success": True, "xy_distance_m": 0.22},
                {"label": "pink bottle:dof", "success": True, "xy_distance_m": 0.03},
                {"label": "yellow bottle:dof", "success": False},
            ]
        )

        self.assertEqual(selected["label"], "pink bottle:dof")  # type: ignore[index]

    def test_grounding_choice_prefers_specific_label_over_generic_near_tie(self) -> None:
        selected = _choose_grounded_pick_label(
            [
                {"label": "pink block:dof", "success": True, "xy_distance_m": 0.0086462},
                {"label": "积木:dof", "success": True, "xy_distance_m": 0.0086461},
            ]
        )

        self.assertEqual(selected["label"], "pink block:dof")  # type: ignore[index]

    def test_generated_label_validation_overrides_far_candidate_arg(self) -> None:
        class FakeClient:
            def run_reasoning(self, name, kwargs):  # noqa: ANN001, ANN201
                label = kwargs["text"][0]
                xyz = {
                    "wrong block:dof": [0.5, 0.5, 0.78],
                    "target block:dof": [0.02, 0.01, 0.78],
                }[label]
                return {"response": {"result": {"status": ["Success"], "xquats": [[xyz]]}}}

        report = _validate_generated_label_arg(
            client=FakeClient(),
            current_label="wrong block:dof",
            target_position=(0.0, 0.0, 0.78),
            fallback_labels=["target block:dof"],
            target_ref="block_2_prop",
        )

        self.assertEqual(report["status"], "overrode")
        self.assertEqual(report["selected_label"], "target block:dof")

    def test_tier5_stack_blocks_pool_adds_stateful_repeated_candidate(self) -> None:
        registry = replace(
            _registry(),
            skills=[
                replace_placeholder_skill("pickplace/semantic_pick.yaml"),
                replace_placeholder_skill("pickplace/semantic_place.yaml"),
                replace_placeholder_skill("pickplace/semantic_pickplace.yaml"),
            ],
        )
        scene = {
            "metadata": {
                "robodojo_asset_refs": [
                    {"id": "block_0_prop", "category": "block", "qualified": True, "has_collision_prims": True, "collision_mode": "visual_mesh"},
                    {"id": "block_1_prop", "category": "block", "qualified": True, "has_collision_prims": True, "collision_mode": "visual_mesh"},
                    {"id": "block_2_prop", "category": "block", "qualified": True, "has_collision_prims": True, "collision_mode": "visual_mesh"},
                ]
            },
            "imports": [
                {"id": "block_0_prop", "pose": {"position": [0.42, -0.24, 0.78]}},
                {"id": "block_1_prop", "pose": {"position": [0.48, -0.38, 0.78]}},
                {"id": "block_2_prop", "pose": {"position": [0.59, -0.32, 0.78]}},
            ],
        }
        task = {
            "task_id": "robodojo_stack_blocks_000",
            "prompt": "Stack the three blocks.",
            "tags": ["robodojo", "stack_blocks", "stack"],
            "success": {"all_of": [{"type": "stacked", "objects": ["block_0_prop", "block_1_prop", "block_2_prop"], "ordered": False}]},
        }

        pool = _pool_items_for_stack_blocks(
            suite_path=Path("/tmp/stack_blocks_000.suite.yaml"),
            scene_path=Path("/tmp/stack_blocks_000.yaml"),
            task=task,
            task_class="stack_blocks",
            scene=scene,
            registry=registry,
            tier=5,
            assets_root=None,
        )
        stateful = [item for item in pool if item.subtask.get("stateful_plan")]

        self.assertGreaterEqual(len(stateful), 1)
        plan = stateful[0].subtask["stateful_plan"]
        self.assertEqual(plan["plan_type"], "repeated_binary_relation")
        self.assertEqual(plan["relation"], "stacked")
        self.assertEqual(len(plan["steps"]), 2)
        self.assertIn("pick_label_1", plan["skill_args"])
        self.assertIn("place_label_2", plan["skill_args"])
        self.assertEqual(len(stateful[0].success["all_of"]), 2)

    def test_scene_alignment_reports_mismatch_for_wrong_webui_scene(self) -> None:
        report = _scene_alignment_from_process_args(
            expected_scene_path="/mnt/workspace/scenes/put_bottles_into_dustbin_010.yaml",
            process_args=[
                "python main.py --manifest-path /mnt/workspace/scenes/put_bottles_into_dustbin_029.yaml --web-viewer-port 8080",
            ],
            webui_port=8080,
        )

        self.assertEqual(report["status"], "mismatch")
        self.assertEqual(report["current_scene_path"], "/mnt/workspace/scenes/put_bottles_into_dustbin_029.yaml")

    def test_grounding_candidates_exclude_scene_refs_as_visual_labels(self) -> None:
        item = _item("robodojo_a_bottle0", primary_pick_label="pink bottle:dof")
        binding = dict(item.binding)
        binding["candidate_pick_labels"] = ["pink bottle:dof", "bottle0_prop", "bottle:dof"]
        item = replace(item, binding=binding)
        item = _with_binding_label_overrides(item, primary_pick_label="pink bottle:dof", primary_place_label=None)
        labels, excluded = _grounding_candidate_labels(item)

        self.assertIn("pink bottle:dof", labels)
        self.assertNotIn("bottle0_prop", labels)
        self.assertIn("bottle0_prop", excluded)

    def test_stack_blocks_pool_extracts_nearest_pair_subtask(self) -> None:
        registry = replace(
            _registry(),
            skills=[
                replace_placeholder_skill("pickplace/semantic_pick.yaml"),
                replace_placeholder_skill("pickplace/semantic_place.yaml"),
            ],
        )
        scene = {
            "metadata": {
                "robodojo_asset_refs": [
                    {"id": "block_0_prop", "category": "block", "qualified": True, "has_collision_prims": True, "collision_mode": "visual_mesh"},
                    {"id": "block_1_prop", "category": "block", "qualified": True, "has_collision_prims": True, "collision_mode": "visual_mesh"},
                    {"id": "block_2_prop", "category": "block", "qualified": True, "has_collision_prims": True, "collision_mode": "visual_mesh"},
                ]
            },
            "imports": [
                {"id": "block_0_prop", "pose": {"position": [0.42, -0.24, 0.78]}},
                {"id": "block_1_prop", "pose": {"position": [0.48, -0.38, 0.78]}},
                {"id": "block_2_prop", "pose": {"position": [0.59, -0.32, 0.78]}},
            ],
        }
        task = {
            "task_id": "robodojo_stack_blocks_000",
            "prompt": "Stack the three blocks.",
            "tags": ["robodojo", "stack_blocks", "stack"],
            "success": {"all_of": [{"type": "stacked", "objects": ["block_0_prop", "block_1_prop", "block_2_prop"], "ordered": False}]},
        }

        pool = _pool_items_for_stack_blocks(
            suite_path=Path("/tmp/stack_blocks_000.suite.yaml"),
            scene_path=Path("/tmp/stack_blocks_000.yaml"),
            task=task,
            task_class="stack_blocks",
            scene=scene,
            registry=registry,
            tier=4,
            assets_root=None,
        )

        self.assertEqual(len(pool), 6)
        self.assertIn(pool[0].subtask["subtask_id"], {"block_1_prop_on_block_2_prop", "block_2_prop_on_block_1_prop"})
        self.assertEqual(pool[0].success["all_of"][0]["type"], "stacked")
        self.assertEqual(pool[0].binding["sequence_intent"], "pick selected source object and stack it on the selected support object")


if __name__ == "__main__":
    unittest.main()
