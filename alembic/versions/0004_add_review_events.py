"""add append-only review events

Revision ID: 0004_add_review_events
Revises: 0003_add_review_state
Create Date: 2026-05-09 23:10:00.000000
"""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
from typing import Any
import uuid

from alembic import op
import sqlalchemy as sa

revision = "0004_add_review_events"
down_revision = "0003_add_review_state"
branch_labels = None
depends_on = None


def _canonical_json(data: Any) -> str:
    return json.dumps(data, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _sha256_without_field(body: dict[str, Any], field: str) -> str:
    hash_body = deepcopy(body)
    hash_body.pop(field, None)
    return hashlib.sha256(_canonical_json(hash_body).encode("utf-8")).hexdigest()


def _evidence_sha256(body: dict[str, Any]) -> str:
    return _sha256_without_field(body, "evidence_sha256")


def _review_event_sha256(body: dict[str, Any]) -> str:
    return _sha256_without_field(body, "event_sha256")


def _format_datetime(value: datetime | str) -> str:
    if isinstance(value, str):
        return value
    if value.tzinfo is None or value.utcoffset() is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _runs_table() -> sa.Table:
    metadata = sa.MetaData()
    return sa.Table(
        "runs",
        metadata,
        sa.Column("id", sa.Uuid()),
        sa.Column("review_outcome", sa.String(length=20)),
        sa.Column("reviewer_identity", sa.String(length=200)),
        sa.Column("review_note", sa.String(length=1000)),
        sa.Column("reviewed_at", sa.DateTime(timezone=True)),
        sa.Column("evidence_sha256", sa.String(length=64)),
    )


def _evidence_table() -> sa.Table:
    metadata = sa.MetaData()
    return sa.Table(
        "evidence_packs",
        metadata,
        sa.Column("id", sa.Uuid()),
        sa.Column("run_id", sa.Uuid()),
        sa.Column("body", sa.JSON()),
        sa.Column("sha256", sa.String(length=64)),
    )


def _review_events_table() -> sa.Table:
    metadata = sa.MetaData()
    return sa.Table(
        "review_events",
        metadata,
        sa.Column("id", sa.Uuid()),
        sa.Column("run_id", sa.Uuid()),
        sa.Column("sequence", sa.Integer()),
        sa.Column("action", sa.String(length=20)),
        sa.Column("actor", sa.String(length=200)),
        sa.Column("reason", sa.String(length=1000)),
        sa.Column("created_at", sa.DateTime(timezone=True)),
        sa.Column("previous_event_sha256", sa.String(length=64)),
        sa.Column("event_sha256", sa.String(length=64)),
    )


def _backfill_review_events() -> None:
    bind = op.get_bind()
    runs = _runs_table()
    review_events = _review_events_table()
    rows = bind.execute(
        sa.select(
            runs.c.id,
            runs.c.review_outcome,
            runs.c.reviewer_identity,
            runs.c.review_note,
            runs.c.reviewed_at,
        ).where(runs.c.review_outcome.is_not(None))
    ).mappings()

    for row in rows:
        event_id = uuid.uuid4()
        created_at = row["reviewed_at"] or datetime.now(timezone.utc)
        event_body = {
            "event_id": str(event_id),
            "run_id": str(row["id"]),
            "sequence": 1,
            "action": row["review_outcome"],
            "actor": row["reviewer_identity"] or "unknown",
            "reason": row["review_note"],
            "created_at": _format_datetime(created_at),
            "previous_event_sha256": None,
        }
        event_body["event_sha256"] = _review_event_sha256(event_body)
        bind.execute(
            review_events.insert().values(
                id=event_id,
                run_id=row["id"],
                sequence=event_body["sequence"],
                action=event_body["action"],
                actor=event_body["actor"],
                reason=event_body["reason"],
                created_at=created_at,
                previous_event_sha256=None,
                event_sha256=event_body["event_sha256"],
            )
        )


def _events_by_run() -> dict[str, list[dict[str, Any]]]:
    bind = op.get_bind()
    review_events = _review_events_table()
    rows = bind.execute(
        sa.select(
            review_events.c.id,
            review_events.c.run_id,
            review_events.c.sequence,
            review_events.c.action,
            review_events.c.actor,
            review_events.c.reason,
            review_events.c.created_at,
            review_events.c.previous_event_sha256,
            review_events.c.event_sha256,
        ).order_by(review_events.c.run_id, review_events.c.sequence)
    ).mappings()

    events_by_run: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        event = {
            "event_id": str(row["id"]),
            "run_id": str(row["run_id"]),
            "sequence": row["sequence"],
            "action": row["action"],
            "actor": row["actor"],
            "reason": row["reason"],
            "created_at": _format_datetime(row["created_at"]),
            "previous_event_sha256": row["previous_event_sha256"],
            "event_sha256": row["event_sha256"],
        }
        events_by_run.setdefault(str(row["run_id"]), []).append(event)
    return events_by_run


def _rewrite_evidence_review_events(*, add_review_events: bool) -> None:
    bind = op.get_bind()
    runs = _runs_table()
    evidence_packs = _evidence_table()
    events_by_run = _events_by_run() if add_review_events else {}
    rows = bind.execute(
        sa.select(runs.c.id, evidence_packs.c.body).join(
            evidence_packs, evidence_packs.c.run_id == runs.c.id
        )
    ).mappings()

    for row in rows:
        body = dict(row["body"])
        if add_review_events:
            body["review_events"] = events_by_run.get(str(row["id"]), [])
        else:
            body.pop("review_events", None)

        body["evidence_sha256"] = _evidence_sha256(body)
        bind.execute(
            evidence_packs.update()
            .where(evidence_packs.c.run_id == row["id"])
            .values(body=body, sha256=body["evidence_sha256"])
        )
        bind.execute(
            runs.update()
            .where(runs.c.id == row["id"])
            .values(evidence_sha256=body["evidence_sha256"])
        )


def upgrade() -> None:
    op.create_table(
        "review_events",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("run_id", sa.Uuid(), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("action", sa.String(length=20), nullable=False),
        sa.Column("actor", sa.String(length=200), nullable=False),
        sa.Column("reason", sa.String(length=1000), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("previous_event_sha256", sa.String(length=64), nullable=True),
        sa.Column("event_sha256", sa.String(length=64), nullable=False),
        sa.ForeignKeyConstraint(["run_id"], ["runs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("run_id", "sequence", name="uq_review_events_run_sequence"),
    )
    op.create_index("ix_review_events_run_id", "review_events", ["run_id"])

    _backfill_review_events()
    _rewrite_evidence_review_events(add_review_events=True)


def downgrade() -> None:
    _rewrite_evidence_review_events(add_review_events=False)
    op.drop_index("ix_review_events_run_id", table_name="review_events")
    op.drop_table("review_events")
