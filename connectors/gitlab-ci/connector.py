#!/usr/bin/env python3
"""EvidencePlane connector for GitLab CI.

Required environment variables (set as CI/CD variables in GitLab):
  EVIDENCEPLANE_URL         Base URL of your EvidencePlane instance
  EVIDENCEPLANE_HMAC_SECRET Per-source signing secret (set as masked variable)

Optional:
  EVIDENCEPLANE_SOURCE_ID   UUID of the registered EvidenceSource record
  EVIDENCEPLANE_DRY_RUN     Set to "1" to print receipt without submitting
  EP_TEST_REPORT_XML        Path to JUnit XML test report (e.g. report.xml)

GitLab CI variables used automatically:
  CI_PROJECT_PATH, CI_COMMIT_SHA, CI_COMMIT_REF_NAME, CI_COMMIT_BEFORE_SHA,
  CI_PIPELINE_URL, CI_MERGE_REQUEST_IID, CI_MERGE_REQUEST_SOURCE_BRANCH_SHA,
  CI_MERGE_REQUEST_SOURCE_BRANCH_NAME, CI_MERGE_REQUEST_TARGET_BRANCH_NAME,
  CI_GITLAB_URL, CI_PROJECT_PATH, GITLAB_USER_LOGIN
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
    before = os.environ.get("CI_COMMIT_BEFORE_SHA", "")
    target = f"{before}...HEAD" if before and set(before) != {"0"} else "HEAD~1...HEAD"
    try:
        out = subprocess.check_output(["git", "diff", "--numstat", target], text=True, stderr=subprocess.DEVNULL)
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
    mr_iid = os.environ.get("CI_MERGE_REQUEST_IID")
    if not mr_iid:
        return None
    gitlab_url = os.environ.get("CI_SERVER_URL", "https://gitlab.com")
    project = os.environ.get("CI_PROJECT_PATH", "")
    head_sha = os.environ.get("CI_MERGE_REQUEST_SOURCE_BRANCH_SHA", os.environ.get("CI_COMMIT_SHA", "0" * 40))
    base_branch = os.environ.get("CI_MERGE_REQUEST_TARGET_BRANCH_NAME", "main")
    return {
        "provider": "gitlab",
        "number": int(mr_iid),
        "url": f"{gitlab_url}/{project}/-/merge_requests/{mr_iid}",
        "head_sha": head_sha,
        "base_branch": base_branch,
    }


def main() -> None:
    url = require_env("EVIDENCEPLANE_URL")
    secret = require_env("EVIDENCEPLANE_HMAC_SECRET")
    dry_run = os.environ.get("EVIDENCEPLANE_DRY_RUN", "") == "1"

    repo_name = os.environ.get("CI_PROJECT_PATH", "unknown/repo")
    commit_sha = os.environ.get("CI_COMMIT_SHA", _git(["rev-parse", "HEAD"])) or "0" * 40
    branch = os.environ.get("CI_COMMIT_REF_NAME", _git(["rev-parse", "--abbrev-ref", "HEAD"])) or "unknown"
    actor = os.environ.get("GITLAB_USER_LOGIN", os.environ.get("CI_COMMIT_AUTHOR", "ci-bot"))
    pipeline_url = os.environ.get("CI_PIPELINE_URL")
    source_id = os.environ.get("EVIDENCEPLANE_SOURCE_ID")

    idempotency_key = hashlib.sha256(f"gitlab:{commit_sha}:{branch}".encode()).hexdigest()[:64]

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
        source_run_url=pipeline_url,
        pull_request=collect_pull_request(),
    )

    result = submit_receipt(receipt, evidenceplane_url=url, hmac_secret=secret, dry_run=dry_run)
    if result:
        decision = result.get("decision", "unknown")
        print(f"EvidencePlane decision: {decision.upper()} (run_id={result.get('run_id')})")
        sys.exit(decision_exit_code(decision))


if __name__ == "__main__":
    main()
