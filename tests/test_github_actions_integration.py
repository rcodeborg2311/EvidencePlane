from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys

from app.models.schemas import RunReceipt

REPO_ROOT = Path(__file__).resolve().parents[1]
RECEIPT_SCRIPT = REPO_ROOT / "scripts" / "github_actions_receipt.py"
FEEDBACK_SCRIPT = REPO_ROOT / "scripts" / "github_actions_feedback.py"


def _run(
    args: list[str], *, cwd: Path = REPO_ROOT, env: dict[str, str] | None = None
) -> subprocess.CompletedProcess:
    full_env = os.environ.copy()
    if env:
        full_env.update(env)
    return subprocess.run(
        args,
        cwd=cwd,
        env=full_env,
        capture_output=True,
        text=True,
        check=False,
    )


def _git(repo: Path, *args: str) -> str:
    result = _run(["git", *args], cwd=repo)
    assert result.returncode == 0, result.stderr
    return result.stdout.strip()


def test_github_actions_receipt_uses_diff_junit_and_secret_scan(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init")
    _git(repo, "config", "user.email", "ci@example.invalid")
    _git(repo, "config", "user.name", "CI")
    (repo / "README.md").write_text("# Demo\n", encoding="utf-8")
    _git(repo, "add", "README.md")
    _git(repo, "commit", "-m", "initial")
    commit_sha = _git(repo, "rev-parse", "HEAD")

    (repo / "README.md").write_text("# Demo\n\nchanged\n", encoding="utf-8")
    junit = tmp_path / "pytest.xml"
    junit.write_text(
        """
        <testsuite>
          <testcase classname="tests.test_demo" name="test_safe"/>
          <testcase classname="tests.test_demo" name="test_blocked">
            <failure message="failed"/>
          </testcase>
        </testsuite>
        """,
        encoding="utf-8",
    )
    secret_scan = tmp_path / "secret-scan.json"
    secret_scan.write_text(
        json.dumps({"findings": [{"path": "README.md"}]}),
        encoding="utf-8",
    )
    output = tmp_path / "receipt.json"

    result = _run(
        [
            sys.executable,
            str(RECEIPT_SCRIPT),
            "--repo-root",
            str(repo),
            "--diff-target",
            "HEAD",
            "--junit",
            str(junit),
            "--secret-scan-result",
            str(secret_scan),
            "--test-exit-code",
            "1",
            "--output",
            str(output),
        ],
        env={
            "GITHUB_REPOSITORY": "acme/payments",
            "GITHUB_SHA": commit_sha,
            "GITHUB_ACTOR": "octocat",
            "GITHUB_RUN_ID": "12345",
            "GITHUB_RUN_ATTEMPT": "2",
            "GITHUB_HEAD_REF": "feature/demo",
        },
    )

    assert result.returncode == 0, result.stderr
    payload = json.loads(output.read_text(encoding="utf-8"))
    receipt = RunReceipt.model_validate(payload)

    assert receipt.idempotency_key == "github-12345-2"
    assert receipt.repo_name == "acme/payments"
    assert receipt.branch == "feature/demo"
    assert receipt.actor == "octocat"
    assert receipt.changed_files[0].path == "README.md"
    assert receipt.changed_files[0].classification == "docs"
    assert receipt.changed_files[0].secret_detected is True
    assert [test.status for test in receipt.tests] == ["passed", "failed"]
    assert receipt.tool_calls[0].exit_code == 1


def decision_payload(decision: str, risk_score: int = 12) -> dict:
    return {
        "run_id": "11111111-1111-4111-8111-111111111111",
        "decision": decision,
        "policy_version": "1.0",
        "risk_score": risk_score,
        "review_status": "pending" if decision == "review" else "not_required",
        "review_outcome": None,
        "reviewer_identity": None,
        "review_note": None,
        "reviewed_at": None,
        "violations": [
            {
                "code": "NO_TEST_EVIDENCE",
                "message": "Code changed without any passed test evidence.",
                "severity": "medium",
            }
        ]
        if decision == "review"
        else [],
        "evidence_pack_id": "22222222-2222-4222-8222-222222222222",
        "evidence_sha256": "a" * 64,
        "created_at": "2026-05-07T00:00:00Z",
    }


def run_feedback(
    tmp_path: Path,
    decision: dict,
    *,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess:
    decision_file = tmp_path / "decision.json"
    summary_file = tmp_path / "summary.md"
    outputs_file = tmp_path / "outputs.txt"
    decision_file.write_text(json.dumps(decision), encoding="utf-8")
    return _run(
        [
            sys.executable,
            str(FEEDBACK_SCRIPT),
            "--decision-file",
            str(decision_file),
            "--summary-file",
            str(summary_file),
            "--outputs-file",
            str(outputs_file),
        ],
        env=env,
    )


def test_github_actions_feedback_allow_exits_zero(tmp_path):
    result = run_feedback(tmp_path, decision_payload("allow"))

    assert result.returncode == 0


def test_github_actions_feedback_review_exits_zero_when_not_enforced(tmp_path):
    result = run_feedback(
        tmp_path,
        decision_payload("review", risk_score=60),
        env={"EVIDENCEPLANE_ENFORCE_REVIEW": "false"},
    )

    assert result.returncode == 0
    assert "::warning::EvidencePlane returned review" in result.stderr


def test_github_actions_feedback_review_exits_nonzero_when_enforced(tmp_path):
    result = run_feedback(
        tmp_path,
        decision_payload("review", risk_score=60),
        env={"EVIDENCEPLANE_ENFORCE_REVIEW": "true"},
    )

    assert result.returncode == 1


def test_github_actions_feedback_block_exits_nonzero_by_default(tmp_path):
    result = run_feedback(tmp_path, decision_payload("block", risk_score=95))

    assert result.returncode == 1
    assert "::error::EvidencePlane returned block" in result.stderr


def test_github_actions_feedback_block_can_be_warning_only(tmp_path):
    result = run_feedback(
        tmp_path,
        decision_payload("block", risk_score=95),
        env={"EVIDENCEPLANE_ENFORCE_BLOCK": "false"},
    )

    assert result.returncode == 0


def test_github_actions_feedback_summary_contains_required_fields(tmp_path):
    decision = decision_payload("review", risk_score=60)
    result = run_feedback(
        tmp_path,
        decision,
        env={
            "EVIDENCEPLANE_ENFORCE_REVIEW": "false",
            "EVIDENCEPLANE_RUN_DETAIL_BASE_URL": "https://evidenceplane.example",
        },
    )

    summary = (tmp_path / "summary.md").read_text(encoding="utf-8")
    assert result.returncode == 0
    assert "EvidencePlane Decision" in summary
    assert "review" in summary
    assert "60" in summary
    assert "1.0" in summary
    assert "a" * 64 in summary
    assert "https://evidenceplane.example/runs/11111111-1111-4111-8111-111111111111" in summary


def test_github_actions_feedback_legacy_enforce_blocks_block(tmp_path):
    decision = decision_payload("block", risk_score=95)
    decision_file = tmp_path / "decision.json"
    decision_file.write_text(json.dumps(decision), encoding="utf-8")

    result = _run(
        [
            sys.executable,
            str(FEEDBACK_SCRIPT),
            "--decision-file",
            str(decision_file),
            "--enforce",
        ]
    )

    assert result.returncode == 1


def test_github_actions_feedback_maps_review_to_warning(tmp_path):
    decision = {
        "run_id": "11111111-1111-4111-8111-111111111111",
        "decision": "review",
        "policy_version": "1.0",
        "risk_score": 60,
        "review_status": "pending",
        "violations": [
            {
                "code": "NO_TEST_EVIDENCE",
                "message": "Code changed without any passed test evidence.",
                "severity": "medium",
            }
        ],
        "evidence_pack_id": "22222222-2222-4222-8222-222222222222",
        "evidence_sha256": "a" * 64,
        "created_at": "2026-05-07T00:00:00Z",
    }
    decision_file = tmp_path / "decision.json"
    summary_file = tmp_path / "summary.md"
    outputs_file = tmp_path / "outputs.txt"
    decision_file.write_text(json.dumps(decision), encoding="utf-8")

    result = _run(
        [
            sys.executable,
            str(FEEDBACK_SCRIPT),
            "--decision-file",
            str(decision_file),
            "--base-url",
            "https://evidenceplane.example",
            "--summary-file",
            str(summary_file),
            "--outputs-file",
            str(outputs_file),
            "--enforce-block",
            "true",
        ]
    )

    assert result.returncode == 0
    assert "::warning::EvidencePlane returned review" in result.stderr
    assert "EvidencePlane Decision" in summary_file.read_text(encoding="utf-8")
    assert "evidenceplane_conclusion=warning" in outputs_file.read_text(
        encoding="utf-8"
    )


def test_github_actions_feedback_enforces_block(tmp_path):
    decision = {
        "run_id": "11111111-1111-4111-8111-111111111111",
        "decision": "block",
        "policy_version": "1.0",
        "risk_score": 95,
        "review_status": "not_required",
        "violations": [],
        "evidence_pack_id": "22222222-2222-4222-8222-222222222222",
        "evidence_sha256": "b" * 64,
        "created_at": "2026-05-07T00:00:00Z",
    }
    decision_file = tmp_path / "decision.json"
    decision_file.write_text(json.dumps(decision), encoding="utf-8")

    result = _run(
        [
            sys.executable,
            str(FEEDBACK_SCRIPT),
            "--decision-file",
            str(decision_file),
        ]
    )

    assert result.returncode == 1
    assert "::error::EvidencePlane returned block" in result.stderr
