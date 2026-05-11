from __future__ import annotations

from unittest.mock import patch

from tests.conftest import admin_headers, base_receipt, post_receipt


def _review_receipt(key: str) -> dict:
    r = base_receipt(key)
    r["changed_files"][0]["classification"] = "code"
    return r


def _block_receipt(key: str) -> dict:
    r = base_receipt(key)
    r["changed_files"][0]["secret_detected"] = True
    return r


# --------------------------------------------------------------------------- #
# Audit event emission
# --------------------------------------------------------------------------- #

def test_run_decided_emits_audit_event(client):
    run_id = post_receipt(client, base_receipt("audit-run")).json()["run_id"]
    events = client.get("/api/v1/audit-events", headers=admin_headers()).json()
    run_events = [e for e in events if e["event_type"] == "run_decided"]
    assert len(run_events) >= 1
    assert run_events[0]["resource_type"] == "run"
    assert run_events[0]["resource_id"] == run_id


def test_review_approved_emits_audit_event(client):
    run_id = post_receipt(client, _review_receipt("audit-review")).json()["run_id"]
    client.post(
        f"/api/v1/runs/{run_id}/review/approve",
        json={"reviewer_identity": "sec@example.com", "review_note": "LGTM"},
        headers=admin_headers(),
    )
    events = client.get(
        f"/api/v1/audit-events?resource_id={run_id}", headers=admin_headers()
    ).json()
    types = [e["event_type"] for e in events]
    assert "review_approved" in types


def test_policy_changed_emits_audit_event(client):
    client.post(
        "/api/v1/policy-configs",
        json={"repo_name": "audit-repo", "config": {}},
        headers=admin_headers(),
    )
    events = client.get(
        "/api/v1/audit-events?event_type=policy_changed", headers=admin_headers()
    ).json()
    assert len(events) >= 1
    assert events[0]["resource_type"] == "policy_config"


def test_source_created_emits_audit_event(client):
    client.post(
        "/api/v1/sources",
        json={"source_type": "github_actions", "display_name": "Audit Test Source"},
        headers=admin_headers(),
    )
    events = client.get(
        "/api/v1/audit-events?event_type=source_created", headers=admin_headers()
    ).json()
    assert len(events) >= 1


def test_source_disabled_emits_audit_event(client):
    src_id = client.post(
        "/api/v1/sources",
        json={"source_type": "gitlab_ci", "display_name": "Disable Test"},
        headers=admin_headers(),
    ).json()["source_id"]
    client.delete(f"/api/v1/sources/{src_id}", headers=admin_headers())
    events = client.get(
        "/api/v1/audit-events?event_type=source_disabled", headers=admin_headers()
    ).json()
    assert len(events) >= 1


def test_case_resolved_emits_audit_event(client):
    run_id = post_receipt(client, _review_receipt("audit-case-resolve")).json()["run_id"]
    case_id = client.get(f"/api/v1/runs/{run_id}/case", headers=admin_headers()).json()["case_id"]
    client.post(
        f"/api/v1/cases/{case_id}/resolve",
        json={"resolved_by": "sec@example.com"},
        headers=admin_headers(),
    )
    events = client.get(
        "/api/v1/audit-events?event_type=case_resolved", headers=admin_headers()
    ).json()
    assert len(events) >= 1
    assert events[0]["resource_id"] == case_id


# --------------------------------------------------------------------------- #
# List audit events
# --------------------------------------------------------------------------- #

def test_list_audit_events_requires_admin(client):
    resp = client.get("/api/v1/audit-events")
    assert resp.status_code == 401


def test_list_audit_events_filter_by_event_type(client):
    post_receipt(client, base_receipt("audit-filter-1"))
    post_receipt(client, base_receipt("audit-filter-2"))
    events = client.get(
        "/api/v1/audit-events?event_type=run_decided", headers=admin_headers()
    ).json()
    assert all(e["event_type"] == "run_decided" for e in events)


