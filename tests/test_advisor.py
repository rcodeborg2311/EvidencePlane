from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

from tests.conftest import admin_headers, base_receipt, post_receipt

_FAKE_LLM_OUTPUT = {
    "verdict_explanation": "The change includes code files with no passing tests, which violates the code quality policy.",
    "suggested_fixes": [
        {
            "violation_code": "CODE_WITHOUT_PASSING_TESTS",
            "suggestion": "Add unit tests covering the changed functions and ensure they pass before merging.",
        }
    ],
    "draft_resolution_note": "Reviewer confirmed that tests are being added in a follow-up PR. Approving with note.",
}


def _fake_call_llm(_api_key, _context_bundle):
    return dict(_FAKE_LLM_OUTPUT)


def _review_receipt(key: str) -> dict:
    r = base_receipt(key)
    r["changed_files"][0]["classification"] = "code"
    return r


def _block_receipt(key: str) -> dict:
    r = base_receipt(key)
    r["changed_files"][0]["secret_detected"] = True
    return r


# --------------------------------------------------------------------------- #
# Request advisor finding
# --------------------------------------------------------------------------- #

@patch("app.services.advisor._call_llm", side_effect=_fake_call_llm)
def test_request_advisor_finding_returns_200(mock_llm, client, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    from app.config import reset_settings_cache
    reset_settings_cache()

    run_id = post_receipt(client, _review_receipt("advisor-basic")).json()["run_id"]
    case_id = client.get(f"/api/v1/runs/{run_id}/case", headers=admin_headers()).json()["case_id"]

    resp = client.post(f"/api/v1/cases/{case_id}/advisor", headers=admin_headers())
    assert resp.status_code == 200
    body = resp.json()
    assert body["is_advisory"] is True
    assert "advisory_warning" in body
    assert len(body["advisory_warning"]) > 0
    assert body["verdict_explanation"] == _FAKE_LLM_OUTPUT["verdict_explanation"]
    assert len(body["suggested_fixes"]) == 1
    assert body["suggested_fixes"][0]["violation_code"] == "CODE_WITHOUT_PASSING_TESTS"
    assert body["draft_resolution_note"] == _FAKE_LLM_OUTPUT["draft_resolution_note"]
    assert len(body["prompt_sha256"]) == 64
    assert body["model_provider"] == "anthropic"
    assert body["model_version"] == "claude-sonnet-4-6"


@patch("app.services.advisor._call_llm", side_effect=_fake_call_llm)
def test_request_advisor_finding_for_block_case(mock_llm, client, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    from app.config import reset_settings_cache
    reset_settings_cache()

    run_id = post_receipt(client, _block_receipt("advisor-block")).json()["run_id"]
    case_id = client.get(f"/api/v1/runs/{run_id}/case", headers=admin_headers()).json()["case_id"]

    resp = client.post(f"/api/v1/cases/{case_id}/advisor", headers=admin_headers())
    assert resp.status_code == 200


@patch("app.services.advisor._call_llm", side_effect=_fake_call_llm)
def test_multiple_findings_are_stored(mock_llm, client, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    from app.config import reset_settings_cache
    reset_settings_cache()

    run_id = post_receipt(client, _review_receipt("advisor-multi")).json()["run_id"]
    case_id = client.get(f"/api/v1/runs/{run_id}/case", headers=admin_headers()).json()["case_id"]

    client.post(f"/api/v1/cases/{case_id}/advisor", headers=admin_headers())
    client.post(f"/api/v1/cases/{case_id}/advisor", headers=admin_headers())

    findings = client.get(f"/api/v1/cases/{case_id}/advisor", headers=admin_headers()).json()
    assert len(findings) == 2


@patch("app.services.advisor._call_llm", side_effect=_fake_call_llm)
def test_finding_has_valid_prompt_sha256(mock_llm, client, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    from app.config import reset_settings_cache
    reset_settings_cache()

    run_id = post_receipt(client, _review_receipt("advisor-sha")).json()["run_id"]
    case_id = client.get(f"/api/v1/runs/{run_id}/case", headers=admin_headers()).json()["case_id"]

    body = client.post(f"/api/v1/cases/{case_id}/advisor", headers=admin_headers()).json()
    sha = body["prompt_sha256"]
    assert len(sha) == 64
    assert all(c in "0123456789abcdef" for c in sha)


# --------------------------------------------------------------------------- #
# Advisor not configured
# --------------------------------------------------------------------------- #

def test_advisor_returns_503_when_no_api_key(client, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    from app.config import reset_settings_cache
    reset_settings_cache()

    run_id = post_receipt(client, _review_receipt("advisor-no-key")).json()["run_id"]
    case_id = client.get(f"/api/v1/runs/{run_id}/case", headers=admin_headers()).json()["case_id"]

    resp = client.post(f"/api/v1/cases/{case_id}/advisor", headers=admin_headers())
    assert resp.status_code == 503


# --------------------------------------------------------------------------- #
# Case not found
# --------------------------------------------------------------------------- #

@patch("app.services.advisor._call_llm", side_effect=_fake_call_llm)
def test_advisor_returns_404_for_unknown_case(mock_llm, client, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    from app.config import reset_settings_cache
    reset_settings_cache()

    import uuid
    resp = client.post(f"/api/v1/cases/{uuid.uuid4()}/advisor", headers=admin_headers())
    assert resp.status_code == 404


# --------------------------------------------------------------------------- #
# List findings — empty before any request
# --------------------------------------------------------------------------- #

def test_list_findings_empty_initially(client):
    run_id = post_receipt(client, _review_receipt("advisor-list-empty")).json()["run_id"]
    case_id = client.get(f"/api/v1/runs/{run_id}/case", headers=admin_headers()).json()["case_id"]

    findings = client.get(f"/api/v1/cases/{case_id}/advisor", headers=admin_headers()).json()
    assert findings == []


# --------------------------------------------------------------------------- #
# Auth
# --------------------------------------------------------------------------- #

def test_request_advisor_requires_auth(client):
    run_id = post_receipt(client, _review_receipt("advisor-auth")).json()["run_id"]
    case_id = client.get(f"/api/v1/runs/{run_id}/case", headers=admin_headers()).json()["case_id"]
    resp = client.post(f"/api/v1/cases/{case_id}/advisor")
    assert resp.status_code == 401


def test_list_advisor_requires_auth(client):
    run_id = post_receipt(client, _review_receipt("advisor-auth-list")).json()["run_id"]
    case_id = client.get(f"/api/v1/runs/{run_id}/case", headers=admin_headers()).json()["case_id"]
    resp = client.get(f"/api/v1/cases/{case_id}/advisor")
    assert resp.status_code == 401


# --------------------------------------------------------------------------- #
# Context bundle excludes sensitive data
# --------------------------------------------------------------------------- #

@patch("app.services.advisor._call_llm")
def test_context_bundle_excludes_secret_values(mock_llm, client, monkeypatch):
    """Confirm the bundle passed to the LLM contains no raw secret messages."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    from app.config import reset_settings_cache
    reset_settings_cache()

    captured = {}

    def capture_call(api_key, context_bundle):
        captured["bundle"] = context_bundle
        return dict(_FAKE_LLM_OUTPUT)

    mock_llm.side_effect = capture_call

    run_id = post_receipt(client, _block_receipt("advisor-no-secrets")).json()["run_id"]
    case_id = client.get(f"/api/v1/runs/{run_id}/case", headers=admin_headers()).json()["case_id"]
    client.post(f"/api/v1/cases/{case_id}/advisor", headers=admin_headers())

    bundle_str = json.dumps(captured["bundle"])
    # changed_files in bundle must contain only path + classification, not file content
    for f in captured["bundle"]["changed_files"]:
        assert set(f.keys()) <= {"path", "classification"}
    # scanner findings summary must not contain raw messages
    for finding in captured["bundle"]["scanner_findings_summary"]:
        assert "message" not in finding
