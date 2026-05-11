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


class PullRequestRef(StrictModel):
    provider: Literal["github", "gitlab", "bitbucket", "azure_devops"] = "github"
    number: int = Field(ge=1)
    url: str = Field(min_length=1, max_length=500)
    head_sha: str = Field(pattern=r"^[0-9a-f]{40}$")
    base_branch: str = Field(min_length=1, max_length=200)


class ScannerResult(StrictModel):
    scanner: str = Field(min_length=1, max_length=100)
    finding_type: Literal["secret", "vulnerability", "sast", "license", "other"]
    severity: Severity
    path: str | None = None
    message: str = Field(min_length=1, max_length=1000)
    rule_id: str | None = None


class ArtifactRef(StrictModel):
    name: str = Field(min_length=1, max_length=200)
    url: str = Field(min_length=1, max_length=500)
    artifact_type: Literal["test_report", "coverage", "sast_report", "build_log", "other"] = "other"
    sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")


class RunReceipt(StrictModel):
    # v1 fields — always required
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
    # v2 fields — optional, backward-compatible
    schema_version: str = Field(default="1.0", max_length=16)
    source_id: str | None = Field(default=None, max_length=64)
    source_run_url: str | None = Field(default=None, max_length=500)
    pull_request: PullRequestRef | None = None
    scanner_results: list[ScannerResult] = Field(default_factory=list)
    artifact_refs: list[ArtifactRef] = Field(default_factory=list)

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


# --------------------------------------------------------------------------- #
# Policy config schemas
# --------------------------------------------------------------------------- #

_VALID_DECISIONS = {"allow", "review", "block"}
_BLOCK_OR_REVIEW = {"block", "review"}


class PolicyConfigPayload(BaseModel):
    failed_tests: str = Field(default="block")
    code_without_passing_tests: str = Field(default="review")
    network_access: str = Field(default="review")
    large_protected_branch_change: str = Field(default="review")
    large_change_threshold: int = Field(default=500, ge=1, le=100_000)

    @field_validator("failed_tests")
    @classmethod
    def validate_failed_tests(cls, v: str) -> str:
        if v not in _BLOCK_OR_REVIEW:
            raise ValueError("failed_tests must be 'block' or 'review'")
        return v

    @field_validator("code_without_passing_tests", "network_access", "large_protected_branch_change")
    @classmethod
    def validate_three_way(cls, v: str) -> str:
        if v not in _VALID_DECISIONS:
            raise ValueError("must be 'allow', 'review', or 'block'")
        return v


class PolicyConfigRequest(BaseModel):
    repo_name: str | None = Field(default=None, max_length=200)
    config: PolicyConfigPayload = Field(default_factory=PolicyConfigPayload)


class PolicyConfigResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    config_id: UUID
    repo_name: str | None
    version: int
    is_active: bool
    config: dict
    created_at: datetime


class DryRunChange(BaseModel):
    run_id: UUID
    repo_name: str
    old_decision: str
    new_decision: str
    violations_added: list[str]
    violations_removed: list[str]


class DryRunRequest(BaseModel):
    repo_name: str = Field(min_length=1, max_length=200)
    config: PolicyConfigPayload = Field(default_factory=PolicyConfigPayload)
    limit: int = Field(default=50, ge=1, le=500)


class DryRunResponse(BaseModel):
    repo_name: str
    total_runs: int
    would_change: int
    changes: list[DryRunChange]


# --------------------------------------------------------------------------- #
# Evidence source schemas
# --------------------------------------------------------------------------- #

SOURCE_TYPES = {"github_actions", "gitlab_ci", "jenkins", "buildkite", "circleci", "azure_devops", "agent_wrapper", "mcp_adapter", "other"}
TRUST_LEVELS = {"ci_verified", "agent_managed", "human_submitted"}


