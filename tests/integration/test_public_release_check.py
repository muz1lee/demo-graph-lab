from __future__ import annotations

import hashlib
import importlib.util
import json
import subprocess
import sys
from pathlib import Path


MODULE_PATH = Path(__file__).parents[2] / "scripts" / "public_release_check.py"
SPEC = importlib.util.spec_from_file_location("public_release_check", MODULE_PATH)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


def test_scan_rejects_secret_and_forbidden_directory(tmp_path: Path) -> None:
    secret = tmp_path / "safe.txt"
    secret.write_text("token = sk-" + "a" * 24, encoding="utf-8")
    run_file = tmp_path / "runs" / "report.txt"
    run_file.parent.mkdir()
    run_file.write_text("result", encoding="utf-8")

    violations = MODULE.scan([secret, run_file], tmp_path)

    assert {violation.reason for violation in violations} == {
        "possible OpenAI-style API key",
        "forbidden path",
    }


def test_scan_accepts_plain_source(tmp_path: Path) -> None:
    source = tmp_path / "method" / "policy.py"
    source.parent.mkdir()
    source.write_text("def build_policy():\n    return None\n", encoding="utf-8")

    assert MODULE.scan([source], tmp_path) == []


def test_release_candidate_paths_include_untracked_nonignored_files(tmp_path: Path) -> None:
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    tracked = tmp_path / "tracked.py"
    tracked.write_text("TRACKED = True\n", encoding="utf-8")
    subprocess.run(["git", "add", "tracked.py"], cwd=tmp_path, check=True)
    untracked = tmp_path / "new_policy.py"
    untracked.write_text("NEW = True\n", encoding="utf-8")
    ignored = tmp_path / "secret.local"
    ignored.write_text("ignored", encoding="utf-8")
    (tmp_path / ".gitignore").write_text("secret.local\n", encoding="utf-8")

    candidates = set(MODULE.release_candidate_paths(tmp_path))

    assert tracked in candidates
    assert untracked in candidates
    assert ignored not in candidates


def test_scan_rejects_secret_boundaries_and_symlinks(tmp_path: Path) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text("TOKEN=not-even-a-recognized-format\n", encoding="utf-8")
    key_file = tmp_path / "client.pem"
    key_file.write_text("placeholder\n", encoding="utf-8")
    endpoint = tmp_path / "endpoint.txt"
    private_host = ".".join(["192", "168", "20", "77"])
    endpoint.write_text(f"http://{private_host}/service\n", encoding="utf-8")
    invalid_utf8 = tmp_path / "opaque.dat"
    invalid_utf8.write_bytes(b"\xff\xfe")
    symlink = tmp_path / "linked.txt"
    symlink.symlink_to(endpoint)

    violations = MODULE.scan(
        [env_file, key_file, endpoint, invalid_utf8, symlink],
        tmp_path,
    )

    assert {violation.reason for violation in violations} == {
        "forbidden filename or file type",
        "possible internal service endpoint",
        "file is not valid UTF-8 text",
        "symbolic links are not allowed",
    }


def test_source_manifest_detects_hash_drift(tmp_path: Path) -> None:
    component_root = tmp_path / "components" / "example"
    component_root.mkdir(parents=True)
    source = component_root / "source.py"
    source.write_text("VALUE = 1\n", encoding="utf-8")
    source_digest = hashlib.sha256(source.read_bytes()).hexdigest()
    component_digest = hashlib.sha256(
        f"source.py\t{source_digest}\n".encode("utf-8")
    ).hexdigest()
    global_digest = hashlib.sha256(
        f"example\t{component_digest}\n".encode("utf-8")
    ).hexdigest()
    manifest_path = tmp_path / MODULE.SOURCE_MANIFEST_PATH
    manifest_path.write_text(
        json.dumps(
            {
                "schema_version": "1.0",
                "component_count": 1,
                "file_count": 1,
                "global_aggregate_sha256": global_digest,
                "components": {
                    "example": {
                        "target": "components/example",
                        "file_count": 1,
                        "aggregate_sha256": component_digest,
                        "files": [
                            {
                                "path": "source.py",
                                "size_bytes": source.stat().st_size,
                                "sha256": source_digest,
                            }
                        ],
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    candidates = [manifest_path, source]
    assert MODULE.validate_source_manifest(tmp_path, candidates) == []

    source.write_text("VALUE = 2\n", encoding="utf-8")
    reasons = {
        violation.reason
        for violation in MODULE.validate_source_manifest(tmp_path, candidates)
    }

    assert "manifest hash/size mismatch: example/source.py" in reasons
    assert "aggregate digest mismatch for component example" in reasons


def test_repository_source_manifest_is_consistent() -> None:
    root = Path(__file__).parents[2]
    candidates = MODULE.release_candidate_paths(root)

    assert MODULE.validate_source_manifest(root, candidates) == []
