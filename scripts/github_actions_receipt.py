from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import subprocess
import sys
from typing import Any
import xml.etree.ElementTree as ET


def classify_path(path: str) -> str:
    lowered = path.lower()
    if lowered.endswith((".md", ".rst", ".txt", ".adoc")):
        return "docs"
    if lowered.endswith((".png", ".jpg", ".jpeg", ".gif", ".pdf", ".zip", ".wasm")):
        return "binary"
    if lowered.endswith((".yml", ".yaml", ".toml", ".ini", ".cfg", ".json", ".xml")):
        return "config"
    return "code"


def _run_git(repo_root: Path, args: list[str]) -> str:
    return subprocess.check_output(
        ["git", *args],
        cwd=repo_root,
        text=True,
        stderr=subprocess.DEVNULL,
    )


def default_diff_target(env: dict[str, str]) -> str:
    base_ref = env.get("GITHUB_BASE_REF")
    if base_ref:
        return f"origin/{base_ref}...HEAD"

    before = env.get("GITHUB_EVENT_BEFORE", "")
    if before and set(before) != {"0"}:
        return f"{before}...HEAD"
    return "HEAD~1...HEAD"


def changed_files(repo_root: Path, diff_target: str) -> list[dict[str, Any]]:
    try:
        output = _run_git(repo_root, ["diff", "--numstat", diff_target])
    except subprocess.CalledProcessError:
        try:
            output = _run_git(repo_root, ["show", "--numstat", "--format=", "HEAD"])
        except subprocess.CalledProcessError:
            output = ""

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

    return files or [
        {
            "path": "repository",
            "classification": "docs",
            "additions": 0,
            "deletions": 0,
            "secret_detected": False,
        }
    ]


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def parse_junit(paths: list[Path]) -> list[dict[str, str]]:
    tests: list[dict[str, str]] = []
    for path in paths:
        if not path.exists():
            continue
        root = ET.parse(path).getroot()
        for testcase in root.iter():
            if _local_name(testcase.tag) != "testcase":
                continue
            name = testcase.attrib.get("name", "unnamed")
            classname = testcase.attrib.get("classname")
            test_name = f"{classname}::{name}" if classname else name
            child_names = {_local_name(child.tag) for child in list(testcase)}
            if "failure" in child_names or "error" in child_names:
                status = "failed"
            elif "skipped" in child_names:
                status = "skipped"
            else:
                status = "passed"
            tests.append({"name": test_name, "status": status})
    return tests


def _json_objects_from_text(text: str) -> list[Any]:
    try:
        return [json.loads(text)]
    except json.JSONDecodeError:
        objects: list[Any] = []
        for line in text.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                objects.append(json.loads(line))
            except json.JSONDecodeError:
                continue
        return objects


def _finding_paths(value: Any) -> set[str]:
    paths: set[str] = set()
    if isinstance(value, list):
        for item in value:
            paths.update(_finding_paths(item))
    elif isinstance(value, dict):
        for key in ("path", "file", "File", "filename", "Filename"):
            if isinstance(value.get(key), str):
                paths.add(value[key])
        source_metadata = value.get("SourceMetadata")
        if isinstance(source_metadata, dict):
            data = source_metadata.get("Data", {})
            git_data = data.get("Git", {}) if isinstance(data, dict) else {}
            if isinstance(git_data, dict) and isinstance(git_data.get("file"), str):
                paths.add(git_data["file"])
        for key in ("findings", "results", "secrets", "verified_secrets"):
            if key in value:
                paths.update(_finding_paths(value[key]))
    return paths


