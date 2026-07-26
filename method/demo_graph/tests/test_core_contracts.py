"""核心公开契约：状态机、候选、后端、伺服、清单、隔离进程。"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from method.demo_graph.backends import (
    LegacyYamlBackend,
    PolicyBackend,
    PythonNodePolicyBackend,
)
from method.demo_graph.candidates import (
    ActionCandidate,
    CandidateDecision,
    CandidateSelector,
)
from method.demo_graph.isolation import IsolatedPolicyWorker, IsolationViolation
from method.demo_graph.manifest import RunManifest
from method.demo_graph.servo import ServoController, ServoOutcome, ServoStatus
from method.demo_graph.state_machine import NodePhase, advance_phase


class StateMachineTests(unittest.TestCase):
    def test_legal_happy_path(self):
        phase = NodePhase.READY
        for nxt in (
            NodePhase.RESOLVING_HOLES,
            NodePhase.CANDIDATES_READY,
            NodePhase.ADMITTED,
            NodePhase.EXECUTING,
            NodePhase.VERIFYING,
            NodePhase.SUCCEEDED,
        ):
            phase = advance_phase(phase, nxt)
        self.assertEqual(phase, NodePhase.SUCCEEDED)

    def test_illegal_skip_is_rejected(self):
        with self.assertRaises(ValueError):
            advance_phase(NodePhase.READY, NodePhase.EXECUTING)

    def test_recoverable_and_failed_are_terminals(self):
        self.assertTrue(NodePhase.RECOVERABLE.is_terminal)
        self.assertTrue(NodePhase.FAILED.is_terminal)
        self.assertTrue(NodePhase.SUCCEEDED.is_terminal)
        with self.assertRaises(ValueError):
            advance_phase(NodePhase.FAILED, NodePhase.READY)


class CandidateTests(unittest.TestCase):
    def test_candidate_is_immutable_and_bound(self):
        candidate = ActionCandidate(
            node_id="pick",
            observation_revision="rev-1",
            observation_digest="sha256:" + ("a" * 64),
            perception_track="qwen_dof_xquat",
            frame="world",
            tcp_pose=(0.1, 0.2, 0.3, 0.0, 0.0, 0.0, 1.0),
            graph_constraints=("grasp_region",),
            evidence_ids=("ev-1",),
            provenance_reference="runtime_perception:rev-1",
        )
        with self.assertRaises(AttributeError):
            candidate.node_id = "other"  # type: ignore[misc]
        self.assertEqual(candidate.node_id, "pick")

    def test_selector_cannot_mutate_or_execute(self):
        candidates = (
            ActionCandidate(
                node_id="pick",
                observation_revision="r",
                observation_digest="sha256:" + ("b" * 64),
                perception_track="track",
                frame="world",
                tcp_pose=(0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 1.0),
                graph_constraints=("c",),
                evidence_ids=("e",),
                provenance_reference="demo",
            ),
        )
        decision = CandidateSelector().select(candidates)
        self.assertEqual(decision.kind, CandidateDecision.SELECT)
        self.assertEqual(decision.selected_index, 0)
        reject = CandidateSelector().reject_all(candidates, reason="unsafe")
        self.assertEqual(reject.kind, CandidateDecision.REJECT_ALL)
        more = CandidateSelector().request_evidence(candidates, reason="need mask")
        self.assertEqual(more.kind, CandidateDecision.REQUEST_EVIDENCE)


class BackendTests(unittest.TestCase):
    def test_python_backend_is_primary(self):
        backend: PolicyBackend = PythonNodePolicyBackend()
        self.assertEqual(backend.name, "python_node_policy")
        self.assertTrue(backend.is_primary)

    def test_legacy_yaml_is_baseline_only(self):
        backend = LegacyYamlBackend()
        self.assertEqual(backend.name, "legacy_yaml")
        self.assertFalse(backend.is_primary)
        with self.assertRaises(NotImplementedError):
            backend.generate_policy({"graph_id": "x"})


class ServoTests(unittest.TestCase):
    def test_servo_returns_bounded_outcome_only(self):
        ticks = {"n": 0}

        def plant():
            ticks["n"] += 1
            return ticks["n"] >= 3

        controller = ServoController(
            observe_error=lambda: 0.2 if ticks["n"] < 3 else 0.0,
            correct=lambda error: None,
            verify=plant,
            max_ticks=10,
            error_tolerance=0.05,
        )
        outcome = controller.run()
        self.assertIsInstance(outcome, ServoOutcome)
        self.assertEqual(outcome.status, ServoStatus.CONVERGED)
        self.assertEqual(ticks["n"], 3)

    def test_servo_abort_on_budget(self):
        controller = ServoController(
            observe_error=lambda: 1.0,
            correct=lambda error: None,
            verify=lambda: False,
            max_ticks=2,
            error_tolerance=0.01,
        )
        outcome = controller.run()
        self.assertEqual(outcome.status, ServoStatus.ABORT)


class ManifestTests(unittest.TestCase):
    def test_manifest_records_digests_and_audit(self):
        manifest = RunManifest(
            ksm_commit="abc123",
            knowin_world_commit="def456",
            knowin_world_dirty_hash=None,
            data_asset_lock="lock-1",
            config_digest="sha256:" + ("c" * 64),
            model_ids=("gpt-test",),
            seed=7,
            graph_digest="sha256:" + ("d" * 64),
            code_digest="sha256:" + ("e" * 64),
            api_audit_digests=("sha256:" + ("f" * 64),),
            golden=True,
        )
        self.assertTrue(manifest.golden)
        payload = manifest.to_dict()
        self.assertEqual(payload["seed"], 7)
        self.assertIn("api_audit_digests", payload)

    def test_dirty_dependency_cannot_be_golden(self):
        with self.assertRaises(ValueError):
            RunManifest(
                ksm_commit="a",
                knowin_world_commit="b",
                knowin_world_dirty_hash="dirty",
                data_asset_lock="lock",
                config_digest="sha256:" + ("c" * 64),
                model_ids=(),
                seed=0,
                graph_digest="sha256:" + ("d" * 64),
                code_digest="sha256:" + ("e" * 64),
                api_audit_digests=(),
                golden=True,
            )


class IsolationTests(unittest.TestCase):
    def test_worker_can_call_allowlisted_broker_only(self):
        def handler(method: str, params: dict):
            if method == "perception.observe":
                return {"revision": "1", "payload": {"ok": True}}
            raise KeyError(method)

        with IsolatedPolicyWorker(handler) as worker:
            result = worker.call("perception.observe", {"node_id": "pick"})
            self.assertEqual(result["revision"], "1")
            with self.assertRaises(IsolationViolation):
                worker.call("session.reset", {})

    def test_worker_rejects_network_probe_script(self):
        script = (
            "import socket\n"
            "s=socket.socket(); s.connect(('1.1.1.1', 80))\n"
        )
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "bad_policy.py"
            path.write_text(script, encoding="utf-8")
            with self.assertRaises(IsolationViolation):
                IsolatedPolicyWorker.run_policy_file(
                    path,
                    broker_handler=lambda method, params: {},
                    allow_network=False,
                )


if __name__ == "__main__":
    unittest.main()