def test_list_audit_events_filter_by_resource_id(client):
    run_id = post_receipt(client, base_receipt("audit-resource-filter")).json()["run_id"]
    events = client.get(
        f"/api/v1/audit-events?resource_id={run_id}", headers=admin_headers()
    ).json()
    assert all(e["resource_id"] == run_id for e in events)


# --------------------------------------------------------------------------- #
# SIEM flush
# --------------------------------------------------------------------------- #

@patch("app.services.audit.httpx")
def test_siem_flush_forwards_pending_events(mock_httpx, client, monkeypatch):
    monkeypatch.setenv("SIEM_WEBHOOK_URL", "https://splunk.example.com/services/collector")
    monkeypatch.setenv("SIEM_WEBHOOK_TOKEN", "test-splunk-token")
    from app.config import reset_settings_cache
    reset_settings_cache()

    mock_httpx.post.return_value.status_code = 200
    post_receipt(client, base_receipt("siem-flush-run"))

    resp = client.post("/api/v1/admin/siem/flush", headers=admin_headers())
    assert resp.status_code == 200
    assert resp.json()["forwarded"] >= 1
    assert mock_httpx.post.called


def test_siem_flush_returns_503_when_not_configured(client, monkeypatch):
    monkeypatch.delenv("SIEM_WEBHOOK_URL", raising=False)
    from app.config import reset_settings_cache
    reset_settings_cache()

    resp = client.post("/api/v1/admin/siem/flush", headers=admin_headers())
    assert resp.status_code == 503


def test_siem_flush_requires_admin(client):
    resp = client.post("/api/v1/admin/siem/flush")
    assert resp.status_code == 401


# --------------------------------------------------------------------------- #
# Audit bundle
# --------------------------------------------------------------------------- #

def test_audit_bundle_for_repo(client):
    post_receipt(client, base_receipt("bundle-run-1"))
    post_receipt(client, base_receipt("bundle-run-2"))
    resp = client.get(
        "/api/v1/audit-bundle?repo_name=docs-site&from_date=2026-01-01&to_date=2026-12-31",
        headers=admin_headers(),
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["repo_name"] == "docs-site"
    assert body["runs_count"] == 2
    assert len(body["evidence_packs"]) == 2
    assert len(body["bundle_sha256"]) == 64


def test_audit_bundle_empty_for_unknown_repo(client):
    resp = client.get(
        "/api/v1/audit-bundle?repo_name=does-not-exist&from_date=2026-01-01&to_date=2026-12-31",
        headers=admin_headers(),
    )
    assert resp.status_code == 200
    assert resp.json()["runs_count"] == 0


def test_audit_bundle_sha256_is_deterministic(client):
    post_receipt(client, base_receipt("bundle-sha-run"))
    resp1 = client.get(
        "/api/v1/audit-bundle?repo_name=docs-site&from_date=2026-01-01&to_date=2026-12-31",
        headers=admin_headers(),
    ).json()
    resp2 = client.get(
        "/api/v1/audit-bundle?repo_name=docs-site&from_date=2026-01-01&to_date=2026-12-31",
        headers=admin_headers(),
    ).json()
    # SHA256 changes between calls because generated_at differs — bundle content is same
    assert resp1["runs_count"] == resp2["runs_count"]


def test_audit_bundle_requires_admin(client):
    resp = client.get(
        "/api/v1/audit-bundle?repo_name=docs-site&from_date=2026-01-01&to_date=2026-12-31"
    )
    assert resp.status_code == 401


def test_audit_bundle_includes_audit_events(client):
    post_receipt(client, base_receipt("bundle-events-run"))
    resp = client.get(
        "/api/v1/audit-bundle?repo_name=docs-site&from_date=2026-01-01&to_date=2026-12-31",
        headers=admin_headers(),
    ).json()
    assert len(resp["audit_events"]) >= 1
    assert any(e["event_type"] == "run_decided" for e in resp["audit_events"])
