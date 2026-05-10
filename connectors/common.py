"""Shared signing, dry-run, and submit logic for all EvidencePlane connectors."""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import sys
from datetime import datetime, timezone
from typing import Any


SCHEMA_VERSION = "2.0"


def classify_path(path: str) -> str:
    lo = path.lower()
    if lo.endswith((".md", ".rst", ".txt", ".adoc")):
        return "docs"
    if lo.endswith((".png", ".jpg", ".jpeg", ".gif", ".pdf", ".zip", ".wasm", ".bin")):
        return "binary"
    if lo.endswith((".yml", ".yaml", ".toml", ".ini", ".cfg", ".json", ".xml", ".tf", ".hcl")):
        return "config"
    return "code"


def sign_receipt(body: bytes, secret: str) -> str:
    return hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()


def build_receipt(
    *,
    idempotency_key: str,
    repo_name: str,
    commit_sha: str,
    branch: str,
    actor: str,
    timestamp_utc: datetime,
    changed_files: list[dict],
    tests: list[dict],
    tool_calls: list[dict],
    policy_context: dict,
    source_id: str | None = None,
    source_run_url: str | None = None,
    pull_request: dict | None = None,
    scanner_results: list[dict] | None = None,
    artifact_refs: list[dict] | None = None,
) -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "idempotency_key": idempotency_key,
        "repo_name": repo_name,
        "commit_sha": commit_sha,
        "branch": branch,
        "actor": actor,
        "timestamp_utc": timestamp_utc.astimezone(timezone.utc).isoformat().replace("+00:00", "Z"),
        "changed_files": changed_files or [{"path": "repository", "classification": "docs", "additions": 0, "deletions": 0, "secret_detected": False}],
        "tests": tests or [],
        "tool_calls": tool_calls or [],
        "policy_context": policy_context,
        "source_id": source_id,
        "source_run_url": source_run_url,
        "pull_request": pull_request,
        "scanner_results": scanner_results or [],
        "artifact_refs": artifact_refs or [],
    }


def submit_receipt(
    receipt: dict[str, Any],
    *,
    evidenceplane_url: str,
    hmac_secret: str,
    dry_run: bool = False,
) -> dict[str, Any] | None:
    body = json.dumps(receipt, separators=(",", ":")).encode("utf-8")
    signature = sign_receipt(body, hmac_secret)
    headers = {
        "Content-Type": "application/json",
        "X-EvidencePlane-Signature": signature,
    }

    if dry_run:
        print("=== DRY RUN — receipt JSON ===")
        print(json.dumps(receipt, indent=2))
        print(f"\n=== Signature: {signature} ===")
        print(f"=== POST {evidenceplane_url}/api/v1/runs ===")
        return None

    try:
        import urllib.request
        req = urllib.request.Request(
            f"{evidenceplane_url.rstrip('/')}/api/v1/runs",
            data=body,
            headers=headers,
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=15) as resp:
            result = json.loads(resp.read())
        return result
    except Exception as exc:
        print(f"ERROR: failed to submit receipt: {exc}", file=sys.stderr)
        sys.exit(1)


def decision_exit_code(decision: str) -> int:
    return {"allow": 0, "review": 0, "block": 1}.get(decision, 1)


def require_env(name: str) -> str:
    value = os.environ.get(name, "")
    if not value:
        print(f"ERROR: required environment variable {name!r} is not set.", file=sys.stderr)
        sys.exit(1)
    return value