class EvidenceSourceRequest(BaseModel):
    source_type: str = Field(min_length=1, max_length=32)
    display_name: str = Field(min_length=1, max_length=200)
    signing_secret: str | None = Field(default=None, min_length=16, max_length=256)
    trust_level: str = Field(default="ci_verified")
    repo_name: str | None = Field(default=None, max_length=200)

    @field_validator("source_type")
    @classmethod
    def validate_source_type(cls, v: str) -> str:
        if v not in SOURCE_TYPES:
            raise ValueError(f"source_type must be one of: {', '.join(sorted(SOURCE_TYPES))}")
        return v

    @field_validator("trust_level")
    @classmethod
    def validate_trust_level(cls, v: str) -> str:
        if v not in TRUST_LEVELS:
            raise ValueError(f"trust_level must be one of: {', '.join(sorted(TRUST_LEVELS))}")
        return v


class EvidenceSourceResponse(BaseModel):
    source_id: UUID
    source_type: str
    display_name: str
    trust_level: str
    enabled: bool
    repo_name: str | None
    last_seen_at: datetime | None
    created_at: datetime


# --------------------------------------------------------------------------- #
# Case Room schemas
# --------------------------------------------------------------------------- #

CASE_STATUSES = {"open", "resolved", "dismissed"}
CASE_TYPES = {"review_required", "blocked_change"}
CASE_SEVERITIES = {"low", "medium", "high"}
LINK_PROVIDERS = {"slack", "jira", "linear", "github", "azure_devops", "other"}
LINK_TYPES = {"issue", "thread", "pr", "page", "other"}


class CaseMessageRequest(BaseModel):
    body: str = Field(min_length=1, max_length=5000)
    author_identity: str = Field(min_length=1, max_length=200)


class CaseExternalLinkRequest(BaseModel):
    provider: str = Field(min_length=1, max_length=32)
    url: str = Field(min_length=1, max_length=500)
    external_id: str | None = Field(default=None, max_length=200)
    link_type: str = Field(default="issue", max_length=32)
    created_by: str | None = Field(default=None, max_length=200)

    @field_validator("provider")
    @classmethod
    def validate_provider(cls, v: str) -> str:
        if v not in LINK_PROVIDERS:
            raise ValueError(f"provider must be one of: {', '.join(sorted(LINK_PROVIDERS))}")
        return v

    @field_validator("link_type")
    @classmethod
    def validate_link_type(cls, v: str) -> str:
        if v not in LINK_TYPES:
            raise ValueError(f"link_type must be one of: {', '.join(sorted(LINK_TYPES))}")
        return v


class CaseResolveRequest(BaseModel):
    resolved_by: str = Field(min_length=1, max_length=200)
    resolution_note: str | None = Field(default=None, max_length=1000)


class CaseMessageResponse(BaseModel):
    message_id: UUID
    author_identity: str
    body: str
    message_sha256: str
    created_at: datetime


class CaseEventResponse(BaseModel):
    event_id: UUID
    event_type: str
    actor_identity: str
    payload: dict | None
    sequence: int
    previous_event_sha256: str | None
    event_sha256: str
    created_at: datetime


class CaseExternalLinkResponse(BaseModel):
    link_id: UUID
    provider: str
    external_id: str | None
    url: str
    link_type: str
    created_by: str | None
    created_at: datetime


class CaseResponse(BaseModel):
    case_id: UUID
    run_id: UUID
    status: str
    case_type: str
    severity: str
    title: str
    summary: str | None
    resolved_by: str | None
    resolution_note: str | None
    created_at: datetime
    resolved_at: datetime | None
    messages: list[CaseMessageResponse] = Field(default_factory=list)
    events: list[CaseEventResponse] = Field(default_factory=list)
    external_links: list[CaseExternalLinkResponse] = Field(default_factory=list)


class CaseSummary(BaseModel):
    case_id: UUID
    run_id: UUID
    status: str
    case_type: str
    severity: str
    title: str
    created_at: datetime
    resolved_at: datetime | None


# --------------------------------------------------------------------------- #
# Advisor layer schemas
# --------------------------------------------------------------------------- #

class AdvisorFindingResponse(BaseModel):
    finding_id: UUID
    case_id: UUID
    model_provider: str
    model_version: str
    prompt_sha256: str
    verdict_explanation: str
    suggested_fixes: list[dict]
    draft_resolution_note: str
    is_advisory: bool
    advisory_warning: str
    created_at: datetime
