from __future__ import annotations

from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.errors import EvidencePlaneError
from app.models.db import EvidenceSource, Organization, utc_now
from app.models.schemas import EvidenceSourceRequest, EvidenceSourceResponse


def _to_response(src: EvidenceSource) -> EvidenceSourceResponse:
    return EvidenceSourceResponse(
        source_id=src.id,
        source_type=src.source_type,
        display_name=src.display_name,
        trust_level=src.trust_level,
        enabled=src.enabled,
        repo_name=getattr(src, "repo_name", None),
        last_seen_at=src.last_seen_at,
        created_at=src.created_at,
    )


def create_evidence_source(
    session: Session,
    request: EvidenceSourceRequest,
) -> EvidenceSourceResponse:
    org = session.scalar(select(Organization).limit(1))
    if org is None:
        raise EvidencePlaneError(500, "no_organization", "No organization found.")

    src = EvidenceSource(
        id=uuid4(),
        organization_id=org.id,
        source_type=request.source_type,
        display_name=request.display_name,
        repo_name=request.repo_name,
        signing_secret_ref=request.signing_secret,
        trust_level=request.trust_level,
        enabled=True,
        created_at=utc_now(),
    )
    session.add(src)
    session.commit()
    session.refresh(src)
    return _to_response(src)


def list_evidence_sources(
    session: Session,
    *,
    include_disabled: bool = False,
) -> list[EvidenceSourceResponse]:
    stmt = select(EvidenceSource).order_by(EvidenceSource.created_at.desc())
    if not include_disabled:
        stmt = stmt.where(EvidenceSource.enabled.is_(True))
    sources = session.scalars(stmt).all()
    return [_to_response(s) for s in sources]


def get_evidence_source(session: Session, source_id) -> EvidenceSourceResponse:
    src = session.get(EvidenceSource, source_id)
    if src is None:
        raise EvidencePlaneError(404, "not_found", "Evidence source not found.")
    return _to_response(src)


def disable_evidence_source(session: Session, source_id) -> None:
    src = session.get(EvidenceSource, source_id)
    if src is None:
        raise EvidencePlaneError(404, "not_found", "Evidence source not found.")
    src.enabled = False
    session.commit()


def resolve_source(
    session: Session,
    source_id_str: str,
) -> EvidenceSource | None:
    """Look up an enabled EvidenceSource by UUID string. Returns None if not found."""
    try:
        from uuid import UUID
        source_uuid = UUID(source_id_str)
    except (ValueError, AttributeError):
        return None
    src = session.get(EvidenceSource, source_uuid)
    return src if (src is not None and src.enabled) else None
