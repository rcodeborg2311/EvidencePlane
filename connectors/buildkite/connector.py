#!/usr/bin/env python3
"""EvidencePlane connector for Buildkite.

Required environment variables (set in Buildkite pipeline or agent env):
  EVIDENCEPLANE_URL         Base URL of your EvidencePlane instance
  EVIDENCEPLANE_HMAC_SECRET Signing secret (use Buildkite secrets plugin)

Optional:
  EVIDENCEPLANE_SOURCE_ID   UUID of the registered EvidenceSource record
  EVIDENCEPLANE_DRY_RUN     Set to "1" to print receipt without submitting
  EP_TEST_REPORT_XML        Path to JUnit XML test report

Buildkite variables used:
  BUILDKITE_REPO, BUILDKITE_COMMIT, BUILDKITE_BRANCH, BUILDKITE_BUILD_URL,
  BUILDKITE_PULL_REQUEST, BUILDKITE_PULL_REQUEST_BASE_BRANCH,
  BUILDKITE_BUILD_CREATOR, BUILDKITE_PIPELINE_SLUG
"""
from __future__ import annotations

import hashlib
import os
import subprocess
import sys
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from common import build_receipt, classify_path, require_env, submit_receipt, decision_exit_code


def _git(args: list[str]) -> str:
    try:
        return subprocess.check_output(["git", *args], text=True, stderr=subprocess.DEVNULL).strip()
    except subprocess.CalledProcessError:
        return ""


def collect_changed_files() -> list[dict]:
    try:
        out = subprocess.check_output(["git", "diff", "--numstat", "HEAD~1...HEAD"], text=True, stderr=subprocess.DEVNULL)
    except subprocess.CalledProcessError:
        out = ""
    files = []
    for line in out.splitlines():
        parts = line.split("\t")
        if len(parts) < 3:
            continue
        add, delete, path = parts
        files.append({"path": path, "classification": classify_path(path),
                       "additions": 0 if add == "-" else int(add),
                       "deletions": 0 if delete == "-" else int(delete),
                       "secret_detected": False})
    return files or [{"path": "repository", "classification": "docs", "additions": 0, "deletions": 0, "secret_detected": False}]


def collect_tests(report_path: str | None) -> list[dict]:
    if not report_path or not Path(report_path).exists():
        return []
    try:
        tree = ET.parse(report_path)
        results = []
        for tc in tree.iter("testcase"):
            failed = tc.find("failure") is not None or tc.find("error") is not None
            skipped = tc.find("skipped") is not None
            status = "failed" if failed else ("skipped" if skipped else "passed")
            name = f"{tc.get('classname', '')}.{tc.get('name', '')}".strip(".")
            results.append({"name": name, "status": status})
        return results
    except Exception:
        return []


def collect_pull_request() -> dict | None:
    pr_number_str = os.environ.get("BUILDKITE_PULL_REQUEST", "false")
    if pr_number_str == "false":
        return None
    try:
        pr_number = int(pr_number_str)
    except ValueError:
        return None
    repo_url = os.environ.get("BUILDKITE_REPO", "")
    commit_sha = os.environ.get("BUILDKITE_COMMIT", "0" * 40)
    base_branch = os.environ.get("BUILDKITE_PULL_REQUEST_BASE_BRANCH", "main")
    return {
        "provider": "github",
        "number": pr_number,
        "url": f"{repo_url.rstrip('.git')}/pull/{pr_number}",
        "head_sha": commit_sha,
        "base_branch": base_branch,
    }


def _repo_name_from_url(repo_url: str) -> str:
    url = repo_url.rstrip("/").rstrip(".git")
    parts = url.split("/")
    if len(parts) >= 2:
        return f"{parts[-2]}/{parts[-1]}"
    return parts[-1] if parts else "unknown/repo"


def main() -> None:
    url = require_env("EVIDENCEPLANE_URL")
    secret = require_env("EVIDENCEPLANE_HMAC_SECRET")
    dry_run = os.environ.get("EVIDENCEPLANE_DRY_RUN", "") == "1"

    repo_url = os.environ.get("BUILDKITE_REPO", "")
    repo_name = _repo_name_from_url(repo_url) if repo_url else "unknown/repo"
    commit_sha = os.environ.get("BUILDKITE_COMMIT", _git(["rev-parse", "HEAD"])) or "0" * 40
    branch = os.environ.get("BUILDKITE_BRANCH", _git(["rev-parse", "--abbrev-ref", "HEAD"])) or "unknown"
    actor = os.environ.get("BUILDKITE_BUILD_CREATOR", "buildkite")
    build_url = os.environ.get("BUILDKITE_BUILD_URL")
    source_id = os.environ.get("EVIDENCEPLANE_SOURCE_ID")

    idempotency_key = hashlib.sha256(f"buildkite:{commit_sha}:{branch}".encode()).hexdigest()[:64]

    receipt = build_receipt(
        idempotency_key=idempotency_key,
        repo_name=repo_name,
        commit_sha=commit_sha,
        branch=branch,
        actor=actor,
        timestamp_utc=datetime.now(timezone.utc),
        changed_files=collect_changed_files(),
        tests=collect_tests(os.environ.get("EP_TEST_REPORT_XML")),
        tool_calls=[],
        policy_context={"protected_branch": False, "emergency_override": False, "approver_email": None},
        source_id=source_id,
        source_run_url=build_url,
        pull_request=collect_pull_request(),
    )

    result = submit_receipt(receipt, evidenceplane_url=url, hmac_secret=secret, dry_run=dry_run)
    if result:
        decision = result.get("decision", "unknown")
        print(f"EvidencePlane decision: {decision.upper()} (run_id={result.get('run_id')})")
        sys.exit(decision_exit_code(decision))


if __name__ == "__main__":
    main()
