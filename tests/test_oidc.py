from __future__ import annotations

import hashlib
import hmac
import time
from unittest.mock import MagicMock, patch

from tests.conftest import admin_headers


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
    monkeypatch.setenv("OIDC_ISSUER", "https://idp.example.com")
    monkeypatch.setenv("OIDC_CLIENT_ID", "client123")
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
    monkeypatch.setenv("OIDC_ISSUER", "https://idp.example.com")
    monkeypatch.setenv("OIDC_CLIENT_ID", "client123")
    monkeypatch.setenv("OIDC_CLIENT_SECRET", "secret456")
    from app.config import reset_settings_cache
    reset_settings_cache()

    resp = client.get("/auth/oidc/callback?code=authcode&state=invalid:state:value")
    assert resp.status_code == 400


@patch("app.services.oidc.httpx")
@patch("app.services.oidc.jwt")
def test_oidc_callback_provisions_user_and_returns_token(mock_jwt, mock_httpx, client, monkeypatch):
    monkeypatch.setenv("OIDC_ISSUER", "https://idp.example.com")
    monkeypatch.setenv("OIDC_CLIENT_ID", "client123")
    monkeypatch.setenv("OIDC_CLIENT_SECRET", "secret456")
    monkeypatch.setenv("OIDC_REDIRECT_URI", "https://ep.example.com/callback")
    from app.config import reset_settings_cache
    reset_settings_cache()

    mock_httpx.post.return_value.status_code = 200
    mock_httpx.post.return_value.json.return_value = {
        "id_token": _fake_id_token("oidc-new@example.com"),
        "access_token": "access-token",
    }
    mock_jwt.decode.return_value = {
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


@patch("app.services.oidc.httpx")
@patch("app.services.oidc.jwt")
def test_oidc_callback_same_email_reuses_user(mock_jwt, mock_httpx, client, monkeypatch):
    monkeypatch.setenv("OIDC_ISSUER", "https://idp.example.com")
    monkeypatch.setenv("OIDC_CLIENT_ID", "client123")
    monkeypatch.setenv("OIDC_CLIENT_SECRET", "secret456")
    monkeypatch.setenv("OIDC_REDIRECT_URI", "https://ep.example.com/callback")
    from app.config import reset_settings_cache
    reset_settings_cache()

    def _mock_token_exchange(*args, **kwargs):
        m = MagicMock()
        m.status_code = 200
        m.json.return_value = {"id_token": _fake_id_token("returning@example.com")}
        return m

    mock_httpx.post.side_effect = _mock_token_exchange
    mock_jwt.decode.return_value = {
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
    monkeypatch.setenv("OIDC_ISSUER", "https://idp.example.com")
    monkeypatch.setenv("OIDC_CLIENT_ID", "client123")
    monkeypatch.setenv("OIDC_CLIENT_SECRET", "secret456")
    from app.config import reset_settings_cache
    reset_settings_cache()

    mock_httpx.post.return_value.status_code = 400
    mock_httpx.post.return_value.json.return_value = {"error": "invalid_grant"}

    state = _valid_state()
    resp = client.get(f"/auth/oidc/callback?code=badcode&state={state}")
    assert resp.status_code == 502
