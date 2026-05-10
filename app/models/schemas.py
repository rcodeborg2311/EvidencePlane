from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

Decision = Literal["allow", "review", "block"]
Severity = Literal["low", "medium", "high"]
ReviewStatus = Literal["not_required", "pending", "approved", "rejected"]
ReviewOutcome = Literal["approved", "rejected"]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ChangedFile(StrictModel):
    path: str
    classification: Literal["code", "config", "docs", "binary"]
    additions: int = Field(ge=0)
    deletions: int = Field(ge=0)
    secret_detected: bool


class TestResult(StrictModel):
    name: str
    status: Literal["passed", "failed", "skipped"]


class ToolCall(StrictModel):
    tool: str
    command: str
    network_access: bool
    exit_code: int


class PolicyContext(StrictModel):
    protected_branch: bool
    emergency_override: bool
    approver_email: str | None


class RunReceipt(StrictModel):
    idempotency_key: str = Field(min_length=1, max_length=64)
    repo_name: str = Field(min_length=1, max_length=200)
    commit_sha: str = Field(pattern=r"^[0-9a-f]{40}$")
    branch: str = Field(min_length=1, max_length=200)
    actor: str = Field(min_length=1, max_length=200)
    timestamp_utc: datetime
    changed_files: list[ChangedFile] = Field(min_length=1)
    tests: list[TestResult]
    tool_calls: list[ToolCall]
    policy_context: PolicyContext

    @field_validator("timestamp_utc")
    @classmethod
    def timestamp_must_be_timezone_aware(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("timestamp_utc must be timezone-aware")
        return value


class Violation(StrictModel):
    code: str
    message: str
    severity: Severity


class ReviewEventSummary(StrictModel):
    event_id: UUID
    sequence: int = Field(ge=1)
    action: ReviewOutcome
    actor: str
    reason: str | None
    created_at: datetime
    previous_event_sha256: str | None
    event_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class DecisionResponse(StrictModel):
    run_id: UUID
    decision: Decision
    policy_version: str = Field(min_length=1, max_length=32)
    risk_score: int = Field(ge=0, le=100)
    review_status: ReviewStatus
    review_outcome: ReviewOutcome | None
    reviewer_identity: str | None
    review_note: str | None
    reviewed_at: datetime | None
    violations: list[Violation]
    evidence_pack_id: UUID
    evidence_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    created_at: datetime


class RunSummary(StrictModel):
    run_id: UUID
    decision: Decision
    policy_version: str = Field(min_length=1, max_length=32)
    risk_score: int = Field(ge=0, le=100)
    review_status: ReviewStatus
    review_outcome: ReviewOutcome | None
    reviewer_identity: str | None
    review_note: str | None
    reviewed_at: datetime | None
    repo_name: str
    branch: str
    actor: str
    timestamp_utc: datetime
    evidence_sha256: str
    created_at: datetime


class RunDetail(RunSummary):
    idempotency_key: str
    commit_sha: str
    changed_files: list[ChangedFile]
    tests: list[TestResult]
    tool_calls: list[ToolCall]
    policy_context: PolicyContext
    violations: list[Violation]
    evidence_pack_id: UUID
    review_events: list[ReviewEventSummary]


class ReviewRequest(StrictModel):
    reviewer_identity: str = Field(min_length=1, max_length=200)
    review_note: str | None = Field(default=None, max_length=1000)
