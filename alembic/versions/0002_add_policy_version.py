"""add policy version

Revision ID: 0002_add_policy_version
Revises: 0001_create_evidenceplane_tables
Create Date: 2026-05-07 00:00:01.000000
"""

from __future__ import annotations

from copy import deepcopy
import hashlib
import json
from typing import Any

from alembic import op
import sqlalchemy as sa

revision = "0002_add_policy_version"
down_revision = "0001_create_evidenceplane_tables"
branch_labels = None
depends_on = None

POLICY_VERSION = "1.0"


def _canonical_json(data: Any) -> str:
    return json.dumps(data, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _evidence_sha256(body: dict[str, Any]) -> str:
    hash_body = deepcopy(body)
    hash_body.pop("evidence_sha256", None)
    return hashlib.sha256(_canonical_json(hash_body).encode("utf-8")).hexdigest()


def _evidence_table() -> sa.Table:
    metadata = sa.MetaData()
    return sa.Table(
        "evidence_packs",
        metadata,
        sa.Column("id", sa.Uuid()),
        sa.Column("body", sa.JSON()),
        sa.Column("sha256", sa.String(length=64)),
    )


def _rewrite_evidence_bodies(*, add_policy_version: bool) -> None:
    bind = op.get_bind()
    evidence_packs = _evidence_table()
    rows = bind.execute(
        sa.select(evidence_packs.c.id, evidence_packs.c.body)
    ).mappings()
    for row in rows:
        body = dict(row["body"])
        if add_policy_version:
            body.setdefault("policy_version", POLICY_VERSION)
        else:
            body.pop("policy_version", None)
        body["evidence_sha256"] = _evidence_sha256(body)
        bind.execute(
            evidence_packs.update()
            .where(evidence_packs.c.id == row["id"])
            .values(body=body, sha256=body["evidence_sha256"])
        )


def upgrade() -> None:
    op.add_column(
        "runs",
        sa.Column(
            "policy_version",
            sa.String(length=32),
            nullable=False,
            server_default=POLICY_VERSION,
        ),
    )
    _rewrite_evidence_bodies(add_policy_version=True)
    op.alter_column("runs", "policy_version", server_default=None)


def downgrade() -> None:
    _rewrite_evidence_bodies(add_policy_version=False)
    op.drop_column("runs", "policy_version")
