from __future__ import annotations

import hashlib
import hmac
import json
from unittest.mock import AsyncMock, patch
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import get_engine
from app.models.db import (
    GitHubCheckRun,
    GitHubInstallation,
    GitHubPullRequest,
    GitHubRepository,
    GitHubWebhookDelivery,
)
from tests.conftest import TEST_WEBHOOK_SECRET, base_receipt, post_receipt


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #

def _gh_sig(body: bytes, secret: str = TEST_WEBHOOK_SECRET) -> str:
    return "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


def webhook_headers(
    body: bytes, event: str, delivery: str = "abc-delivery-001"
) -> dict[str, str]:
    return {
        "Content-Type": "application/json",
        "X-GitHub-Event": event,
        "X-GitHub-Delivery": delivery,
        "X-Hub-Signature-256": _gh_sig(body),
    }


def send_webhook(client, payload: dict, event: str, delivery: str = "abc-delivery-001"):
    body = json.dumps(payload).encode()
    return client.post(
        "/api/v1/github/webhook",
        content=body,
        headers=webhook_headers(body, event, delivery),
    )


_INSTALLATION_PAYLOAD = {
    "action": "created",
    "installation": {
        "id": 99001,
        "account": {"login": "myorg", "type": "Organization"},
        "permissions": {"checks": "write", "pull_requests": "read"},
    },
    "repositories": [
        {"id": 77001, "name": "my-repo", "full_name": "myorg/my-repo", "private": False},
    ],
}

_PR_PAYLOAD = {
    "action": "opened",
    "installation": {"id": 99001},
    "pull_request": {
        "id": 55001,
        "number": 42,
        "head": {"sha": "aabbccdd" * 5},
        "base": {"ref": "main"},
        "user": {"login": "dev"},
        "html_url": "https://github.com/myorg/my-repo/pull/42",
        "state": "open",
    },
    "repository": {
        "id": 77001,
        "name": "my-repo",
        "full_name": "myorg/my-repo",
        "owner": {"login": "myorg"},
    },
}


# --------------------------------------------------------------------------- #
# Signature validation
# --------------------------------------------------------------------------- #

def test_webhook_rejects_missing_signature(github_client):
    body = json.dumps(_INSTALLATION_PAYLOAD).encode()
    resp = github_client.post(
        "/api/v1/github/webhook",
        content=body,
        headers={
            "Content-Type": "application/json",
            "X-GitHub-Event": "installation",
            "X-GitHub-Delivery": "no-sig-delivery",
        },
    )
    assert resp.status_code == 401
    assert resp.json()["error"]["code"] == "invalid_signature"


def test_webhook_rejects_wrong_signature(github_client):
    body = json.dumps(_INSTALLATION_PAYLOAD).encode()
    resp = github_client.post(
        "/api/v1/github/webhook",
        content=body,
        headers={
            "Content-Type": "application/json",
            "X-GitHub-Event": "installation",
            "X-GitHub-Delivery": "bad-sig-delivery",
            "X-Hub-Signature-256": "sha256=" + "0" * 64,
        },
    )
    assert resp.status_code == 401
    assert resp.json()["error"]["code"] == "invalid_signature"


def test_webhook_accepts_valid_signature(github_client):
    resp = send_webhook(github_client, _INSTALLATION_PAYLOAD, "installation")
    assert resp.status_code == 200
    assert resp.json()["ok"] is True


# --------------------------------------------------------------------------- #
# Installation event
# --------------------------------------------------------------------------- #

def test_installation_event_stores_installation(github_client):
    send_webhook(github_client, _INSTALLATION_PAYLOAD, "installation")

    with Session(get_engine()) as session:
        inst = session.scalar(
            select(GitHubInstallation).where(
                GitHubInstallation.installation_id == 99001
            )
        )
        assert inst is not None
        assert inst.account_login == "myorg"
        assert inst.account_type == "Organization"
        assert inst.suspended_at is None


def test_installation_event_stores_repositories(github_client):
    send_webhook(github_client, _INSTALLATION_PAYLOAD, "installation")

    with Session(get_engine()) as session:
        repo = session.scalar(
            select(GitHubRepository).where(
                GitHubRepository.github_repo_id == 77001
            )
        )
        assert repo is not None
        assert repo.full_name == "myorg/my-repo"
        assert repo.owner == "myorg"
        assert repo.installation_id == 99001


def test_installation_suspend_sets_suspended_at(github_client):
    payload = {**_INSTALLATION_PAYLOAD, "action": "suspend"}
    send_webhook(github_client, payload, "installation", delivery="suspend-del")

    with Session(get_engine()) as session:
        inst = session.scalar(
            select(GitHubInstallation).where(
                GitHubInstallation.installation_id == 99001
            )
        )
        assert inst is not None
        assert inst.suspended_at is not None


# --------------------------------------------------------------------------- #
# Delivery idempotency
# --------------------------------------------------------------------------- #

def test_duplicate_delivery_is_skipped(github_client):
    send_webhook(github_client, _INSTALLATION_PAYLOAD, "installation", delivery="idem-001")
    resp = send_webhook(github_client, _INSTALLATION_PAYLOAD, "installation", delivery="idem-001")
    assert resp.status_code == 200
    assert resp.json().get("skipped") is True

    with Session(get_engine()) as session:
        deliveries = session.scalars(
            select(GitHubWebhookDelivery).where(
                GitHubWebhookDelivery.delivery_guid == "idem-001"
            )
        ).all()
    assert len(deliveries) == 1


