from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import hmac
import json
import os
from pathlib import Path
import subprocess
import sys
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


SIGNATURE_HEADER = "X-EvidencePlane-Signature"


def utc_timestamp() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def bool_flag(value: str, *, default: bool) -> bool:
    if value == "":
        return default
    return value.lower() in {"1", "true", "yes", "on"}


def classify_path(path: str) -> str:
    lowered = path.lower()
    name = Path(lowered).name
    if lowered.endswith((".md", ".txt", ".rst", ".adoc")):
        return "docs"
    if lowered.endswith((".png", ".jpg", ".jpeg", ".gif", ".pdf", ".zip", ".wasm")):
        return "binary"
    if lowered.endswith((".yml", ".yaml", ".toml", ".ini", ".cfg", ".json", ".xml")):
        return "config"
    if name in {"dockerfile", "makefile", ".env", ".env.example"}:
        return "config"
    return "code"


def run_shell(command: str, repo_path: Path) -> int:
    completed = subprocess.run(command, cwd=repo_path, shell=True, check=False)
    return completed.returncode


def git(repo_path: Path, *args: str) -> str:
    try:
        return subprocess.check_output(
            ["git", *args],
            cwd=repo_path,
            text=True,
            stderr=subprocess.DEVNULL,
        ).strip()
    except FileNotFoundError as exc:
        raise RuntimeError("git is required but was not found") from exc
    except subprocess.CalledProcessError as exc:
        raise RuntimeError(f"git command failed: git {' '.join(args)}") from exc


def repo_identity(repo_path: Path) -> tuple[str, str]:
    commit_sha = git(repo_path, "rev-parse", "HEAD").lower()
    branch = git(repo_path, "rev-parse", "--abbrev-ref", "HEAD")
    if len(commit_sha) != 40 or any(char not in "0123456789abcdef" for char in commit_sha):
        raise RuntimeError("git did not return a valid 40-character lowercase commit SHA")
    if not branch:
        raise RuntimeError("git did not return a branch name")
    return commit_sha, branch


def changed_files(repo_path: Path) -> list[dict[str, Any]]:
    output = git(repo_path, "diff", "--numstat", "HEAD", "--")
    files: list[dict[str, Any]] = []
    for line in output.splitlines():
        parts = line.split("\t")
        if len(parts) < 3:
            continue
        additions, deletions, path = parts[0], parts[1], parts[2]
        files.append(
            {
                "path": path,
                "classification": classify_path(path),
                "additions": 0 if additions == "-" else int(additions),
                "deletions": 0 if deletions == "-" else int(deletions),
                "secret_detected": False,
            }
        )
    if not files:
        raise RuntimeError("no changed files detected with git diff --numstat HEAD")
    return files


