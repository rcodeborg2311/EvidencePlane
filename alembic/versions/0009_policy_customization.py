"""Policy customization: add repo_name and is_active to policy_configs

Revision ID: 0009_policy_customization
Revises: 0008_github_app_v1
Create Date: 2026-05-10
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "0009_policy_customization"
down_revision = "0008_github_app_v1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "policy_configs",
        sa.Column("repo_name", sa.String(200), nullable=True),
    )
    op.add_column(
        "policy_configs",
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default="true"),
    )
    op.create_index(
        "ix_policy_configs_repo_name", "policy_configs", ["repo_name"]
    )


def downgrade() -> None:
    op.drop_index("ix_policy_configs_repo_name", table_name="policy_configs")
    op.drop_column("policy_configs", "is_active")
    op.drop_column("policy_configs", "repo_name")
