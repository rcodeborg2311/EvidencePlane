from __future__ import annotations

from dataclasses import dataclass
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
_ID_TOKEN_ALGORITHMS = ["RS256", "ES256"]


@dataclass(frozen=True)
class OidcProviderMetadata:
    issuer: str
    authorization_endpoint: str | None
    token_endpoint: str
    jwks_uri: str


def _normalize_issuer(issuer: str) -> str:
    return issuer.rstrip("/")


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


def _fetch_provider_metadata(issuer: str) -> OidcProviderMetadata:
    expected_issuer = _normalize_issuer(issuer)
    discovery_url = f"{expected_issuer}/.well-known/openid-configuration"
    try:
        resp = httpx.get(discovery_url, timeout=10.0)
    except httpx.RequestError as exc:
        raise EvidencePlaneError(
            502, "oidc_discovery_error", "OIDC discovery request failed."
        ) from exc

    if resp.status_code != 200:
        raise EvidencePlaneError(
            502,
            "oidc_discovery_error",
            f"OIDC discovery endpoint returned {resp.status_code}.",
        )

    try:
        data = resp.json()
    except ValueError as exc:
        raise EvidencePlaneError(
            502, "oidc_discovery_error", "OIDC discovery response is not valid JSON."
        ) from exc

    discovered_issuer = data.get("issuer")
    token_endpoint = data.get("token_endpoint")
    jwks_uri = data.get("jwks_uri")
    authorization_endpoint = data.get("authorization_endpoint")
    if discovered_issuer != expected_issuer:
        raise EvidencePlaneError(
            502,
            "oidc_discovery_error",
            "OIDC discovery issuer does not match configured issuer.",
        )
    if not isinstance(token_endpoint, str) or not token_endpoint:
        raise EvidencePlaneError(
            502,
            "oidc_discovery_error",
            "OIDC discovery response is missing token_endpoint.",
        )
    if not isinstance(jwks_uri, str) or not jwks_uri:
        raise EvidencePlaneError(
            502,
            "oidc_discovery_error",
            "OIDC discovery response is missing jwks_uri.",
        )

    return OidcProviderMetadata(
        issuer=discovered_issuer,
        authorization_endpoint=authorization_endpoint
        if isinstance(authorization_endpoint, str)
        else None,
        token_endpoint=token_endpoint,
        jwks_uri=jwks_uri,
    )


def _decode_id_token(
    id_token: str,
    *,
    metadata: OidcProviderMetadata,
    client_id: str,
) -> dict:
    try:
        signing_key = jwt.PyJWKClient(metadata.jwks_uri).get_signing_key_from_jwt(
            id_token
        )
        return jwt.decode(
            id_token,
            signing_key.key,
            algorithms=_ID_TOKEN_ALGORITHMS,
            audience=client_id,
            issuer=metadata.issuer,
            leeway=60,
            options={"require": ["exp", "iat", "sub"]},
        )
    except jwt.PyJWTError as exc:
        raise EvidencePlaneError(
            401, "oidc_invalid_token", "OIDC ID token failed verification."
        ) from exc


def exchange_code(
    code: str,
    *,
    issuer: str,
    client_id: str,
    client_secret: str,
    redirect_uri: str,
) -> dict:
    """Exchange auth code for ID token. Returns decoded claims dict."""
    metadata = _fetch_provider_metadata(issuer)
    resp = httpx.post(
        metadata.token_endpoint,
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

    return _decode_id_token(id_token, metadata=metadata, client_id=client_id)


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
