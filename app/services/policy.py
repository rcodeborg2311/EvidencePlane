from __future__ import annotations

from dataclasses import dataclass

from app.models.schemas import Decision, RunReceipt, Violation

POLICY_VERSION = "1.0"

VIOLATION_MESSAGES = {
    "SECRET_PATTERN_DETECTED": "A changed file indicated a detected secret pattern.",
    "FAILED_TESTS": "One or more tests failed.",
    "NO_TEST_EVIDENCE": "Code changed without any passed test evidence.",
    "NETWORK_TOOL_USAGE": "A tool call used network access.",
    "LARGE_CHANGE_ON_PROTECTED_BRANCH": (
        "A protected branch change exceeded the configured line threshold."
    ),
    "OVERRIDE_WITHOUT_APPROVER": "Emergency override was requested without an approver.",
}

_RISK_FOR_DECISION = {"block": 85, "allow": 12}


@dataclass
class PolicySettings:
    failed_tests: str = "block"
    code_without_passing_tests: str = "review"
    network_access: str = "review"
    large_protected_branch_change: str = "review"
    large_change_threshold: int = 500

    def to_snapshot(self) -> dict:
        return {
            "failed_tests": self.failed_tests,
            "code_without_passing_tests": self.code_without_passing_tests,
            "network_access": self.network_access,
            "large_protected_branch_change": self.large_protected_branch_change,
            "large_change_threshold": self.large_change_threshold,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "PolicySettings":
        return cls(
            failed_tests=data.get("failed_tests", "block"),
            code_without_passing_tests=data.get("code_without_passing_tests", "review"),
            network_access=data.get("network_access", "review"),
            large_protected_branch_change=data.get("large_protected_branch_change", "review"),
            large_change_threshold=int(data.get("large_change_threshold", 500)),
        )


DEFAULT_SETTINGS = PolicySettings()


@dataclass(frozen=True)
class PolicyDecision:
    decision: Decision
    risk_score: int
    violations: list[Violation]


def _violation(code: str, severity: str) -> Violation:
    return Violation(code=code, message=VIOLATION_MESSAGES[code], severity=severity)


def _risk(decision: str, fallback: int) -> int:
    return _RISK_FOR_DECISION.get(decision, fallback)


def evaluate_policy(
    receipt: RunReceipt, settings: PolicySettings | None = None
) -> PolicyDecision:
    if settings is None:
        settings = DEFAULT_SETTINGS

    violations: list[Violation] = []

    if any(changed_file.secret_detected for changed_file in receipt.changed_files):
        # secret detection is never configurable — always block
        decision: Decision = "block"
        risk_score = 95
        violations.append(_violation("SECRET_PATTERN_DETECTED", "high"))
    elif any(test.status == "failed" for test in receipt.tests):
        decision = settings.failed_tests  # type: ignore[assignment]
        risk_score = 90 if decision == "block" else _risk(decision, 65)
        violations.append(
            _violation("FAILED_TESTS", "high" if decision == "block" else "medium")
        )
    elif (
        any(cf.classification == "code" for cf in receipt.changed_files)
        and not any(t.status == "passed" for t in receipt.tests)
    ):
        decision = settings.code_without_passing_tests  # type: ignore[assignment]
        risk_score = _risk(decision, 60)
        if decision != "allow":
            violations.append(_violation("NO_TEST_EVIDENCE", "medium"))
    elif any(tool_call.network_access for tool_call in receipt.tool_calls):
        decision = settings.network_access  # type: ignore[assignment]
        risk_score = _risk(decision, 55)
        if decision != "allow":
            violations.append(_violation("NETWORK_TOOL_USAGE", "medium"))
    elif (
        receipt.policy_context.protected_branch
        and sum(
            cf.additions + cf.deletions for cf in receipt.changed_files
        )
        > settings.large_change_threshold
    ):
        decision = settings.large_protected_branch_change  # type: ignore[assignment]
        risk_score = _risk(decision, 70)
        if decision != "allow":
            violations.append(_violation("LARGE_CHANGE_ON_PROTECTED_BRANCH", "medium"))
    else:
        decision = "allow"
        risk_score = 12

    if (
        receipt.policy_context.emergency_override
        and receipt.policy_context.approver_email is None
    ):
        violations.append(_violation("OVERRIDE_WITHOUT_APPROVER", "high"))
        decision = "block"
        risk_score = 100

    return PolicyDecision(
        decision=decision,
        risk_score=risk_score,
        violations=violations,
    )
