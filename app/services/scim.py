from __future__ import annotations

import re
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.errors import EvidencePlaneError
from app.models.db import Membership, Organization, User, UserSession, utc_now

_SCIM_USER_SCHEMA = "urn:ietf:params:scim:schemas:core:2.0:User"
_SCIM_LIST_SCHEMA = "urn:ietf:params:scim:api:messages:2.0:ListResponse"
_SCIM_PATCH_SCHEMA = "urn:ietf:params:scim:api:messages:2.0:PatchOp"
_SCIM_ERROR_SCHEMA = "urn:ietf:params:scim:api:messages:2.0:Error"


def _user_to_scim(user: User, membership: Membership | None = None) -> dict:
    active = membership.status == "active" if membership else True
    return {
        "schemas": [_SCIM_USER_SCHEMA],
        "id": str(user.id),
        "externalId": user.external_identity_id,
        "userName": user.email,
        "displayName": user.display_name or user.email,
        "emails": [{"value": user.email, "primary": True}],
        "active": active,
        "meta": {
            "resourceType": "User",
            "created": user.created_at.isoformat(),
            "lastModified": (
                user.last_seen_at.isoformat() if user.last_seen_at else user.created_at.isoformat()
            ),
            "location": f"/scim/v2/Users/{user.id}",
        },
    }


def _parse_filter(filter_str: str | None) -> str | None:
    """Parse `userName eq "value"` SCIM filter. Returns email value or None."""
    if not filter_str:
        return None
    match = re.match(r'userName\s+eq\s+"([^"]+)"', filter_str.strip(), re.IGNORECASE)
    return match.group(1) if match else None


def _get_org(session: Session) -> Organization:
    org = session.scalar(select(Organization).limit(1))
    if org is None:
        raise EvidencePlaneError(500, "no_organization", "No organization found.")
    return org


def scim_list_users(session: Session, filter_str: str | None = None) -> dict:
    org = _get_org(session)
    stmt = select(User).where(User.organization_id == org.id).order_by(User.created_at)
    email_filter = _parse_filter(filter_str)
    if email_filter:
        stmt = stmt.where(User.email == email_filter)
    users = session.scalars(stmt).all()

    resources = []
    for user in users:
        membership = session.scalar(
            select(Membership).where(
                Membership.user_id == user.id,
                Membership.organization_id == org.id,
            )
        )
        resources.append(_user_to_scim(user, membership))

    return {
        "schemas": [_SCIM_LIST_SCHEMA],
        "totalResults": len(resources),
        "startIndex": 1,
        "itemsPerPage": len(resources),
        "Resources": resources,
    }


def scim_create_user(session: Session, scim_body: dict) -> dict:
    org = _get_org(session)
    email = scim_body.get("userName") or ""
    if not email:
        raise EvidencePlaneError(400, "invalid_request", "userName is required.")

    existing = session.scalar(
        select(User).where(User.organization_id == org.id, User.email == email)
    )
    if existing is not None:
        raise EvidencePlaneError(409, "uniqueness", f"User {email} already exists.")

    display_name = scim_body.get("displayName") or email
    external_id = scim_body.get("externalId")
    role = "viewer"
    if scim_body.get("roles"):
        role_names = [r.get("value", "viewer") for r in scim_body["roles"]]
        if "admin" in role_names:
            role = "admin"
        elif "reviewer" in role_names:
            role = "reviewer"

    now = utc_now()
    user = User(
        id=uuid4(),
        organization_id=org.id,
        email=email,
        display_name=display_name,
        external_identity_id=external_id,
        created_at=now,
    )
    session.add(user)
    session.flush()

    membership = Membership(
        id=uuid4(),
        organization_id=org.id,
        user_id=user.id,
        role=role,
        status="active",
    )
    session.add(membership)
    session.commit()
    session.refresh(user)
    return _user_to_scim(user, membership)


def scim_get_user(session: Session, user_id: UUID) -> dict:
    org = _get_org(session)
    user = session.get(User, user_id)
    if user is None or user.organization_id != org.id:
        raise EvidencePlaneError(404, "not_found", "User not found.")
    membership = session.scalar(
        select(Membership).where(
            Membership.user_id == user.id,
            Membership.organization_id == org.id,
        )
    )
    return _user_to_scim(user, membership)


def scim_patch_user(session: Session, user_id: UUID, scim_body: dict) -> dict:
    org = _get_org(session)
    user = session.get(User, user_id)
    if user is None or user.organization_id != org.id:
        raise EvidencePlaneError(404, "not_found", "User not found.")

    membership = session.scalar(
        select(Membership).where(
            Membership.user_id == user.id,
            Membership.organization_id == org.id,
        )
    )

    for operation in scim_body.get("Operations", []):
        op = operation.get("op", "").lower()
        value = operation.get("value", {})
        path = operation.get("path", "")

        if op == "replace":
            if isinstance(value, dict):
                if "active" in value and not value["active"]:
                    _deactivate_user(session, user, membership)
                if "active" in value and value["active"] and membership:
                    membership.status = "active"
                if "displayName" in value:
                    user.display_name = value["displayName"]
            elif path.lower() == "active" and value is False:
                _deactivate_user(session, user, membership)

    session.commit()
    session.refresh(user)
    if membership:
        session.refresh(membership)
    return _user_to_scim(user, membership)


def scim_delete_user(session: Session, user_id: UUID) -> None:
    org = _get_org(session)
    user = session.get(User, user_id)
    if user is None or user.organization_id != org.id:
        raise EvidencePlaneError(404, "not_found", "User not found.")

    membership = session.scalar(
        select(Membership).where(
            Membership.user_id == user.id,
            Membership.organization_id == org.id,
        )
    )
    _deactivate_user(session, user, membership)
    session.commit()


def _deactivate_user(session: Session, user: User, membership: Membership | None) -> None:
    if membership:
        membership.status = "inactive"
    # Revoke all active sessions
    sessions = session.scalars(
        select(UserSession).where(
            UserSession.user_id == user.id,
            UserSession.revoked_at.is_(None),
        )
    ).all()
    now = utc_now()
    for s in sessions:
        s.revoked_at = now
