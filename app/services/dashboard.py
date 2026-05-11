from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.db import Case, Run, ViolationRecord
from app.models.schemas import CaseBreakdown, DecisionBreakdown, DashboardResponse, ViolationCount


def get_dashboard(session: Session) -> DashboardResponse:
    decision_rows = session.execute(
        select(Run.decision, func.count(Run.id)).group_by(Run.decision)
    ).all()
    decision_counts: dict[str, int] = {row[0]: row[1] for row in decision_rows}

    pending_reviews = session.scalar(
        select(func.count(Run.id)).where(Run.review_status == "pending")
    ) or 0

    case_rows = session.execute(
        select(Case.status, func.count(Case.id)).group_by(Case.status)
    ).all()
    case_counts: dict[str, int] = {row[0]: row[1] for row in case_rows}

    violation_rows = session.execute(
        select(ViolationRecord.code, func.count(ViolationRecord.id))
        .group_by(ViolationRecord.code)
        .order_by(func.count(ViolationRecord.id).desc())
        .limit(10)
    ).all()
    top_violations = [ViolationCount(code=row[0], count=row[1]) for row in violation_rows]

    return DashboardResponse(
        total_runs=sum(decision_counts.values()),
        decisions=DecisionBreakdown(
            allow=decision_counts.get("allow", 0),
            review=decision_counts.get("review", 0),
            block=decision_counts.get("block", 0),
        ),
        pending_reviews=pending_reviews,
        cases=CaseBreakdown(
            open=case_counts.get("open", 0),
            resolved=case_counts.get("resolved", 0),
            dismissed=case_counts.get("dismissed", 0),
        ),
        top_violations=top_violations,
    )
