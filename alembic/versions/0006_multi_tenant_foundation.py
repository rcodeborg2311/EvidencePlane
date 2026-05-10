"""add multi-tenant foundation: organizations, users, memberships, repositories, evidence_sources, policy_configs

Revision ID: 0006_multi_tenant_foundation
Revises: 0005_immutable_review_events
Create Date: 2026-05-10
"""

from __future__ import annotations

import uuid as _uuid

from alembic import op
import sqlalchemy as sa

revision = "0006_multi_tenant_foundation"
down_revision = "0005_immutable_review_events"
branch_labels = None
depends_on = None

DEFAULT_ORG_ID = _uuid.UUID("00000000-0000-4000-8000-000000000001")
DEFAULT_REPO_ID = _uuid.UUID("00000000-0000-4000-8000-000000000002")
DEFAULT_SOURCE_ID = _uuid.UUID("00000000-0000-4000-8000-000000000003")


def upgrade() -> None:
    # ------------------------------------------------------------------ #
    # organizations
    # ------------------------------------------------------------------ #
    op.create_table(
        "organizations",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("slug", sa.String(100), nullable=False),
        sa.Column("plan", sa.String(32), nullable=False, server_default="pilot"),
        sa.Column("status", sa.String(20), nullable=False, server_default="active"),
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
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("slug", name="uq_organizations_slug"),
    )

    # ------------------------------------------------------------------ #
    # users
    # ------------------------------------------------------------------ #
    op.create_table(
        "users",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.Column("email", sa.String(254), nullable=False),
        sa.Column("display_name", sa.String(200), nullable=True),
        sa.Column("external_identity_id", sa.String(200), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["organization_id"], ["organizations.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "organization_id", "email", name="uq_users_org_email"
        ),
    )

    # ------------------------------------------------------------------ #
    # memberships
    # ------------------------------------------------------------------ #
    op.create_table(
        "memberships",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("role", sa.String(32), nullable=False, server_default="viewer"),
        sa.Column("status", sa.String(20), nullable=False, server_default="active"),
        sa.ForeignKeyConstraint(
            ["organization_id"], ["organizations.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "organization_id", "user_id", name="uq_memberships_org_user"
        ),
    )

    # ------------------------------------------------------------------ #
    # repositories
    # ------------------------------------------------------------------ #
    op.create_table(
        "repositories",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.Column(
            "provider",
            sa.String(32),
            nullable=False,
            server_default="github",
        ),
        sa.Column("owner", sa.String(200), nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("external_id", sa.String(200), nullable=True),
        sa.Column("default_branch", sa.String(200), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"], ["organizations.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "organization_id",
            "provider",
            "owner",
            "name",
            name="uq_repositories_org_provider_owner_name",
        ),
    )

    # ------------------------------------------------------------------ #
    # evidence_sources
    # ------------------------------------------------------------------ #
    op.create_table(
        "evidence_sources",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.Column("repository_id", sa.Uuid(), nullable=True),
        sa.Column("source_type", sa.String(32), nullable=False),
        sa.Column("display_name", sa.String(200), nullable=False),
        sa.Column("signing_secret_ref", sa.String(500), nullable=True),
        sa.Column(
            "trust_level",
            sa.String(32),
            nullable=False,
            server_default="ci_verified",
        ),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"], ["organizations.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["repository_id"], ["repositories.id"], ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id"),
    )

    # ------------------------------------------------------------------ #
    # policy_configs
    # ------------------------------------------------------------------ #
    op.create_table(
        "policy_configs",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.Column("repository_id", sa.Uuid(), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("config_json", sa.JSON(), nullable=False),
        sa.Column("created_by", sa.Uuid(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"], ["organizations.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["repository_id"], ["repositories.id"], ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_policy_configs_org_repo",
        "policy_configs",
        ["organization_id", "repository_id"],
    )

    # ------------------------------------------------------------------ #
    # seed default tenant so existing runs can be migrated
    # ------------------------------------------------------------------ #
    bind = op.get_bind()
    bind.execute(
        sa.text(
            "INSERT INTO organizations (id, name, slug, plan, status) "
            "VALUES (:id, :name, :slug, :plan, :status)"
        ),
        {
            "id": str(DEFAULT_ORG_ID),
            "name": "Default Organization",
            "slug": "default",
            "plan": "pilot",
            "status": "active",
        },
    )
    bind.execute(
        sa.text(
            "INSERT INTO repositories (id, organization_id, provider, owner, name) "
            "VALUES (:id, :org_id, :provider, :owner, :name)"
        ),
        {
            "id": str(DEFAULT_REPO_ID),
            "org_id": str(DEFAULT_ORG_ID),
            "provider": "github",
            "owner": "default",
            "name": "default",
        },
    )
    bind.execute(
        sa.text(
            "INSERT INTO evidence_sources "
            "(id, organization_id, repository_id, source_type, display_name, trust_level, enabled) "
            "VALUES (:id, :org_id, :repo_id, :source_type, :display_name, :trust_level, :enabled)"
        ),
        {
            "id": str(DEFAULT_SOURCE_ID),
            "org_id": str(DEFAULT_ORG_ID),
            "repo_id": str(DEFAULT_REPO_ID),
            "source_type": "github_actions",
            "display_name": "Default GitHub Actions Source",
            "trust_level": "ci_verified",
            "enabled": True,
        },
    )

    # ------------------------------------------------------------------ #
    # add FK columns to runs and evidence_packs (nullable for migration safety)
    # ------------------------------------------------------------------ #
    op.add_column(
        "runs",
        sa.Column("organization_id", sa.Uuid(), nullable=True),
    )
    op.add_column(
        "runs",
        sa.Column("repository_id", sa.Uuid(), nullable=True),
    )
    op.add_column(
        "runs",
        sa.Column("evidence_source_id", sa.Uuid(), nullable=True),
    )
    op.add_column(
        "evidence_packs",
        sa.Column("organization_id", sa.Uuid(), nullable=True),
    )
    op.add_column(
        "review_events",
        sa.Column("organization_id", sa.Uuid(), nullable=True),
    )
    op.add_column(
        "review_events",
        sa.Column("actor_user_id", sa.Uuid(), nullable=True),
    )

    # ------------------------------------------------------------------ #
    # backfill existing rows to default tenant
    # ------------------------------------------------------------------ #
    bind.execute(
        sa.text(
            "UPDATE runs SET organization_id = :org_id, "
            "repository_id = :repo_id, evidence_source_id = :source_id "
            "WHERE organization_id IS NULL"
        ),
        {
            "org_id": str(DEFAULT_ORG_ID),
            "repo_id": str(DEFAULT_REPO_ID),
            "source_id": str(DEFAULT_SOURCE_ID),
        },
    )
    bind.execute(
        sa.text(
            "UPDATE evidence_packs SET organization_id = :org_id "
            "WHERE organization_id IS NULL"
        ),
        {"org_id": str(DEFAULT_ORG_ID)},
    )
    bind.execute(
        sa.text(
            "UPDATE review_events SET organization_id = :org_id "
            "WHERE organization_id IS NULL"
        ),
        {"org_id": str(DEFAULT_ORG_ID)},
    )

    # ------------------------------------------------------------------ #
    # add FK constraints now that all rows are backfilled
    # ------------------------------------------------------------------ #
    op.create_foreign_key(
        "fk_runs_organization_id",
        "runs",
        "organizations",
        ["organization_id"],
        ["id"],
    )
    op.create_foreign_key(
        "fk_runs_repository_id",
        "runs",
        "repositories",
        ["repository_id"],
        ["id"],
    )
    op.create_foreign_key(
        "fk_runs_evidence_source_id",
        "runs",
        "evidence_sources",
        ["evidence_source_id"],
        ["id"],
    )
    op.create_foreign_key(
        "fk_evidence_packs_organization_id",
        "evidence_packs",
        "organizations",
        ["organization_id"],
        ["id"],
    )
    op.create_foreign_key(
        "fk_review_events_organization_id",
        "review_events",
        "organizations",
        ["organization_id"],
        ["id"],
    )

    op.create_index("ix_runs_organization_id", "runs", ["organization_id"])
    op.create_index("ix_runs_repository_id", "runs", ["repository_id"])
    op.create_index(
        "ix_evidence_packs_organization_id", "evidence_packs", ["organization_id"]
    )


def downgrade() -> None:
    op.drop_index("ix_evidence_packs_organization_id", table_name="evidence_packs")
    op.drop_index("ix_runs_repository_id", table_name="runs")
    op.drop_index("ix_runs_organization_id", table_name="runs")

    op.drop_constraint(
        "fk_review_events_organization_id", "review_events", type_="foreignkey"
    )
    op.drop_constraint(
        "fk_evidence_packs_organization_id", "evidence_packs", type_="foreignkey"
    )
    op.drop_constraint(
        "fk_runs_evidence_source_id", "runs", type_="foreignkey"
    )
    op.drop_constraint("fk_runs_repository_id", "runs", type_="foreignkey")
    op.drop_constraint("fk_runs_organization_id", "runs", type_="foreignkey")

    op.drop_column("review_events", "actor_user_id")
    op.drop_column("review_events", "organization_id")
    op.drop_column("evidence_packs", "organization_id")
    op.drop_column("runs", "evidence_source_id")
    op.drop_column("runs", "repository_id")
    op.drop_column("runs", "organization_id")

    op.drop_index("ix_policy_configs_org_repo", table_name="policy_configs")
    op.drop_table("policy_configs")
    op.drop_table("evidence_sources")
    op.drop_table("repositories")
    op.drop_table("memberships")
    op.drop_table("users")
    op.drop_table("organizations")
