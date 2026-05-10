"""GitHub App v1: installations, repositories, pull requests, check runs, webhook deliveries, repo settings

Revision ID: 0008_github_app_v1
Revises: 0007_add_sessions
Create Date: 2026-05-10
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "0008_github_app_v1"
down_revision = "0007_add_sessions"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "github_installations",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("organization_id", sa.Uuid(), nullable=True),
        sa.Column("installation_id", sa.BigInteger(), nullable=False),
        sa.Column("account_login", sa.String(200), nullable=False),
        sa.Column("account_type", sa.String(32), nullable=False),
        sa.Column(
            "installed_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("suspended_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("permissions_json", sa.JSON(), nullable=False),
        sa.ForeignKeyConstraint(
            ["organization_id"], ["organizations.id"], ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "installation_id", name="uq_github_installations_installation_id"
        ),
    )
    op.create_index(
        "ix_github_installations_installation_id",
        "github_installations",
        ["installation_id"],
    )

    op.create_table(
        "github_repositories",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("installation_id", sa.BigInteger(), nullable=False),
        sa.Column("organization_id", sa.Uuid(), nullable=True),
        sa.Column("github_repo_id", sa.BigInteger(), nullable=False),
        sa.Column("owner", sa.String(200), nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("full_name", sa.String(401), nullable=False),
        sa.Column("default_branch", sa.String(200), nullable=True),
        sa.Column("private", sa.Boolean(), nullable=False, server_default="0"),
        sa.Column("linked_repository_id", sa.Uuid(), nullable=True),
        sa.ForeignKeyConstraint(
            ["linked_repository_id"], ["repositories.id"], ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "github_repo_id", name="uq_github_repositories_github_repo_id"
        ),
    )
    op.create_index(
        "ix_github_repositories_full_name", "github_repositories", ["full_name"]
    )
    op.create_index(
        "ix_github_repositories_installation_id",
        "github_repositories",
        ["installation_id"],
    )

    op.create_table(
        "github_pull_requests",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("github_repo_id", sa.BigInteger(), nullable=False),
        sa.Column("github_pr_id", sa.BigInteger(), nullable=False),
        sa.Column("number", sa.Integer(), nullable=False),
        sa.Column("head_sha", sa.String(40), nullable=False),
        sa.Column("base_branch", sa.String(200), nullable=False),
        sa.Column("author_login", sa.String(200), nullable=True),
        sa.Column("url", sa.String(500), nullable=False),
        sa.Column("state", sa.String(20), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("github_pr_id", name="uq_github_pull_requests_github_pr_id"),
    )
    op.create_index(
        "ix_github_pull_requests_head_sha", "github_pull_requests", ["head_sha"]
    )

    op.create_table(
        "github_check_runs",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("run_id", sa.Uuid(), nullable=True),
        sa.Column("github_repo_id", sa.BigInteger(), nullable=False),
        sa.Column("installation_id", sa.BigInteger(), nullable=False),
        sa.Column("external_check_run_id", sa.BigInteger(), nullable=True),
        sa.Column("head_sha", sa.String(40), nullable=False),
        sa.Column("pull_number", sa.Integer(), nullable=True),
        sa.Column("status", sa.String(20), nullable=False, server_default="queued"),
        sa.Column("conclusion", sa.String(20), nullable=True),
        sa.Column("html_url", sa.String(500), nullable=True),
        sa.Column("pr_comment_id", sa.BigInteger(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.ForeignKeyConstraint(["run_id"], ["runs.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_github_check_runs_head_sha", "github_check_runs", ["head_sha"]
    )
    op.create_index(
        "ix_github_check_runs_github_repo_id", "github_check_runs", ["github_repo_id"]
    )

    op.create_table(
        "github_webhook_deliveries",
        sa.Column("delivery_guid", sa.String(64), nullable=False),
        sa.Column("installation_id", sa.BigInteger(), nullable=True),
        sa.Column("event_type", sa.String(80), nullable=False),
        sa.Column(
            "status", sa.String(20), nullable=False, server_default="received"
        ),
        sa.Column(
            "received_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("processed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error_message", sa.String(500), nullable=True),
        sa.PrimaryKeyConstraint("delivery_guid"),
    )

    op.create_table(
        "repo_settings",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("repository_id", sa.Uuid(), nullable=False),
        sa.Column(
            "enforce_block", sa.Boolean(), nullable=False, server_default="0"
        ),
        sa.Column(
            "enforce_review", sa.Boolean(), nullable=False, server_default="0"
        ),
        sa.Column("policy_config_id", sa.Uuid(), nullable=True),
        sa.Column(
            "feedback_mode",
            sa.String(32),
            nullable=False,
            server_default="comment_and_check",
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.ForeignKeyConstraint(
            ["repository_id"], ["repositories.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["policy_config_id"], ["policy_configs.id"], ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("repository_id", name="uq_repo_settings_repository_id"),
    )


def downgrade() -> None:
    op.drop_table("repo_settings")
    op.drop_index(
        "ix_github_check_runs_github_repo_id", table_name="github_check_runs"
    )
    op.drop_index("ix_github_check_runs_head_sha", table_name="github_check_runs")
    op.drop_table("github_check_runs")
    op.drop_table("github_webhook_deliveries")
    op.drop_index(
        "ix_github_pull_requests_head_sha", table_name="github_pull_requests"
    )
    op.drop_table("github_pull_requests")
    op.drop_index(
        "ix_github_repositories_installation_id", table_name="github_repositories"
    )
    op.drop_index(
        "ix_github_repositories_full_name", table_name="github_repositories"
    )
    op.drop_table("github_repositories")
    op.drop_index(
        "ix_github_installations_installation_id", table_name="github_installations"
    )
    op.drop_table("github_installations")