def secret_scan_findings(path: Path | None, env: dict[str, str]) -> tuple[bool, set[str]]:
    env_flag = env.get("EVIDENCEPLANE_SECRET_DETECTED", "").lower()
    if env_flag in {"1", "true", "yes"}:
        return True, set()
    if path is None or not path.exists():
        return False, set()

    objects = _json_objects_from_text(path.read_text(encoding="utf-8"))
    if not objects:
        return False, set()

    detected = False
    finding_paths: set[str] = set()
    for item in objects:
        finding_paths.update(_finding_paths(item))
        if item is True:
            detected = True
        elif isinstance(item, list) and item:
            detected = True
        elif isinstance(item, dict):
            for key in (
                "secret_detected",
                "secrets_detected",
                "detected",
                "has_findings",
            ):
                if item.get(key) is True:
                    detected = True
            for key in ("findings", "results", "secrets", "verified_secrets"):
                value = item.get(key)
                if isinstance(value, list) and value:
                    detected = True
    return detected or bool(finding_paths), finding_paths


def apply_secret_findings(
    files: list[dict[str, Any]], *, detected: bool, finding_paths: set[str]
) -> list[dict[str, Any]]:
    if not detected:
        return files

    if finding_paths:
        for file in files:
            file["secret_detected"] = file["path"] in finding_paths
        if any(file["secret_detected"] for file in files):
            return files

    files[0]["secret_detected"] = True
    return files


def build_receipt(args: argparse.Namespace, env: dict[str, str]) -> dict[str, Any]:
    repo_root = args.repo_root.resolve()
    diff_target = args.diff_target or default_diff_target(env)
    files = changed_files(repo_root, diff_target)
    detected, finding_paths = secret_scan_findings(args.secret_scan_result, env)
    files = apply_secret_findings(files, detected=detected, finding_paths=finding_paths)

    tests = parse_junit(args.junit)
    test_exit_code = args.test_exit_code
    if test_exit_code is None:
        test_exit_code = int(env.get("TEST_EXIT_CODE", "0"))
    if not tests:
        tests = [
            {
                "name": "github-actions::tests",
                "status": "passed" if test_exit_code == 0 else "failed",
            }
        ]

    return {
        "idempotency_key": (
            f"github-{env['GITHUB_RUN_ID']}-{env.get('GITHUB_RUN_ATTEMPT', '1')}"
        ),
        "repo_name": env["GITHUB_REPOSITORY"],
        "commit_sha": env["GITHUB_SHA"].lower(),
        "branch": env.get("GITHUB_HEAD_REF") or env.get("GITHUB_REF_NAME", "unknown"),
        "actor": env["GITHUB_ACTOR"],
        "timestamp_utc": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "changed_files": files,
        "tests": tests,
        "tool_calls": [
            {
                "tool": args.test_tool,
                "command": args.test_command,
                "network_access": args.tool_network_access,
                "exit_code": test_exit_code,
            }
        ],
        "policy_context": {
            "protected_branch": env.get("GITHUB_REF_PROTECTED", "false").lower()
            == "true",
            "emergency_override": False,
            "approver_email": None,
        },
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build a GitHub Actions RunReceipt.")
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--repo-root", type=Path, default=Path("."))
    parser.add_argument("--diff-target")
    parser.add_argument("--junit", action="append", type=Path, default=[])
    parser.add_argument("--secret-scan-result", type=Path)
    parser.add_argument(
        "--test-exit-code",
        type=lambda x: int(x) if x and x.strip() else None,
        default=None,
    )
    parser.add_argument("--test-tool", default="pytest")
    parser.add_argument("--test-command", default="python -m pytest -q")
    parser.add_argument("--tool-network-access", action="store_true")
    args = parser.parse_args(argv)

    required_env = ["GITHUB_REPOSITORY", "GITHUB_SHA", "GITHUB_ACTOR", "GITHUB_RUN_ID"]
    missing = [name for name in required_env if not os.environ.get(name)]
    if missing:
        print(
            f"error: missing required GitHub environment: {', '.join(missing)}",
            file=sys.stderr,
        )
        return 2

    receipt = build_receipt(args, os.environ)
    args.output.write_text(
        json.dumps(receipt, separators=(",", ":")),
        encoding="utf-8",
    )
    print(str(args.output))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
