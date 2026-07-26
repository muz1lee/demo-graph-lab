from __future__ import annotations

import unittest

from ksm.trace import add_visual_evidence_event, pipeline_status_to_trace
from ksm.trace_analysis import analyze_trace


class TraceAnalysisTests(unittest.TestCase):
    def test_kw_pipeline_failure_and_visual_probe_become_aspire_trace_analysis(self) -> None:
        status = {
            "response": {
                "running": False,
                "success": [{"success": False}],
                "logs": [
                    {
                        "skill_file": "knowin_skills/test/candidate.yaml",
                        "status": "failed",
                        "logs": [
                            {
                                "status": "failed",
                                "action_type": "subskill",
                                "step": {
                                    "action": "pickplace/semantic_pick.yaml",
                                    "args": {"pick_label": "blue bottle:dof"},
                                },
                            }
                        ],
                    }
                ],
            }
        }
        trace = pipeline_status_to_trace(status)
        trace = add_visual_evidence_event(
            trace,
            {
                "schema": "ksm.visual_feedback.v1",
                "status": "analyzed",
                "provider": "test",
                "analysis_available": True,
                "analysis": {
                    "visible_state_changes": [
                        {
                            "object": "blue bottle",
                            "changed": False,
                            "change": "none",
                            "summary": "Bottle stayed in its initial pose after the grasp attempt.",
                        }
                    ]
                },
            },
        )

        analysis = analyze_trace(trace)

        self.assertEqual(analysis["schema"], "ksm.aspire_kw.trace_analysis.v1")
        self.assertIn("semantic_pick_action_failed", analysis["trace_failure_breakdown"])
        self.assertIn("no_observable_visual_state_change", analysis["trace_failure_breakdown"])
        self.assertTrue(analysis["visual_effect_probes"])
        self.assertEqual(analysis["rich_trace_feature_counts"]["visual_feedback"], 1)


if __name__ == "__main__":
    unittest.main()
