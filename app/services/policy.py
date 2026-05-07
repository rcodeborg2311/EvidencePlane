from __future__ import annotations

from dataclasses import dataclass

from app.models.schemas import Decision, RunReceipt, Violation

VIOLATION_MESSAGES = {
    "SECRET_PATTERN_DETECTED": "A changed file indicated a detected secret pattern.",
    "FAILED_TESTS": "One or more tests failed.",
    "NO_TEST_EVIDENCE": "Code changed without any passed test evidence.",
    "NETWORK_TOOL_USAGE": "A tool call used network access.",
    "LARGE_CHANGE_ON_PROTECTED_BRANCH": (
        "A protected branch change exceeded 500 added and deleted lines."
    ),
    "OVERRIDE_WITHOUT_APPROVER": "Emergency override was requested without an approver.",
}


@dataclass(frozen=True)
class PolicyDecision:
    decision: Decision
    risk_score: int
    violations: list[Violation]


def _violation(code: str, severity: str) -> Violation:
    return Violation(code=code, message=VIOLATION_MESSAGES[code], severity=severity)


def evaluate_policy(receipt: RunReceipt) -> PolicyDecision:
    violations: list[Violation] = []

    if any(changed_file.secret_detected for changed_file in receipt.changed_files):
        decision: Decision = "block"
        risk_score = 95
        violations.append(_violation("SECRET_PATTERN_DETECTED", "high"))
    elif any(test.status == "failed" for test in receipt.tests):
        decision = "block"
        risk_score = 90
        violations.append(_violation("FAILED_TESTS", "high"))
    elif (
        any(changed_file.classification == "code" for changed_file in receipt.changed_files)
        and not any(test.status == "passed" for test in receipt.tests)
    ):
        decision = "review"
        risk_score = 60
        violations.append(_violation("NO_TEST_EVIDENCE", "medium"))
    elif any(tool_call.network_access for tool_call in receipt.tool_calls):
        decision = "review"
        risk_score = 55
        violations.append(_violation("NETWORK_TOOL_USAGE", "medium"))
    elif (
        receipt.policy_context.protected_branch
        and sum(
            changed_file.additions + changed_file.deletions
            for changed_file in receipt.changed_files
        )
        > 500
    ):
        decision = "review"
        risk_score = 70
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
