from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import subprocess
import sys

from app.models.schemas import RunReceipt

REPO_ROOT = Path(__file__).resolve().parents[1]
ADAPTER = REPO_ROOT / "adapters" / "evidenceplane-agent-adapter" / "agent_wrapper.py"


def load_adapter():
    spec = importlib.util.spec_from_file_location("evidenceplane_agent_wrapper", ADAPTER)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def run(args: list[str], cwd: Path = REPO_ROOT) -> subprocess.CompletedProcess:
    return subprocess.run(
        args,
        cwd=cwd,
        capture_output=True,
        text=True,
        check=False,
    )


def git(repo: Path, *args: str) -> str:
    result = run(["git", *args], cwd=repo)
    assert result.returncode == 0, result.stderr
    return result.stdout.strip()


def test_agent_adapter_classifies_docs_config_and_code():
    adapter = load_adapter()

    assert adapter.classify_path("README.md") == "docs"
    assert adapter.classify_path("docker-compose.yml") == "config"
    assert adapter.classify_path("src/app.py") == "code"


def test_agent_adapter_scanner_result_marks_secret_paths(tmp_path):
    adapter = load_adapter()
    scanner = tmp_path / "scanner.json"
    scanner.write_text(
        json.dumps({"secret_detected": True, "paths": ["src/settings.py"]}),
        encoding="utf-8",
    )
    files = [
        {"path": "README.md", "secret_detected": False},
        {"path": "src/settings.py", "secret_detected": False},
    ]

    detected, paths = adapter.read_scanner_result(scanner)
    adapter.apply_scanner_result(files, detected, paths)

    assert files[0]["secret_detected"] is False
    assert files[1]["secret_detected"] is True


def test_agent_adapter_enforcement_exit_logic():
    adapter = load_adapter()

    assert adapter.should_fail("allow", enforce_review=True, enforce_block=True) is False
    assert adapter.should_fail("review", enforce_review=False, enforce_block=True) is False
    assert adapter.should_fail("review", enforce_review=True, enforce_block=True) is True
    assert adapter.should_fail("block", enforce_review=False, enforce_block=True) is True
    assert adapter.should_fail("block", enforce_review=False, enforce_block=False) is False


def test_agent_adapter_dry_run_creates_valid_receipt_without_secret(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init")
    git(repo, "config", "user.email", "agent@example.invalid")
    git(repo, "config", "user.name", "Agent")
    (repo / "README.md").write_text("# Demo\n", encoding="utf-8")
    git(repo, "add", "README.md")
    git(repo, "commit", "-m", "initial")
    (repo / "README.md").write_text("# Demo\n\nChanged\n", encoding="utf-8")
    receipt_output = tmp_path / "receipt.json"

    result = run(
        [
            sys.executable,
            str(ADAPTER),
            "--repo-path",
            str(repo),
            "--receipt-output",
            str(receipt_output),
            "--dry-run",
            "--pretty",
        ]
    )

    assert result.returncode == 0, result.stderr
    payload = json.loads(receipt_output.read_text(encoding="utf-8"))
    receipt = RunReceipt.model_validate(payload)
    assert receipt.changed_files[0].path == "README.md"
    assert receipt.changed_files[0].classification == "docs"
