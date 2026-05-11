"""Stage 6: Advisor layer — advisor_findings table

Revision ID: 0012_advisor_findings
Revises: 0011_case_rooms
Create Date: 2026-05-10
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0012_advisor_findings"
down_revision = "0011_case_rooms"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "advisor_findings",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("case_id", sa.Uuid(), nullable=False),
        sa.Column("model_provider", sa.String(64), nullable=False),
        sa.Column("model_version", sa.String(64), nullable=False),
        sa.Column("prompt_sha256", sa.String(64), nullable=False),
        sa.Column("output_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["case_id"], ["cases.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_advisor_findings_case_id", "advisor_findings", ["case_id"])


def downgrade() -> None:
    op.drop_table("advisor_findings")
