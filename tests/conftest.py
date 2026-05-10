from __future__ import annotations

import hashlib
import hmac
import json
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.config import reset_settings_cache
from app.database import get_engine, reset_database_cache
from app.main import create_app
from app.models.db import Base, Organization
from app.services.security import SIGNATURE_HEADER

TEST_SECRET = "test-hmac-secret"
TEST_WEBHOOK_SECRET = "test-webhook-secret"


def _seed_org(engine) -> None:
    from sqlalchemy.orm import Session
    with Session(engine) as session:
        if session.query(Organization).count() == 0:
            session.add(Organization(name="Test Org", slug="test-org"))
            session.commit()


@pytest.fixture()
def client(tmp_path, monkeypatch):
    db_path = tmp_path / "evidenceplane.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db_path}")
    monkeypatch.setenv("EVIDENCEPLANE_HMAC_SECRET", TEST_SECRET)
    monkeypatch.setenv("ADMIN_TOKEN", "test-admin-token")
    reset_settings_cache()
    reset_database_cache()

    engine = get_engine()
    Base.metadata.create_all(engine)
    _seed_org(engine)
    app = create_app()
    with TestClient(app) as test_client:
        yield test_client

    reset_database_cache()
    reset_settings_cache()


@pytest.fixture()
def github_client(tmp_path, monkeypatch):
    db_path = tmp_path / "evidenceplane.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db_path}")
    monkeypatch.setenv("EVIDENCEPLANE_HMAC_SECRET", TEST_SECRET)
    monkeypatch.setenv("ADMIN_TOKEN", "test-admin-token")
    monkeypatch.setenv("GITHUB_WEBHOOK_SECRET", TEST_WEBHOOK_SECRET)
    reset_settings_cache()
    reset_database_cache()

    engine = get_engine()
    Base.metadata.create_all(engine)
    _seed_org(engine)
    app = create_app()
    with TestClient(app) as test_client:
        yield test_client

    reset_database_cache()
    reset_settings_cache()


def json_body(payload: dict[str, Any]) -> bytes:
    return json.dumps(payload, separators=(",", ":")).encode("utf-8")


def signature_for(body: bytes, secret: str = TEST_SECRET) -> str:
    return hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()


def signed_headers(body: bytes) -> dict[str, str]:
    return {
        "Content-Type": "application/json",
        SIGNATURE_HEADER: signature_for(body),
    }


def admin_headers(token: str = "test-admin-token") -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def post_receipt(client: TestClient, payload: dict[str, Any]):
    body = json_body(payload)
    return client.post("/api/v1/runs", content=body, headers=signed_headers(body))


def base_receipt(idempotency_key: str = "run-001") -> dict[str, Any]:
    return {
        "idempotency_key": idempotency_key,
        "repo_name": "docs-site",
        "commit_sha": "1111111111111111111111111111111111111111",
        "branch": "docs/update-readme",
        "actor": "ci-bot",
        "timestamp_utc": "2026-05-06T19:00:00Z",
        "changed_files": [
            {
                "path": "README.md",
                "classification": "docs",
                "additions": 10,
                "deletions": 2,
                "secret_detected": False,
            }
        ],
        "tests": [],
        "tool_calls": [],
        "policy_context": {
            "protected_branch": False,
            "emergency_override": False,
            "approver_email": None,
        },
    }
