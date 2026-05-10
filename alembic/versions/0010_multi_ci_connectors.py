"""Stage 4: multi-CI connector support - evidence source repo_name, run v2 fields

Revision ID: 0010_multi_ci_connectors
Revises: 0009_policy_customization
Create Date: 2026-05-10
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0010_multi_ci_connectors"
down_revision = "0009_policy_customization"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # evidence_sources: add repo_name for per-repo source scoping
    op.add_column(
        "evidence_sources",
        sa.Column("repo_name", sa.String(200), nullable=True),
    )
    op.create_index("ix_evidence_sources_repo_name", "evidence_sources", ["repo_name"])

    # runs: add v2 receipt fields
    op.add_column("runs", sa.Column("schema_version", sa.String(16), nullable=False, server_default="1.0"))
    op.add_column("runs", sa.Column("source_run_url", sa.String(500), nullable=True))
    op.add_column("runs", sa.Column("pull_request_json", sa.JSON(), nullable=True))
    op.add_column("runs", sa.Column("scanner_results_json", sa.JSON(), nullable=True))
    op.add_column("runs", sa.Column("artifact_refs_json", sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column("runs", "artifact_refs_json")
    op.drop_column("runs", "scanner_results_json")
    op.drop_column("runs", "pull_request_json")
    op.drop_column("runs", "source_run_url")
    op.drop_column("runs", "schema_version")
    op.drop_index("ix_evidence_sources_repo_name", table_name="evidence_sources")
    op.drop_column("evidence_sources", "repo_name")
