from __future__ import annotations

import hashlib
import hmac
import time
from uuid import uuid4

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.db import (
    GitHubCheckRun,
    GitHubInstallation,
    GitHubPullRequest,
    GitHubRepository,
    GitHubWebhookDelivery,
    utc_now,
)

GITHUB_API = "https://api.github.com"
CHECK_RUN_NAME = "EvidencePlane"

_DECISION_TO_CONCLUSION = {
    "allow": "success",
    "review": "neutral",
    "block": "failure",
}


# --------------------------------------------------------------------------- #
# Signature validation
# --------------------------------------------------------------------------- #

def validate_github_signature(signature: str | None, body: bytes, secret: str) -> bool:
    if not signature or not signature.startswith("sha256="):
        return False
    expected = "sha256=" + hmac.new(
        secret.encode("utf-8"), body, hashlib.sha256
    ).hexdigest()
    return hmac.compare_digest(expected, signature)


# --------------------------------------------------------------------------- #
# GitHub App JWT
# --------------------------------------------------------------------------- #

def make_github_app_jwt(app_id: str, private_key_pem: str) -> str:
    try:
        import jwt as pyjwt
    except ImportError as exc:
        raise RuntimeError(
            "PyJWT[crypto] is required for GitHub App JWT signing."
        ) from exc
    now = int(time.time())
    return pyjwt.encode(
        {"iat": now - 60, "exp": now + 600, "iss": app_id},
        private_key_pem,
        algorithm="RS256",
    )


# --------------------------------------------------------------------------- #
# GitHub API calls
# --------------------------------------------------------------------------- #

def _gh_headers(token: str, *, is_jwt: bool = False) -> dict[str, str]:
    auth = f"Bearer {token}" if is_jwt else f"token {token}"
    return {
        "Authorization": auth,
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }


async def get_installation_token(installation_id: int, app_jwt: str) -> str:
    async with httpx.AsyncClient() as client:
        resp = await client.post(
            f"{GITHUB_API}/app/installations/{installation_id}/access_tokens",
            headers=_gh_headers(app_jwt, is_jwt=True),
            timeout=10,
        )
        resp.raise_for_status()
        return resp.json()["token"]


async def create_github_check_run(
    *,
    owner: str,
    repo: str,
    head_sha: str,
    installation_token: str,
) -> int:
    async with httpx.AsyncClient() as client:
        resp = await client.post(
            f"{GITHUB_API}/repos/{owner}/{repo}/check-runs",
            headers=_gh_headers(installation_token),
            json={"name": CHECK_RUN_NAME, "head_sha": head_sha, "status": "queued"},
            timeout=10,
        )
        resp.raise_for_status()
        return resp.json()["id"]


async def update_github_check_run(
    *,
    owner: str,
    repo: str,
    check_run_id: int,
    decision: str,
    summary_text: str,
    details_url: str,
    installation_token: str,
) -> None:
    conclusion = _DECISION_TO_CONCLUSION.get(decision, "neutral")
    async with httpx.AsyncClient() as client:
        resp = await client.patch(
            f"{GITHUB_API}/repos/{owner}/{repo}/check-runs/{check_run_id}",
            headers=_gh_headers(installation_token),
            json={
                "status": "completed",
                "conclusion": conclusion,
                "details_url": details_url,
                "output": {
                    "title": f"EvidencePlane: {decision.upper()}",
                    "summary": summary_text,
                },
            },
            timeout=10,
        )
        resp.raise_for_status()


async def upsert_github_pr_comment(
    *,
    owner: str,
    repo: str,
    pull_number: int,
    body_text: str,
    comment_id: int | None,
    installation_token: str,
) -> int:
    headers = _gh_headers(installation_token)
    async with httpx.AsyncClient() as client:
        if comment_id:
            resp = await client.patch(
                f"{GITHUB_API}/repos/{owner}/{repo}/issues/comments/{comment_id}",
                headers=headers,
                json={"body": body_text},
                timeout=10,
            )
        else:
            resp = await client.post(
                f"{GITHUB_API}/repos/{owner}/{repo}/issues/{pull_number}/comments",
                headers=headers,
                json={"body": body_text},
                timeout=10,
            )
        resp.raise_for_status()
        return resp.json()["id"]


# --------------------------------------------------------------------------- #
# DB operations
# --------------------------------------------------------------------------- #

def store_delivery(
    session: Session,
    *,
    delivery_guid: str,
    event_type: str,
    installation_id: int | None,
) -> GitHubWebhookDelivery | None:
    """Return None if this delivery was already stored (idempotent replay)."""
    existing = session.get(GitHubWebhookDelivery, delivery_guid)
    if existing is not None:
        return None
    delivery = GitHubWebhookDelivery(
        delivery_guid=delivery_guid,
        installation_id=installation_id,
        event_type=event_type,
        status="received",
        received_at=utc_now(),
    )
    session.add(delivery)
    session.commit()
    return delivery


