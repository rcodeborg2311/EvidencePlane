from __future__ import annotations

from app.services.evidence import compute_evidence_sha256
from tests.conftest import base_receipt, post_receipt


def test_post_run_then_get_run_detail_matches_persisted_decision(client):
    created = post_receipt(client, base_receipt("integration-detail"))
    run_id = created.json()["run_id"]

    fetched = client.get(f"/api/v1/runs/{run_id}")

    assert fetched.status_code == 200
    assert fetched.json()["decision"] == created.json()["decision"]


def test_post_run_then_get_evidence_contains_run_id_and_matching_sha(client):
    created = post_receipt(client, base_receipt("integration-evidence"))
    run_id = created.json()["run_id"]

    fetched = client.get(f"/api/v1/evidence/{run_id}")

    assert fetched.status_code == 200
    evidence = fetched.json()
    assert evidence["run_id"] == run_id
    assert evidence["evidence_sha256"] == compute_evidence_sha256(evidence)


def test_dashboard_html_contains_latest_runs(client):
    response = client.get("/")

    assert response.status_code == 200
    assert "Latest Runs" in response.text


def test_api_docs_are_disabled_by_default(client):
    response = client.get("/docs")

    assert response.status_code == 404


def test_request_id_header_is_returned(client):
    response = client.get("/healthz", headers={"X-Request-ID": "test-request-id"})

    assert response.status_code == 200
    assert response.headers["X-Request-ID"] == "test-request-id"


def test_run_detail_html_contains_decision_badge_and_changed_file_path(client):
    payload = base_receipt("integration-html")
    payload["changed_files"][0]["path"] = "docs/policy.md"
    created = post_receipt(client, payload)
    run_id = created.json()["run_id"]

    response = client.get(f"/runs/{run_id}")

    assert response.status_code == 200
    assert "decision-badge" in response.text
    assert "docs/policy.md" in response.text
