from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from ksm.config import ArtifactConfig, LLMConfig, ManagerConfig, PipelineConfig, SkillLibraryConfig
from ksm.publisher import publish_skill


def _config(root: Path) -> ManagerConfig:
    return ManagerConfig(
        root_dir=root,
        kw_repo=root / "kw",
        k1_dir=root / "k1",
        test_skill_dir="knowin_skills/knowin_skill_manager_tests",
        pipeline=PipelineConfig(mode="direct", base_url="http://127.0.0.1:8000", poll_interval_s=0.01, timeout_s=1.0),
        artifacts=ArtifactConfig(candidates_dir=root / "candidates", runs_dir=root / "runs"),
        llm=LLMConfig(
            provider="openai",
            base_url="",
            base_url_env="",
            api_key_env="",
            model="",
            model_env="",
            auth_mode="bearer",
            env_file=None,
            temperature=1.0,
            max_tokens=1,
            timeout_s=1.0,
        ),
        skill_library=SkillLibraryConfig(root=root / "lib"),
    )


class PublisherTests(unittest.TestCase):
    def test_publish_shortens_overlong_candidate_filename(self) -> None:
        with TemporaryDirectory() as raw:
            root = Path(raw)
            source = root / "candidate.yaml"
            source.write_text("schema_version: 1.0.0\nworkflow: []\n", encoding="utf-8")
            candidate_id = "robodojo_" + ("very_long_candidate_name_" * 20)

            result = publish_skill(config=_config(root), candidate_id=candidate_id, source_path=source)

            published = Path(result.published_path)
            self.assertTrue(published.exists())
            self.assertLessEqual(len(published.name.encode("utf-8")), 180)
            self.assertTrue(result.pipeline_skill_path.endswith(".yaml"))
            self.assertIn("knowin_skill_manager_tests", result.pipeline_skill_path)


if __name__ == "__main__":
    unittest.main()
