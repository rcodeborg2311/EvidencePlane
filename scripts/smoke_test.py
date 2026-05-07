from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import time

import httpx


def signed_headers(body: bytes, secret: str) -> dict[str, str]:
    signature = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
    return {
        "Content-Type": "application/json",
        "X-EvidencePlane-Signature": signature,
    }


def receipt(idempotency_key: str) -> dict:
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


def wait_for_health(base_url: str) -> None:
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        try:
            response = httpx.get(f"{base_url}/healthz", timeout=2)
            if response.status_code == 200:
                return
        except httpx.HTTPError:
            time.sleep(0.5)
            continue
        time.sleep(0.5)
    raise SystemExit("healthz did not return 200 before timeout")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--secret", required=True)
    args = parser.parse_args()

    wait_for_health(args.base_url)
    body = json.dumps(receipt(f"ci-smoke-{int(time.time())}"), separators=(",", ":")).encode()
    response = httpx.post(
        f"{args.base_url}/api/v1/runs",
        content=body,
        headers=signed_headers(body, args.secret),
        timeout=10,
    )
    response.raise_for_status()
    if response.json()["decision"] != "allow":
        raise SystemExit("smoke ingest did not return allow")


if __name__ == "__main__":
    main()