def test_delivery_record_is_marked_processed(github_client):
    send_webhook(github_client, _INSTALLATION_PAYLOAD, "installation", delivery="proc-001")

    with Session(get_engine()) as session:
        d = session.get(GitHubWebhookDelivery, "proc-001")
        assert d is not None
        assert d.status == "processed"
        assert d.processed_at is not None


# --------------------------------------------------------------------------- #
# Pull request event
# --------------------------------------------------------------------------- #

def test_pull_request_opened_stores_pr_record(github_client):
    send_webhook(github_client, _INSTALLATION_PAYLOAD, "installation", delivery="inst-pr")
    send_webhook(github_client, _PR_PAYLOAD, "pull_request", delivery="pr-001")

    with Session(get_engine()) as session:
        pr = session.scalar(
            select(GitHubPullRequest).where(GitHubPullRequest.github_pr_id == 55001)
        )
        assert pr is not None
        assert pr.number == 42
        assert pr.head_sha == "aabbccdd" * 5


def test_pull_request_opened_creates_pending_check_run(github_client):
    send_webhook(github_client, _INSTALLATION_PAYLOAD, "installation", delivery="inst-cr")
    send_webhook(github_client, _PR_PAYLOAD, "pull_request", delivery="pr-cr-001")

    with Session(get_engine()) as session:
        cr = session.scalar(
            select(GitHubCheckRun).where(
                GitHubCheckRun.head_sha == "aabbccdd" * 5
            )
        )
        assert cr is not None
        assert cr.status == "queued"
        assert cr.github_repo_id == 77001
        assert cr.installation_id == 99001
        assert cr.pull_number == 42


def test_pull_request_synchronize_updates_head_sha(github_client):
    send_webhook(github_client, _INSTALLATION_PAYLOAD, "installation", delivery="inst-sync")
    send_webhook(github_client, _PR_PAYLOAD, "pull_request", delivery="pr-sync-open")

    new_sha = "deadbeef" * 5
    sync_payload = {
        **_PR_PAYLOAD,
        "action": "synchronize",
        "pull_request": {**_PR_PAYLOAD["pull_request"], "head": {"sha": new_sha}},
    }
    send_webhook(github_client, sync_payload, "pull_request", delivery="pr-sync-push")

    with Session(get_engine()) as session:
        pr = session.scalar(
            select(GitHubPullRequest).where(GitHubPullRequest.github_pr_id == 55001)
        )
        assert pr is not None
        assert pr.head_sha == new_sha


def test_unhandled_pr_action_is_silently_ignored(github_client):
    payload = {**_PR_PAYLOAD, "action": "closed"}
    resp = send_webhook(github_client, payload, "pull_request", delivery="pr-closed")
    assert resp.status_code == 200

    with Session(get_engine()) as session:
        count = len(session.scalars(select(GitHubCheckRun)).all())
    assert count == 0


# --------------------------------------------------------------------------- #
# Receipt → check run update (mocked GitHub API)
# --------------------------------------------------------------------------- #

def test_receipt_updates_check_run_when_github_app_configured(
    tmp_path, monkeypatch
):
    """When GITHUB_APP_ID is set and a check run exists, receipt updates it."""
    from app.config import reset_settings_cache
    from app.database import get_engine, reset_database_cache
    from app.main import create_app
    from app.models.db import Base
    from fastapi.testclient import TestClient
    from tests.conftest import TEST_SECRET, TEST_WEBHOOK_SECRET, signed_headers, json_body

    db_path = tmp_path / "evidenceplane.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db_path}")
    monkeypatch.setenv("EVIDENCEPLANE_HMAC_SECRET", TEST_SECRET)
    monkeypatch.setenv("ADMIN_TOKEN", "test-admin-token")
    monkeypatch.setenv("GITHUB_WEBHOOK_SECRET", TEST_WEBHOOK_SECRET)
    monkeypatch.setenv("GITHUB_APP_ID", "12345")
    monkeypatch.setenv("GITHUB_APP_PRIVATE_KEY", "fake-key")
    reset_settings_cache()
    reset_database_cache()

    engine = get_engine()
    Base.metadata.create_all(engine)
    app = create_app()

    head_sha = "aabbccdd" * 5
    receipt_payload = {
        **base_receipt("check-run-update"),
        "repo_name": "myorg/my-repo",
        "commit_sha": head_sha,
    }

    with patch(
        "app.services.github.make_github_app_jwt", return_value="fake-jwt"
    ), patch(
        "app.services.github.get_installation_token",
        new=AsyncMock(return_value="fake-token"),
    ), patch(
        "app.services.github.create_github_check_run",
        new=AsyncMock(return_value=88001),
    ), patch(
        "app.services.github.update_github_check_run",
        new=AsyncMock(),
    ) as mock_update:
        with TestClient(app) as client:
            inst_body = json.dumps(_INSTALLATION_PAYLOAD).encode()
            client.post(
                "/api/v1/github/webhook",
                content=inst_body,
                headers=webhook_headers(inst_body, "installation", "inst-cu"),
            )
            pr_body = json.dumps({
                **_PR_PAYLOAD,
                "pull_request": {
                    **_PR_PAYLOAD["pull_request"],
                    "head": {"sha": head_sha},
                },
            }).encode()
            client.post(
                "/api/v1/github/webhook",
                content=pr_body,
                headers=webhook_headers(pr_body, "pull_request", "pr-cu"),
            )

            body = json_body(receipt_payload)
            resp = client.post(
                "/api/v1/runs",
                content=body,
                headers=signed_headers(body),
            )
            assert resp.status_code == 200
            assert mock_update.called
            call_kwargs = mock_update.call_args.kwargs
            assert call_kwargs["decision"] == "allow"

    reset_database_cache()
    reset_settings_cache()
