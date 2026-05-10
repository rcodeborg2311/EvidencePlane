from __future__ import annotations

from datetime import datetime, timezone
import uuid

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, JSON, String, UniqueConstraint, Uuid
from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


jsonb_type = JSON().with_variant(postgresql.JSONB(), "postgresql")


class Base(DeclarativeBase):
    pass


class Organization(Base):
    __tablename__ = "organizations"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    slug: Mapped[str] = mapped_column(String(100), nullable=False, unique=True)
    plan: Mapped[str] = mapped_column(String(32), nullable=False, default="pilot")
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="active")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now, onupdate=utc_now
    )

    users: Mapped[list[User]] = relationship("User", back_populates="organization")
    repositories: Mapped[list[Repository]] = relationship(
        "Repository", back_populates="organization"
    )
    evidence_sources: Mapped[list[EvidenceSource]] = relationship(
        "EvidenceSource", back_populates="organization"
    )


class User(Base):
    __tablename__ = "users"
    __table_args__ = (
        UniqueConstraint("organization_id", "email", name="uq_users_org_email"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    organization_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    email: Mapped[str] = mapped_column(String(254), nullable=False)
    display_name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    external_identity_id: Mapped[str | None] = mapped_column(String(200), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )
    last_seen_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    organization: Mapped[Organization] = relationship(
        "Organization", back_populates="users"
    )
    memberships: Mapped[list[Membership]] = relationship(
        "Membership", back_populates="user"
    )


class Membership(Base):
    __tablename__ = "memberships"
    __table_args__ = (
        UniqueConstraint(
            "organization_id", "user_id", name="uq_memberships_org_user"
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    organization_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    role: Mapped[str] = mapped_column(String(32), nullable=False, default="viewer")
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="active")

    user: Mapped[User] = relationship("User", back_populates="memberships")


class Repository(Base):
    __tablename__ = "repositories"
    __table_args__ = (
        UniqueConstraint(
            "organization_id",
            "provider",
            "owner",
            "name",
            name="uq_repositories_org_provider_owner_name",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    organization_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    provider: Mapped[str] = mapped_column(String(32), nullable=False, default="github")
    owner: Mapped[str] = mapped_column(String(200), nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    external_id: Mapped[str | None] = mapped_column(String(200), nullable=True)
    default_branch: Mapped[str | None] = mapped_column(String(200), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )

    organization: Mapped[Organization] = relationship(
        "Organization", back_populates="repositories"
    )
    evidence_sources: Mapped[list[EvidenceSource]] = relationship(
        "EvidenceSource", back_populates="repository"
    )


class EvidenceSource(Base):
    __tablename__ = "evidence_sources"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    organization_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    repository_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("repositories.id", ondelete="SET NULL"), nullable=True
    )
    source_type: Mapped[str] = mapped_column(String(32), nullable=False)
    display_name: Mapped[str] = mapped_column(String(200), nullable=False)
    signing_secret_ref: Mapped[str | None] = mapped_column(String(500), nullable=True)
    trust_level: Mapped[str] = mapped_column(
        String(32), nullable=False, default="ci_verified"
    )
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    last_seen_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )

    organization: Mapped[Organization] = relationship(
        "Organization", back_populates="evidence_sources"
    )
    repository: Mapped[Repository | None] = relationship(
        "Repository", back_populates="evidence_sources"
    )


class PolicyConfig(Base):
    __tablename__ = "policy_configs"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    organization_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    repository_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("repositories.id", ondelete="SET NULL"), nullable=True
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    config_json: Mapped[dict] = mapped_column(jsonb_type, nullable=False)
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )


class Run(Base):
    __tablename__ = "runs"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    organization_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("organizations.id"), nullable=True, index=True
    )
    repository_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("repositories.id"), nullable=True, index=True
    )
    evidence_source_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("evidence_sources.id"), nullable=True
    )
    idempotency_key: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    payload_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    repo_name: Mapped[str] = mapped_column(String(200), nullable=False)
    commit_sha: Mapped[str] = mapped_column(String(40), nullable=False)
    branch: Mapped[str] = mapped_column(String(200), nullable=False)
    actor: Mapped[str] = mapped_column(String(200), nullable=False)
    timestamp_utc: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    decision: Mapped[str] = mapped_column(String(16), nullable=False)
    policy_version: Mapped[str] = mapped_column(String(32), nullable=False, default="1.0")
    risk_score: Mapped[int] = mapped_column(Integer, nullable=False)
    review_status: Mapped[str] = mapped_column(String(20), nullable=False)
    review_outcome: Mapped[str | None] = mapped_column(String(20), nullable=True)
    reviewer_identity: Mapped[str | None] = mapped_column(String(200), nullable=True)
    review_note: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    reviewed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    evidence_pack_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    evidence_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )
    normalized_input: Mapped[dict] = mapped_column(jsonb_type, nullable=False)
    changed_files: Mapped[list] = mapped_column(jsonb_type, nullable=False)
    tests: Mapped[list] = mapped_column(jsonb_type, nullable=False)
    tool_calls: Mapped[list] = mapped_column(jsonb_type, nullable=False)
    policy_context: Mapped[dict] = mapped_column(jsonb_type, nullable=False)

    violations: Mapped[list[ViolationRecord]] = relationship(
        "ViolationRecord",
        back_populates="run",
        cascade="all, delete-orphan",
        order_by="ViolationRecord.sort_order",
    )
    evidence_pack: Mapped[EvidencePack] = relationship(
        "EvidencePack",
        back_populates="run",
        cascade="all, delete-orphan",
        uselist=False,
    )
    review_events: Mapped[list[ReviewEvent]] = relationship(
        "ReviewEvent",
        back_populates="run",
        cascade="all, delete-orphan",
        order_by="ReviewEvent.sequence",
    )


class ViolationRecord(Base):
    __tablename__ = "violations"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    run_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("runs.id", ondelete="CASCADE"), nullable=False
    )
    code: Mapped[str] = mapped_column(String(80), nullable=False)
    message: Mapped[str] = mapped_column(String(500), nullable=False)
    severity: Mapped[str] = mapped_column(String(16), nullable=False)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False)

    run: Mapped[Run] = relationship("Run", back_populates="violations")


class EvidencePack(Base):
    __tablename__ = "evidence_packs"
    __table_args__ = (UniqueConstraint("run_id", name="uq_evidence_packs_run_id"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    organization_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("organizations.id"), nullable=True, index=True
    )
    run_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("runs.id", ondelete="CASCADE"), nullable=False
    )
    body: Mapped[dict] = mapped_column(jsonb_type, nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )

    run: Mapped[Run] = relationship("Run", back_populates="evidence_pack")


class ReviewEvent(Base):
    __tablename__ = "review_events"
    __table_args__ = (
        UniqueConstraint("run_id", "sequence", name="uq_review_events_run_sequence"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    organization_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("organizations.id"), nullable=True
    )
    actor_user_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("users.id"), nullable=True
    )
    run_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("runs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    action: Mapped[str] = mapped_column(String(20), nullable=False)
    actor: Mapped[str] = mapped_column(String(200), nullable=False)
    reason: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    previous_event_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    event_sha256: Mapped[str] = mapped_column(String(64), nullable=False)

    run: Mapped[Run] = relationship("Run", back_populates="review_events")