def read_scanner_result(path: Path | None) -> tuple[bool, set[str]]:
    if path is None:
        return False, set()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise RuntimeError(f"could not read scanner result: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"scanner result is not valid JSON: {exc.msg}") from exc

    if not isinstance(data, dict):
        raise RuntimeError("scanner result must be a JSON object")
    detected = data.get("secret_detected") is True
    paths_value = data.get("paths", [])
    if paths_value is None:
        paths_value = []
    if not isinstance(paths_value, list) or not all(
        isinstance(item, str) for item in paths_value
    ):
        raise RuntimeError("scanner result paths must be a list of strings")
    return detected or bool(paths_value), set(paths_value)


def apply_scanner_result(files: list[dict[str, Any]], detected: bool, paths: set[str]) -> None:
    if not detected:
        return
    if paths:
        for file in files:
            file["secret_detected"] = file["path"] in paths
        if any(file["secret_detected"] for file in files):
            return
    files[0]["secret_detected"] = True


def should_fail(decision: str, *, enforce_review: bool, enforce_block: bool) -> bool:
    if decision == "review":
        return enforce_review
    if decision == "block":
        return enforce_block
    return False


def build_receipt(
    *,
    repo_path: Path,
    agent_command: str | None,
    agent_exit_code: int | None,
    test_command: str | None,
    test_exit_code: int | None,
    scanner_result: Path | None,
    started_at: str,
) -> dict[str, Any]:
    commit_sha, branch = repo_identity(repo_path)
    files = changed_files(repo_path)
    detected, paths = read_scanner_result(scanner_result)
    apply_scanner_result(files, detected, paths)

    tool_calls: list[dict[str, Any]] = []
    if agent_command is not None and agent_exit_code is not None:
        tool_calls.append(
            {
                "tool": "agent-command",
                "command": agent_command,
                "network_access": False,
                "exit_code": agent_exit_code,
            }
        )
    if test_command is not None and test_exit_code is not None:
        tool_calls.append(
            {
                "tool": "test-command",
                "command": test_command,
                "network_access": False,
                "exit_code": test_exit_code,
            }
        )

    tests = []
    if test_command is not None and test_exit_code is not None:
        tests.append(
            {
                "name": "agent-wrapper::test-command",
                "status": "passed" if test_exit_code == 0 else "failed",
            }
        )

    return {
        "idempotency_key": f"agent-{commit_sha[:12]}-{started_at.replace(':', '').replace('-', '')[:15]}",
        "repo_name": repo_path.name,
        "commit_sha": commit_sha,
        "branch": branch,
        "actor": os.environ.get("USER") or os.environ.get("USERNAME") or "agent-wrapper",
        "timestamp_utc": started_at,
        "changed_files": files,
        "tests": tests,
        "tool_calls": tool_calls,
        "policy_context": {
            "protected_branch": False,
            "emergency_override": False,
            "approver_email": None,
        },
    }


def sign_body(body: bytes, secret: str) -> str:
    return hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()


def submit_receipt(url: str, body: bytes, secret: str) -> dict[str, Any]:
    request = Request(
        f"{url.rstrip('/')}/api/v1/runs",
        data=body,
        headers={
            "Content-Type": "application/json",
            SIGNATURE_HEADER: sign_body(body, secret),
        },
        method="POST",
    )
    try:
        with urlopen(request, timeout=10) as response:
            return json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        body_text = exc.read().decode("utf-8")
        raise RuntimeError(f"EvidencePlane returned HTTP {exc.code}: {body_text}") from exc
    except URLError as exc:
        raise RuntimeError(f"EvidencePlane request failed: {exc}") from exc


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Wrap a coding-agent command and submit an EvidencePlane receipt."
    )
    parser.add_argument("--repo-path", required=True, type=Path)
    parser.add_argument("--agent-command")
    parser.add_argument("--test-command")
    parser.add_argument("--evidenceplane-url", default="http://127.0.0.1:8000")
    parser.add_argument("--receipt-output", required=True, type=Path)
    parser.add_argument("--scanner-result", type=Path)
    parser.add_argument("--enforce-review", default="false")
    parser.add_argument("--enforce-block", default="true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--pretty", action="store_true")
    args = parser.parse_args(argv)

    repo_path = args.repo_path.resolve()
    if not (repo_path / ".git").exists():
        print("error: repo path is not a git repository", file=sys.stderr)
        return 2

    started_at = utc_timestamp()
    agent_exit_code = None
    test_exit_code = None
    if args.agent_command:
        agent_exit_code = run_shell(args.agent_command, repo_path)
    if args.test_command:
        test_exit_code = run_shell(args.test_command, repo_path)

    try:
        receipt = build_receipt(
            repo_path=repo_path,
            agent_command=args.agent_command,
            agent_exit_code=agent_exit_code,
            test_command=args.test_command,
            test_exit_code=test_exit_code,
            scanner_result=args.scanner_result,
            started_at=started_at,
        )
    except RuntimeError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    body = json.dumps(receipt, separators=(",", ":")).encode("utf-8")
    args.receipt_output.write_bytes(body)

    if args.dry_run:
        print(json.dumps(receipt, indent=2 if args.pretty else None, sort_keys=args.pretty))
        return 0

    secret = os.environ.get("EVIDENCEPLANE_HMAC_SECRET")
    if not secret:
        print("error: EVIDENCEPLANE_HMAC_SECRET is required", file=sys.stderr)
        return 2

    try:
        decision = submit_receipt(args.evidenceplane_url, body, secret)
    except RuntimeError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    print(json.dumps(decision, indent=2 if args.pretty else None, sort_keys=args.pretty))
    return (
        1
        if should_fail(
            decision["decision"],
            enforce_review=bool_flag(args.enforce_review, default=False),
            enforce_block=bool_flag(args.enforce_block, default=True),
        )
        else 0
    )


if __name__ == "__main__":
    raise SystemExit(main())
