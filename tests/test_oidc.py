from __future__ import annotations

import hashlib
import hmac
import time
from unittest.mock import MagicMock, patch

_ISSUER = "https://idp.example.com"
_CLIENT_ID = "client123"
_CLIENT_SECRET = "secret456"
_REDIRECT_URI = "https://ep.example.com/callback"


def _valid_state(hmac_secret: str = "test-hmac-secret") -> str:
    import secrets
    nonce = secrets.token_urlsafe(16)
    ts = str(int(time.time()))
    sig = hmac.new(hmac_secret.encode(), f"{nonce}:{ts}".encode(), hashlib.sha256).hexdigest()
    return f"{nonce}:{ts}:{sig}"


def _fake_id_token(email: str = "oidc@example.com", sub: str = "oidc-sub-001") -> str:
    import base64, json

    def _b64(data: bytes) -> str:
        return base64.urlsafe_b64encode(data).rstrip(b"=").decode()

    header = _b64(json.dumps({"alg": "RS256", "typ": "JWT"}).encode())
    payload = _b64(json.dumps({
        "sub": sub,
        "email": email,
        "name": "OIDC Test User",
        "iat": int(time.time()),
        "exp": int(time.time()) + 3600,
    }).encode())
    return f"{header}.{payload}.fakesig"


def _configure_oidc(monkeypatch):
    monkeypatch.setenv("OIDC_ISSUER", _ISSUER)
    monkeypatch.setenv("OIDC_CLIENT_ID", _CLIENT_ID)
    monkeypatch.setenv("OIDC_CLIENT_SECRET", _CLIENT_SECRET)
    monkeypatch.setenv("OIDC_REDIRECT_URI", _REDIRECT_URI)
    from app.config import reset_settings_cache
    reset_settings_cache()


def _mock_discovery(mock_httpx):
    mock_httpx.get.return_value.status_code = 200
    mock_httpx.get.return_value.json.return_value = {
        "issuer": _ISSUER,
        "authorization_endpoint": f"{_ISSUER}/authorize",
        "token_endpoint": f"{_ISSUER}/oauth/token",
        "jwks_uri": f"{_ISSUER}/jwks",
    }


def _mock_token_response(mock_httpx, *, id_token: str):
    mock_httpx.post.return_value.status_code = 200
    mock_httpx.post.return_value.json.return_value = {
        "id_token": id_token,
        "access_token": "access-token",
    }


# --------------------------------------------------------------------------- #
# OIDC not configured
# --------------------------------------------------------------------------- #

def test_oidc_login_returns_503_when_not_configured(client, monkeypatch):
    monkeypatch.delenv("OIDC_ISSUER", raising=False)
    from app.config import reset_settings_cache
    reset_settings_cache()
    resp = client.get("/auth/oidc/login", follow_redirects=False)
    assert resp.status_code == 503


def test_oidc_callback_returns_503_when_not_configured(client, monkeypatch):
    monkeypatch.delenv("OIDC_ISSUER", raising=False)
    from app.config import reset_settings_cache
    reset_settings_cache()
    resp = client.get("/auth/oidc/callback?code=abc&state=x:y:z")
    assert resp.status_code == 503


# --------------------------------------------------------------------------- #
# OIDC login redirect
# --------------------------------------------------------------------------- #

def test_oidc_login_redirects_to_idp(client, monkeypatch):
    monkeypatch.setenv("OIDC_ISSUER", _ISSUER)
    monkeypatch.setenv("OIDC_CLIENT_ID", _CLIENT_ID)
    monkeypatch.setenv("OIDC_REDIRECT_URI", "https://ep.example.com/auth/oidc/callback")
    from app.config import reset_settings_cache
    reset_settings_cache()

    resp = client.get("/auth/oidc/login", follow_redirects=False)
    assert resp.status_code == 302
    location = resp.headers["location"]
    assert "idp.example.com/authorize" in location
    assert "client_id=client123" in location
    assert "response_type=code" in location
    assert "state=" in location


# --------------------------------------------------------------------------- #
# OIDC callback
# --------------------------------------------------------------------------- #

def test_oidc_callback_invalid_state_returns_400(client, monkeypatch):
    monkeypatch.setenv("OIDC_ISSUER", _ISSUER)
    monkeypatch.setenv("OIDC_CLIENT_ID", _CLIENT_ID)
    monkeypatch.setenv("OIDC_CLIENT_SECRET", _CLIENT_SECRET)
    from app.config import reset_settings_cache
    reset_settings_cache()

    resp = client.get("/auth/oidc/callback?code=authcode&state=invalid:state:value")
    assert resp.status_code == 400


