from __future__ import annotations

from datetime import timezone
from uuid import UUID
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import get_engine
from app.models.db import ReviewEvent
from app.services.evidence import (
    build_review_event,
    compute_evidence_sha256,
    compute_review_event_sha256,
)
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


def event_body_from_record(event: ReviewEvent) -> dict:
    created_at = event.created_at
    if created_at.tzinfo is None or created_at.utcoffset() is None:
        created_at = created_at.replace(tzinfo=timezone.utc)
    else:
        created_at = created_at.astimezone(timezone.utc)
    return {
        "event_id": str(event.id),
        "run_id": str(event.run_id),
        "sequence": event.sequence,
        "action": event.action,
        "actor": event.actor,
        "reason": event.reason,
        "created_at": created_at.isoformat().replace("+00:00", "Z"),
        "previous_event_sha256": event.previous_event_sha256,
        "event_sha256": event.event_sha256,
    }


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
    assert len(data["review_events"]) == 1
    assert data["review_events"][0]["action"] == "approved"


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
    assert response.json()["error"]["code"] == "auth_required"


def test_invalid_admin_token_returns_401(client):
    created = post_receipt(client, review_receipt("invalid-admin"))
    run_id = created.json()["run_id"]

    response = approve(client, run_id, token="wrong-token")

    # Unknown token is unauthenticated (401), not forbidden (403).
    # 403 would mean authenticated-but-unauthorized; an unrecognised token
    # is not authenticated at all.
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "invalid_token"


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
    assert evidence["review_events"] == [
        {
            "event_id": evidence["review_events"][0]["event_id"],
            "run_id": run_id,
            "sequence": 1,
            "action": "approved",
            "actor": "security@example.com",
            "reason": "Approved after checking test gap.",
            "created_at": approved.json()["reviewed_at"],
            "previous_event_sha256": None,
            "event_sha256": evidence["review_events"][0]["event_sha256"],
        }
    ]
    assert evidence["review_events"][0]["event_sha256"] == compute_review_event_sha256(
        evidence["review_events"][0]
    )
    assert evidence["evidence_sha256"] == compute_evidence_sha256(evidence)


def test_evidence_export_fails_when_review_event_content_is_tampered(client):
    created = post_receipt(client, review_receipt("evidence-review-tamper"))
    run_id = created.json()["run_id"]
    approve(client, run_id)

    with Session(get_engine()) as session:
        event = session.scalar(
            select(ReviewEvent).where(ReviewEvent.run_id == UUID(run_id))
        )
        assert event is not None
        event.actor = "attacker@example.com"
        session.commit()

    response = client.get(f"/api/v1/evidence/{run_id}")

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "service_unavailable"


def test_evidence_export_fails_when_review_event_sequence_is_tampered(client):
    created = post_receipt(client, review_receipt("evidence-review-sequence-tamper"))
    run_id = created.json()["run_id"]
    approve(client, run_id)

    with Session(get_engine()) as session:
        event = session.scalar(
            select(ReviewEvent).where(ReviewEvent.run_id == UUID(run_id))
        )
        assert event is not None
        event.sequence = 2
        body = event_body_from_record(event)
        body["event_sha256"] = compute_review_event_sha256(body)
        event.event_sha256 = body["event_sha256"]
        session.commit()

    response = client.get(f"/api/v1/evidence/{run_id}")

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "service_unavailable"


def test_evidence_export_fails_when_review_event_chain_is_tampered(client):
    created = post_receipt(client, review_receipt("evidence-review-chain-tamper"))
    run_id = created.json()["run_id"]
    approve(client, run_id)

    with Session(get_engine()) as session:
        first_event = session.scalar(
            select(ReviewEvent).where(ReviewEvent.run_id == UUID(run_id))
        )
        assert first_event is not None
        second_event_id = uuid4()
        created_at = first_event.created_at
        if created_at.tzinfo is None or created_at.utcoffset() is None:
            created_at = created_at.replace(tzinfo=timezone.utc)
        else:
            created_at = created_at.astimezone(timezone.utc)
        second_body = build_review_event(
            event_id=second_event_id,
            run_id=UUID(run_id),
            sequence=2,
            action="approved",
            actor="security@example.com",
            reason="Injected event with invalid chain pointer.",
            created_at=created_at,
            previous_event_sha256="0" * 64,
        )
        session.add(
            ReviewEvent(
                id=second_event_id,
                run_id=UUID(run_id),
                sequence=2,
                action="approved",
                actor="security@example.com",
                reason="Injected event with invalid chain pointer.",
                created_at=created_at,
                previous_event_sha256="0" * 64,
                event_sha256=second_body["event_sha256"],
            )
        )
        session.commit()

    response = client.get(f"/api/v1/evidence/{run_id}")

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "service_unavailable"


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
    assert "Review history" in approved_html
    assert "Rejected by human reviewer" in rejected_html
    assert "Human review not required" in not_required_html
