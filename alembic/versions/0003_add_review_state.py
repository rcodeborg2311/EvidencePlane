"""add review state

Revision ID: 0003_add_review_state
Revises: 0002_add_policy_version
Create Date: 2026-05-07 00:00:02.000000
"""

from __future__ import annotations

from copy import deepcopy
import hashlib
import json
from typing import Any

from alembic import op
import sqlalchemy as sa

revision = "0003_add_review_state"
down_revision = "0002_add_policy_version"
branch_labels = None
depends_on = None


def _canonical_json(data: Any) -> str:
    return json.dumps(data, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _evidence_sha256(body: dict[str, Any]) -> str:
    hash_body = deepcopy(body)
    hash_body.pop("evidence_sha256", None)
    return hashlib.sha256(_canonical_json(hash_body).encode("utf-8")).hexdigest()


def _runs_table() -> sa.Table:
    metadata = sa.MetaData()
    return sa.Table(
        "runs",
        metadata,
        sa.Column("id", sa.Uuid()),
        sa.Column("decision", sa.String(length=16)),
        sa.Column("review_status", sa.String(length=20)),
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


def _rewrite_evidence_review_fields(*, add_review_fields: bool) -> None:
    bind = op.get_bind()
    runs = _runs_table()
    evidence_packs = _evidence_table()
    rows = bind.execute(
        sa.select(
            runs.c.id,
            runs.c.decision,
            runs.c.review_status,
            runs.c.review_outcome,
            runs.c.reviewer_identity,
            runs.c.review_note,
            runs.c.reviewed_at,
            evidence_packs.c.body,
        ).join(evidence_packs, evidence_packs.c.run_id == runs.c.id)
    ).mappings()

    for row in rows:
        body = dict(row["body"])
        if add_review_fields:
            review_status = row["review_status"] or (
                "pending" if row["decision"] == "review" else "not_required"
            )
            body["review_status"] = review_status
            body["review_outcome"] = row["review_outcome"]
            body["reviewer_identity"] = row["reviewer_identity"]
            body["review_note"] = row["review_note"]
            body["reviewed_at"] = (
                row["reviewed_at"].isoformat().replace("+00:00", "Z")
                if row["reviewed_at"]
                else None
            )
        else:
            body.pop("review_status", None)
            body.pop("review_outcome", None)
            body.pop("reviewer_identity", None)
            body.pop("review_note", None)
            body.pop("reviewed_at", None)

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
    with op.batch_alter_table("runs") as batch_op:
        batch_op.add_column(sa.Column("review_status", sa.String(length=20)))
        batch_op.add_column(sa.Column("review_outcome", sa.String(length=20)))
        batch_op.add_column(sa.Column("reviewer_identity", sa.String(length=200)))
        batch_op.add_column(sa.Column("review_note", sa.String(length=1000)))
        batch_op.add_column(sa.Column("reviewed_at", sa.DateTime(timezone=True)))

    op.execute(
        "UPDATE runs SET review_status = "
        "CASE WHEN decision = 'review' THEN 'pending' ELSE 'not_required' END"
    )

    with op.batch_alter_table("runs") as batch_op:
        batch_op.alter_column(
            "review_status",
            existing_type=sa.String(length=20),
            nullable=False,
        )

    _rewrite_evidence_review_fields(add_review_fields=True)


def downgrade() -> None:
    _rewrite_evidence_review_fields(add_review_fields=False)
    with op.batch_alter_table("runs") as batch_op:
        batch_op.drop_column("reviewed_at")
        batch_op.drop_column("review_note")
        batch_op.drop_column("reviewer_identity")
        batch_op.drop_column("review_outcome")
        batch_op.drop_column("review_status")