@patch("app.services.oidc.httpx")
@patch("app.services.oidc._decode_id_token")
def test_oidc_callback_provisions_user_and_returns_token(mock_decode, mock_httpx, client, monkeypatch):
    _configure_oidc(monkeypatch)
    _mock_discovery(mock_httpx)
    _mock_token_response(mock_httpx, id_token=_fake_id_token("oidc-new@example.com"))
    mock_decode.return_value = {
        "sub": "oidc-sub-42",
        "email": "oidc-new@example.com",
        "name": "OIDC New User",
    }

    state = _valid_state()
    resp = client.get(f"/auth/oidc/callback?code=authcode&state={state}")
    assert resp.status_code == 200
    body = resp.json()
    assert "token" in body
    assert body["token_type"] == "Bearer"
    assert len(body["token"]) > 0
    mock_httpx.get.assert_called_once_with(
        f"{_ISSUER}/.well-known/openid-configuration", timeout=10.0
    )
    mock_httpx.post.assert_called_once()
    assert mock_httpx.post.call_args.args[0] == f"{_ISSUER}/oauth/token"


@patch("app.services.oidc.httpx")
@patch("app.services.oidc._decode_id_token")
def test_oidc_callback_same_email_reuses_user(mock_decode, mock_httpx, client, monkeypatch):
    _configure_oidc(monkeypatch)
    _mock_discovery(mock_httpx)

    def _mock_token_exchange(*args, **kwargs):
        m = MagicMock()
        m.status_code = 200
        m.json.return_value = {"id_token": _fake_id_token("returning@example.com")}
        return m

    mock_httpx.post.side_effect = _mock_token_exchange
    mock_decode.return_value = {
        "sub": "sub-returning",
        "email": "returning@example.com",
        "name": "Returning User",
    }

    # First login — creates user
    state1 = _valid_state()
    resp1 = client.get(f"/auth/oidc/callback?code=code1&state={state1}")
    assert resp1.status_code == 200
    token1 = resp1.json()["token"]

    # Second login — same email, different token
    state2 = _valid_state()
    resp2 = client.get(f"/auth/oidc/callback?code=code2&state={state2}")
    assert resp2.status_code == 200
    token2 = resp2.json()["token"]

    # Tokens are different (new session each time)
    assert token1 != token2


@patch("app.services.oidc.httpx")
def test_oidc_callback_token_endpoint_failure_returns_502(mock_httpx, client, monkeypatch):
    _configure_oidc(monkeypatch)
    _mock_discovery(mock_httpx)

    mock_httpx.post.return_value.status_code = 400
    mock_httpx.post.return_value.json.return_value = {"error": "invalid_grant"}

    state = _valid_state()
    resp = client.get(f"/auth/oidc/callback?code=badcode&state={state}")
    assert resp.status_code == 502


@patch("app.services.oidc.httpx")
def test_oidc_callback_discovery_issuer_mismatch_returns_502(mock_httpx, client, monkeypatch):
    _configure_oidc(monkeypatch)
    mock_httpx.get.return_value.status_code = 200
    mock_httpx.get.return_value.json.return_value = {
        "issuer": "https://evil.example.com",
        "token_endpoint": f"{_ISSUER}/oauth/token",
        "jwks_uri": f"{_ISSUER}/jwks",
    }

    state = _valid_state()
    resp = client.get(f"/auth/oidc/callback?code=authcode&state={state}")
    assert resp.status_code == 502
    mock_httpx.post.assert_not_called()


@patch("app.services.oidc.httpx")
@patch("app.services.oidc._decode_id_token")
def test_oidc_callback_invalid_id_token_returns_401(mock_decode, mock_httpx, client, monkeypatch):
    from app.errors import EvidencePlaneError

    _configure_oidc(monkeypatch)
    _mock_discovery(mock_httpx)
    _mock_token_response(mock_httpx, id_token=_fake_id_token("oidc-new@example.com"))
    mock_decode.side_effect = EvidencePlaneError(
        401, "oidc_invalid_token", "OIDC ID token failed verification."
    )

    state = _valid_state()
    resp = client.get(f"/auth/oidc/callback?code=authcode&state={state}")
    assert resp.status_code == 401


@patch("app.services.oidc.jwt.decode")
@patch("app.services.oidc.jwt.PyJWKClient")
def test_decode_id_token_uses_jwks_and_verifies_claims(mock_jwks_client, mock_decode):
    from app.services.oidc import OidcProviderMetadata, _decode_id_token

    metadata = OidcProviderMetadata(
        issuer=_ISSUER,
        authorization_endpoint=f"{_ISSUER}/authorize",
        token_endpoint=f"{_ISSUER}/oauth/token",
        jwks_uri=f"{_ISSUER}/jwks",
    )
    signing_key = MagicMock()
    signing_key.key = "public-key"
    mock_jwks_client.return_value.get_signing_key_from_jwt.return_value = signing_key
    mock_decode.return_value = {"sub": "sub", "email": "verified@example.com"}

    claims = _decode_id_token(
        "header.payload.signature", metadata=metadata, client_id=_CLIENT_ID
    )

    assert claims["email"] == "verified@example.com"
    mock_jwks_client.assert_called_once_with(f"{_ISSUER}/jwks")
    mock_jwks_client.return_value.get_signing_key_from_jwt.assert_called_once_with(
        "header.payload.signature"
    )
    mock_decode.assert_called_once_with(
        "header.payload.signature",
        "public-key",
        algorithms=["RS256", "ES256"],
        audience=_CLIENT_ID,
        issuer=_ISSUER,
        leeway=60,
        options={"require": ["exp", "iat", "sub"]},
    )