def mark_delivery_processed(
    session: Session, delivery_guid: str, *, error: str | None = None
) -> None:
    delivery = session.get(GitHubWebhookDelivery, delivery_guid)
    if delivery is not None:
        delivery.status = "failed" if error else "processed"
        delivery.processed_at = utc_now()
        delivery.error_message = error
        session.commit()


def upsert_installation(
    session: Session,
    *,
    installation_id: int,
    account_login: str,
    account_type: str,
    permissions: dict,
    suspended: bool = False,
) -> GitHubInstallation:
    inst = session.scalar(
        select(GitHubInstallation).where(
            GitHubInstallation.installation_id == installation_id
        )
    )
    now = utc_now()
    if inst is None:
        inst = GitHubInstallation(
            id=uuid4(),
            installation_id=installation_id,
            account_login=account_login,
            account_type=account_type,
            permissions_json=permissions,
            installed_at=now,
            suspended_at=now if suspended else None,
        )
        session.add(inst)
    else:
        inst.account_login = account_login
        inst.suspended_at = now if suspended else None
        inst.permissions_json = permissions
    session.commit()
    return inst


def upsert_github_repo(
    session: Session,
    *,
    installation_id: int,
    github_repo_id: int,
    owner: str,
    name: str,
    full_name: str,
    default_branch: str | None,
    private: bool,
) -> GitHubRepository:
    repo = session.scalar(
        select(GitHubRepository).where(
            GitHubRepository.github_repo_id == github_repo_id
        )
    )
    if repo is None:
        repo = GitHubRepository(
            id=uuid4(),
            installation_id=installation_id,
            github_repo_id=github_repo_id,
            owner=owner,
            name=name,
            full_name=full_name,
            default_branch=default_branch,
            private=private,
        )
        session.add(repo)
    else:
        repo.installation_id = installation_id
        repo.owner = owner
        repo.name = name
        repo.full_name = full_name
        repo.default_branch = default_branch
        repo.private = private
    session.commit()
    return repo


def upsert_pull_request(
    session: Session,
    *,
    github_repo_id: int,
    github_pr_id: int,
    number: int,
    head_sha: str,
    base_branch: str,
    author_login: str | None,
    url: str,
    state: str,
) -> GitHubPullRequest:
    pr = session.scalar(
        select(GitHubPullRequest).where(
            GitHubPullRequest.github_pr_id == github_pr_id
        )
    )
    if pr is None:
        pr = GitHubPullRequest(
            id=uuid4(),
            github_repo_id=github_repo_id,
            github_pr_id=github_pr_id,
            number=number,
            head_sha=head_sha,
            base_branch=base_branch,
            author_login=author_login,
            url=url,
            state=state,
        )
        session.add(pr)
    else:
        pr.head_sha = head_sha
        pr.state = state
    session.commit()
    return pr


def create_pending_check_run(
    session: Session,
    *,
    github_repo_id: int,
    installation_id: int,
    head_sha: str,
    pull_number: int | None,
    external_check_run_id: int | None = None,
) -> GitHubCheckRun:
    check_run = GitHubCheckRun(
        id=uuid4(),
        github_repo_id=github_repo_id,
        installation_id=installation_id,
        head_sha=head_sha,
        pull_number=pull_number,
        external_check_run_id=external_check_run_id,
        status="queued",
        created_at=utc_now(),
        updated_at=utc_now(),
    )
    session.add(check_run)
    session.commit()
    return check_run


def find_check_run_for_commit(
    session: Session, *, repo_full_name: str, head_sha: str
) -> GitHubCheckRun | None:
    github_repo = session.scalar(
        select(GitHubRepository).where(GitHubRepository.full_name == repo_full_name)
    )
    if github_repo is None:
        return None
    return session.scalar(
        select(GitHubCheckRun)
        .where(
            GitHubCheckRun.github_repo_id == github_repo.github_repo_id,
            GitHubCheckRun.head_sha == head_sha,
        )
        .order_by(GitHubCheckRun.created_at.desc())
    )


# --------------------------------------------------------------------------- #
# Formatting
# --------------------------------------------------------------------------- #

def build_check_run_summary(
    decision: str,
    violations: list,
    run_detail_url: str,
) -> str:
    icon = {"allow": "✅", "review": "⏳", "block": "🚫"}.get(decision, "ℹ️")
    lines = [f"{icon} **EvidencePlane decision: {decision.upper()}**", ""]
    if violations:
        lines.append("**Policy violations:**")
        for v in violations:
            if isinstance(v, dict):
                code, msg = v.get("code", ""), v.get("message", "")
            else:
                code, msg = getattr(v, "code", ""), getattr(v, "message", "")
            lines.append(f"- `{code}`: {msg}")
        lines.append("")
    lines.append(f"[View evidence]({run_detail_url})")
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# High-level event handlers (called from route layer)
# --------------------------------------------------------------------------- #

