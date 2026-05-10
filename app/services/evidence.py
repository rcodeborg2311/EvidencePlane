from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
from typing import Any
from uuid import UUID

from app.models.schemas import Decision, RunReceipt, Violation


def format_datetime(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def canonical_json(data: Any) -> str:
    return json.dumps(data, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def canonical_sha256(data: Any) -> str:
    return hashlib.sha256(canonical_json(data).encode("utf-8")).hexdigest()


def normalized_input(receipt: RunReceipt) -> dict[str, Any]:
    return receipt.model_dump(mode="json")


def evidence_hash_body(evidence_pack: dict[str, Any]) -> dict[str, Any]:
    body = deepcopy(evidence_pack)
    body.pop("evidence_sha256", None)
    return body


def compute_evidence_sha256(evidence_pack: dict[str, Any]) -> str:
    return canonical_sha256(evidence_hash_body(evidence_pack))


def review_event_hash_body(review_event: dict[str, Any]) -> dict[str, Any]:
    body = deepcopy(review_event)
    body.pop("event_sha256", None)
    return body


def compute_review_event_sha256(review_event: dict[str, Any]) -> str:
    return canonical_sha256(review_event_hash_body(review_event))


def build_review_event(
    *,
    event_id: UUID,
    run_id: UUID,
    sequence: int,
    action: str,
    actor: str,
    reason: str | None,
    created_at: datetime,
    previous_event_sha256: str | None,
) -> dict[str, Any]:
    event: dict[str, Any] = {
        "event_id": str(event_id),
        "run_id": str(run_id),
        "sequence": sequence,
        "action": action,
        "actor": actor,
        "reason": reason,
        "created_at": format_datetime(created_at),
        "previous_event_sha256": previous_event_sha256,
    }
    event["event_sha256"] = compute_review_event_sha256(event)
    return event


def build_evidence_pack(
    *,
    evidence_pack_id: UUID,
    run_id: UUID,
    receipt: RunReceipt,
    decision: Decision,
    policy_version: str,
    risk_score: int,
    review_status: str,
    review_outcome: str | None,
    reviewer_identity: str | None,
    review_note: str | None,
    reviewed_at: datetime | None,
    violations: list[Violation],
    generated_at: datetime,
    policy_config_id: str | None = None,
    policy_config_snapshot: dict | None = None,
) -> dict[str, Any]:
    pack: dict[str, Any] = {
        "evidence_pack_id": str(evidence_pack_id),
        "run_id": str(run_id),
        "normalized_input": normalized_input(receipt),
        "decision": decision,
        "policy_version": policy_version,
        "policy_config_id": policy_config_id,
        "policy_config_snapshot": policy_config_snapshot,
        "risk_score": risk_score,
        "review_status": review_status,
        "review_outcome": review_outcome,
        "reviewer_identity": reviewer_identity,
        "review_note": review_note,
        "reviewed_at": format_datetime(reviewed_at) if reviewed_at else None,
        "review_events": [],
        "violations": [violation.model_dump(mode="json") for violation in violations],
        "generated_at": format_datetime(generated_at),
        "version": "1.0",
    }
    pack["evidence_sha256"] = compute_evidence_sha256(pack)
    return pack
