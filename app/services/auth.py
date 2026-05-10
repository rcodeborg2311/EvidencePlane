from __future__ import annotations

import hashlib
import secrets
from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.errors import EvidencePlaneError
from app.models.db import Membership, Organization, User, UserSession, role_meets, utc_now

SESSION_TTL_DAYS = 90
TOKEN_BYTES = 32


def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _now() -> datetime:
    return datetime.now(timezone.utc)


def provision_user(
    session: Session,
    *,
    organization_id: UUID,
    email: str,
    display_name: str | None,
    role: str,
) -> tuple[User, str]:
    """Create a user + membership and issue an initial session token.

    Returns (user, plaintext_token). The plaintext token is shown once and
    never stored — the caller must pass it to the user securely.
    """
    org = session.get(Organization, organization_id)
    if org is None:
        raise EvidencePlaneError(404, "org_not_found", "Organization not found.")

    existing = session.scalar(
        select(User).where(
            User.organization_id == organization_id,
            User.email == email,
        )
    )
    if existing is not None:
        raise EvidencePlaneError(
            409, "user_already_exists", f"A user with email {email!r} already exists."
        )

    user = User(
        id=uuid4(),
        organization_id=organization_id,
        email=email,
        display_name=display_name,
    )
    membership = Membership(
        id=uuid4(),
        organization_id=organization_id,
        user_id=user.id,
        role=role,
        status="active",
    )
    session.add(user)
    session.add(membership)

    token, user_session = _create_session(user=user, role=role)
    session.add(user_session)
    session.commit()
    return user, token


def issue_session(
    session: Session,
    *,
    user_id: UUID,
    organization_id: UUID,
) -> tuple[UserSession, str]:
    """Issue a new session for an existing user. Role taken from active membership."""
    user = session.get(User, user_id)
    if user is None or user.organization_id != organization_id:
        raise EvidencePlaneError(404, "user_not_found", "User not found.")

    membership = session.scalar(
        select(Membership).where(
            Membership.user_id == user_id,
            Membership.organization_id == organization_id,
            Membership.status == "active",
        )
    )
    if membership is None:
        raise EvidencePlaneError(
            403, "no_active_membership", "User has no active membership."
        )

    token, user_session = _create_session(user=user, role=membership.role)
    session.add(user_session)
    session.commit()
    return user_session, token


def _create_session(*, user: User, role: str) -> tuple[str, UserSession]:
    token = secrets.token_urlsafe(TOKEN_BYTES)
    return token, UserSession(
        id=uuid4(),
        user_id=user.id,
        organization_id=user.organization_id,
        token_hash=_hash_token(token),
        role=role,
        expires_at=_now() + timedelta(days=SESSION_TTL_DAYS),
    )


def resolve_session(db: Session, token: str) -> UserSession:
    """Look up and validate a session by its plaintext token."""
    token_hash = _hash_token(token)
    user_session = db.scalar(
        select(UserSession).where(UserSession.token_hash == token_hash)
    )
    if user_session is None:
        raise EvidencePlaneError(401, "invalid_token", "Session token is invalid.")
    if user_session.revoked_at is not None:
        raise EvidencePlaneError(401, "token_revoked", "Session token has been revoked.")
    if user_session.expires_at and user_session.expires_at < _now():
        raise EvidencePlaneError(401, "token_expired", "Session token has expired.")

    user_session.last_used_at = utc_now()
    db.commit()
    return user_session


def revoke_session(db: Session, token: str) -> None:
    token_hash = _hash_token(token)
    user_session = db.scalar(
        select(UserSession).where(UserSession.token_hash == token_hash)
    )
    if user_session is None:
        raise EvidencePlaneError(404, "not_found", "Session not found.")
    user_session.revoked_at = utc_now()
    db.commit()


def check_role(user_session: UserSession, required: str) -> None:
    if not role_meets(user_session.role, required):
        raise EvidencePlaneError(
            403,
            "insufficient_role",
            f"This action requires role '{required}' or higher.",
        )
