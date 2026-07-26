from __future__ import annotations

import os
import unittest
from pathlib import Path

from ksm.config import load_config
from ksm.io import read_yaml
from ksm.policy import validate_workflow_nodes
from ksm.registry import build_registry


class NativeSkillPolicyCompatibilityTests(unittest.TestCase):
    def test_native_if_shape_is_accepted_and_scalar_rewrite_is_rejected(self) -> None:
        native_if = [
            {
                "if": {
                    "condition": "= observed_success",
                    "do": [{"action": "/ctrl/go_home", "args": {"arm_id": 0}}],
                },
                "else": [{"assert": "= False", "message": "observable condition failed"}],
            }
        ]
        self.assertEqual(validate_workflow_nodes(native_if), [])

        scalar_if = [
            {
                "if": "= observed_success",
                "do": [{"action": "/ctrl/go_home", "args": {"arm_id": 0}}],
            }
        ]
        self.assertIn(
            "workflow[0].if must be a mapping with condition and do",
            validate_workflow_nodes(scalar_if),
        )

    def test_installed_native_skill_workflows_match_policy_grammar(self) -> None:
        config_path = Path(os.environ.get("KSM_NATIVE_CONFIG", "configs/local_1021.yaml"))
        if not config_path.exists():
            self.skipTest(f"native KSM config is unavailable: {config_path}")

        registry = build_registry(load_config(config_path))
        skill_root = Path(registry.k1_dir) / "knowin_skills"
        if not skill_root.exists():
            self.skipTest(f"native KW skill tree is unavailable: {skill_root}")

        test_prefix = registry.test_skill_dir.removeprefix("knowin_skills/").strip("/")
        checked: list[str] = []
        failures: dict[str, list[str]] = {}
        for skill_path in sorted(skill_root.rglob("*.yaml")):
            relative = skill_path.relative_to(skill_root).as_posix()
            if test_prefix and (relative == test_prefix or relative.startswith(f"{test_prefix}/")):
                continue
            data = read_yaml(skill_path)
            if not isinstance(data, dict) or not isinstance(data.get("workflow"), list):
                continue
            checked.append(relative)
            violations = validate_workflow_nodes(data["workflow"])
            if violations:
                failures[relative] = violations

        self.assertGreaterEqual(len(checked), 1, "no native KW skill workflows were discovered")
        self.assertEqual(failures, {}, f"native workflow grammar regressions: {failures}")


if __name__ == "__main__":
    unittest.main()
