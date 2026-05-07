from __future__ import annotations

from uuid import uuid4

from app.services.evidence import compute_evidence_sha256
from tests.conftest import admin_headers, base_receipt, post_receipt


def review_receipt(idempotency_key: str) -> dict:
    payload = base_receipt(idempotency_key)
    payload["changed_files"][0]["path"] = "src/review_me.py"
    payload["changed_files"][0]["classification"] = "code"
    payload["tests"] = [{"name": "tests/test_placeholder.py::test_stub", "status": "skipped"}]
    return payload


def block_receipt(idempotency_key: str) -> dict:
    payload = base_receipt(idempotency_key)
    payload["changed_files"][0]["secret_detected"] = True
    return payload


def approve(client, run_id: str, token: str = "test-admin-token"):
    return client.post(
        f"/api/v1/runs/{run_id}/review/approve",
        json={
            "reviewer_identity": "security@example.com",
            "review_note": "Approved after checking test gap.",
        },
        headers=admin_headers(token),
    )


def reject(client, run_id: str, token: str = "test-admin-token"):
    return client.post(
        f"/api/v1/runs/{run_id}/review/reject",
        json={
            "reviewer_identity": "security@example.com",
            "review_note": "Rejected pending stronger evidence.",
        },
        headers=admin_headers(token),
    )


def test_review_decision_creates_pending_review(client):
    response = post_receipt(client, review_receipt("review-pending"))

    assert response.status_code == 200
    assert response.json()["decision"] == "review"
    assert response.json()["review_status"] == "pending"
    assert response.json()["review_outcome"] is None


def test_allow_decision_creates_not_required_review(client):
    response = post_receipt(client, base_receipt("allow-not-required"))

    assert response.status_code == 200
    assert response.json()["decision"] == "allow"
    assert response.json()["review_status"] == "not_required"


def test_block_decision_creates_not_required_review(client):
    response = post_receipt(client, block_receipt("block-not-required"))

    assert response.status_code == 200
    assert response.json()["decision"] == "block"
    assert response.json()["review_status"] == "not_required"


def test_approve_pending_review_with_valid_admin_token_succeeds(client):
    created = post_receipt(client, review_receipt("approve-pending"))
    run_id = created.json()["run_id"]

    response = approve(client, run_id)

    assert response.status_code == 200
    data = response.json()
    assert data["review_status"] == "approved"
    assert data["review_outcome"] == "approved"
    assert data["reviewer_identity"] == "security@example.com"
    assert data["reviewed_at"] is not None


def test_reject_pending_review_with_valid_admin_token_succeeds(client):
    created = post_receipt(client, review_receipt("reject-pending"))
    run_id = created.json()["run_id"]

    response = reject(client, run_id)

    assert response.status_code == 200
    data = response.json()
    assert data["review_status"] == "rejected"
    assert data["review_outcome"] == "rejected"
    assert data["reviewer_identity"] == "security@example.com"
    assert data["reviewed_at"] is not None


def test_missing_admin_token_returns_401(client):
    created = post_receipt(client, review_receipt("missing-admin"))
    run_id = created.json()["run_id"]

    response = client.post(
        f"/api/v1/runs/{run_id}/review/approve",
        json={"reviewer_identity": "security@example.com", "review_note": None},
    )

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "admin_auth_required"


def test_invalid_admin_token_returns_403(client):
    created = post_receipt(client, review_receipt("invalid-admin"))
    run_id = created.json()["run_id"]

    response = approve(client, run_id, token="wrong-token")

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "admin_auth_invalid"


def test_approving_non_pending_run_returns_409(client):
    created = post_receipt(client, base_receipt("approve-non-pending"))
    run_id = created.json()["run_id"]

    response = approve(client, run_id)

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "review_not_pending"


def test_rejecting_non_pending_run_returns_409(client):
    created = post_receipt(client, base_receipt("reject-non-pending"))
    run_id = created.json()["run_id"]

    response = reject(client, run_id)

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "review_not_pending"


def test_unknown_review_run_returns_404_run_not_found(client):
    response = client.post(
        f"/api/v1/runs/{uuid4()}/review/approve",
        json={"reviewer_identity": "security@example.com", "review_note": None},
        headers=admin_headers(),
    )

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "run_not_found"


def test_run_detail_json_includes_review_state(client):
    created = post_receipt(client, review_receipt("detail-review-state"))
    run_id = created.json()["run_id"]

    response = client.get(f"/api/v1/runs/{run_id}")

    assert response.status_code == 200
    assert response.json()["review_status"] == "pending"
    assert response.json()["review_outcome"] is None


def test_evidence_export_includes_current_review_state(client):
    created = post_receipt(client, review_receipt("evidence-review-state"))
    run_id = created.json()["run_id"]
    approved = approve(client, run_id)

    response = client.get(f"/api/v1/evidence/{run_id}")

    assert response.status_code == 200
    evidence = response.json()
    assert evidence["review_status"] == "approved"
    assert evidence["review_outcome"] == "approved"
    assert evidence["reviewer_identity"] == "security@example.com"
    assert evidence["reviewed_at"] == approved.json()["reviewed_at"]
    assert evidence["evidence_sha256"] == compute_evidence_sha256(evidence)


def test_dashboard_html_includes_pending_review_state(client):
    post_receipt(client, review_receipt("dashboard-pending"))

    response = client.get("/")

    assert response.status_code == 200
    assert "Pending reviews" in response.text
    assert "review-pending" in response.text


def test_run_detail_html_renders_review_states(client):
    pending = post_receipt(client, review_receipt("html-pending")).json()["run_id"]
    approved = post_receipt(client, review_receipt("html-approved")).json()["run_id"]
    rejected = post_receipt(client, review_receipt("html-rejected")).json()["run_id"]
    not_required = post_receipt(client, base_receipt("html-not-required")).json()["run_id"]
    approve(client, approved)
    reject(client, rejected)

    pending_html = client.get(f"/runs/{pending}").text
    approved_html = client.get(f"/runs/{approved}").text
    rejected_html = client.get(f"/runs/{rejected}").text
    not_required_html = client.get(f"/runs/{not_required}").text

    assert "Human review required" in pending_html
    assert "Approved by human reviewer" in approved_html
    assert "Rejected by human reviewer" in rejected_html
    assert "Human review not required" in not_required_html
