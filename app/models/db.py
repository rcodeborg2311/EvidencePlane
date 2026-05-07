from __future__ import annotations

from datetime import datetime, timezone
import uuid

from sqlalchemy import DateTime, ForeignKey, Integer, JSON, String, UniqueConstraint, Uuid
from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


jsonb_type = JSON().with_variant(postgresql.JSONB(), "postgresql")


class Base(DeclarativeBase):
    """Base class for SQLAlchemy ORM models."""


class Run(Base):
    __tablename__ = "runs"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    idempotency_key: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    payload_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    repo_name: Mapped[str] = mapped_column(String(200), nullable=False)
    commit_sha: Mapped[str] = mapped_column(String(40), nullable=False)
    branch: Mapped[str] = mapped_column(String(200), nullable=False)
    actor: Mapped[str] = mapped_column(String(200), nullable=False)
    timestamp_utc: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    decision: Mapped[str] = mapped_column(String(16), nullable=False)
    risk_score: Mapped[int] = mapped_column(Integer, nullable=False)
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
    run_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("runs.id", ondelete="CASCADE"), nullable=False
    )
    body: Mapped[dict] = mapped_column(jsonb_type, nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )

    run: Mapped[Run] = relationship("Run", back_populates="evidence_pack")
