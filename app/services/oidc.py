from __future__ import annotations

import hashlib
import hmac as _hmac
import secrets
import time
from uuid import uuid4

import httpx
import jwt
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.errors import EvidencePlaneError
from app.models.db import Membership, Organization, User, UserSession, utc_now
from app.services.auth import _create_session  # reuse internal helper


_SCOPES = "openid email profile"


def generate_state(hmac_secret: str) -> str:
    nonce = secrets.token_urlsafe(16)
    ts = str(int(time.time()))
    sig = _hmac.new(hmac_secret.encode(), f"{nonce}:{ts}".encode(), hashlib.sha256).hexdigest()
    return f"{nonce}:{ts}:{sig}"


def validate_state(state: str, hmac_secret: str) -> bool:
    parts = state.split(":")
    if len(parts) != 3:
        return False
    nonce, ts_str, sig = parts
    expected = _hmac.new(hmac_secret.encode(), f"{nonce}:{ts_str}".encode(), hashlib.sha256).hexdigest()
    if not _hmac.compare_digest(expected, sig):
        return False
    try:
        age = int(time.time()) - int(ts_str)
        return 0 <= age < 600  # 10-minute validity window
    except (ValueError, OverflowError):
        return False


def build_auth_url(
    issuer: str,
    client_id: str,
    redirect_uri: str,
    state: str,
) -> str:
    return (
        f"{issuer.rstrip('/')}/authorize"
        f"?response_type=code"
        f"&client_id={client_id}"
        f"&redirect_uri={redirect_uri}"
        f"&scope={_SCOPES.replace(' ', '+')}"
        f"&state={state}"
    )


def exchange_code(
    code: str,
    *,
    issuer: str,
    client_id: str,
    client_secret: str,
    redirect_uri: str,
) -> dict:
    """Exchange auth code for ID token. Returns decoded claims dict."""
    token_url = f"{issuer.rstrip('/')}/token"
    resp = httpx.post(
        token_url,
        data={
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": redirect_uri,
            "client_id": client_id,
            "client_secret": client_secret,
        },
        timeout=10.0,
    )
    if resp.status_code != 200:
        raise EvidencePlaneError(
            502, "oidc_token_error", f"Token endpoint returned {resp.status_code}."
        )
    token_response = resp.json()
    id_token = token_response.get("id_token")
    if not id_token:
        raise EvidencePlaneError(502, "oidc_no_id_token", "No id_token in token response.")

    # Decode without signature verification. Production deployments should
    # fetch JWKS from {issuer}/.well-known/openid-configuration and verify.
    claims = jwt.decode(
        id_token,
        options={"verify_signature": False, "verify_exp": False},
        algorithms=["RS256", "ES256", "HS256"],
    )
    return claims


def provision_oidc_user(session: Session, claims: dict) -> tuple[User, str]:
    """Upsert a user from OIDC claims and issue a session token."""
    email = claims.get("email")
    if not email:
        raise EvidencePlaneError(400, "oidc_no_email", "ID token has no email claim.")

    org = session.scalar(select(Organization).limit(1))
    if org is None:
        raise EvidencePlaneError(500, "no_organization", "No organization found.")

    user = session.scalar(
        select(User).where(User.organization_id == org.id, User.email == email)
    )
    if user is None:
        user = User(
            id=uuid4(),
            organization_id=org.id,
            email=email,
            display_name=claims.get("name") or claims.get("given_name"),
            external_identity_id=claims.get("sub"),
            created_at=utc_now(),
        )
        session.add(user)
        session.flush()

        membership = Membership(
            id=uuid4(),
            organization_id=org.id,
            user_id=user.id,
            role="viewer",
            status="active",
        )
        session.add(membership)
        session.flush()
    else:
        membership = session.scalar(
            select(Membership).where(
                Membership.user_id == user.id,
                Membership.organization_id == org.id,
                Membership.status == "active",
            )
        )
        if membership is None:
            raise EvidencePlaneError(
                403, "account_inactive", "Account is inactive. Contact your administrator."
            )

    token, user_session = _create_session(user=user, role=membership.role)
    session.add(user_session)
    session.commit()
    return user, token
