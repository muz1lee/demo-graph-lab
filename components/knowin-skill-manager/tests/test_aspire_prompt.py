from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from ksm.aspire import build_aspire_prompt
from ksm.registry import SkillSummary, ToolRegistry
from ksm.suite import SuiteSpec, SuiteTask


class AspirePromptTests(unittest.TestCase):
    def test_prompt_distinguishes_feedback_gaps_from_mechanism_repairs(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            task_path = root / "task.yaml"
            task_path.write_text(
                """
task_id: stack_task
description: Stack objects.
args: {}
""".strip(),
                encoding="utf-8",
            )
            suite = SuiteSpec(
                suite_id="suite",
                description="test suite",
                manifest_path=str(root / "suite.yaml"),
                output_root=str(root / "runs"),
                publish_subdir="tests",
                success_threshold=1.0,
                tasks=[
                    SuiteTask(
                        task_id="stack_task",
                        task_path=str(task_path),
                        description="Stack objects.",
                        skill_args={},
                        predicates=[],
                        reset_layout=True,
                        metadata={},
                    )
                ],
                candidates=[],
            )
            registry = ToolRegistry(
                k1_dir=str(root),
                test_skill_dir="tests",
                ctrl=[],
                info=[],
                reasoning=[],
                namespaces=[],
                skills=[
                    SkillSummary(
                        path="pickplace/semantic_pickplace.yaml",
                        description="semantic pick and place",
                        args={"arm_id": 0, "pick_label": "", "place_label": ""},
                        actions=[],
                    )
                ],
            )

            prompt = build_aspire_prompt(
                suite=suite,
                target_task_path=task_path,
                candidate_id="candidate",
                registry=registry,
                history={},
                skill_context={},
                generation_index=1,
                candidate_index=1,
                population_size=1,
            )

        self.assertIn("extra verifier/assertion/trace steps", prompt)
        self.assertIn("as a mechanism-level repair", prompt)
        self.assertIn("reason from the concrete evidence and summaries", prompt)
        self.assertIn("rather than from a fixed failure taxonomy", prompt)
        self.assertNotIn("failed_strategy_families", prompt)


if __name__ == "__main__":
    unittest.main()
