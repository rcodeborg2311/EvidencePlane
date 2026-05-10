from __future__ import annotations

from tests.conftest import base_receipt, post_receipt


def _v2_receipt(key: str, **overrides) -> dict:
    return {**base_receipt(key), **overrides}


# --------------------------------------------------------------------------- #
# v2 fields accepted and stored
# --------------------------------------------------------------------------- #

def test_schema_version_field_accepted(client):
    resp = post_receipt(client, _v2_receipt("v2-schema-version", schema_version="2.0"))
    assert resp.status_code == 200


def test_source_run_url_stored_in_evidence(client):
    run_url = "https://ci.example.com/builds/42"
    receipt = _v2_receipt("v2-source-run-url", source_run_url=run_url)
    run_id = post_receipt(client, receipt).json()["run_id"]
    evidence = client.get(f"/api/v1/evidence/{run_id}").json()
    assert evidence["normalized_input"]["source_run_url"] == run_url


def test_pull_request_field_stored(client):
    pr = {
        "provider": "github",
        "number": 42,
        "url": "https://github.com/myorg/repo/pull/42",
        "head_sha": "a" * 40,
        "base_branch": "main",
    }
    receipt = _v2_receipt("v2-pull-request", pull_request=pr)
    run_id = post_receipt(client, receipt).json()["run_id"]
    evidence = client.get(f"/api/v1/evidence/{run_id}").json()
    assert evidence["normalized_input"]["pull_request"]["number"] == 42
    assert evidence["normalized_input"]["pull_request"]["provider"] == "github"


def test_gitlab_pull_request_provider(client):
    pr = {
        "provider": "gitlab",
        "number": 7,
        "url": "https://gitlab.com/myorg/repo/-/merge_requests/7",
        "head_sha": "b" * 40,
        "base_branch": "develop",
    }
    receipt = _v2_receipt("v2-gitlab-pr", pull_request=pr)
    resp = post_receipt(client, receipt)
    assert resp.status_code == 200


def test_scanner_results_stored(client):
    scanners = [
        {"scanner": "trivy", "finding_type": "vulnerability", "severity": "high",
         "path": "requirements.txt", "message": "CVE-2023-1234 in requests==2.28.0", "rule_id": "CVE-2023-1234"},
        {"scanner": "detect-secrets", "finding_type": "secret", "severity": "high",
         "path": "config.py", "message": "High entropy string detected", "rule_id": None},
    ]
    receipt = _v2_receipt("v2-scanner-results", scanner_results=scanners)
    run_id = post_receipt(client, receipt).json()["run_id"]
    evidence = client.get(f"/api/v1/evidence/{run_id}").json()
    stored = evidence["normalized_input"]["scanner_results"]
    assert len(stored) == 2
    assert stored[0]["scanner"] == "trivy"
    assert stored[1]["finding_type"] == "secret"


def test_artifact_refs_stored(client):
    artifacts = [
        {"name": "test-report", "url": "https://ci.example.com/artifacts/report.xml",
         "artifact_type": "test_report", "sha256": "a" * 64},
        {"name": "coverage", "url": "https://ci.example.com/artifacts/coverage.html",
         "artifact_type": "coverage", "sha256": None},
    ]
    receipt = _v2_receipt("v2-artifact-refs", artifact_refs=artifacts)
    run_id = post_receipt(client, receipt).json()["run_id"]
    evidence = client.get(f"/api/v1/evidence/{run_id}").json()
    stored = evidence["normalized_input"]["artifact_refs"]
    assert len(stored) == 2
    assert stored[0]["name"] == "test-report"


def test_all_v2_fields_together(client):
    receipt = _v2_receipt(
        "v2-all-fields",
        schema_version="2.0",
        source_run_url="https://ci.example.com/builds/99",
        pull_request={
            "provider": "github",
            "number": 99,
            "url": "https://github.com/myorg/repo/pull/99",
            "head_sha": "c" * 40,
            "base_branch": "main",
        },
        scanner_results=[
            {"scanner": "semgrep", "finding_type": "sast", "severity": "medium",
             "message": "SQL injection risk", "path": "app/db.py", "rule_id": "sql-injection"},
        ],
        artifact_refs=[
            {"name": "sast-report", "url": "https://ci.example.com/sast.json", "artifact_type": "sast_report"},
        ],
    )
    resp = post_receipt(client, receipt)
    assert resp.status_code == 200


# --------------------------------------------------------------------------- #
# Validation
# --------------------------------------------------------------------------- #

def test_invalid_pr_provider_rejected(client):
    pr = {"provider": "bitbucket_server", "number": 1, "url": "https://x.com", "head_sha": "a" * 40, "base_branch": "main"}
    resp = post_receipt(client, _v2_receipt("v2-bad-pr-provider", pull_request=pr))
    assert resp.status_code == 422


def test_invalid_scanner_finding_type_rejected(client):
    scanners = [{"scanner": "x", "finding_type": "malware", "severity": "high", "message": "bad"}]
    resp = post_receipt(client, _v2_receipt("v2-bad-finding-type", scanner_results=scanners))
    assert resp.status_code == 422


def test_v1_receipt_still_accepted(client):
    resp = post_receipt(client, base_receipt("v1-compat"))
    assert resp.status_code == 200


# --------------------------------------------------------------------------- #
# Connector dry-run (unit test the common module)
# --------------------------------------------------------------------------- #

def test_connector_common_build_receipt():
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "connectors"))
    from common import build_receipt
    from datetime import datetime, timezone

    receipt = build_receipt(
        idempotency_key="test-key-abc123",
        repo_name="myorg/myrepo",
        commit_sha="a" * 40,
        branch="main",
        actor="ci-bot",
        timestamp_utc=datetime(2026, 5, 10, 12, 0, 0, tzinfo=timezone.utc),
        changed_files=[],
        tests=[{"name": "test_foo", "status": "passed"}],
        tool_calls=[],
        policy_context={"protected_branch": False, "emergency_override": False, "approver_email": None},
        source_run_url="https://ci.example.com/builds/1",
    )
    assert receipt["schema_version"] == "2.0"
    assert receipt["repo_name"] == "myorg/myrepo"
    assert receipt["source_run_url"] == "https://ci.example.com/builds/1"
    assert len(receipt["changed_files"]) == 1
    assert receipt["changed_files"][0]["path"] == "repository"


def test_connector_common_dry_run(capsys):
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "connectors"))
    from common import build_receipt, submit_receipt
    from datetime import datetime, timezone

    receipt = build_receipt(
        idempotency_key="dry-run-key",
        repo_name="myorg/repo",
        commit_sha="b" * 40,
        branch="feature",
        actor="dev",
        timestamp_utc=datetime(2026, 5, 10, tzinfo=timezone.utc),
        changed_files=[],
        tests=[],
        tool_calls=[],
        policy_context={"protected_branch": False, "emergency_override": False, "approver_email": None},
    )
    result = submit_receipt(receipt, evidenceplane_url="https://unused.example.com", hmac_secret="test-secret", dry_run=True)
    assert result is None
    out = capsys.readouterr().out
    assert "DRY RUN" in out
    assert "myorg/repo" in out