def handle_installation_event(session: Session, payload: dict) -> None:
    action = payload.get("action", "")
    inst_data = payload.get("installation", {})
    installation_id = inst_data.get("id")
    if not installation_id:
        return
    account = inst_data.get("account", {})

    upsert_installation(
        session,
        installation_id=installation_id,
        account_login=account.get("login", ""),
        account_type=account.get("type", "Organization"),
        permissions=inst_data.get("permissions", {}),
        suspended=action == "suspend",
    )

    repos = payload.get("repositories", [])
    for repo in repos:
        upsert_github_repo(
            session,
            installation_id=installation_id,
            github_repo_id=repo["id"],
            owner=account.get("login", ""),
            name=repo["name"],
            full_name=repo["full_name"],
            default_branch=None,
            private=repo.get("private", False),
        )


async def handle_pull_request_event(
    session: Session,
    payload: dict,
    *,
    app_id: str | None,
    app_private_key: str | None,
) -> None:
    action = payload.get("action", "")
    if action not in ("opened", "synchronize", "reopened"):
        return

    pr_data = payload.get("pull_request", {})
    repo_data = payload.get("repository", {})
    inst_data = payload.get("installation", {})
    installation_id = inst_data.get("id")
    github_repo_id = repo_data.get("id")
    head_sha = pr_data.get("head", {}).get("sha", "")
    pull_number = pr_data.get("number")

    upsert_pull_request(
        session,
        github_repo_id=github_repo_id,
        github_pr_id=pr_data.get("id"),
        number=pull_number,
        head_sha=head_sha,
        base_branch=pr_data.get("base", {}).get("ref", ""),
        author_login=pr_data.get("user", {}).get("login"),
        url=pr_data.get("html_url", ""),
        state=pr_data.get("state", "open"),
    )

    check_run = create_pending_check_run(
        session,
        github_repo_id=github_repo_id,
        installation_id=installation_id,
        head_sha=head_sha,
        pull_number=pull_number,
    )

    if app_id and app_private_key and installation_id:
        try:
            app_jwt = make_github_app_jwt(app_id, app_private_key)
            token = await get_installation_token(installation_id, app_jwt)
            owner = repo_data.get("owner", {}).get("login", "")
            repo_name = repo_data.get("name", "")
            ext_id = await create_github_check_run(
                owner=owner,
                repo=repo_name,
                head_sha=head_sha,
                installation_token=token,
            )
            check_run.external_check_run_id = ext_id
            check_run.status = "queued"
            session.commit()
        except Exception:
            pass


async def update_check_run_for_run(
    session: Session,
    *,
    repo_full_name: str,
    head_sha: str,
    decision: str,
    run_id: str,
    violations: list,
    app_id: str | None,
    app_private_key: str | None,
    base_url: str,
) -> None:
    """Called after a receipt is stored. Updates matching GitHub check run if one exists."""
    check_run = find_check_run_for_commit(
        session, repo_full_name=repo_full_name, head_sha=head_sha
    )
    if check_run is None or check_run.external_check_run_id is None:
        if check_run is not None:
            check_run.run_id_str = run_id
            check_run.status = "completed"
            check_run.conclusion = _DECISION_TO_CONCLUSION.get(decision, "neutral")
            session.commit()
        return

    if not app_id or not app_private_key:
        check_run.status = "completed"
        check_run.conclusion = _DECISION_TO_CONCLUSION.get(decision, "neutral")
        session.commit()
        return

    github_repo = session.scalar(
        select(GitHubRepository).where(
            GitHubRepository.github_repo_id == check_run.github_repo_id
        )
    )
    if github_repo is None:
        return

    details_url = f"{base_url}/runs/{run_id}"
    summary = build_check_run_summary(decision, violations, details_url)

    try:
        app_jwt = make_github_app_jwt(app_id, app_private_key)
        token = await get_installation_token(check_run.installation_id, app_jwt)

        await update_github_check_run(
            owner=github_repo.owner,
            repo=github_repo.name,
            check_run_id=check_run.external_check_run_id,
            decision=decision,
            summary_text=summary,
            details_url=details_url,
            installation_token=token,
        )

        if check_run.pull_number is not None:
            comment_body = f"## EvidencePlane: {decision.upper()}\n\n{summary}"
            new_comment_id = await upsert_github_pr_comment(
                owner=github_repo.owner,
                repo=github_repo.name,
                pull_number=check_run.pull_number,
                body_text=comment_body,
                comment_id=check_run.pr_comment_id,
                installation_token=token,
            )
            check_run.pr_comment_id = new_comment_id

        check_run.status = "completed"
        check_run.conclusion = _DECISION_TO_CONCLUSION.get(decision, "neutral")
        session.commit()
    except Exception:
        pass
