from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys
from typing import Any


CONCLUSIONS = {
    "allow": "success",
    "review": "warning",
    "block": "failure",
}


def bool_flag(value: str | None, *, default: bool) -> bool:
    if value is None or value == "":
        return default
    return value.lower() in {"1", "true", "yes", "on"}


def configured_bool(cli_value: str | None, env_name: str, *, default: bool) -> bool:
    if cli_value is not None:
        return bool_flag(cli_value, default=default)
    return bool_flag(os.environ.get(env_name), default=default)


def evidence_url(base_url: str | None, run_id: str) -> str | None:
    if not base_url:
        return None
    return f"{base_url.rstrip('/')}/runs/{run_id}"


def next_action(decision: str) -> str:
    if decision == "allow":
        return "allowed"
    if decision == "review":
        return "human review required"
    return "blocked"


def summary_markdown(decision: dict[str, Any], evidence_link: str | None) -> str:
    status = decision["decision"]
    review_status = decision.get("review_status", "unknown")
    lines = [
        "## EvidencePlane Decision",
        "",
        "| Field | Value |",
        "| --- | --- |",
        f"| Decision | `{status}` |",
        f"| Next action | `{next_action(status)}` |",
        f"| Risk score | `{decision['risk_score']}` |",
        f"| Policy version | `{decision.get('policy_version', 'unknown')}` |",
        f"| Review status | `{review_status}` |",
        f"| Evidence SHA-256 | `{decision['evidence_sha256']}` |",
        f"| Run ID | `{decision['run_id']}` |",
    ]
    if evidence_link:
        lines.append(f"| EvidencePlane run | {evidence_link} |")

    violations = decision.get("violations", [])
    lines.extend(["", "### Violations", ""])
    if violations:
        lines.extend(["| Severity | Code | Message |", "| --- | --- | --- |"])
        for violation in violations:
            lines.append(
                f"| `{violation['severity']}` | `{violation['code']}` | "
                f"{violation['message']} |"
            )
    else:
        lines.append("No policy violations were recorded.")
    return "\n".join(lines) + "\n"


def write_outputs(outputs_file: Path | None, values: dict[str, str]) -> None:
    if outputs_file is None:
        return
    with outputs_file.open("a", encoding="utf-8") as stream:
        for key, value in values.items():
            print(f"{key}={value}", file=stream)


def should_fail(decision: str, *, enforce_review: bool, enforce_block: bool) -> bool:
    if decision == "review":
        return enforce_review
    if decision == "block":
        return enforce_block
    return False


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Publish EvidencePlane decision feedback for GitHub Actions."
    )
    parser.add_argument("--decision-file", required=True, type=Path)
    parser.add_argument("--base-url")
    parser.add_argument("--run-detail-base-url")
    parser.add_argument("--summary-file", type=Path)
    parser.add_argument("--outputs-file", type=Path)
    parser.add_argument("--enforce-review", choices=["true", "false"])
    parser.add_argument("--enforce-block", choices=["true", "false"])
    parser.add_argument(
        "--enforce",
        action="store_true",
        help="Backward-compatible alias for --enforce-block true.",
    )
    args = parser.parse_args(argv)

    decision = json.loads(args.decision_file.read_text(encoding="utf-8"))
    status = decision["decision"]
    conclusion = CONCLUSIONS[status]
    enforce_review = configured_bool(
        args.enforce_review,
        "EVIDENCEPLANE_ENFORCE_REVIEW",
        default=False,
    )
    enforce_block = (
        True
        if args.enforce
        else configured_bool(
            args.enforce_block,
            "EVIDENCEPLANE_ENFORCE_BLOCK",
            default=True,
        )
    )

    detail_base_url = (
        args.run_detail_base_url
        or os.environ.get("EVIDENCEPLANE_RUN_DETAIL_BASE_URL")
        or args.base_url
        or os.environ.get("EVIDENCEPLANE_URL")
    )
    link = evidence_url(detail_base_url, decision["run_id"])
    summary = summary_markdown(decision, link)

    summary_file = args.summary_file
    if summary_file is None and os.environ.get("GITHUB_STEP_SUMMARY"):
        summary_file = Path(os.environ["GITHUB_STEP_SUMMARY"])
    if summary_file is not None:
        with summary_file.open("a", encoding="utf-8") as stream:
            stream.write(summary)

    outputs_file = args.outputs_file
    if outputs_file is None and os.environ.get("GITHUB_OUTPUT"):
        outputs_file = Path(os.environ["GITHUB_OUTPUT"])
    write_outputs(
        outputs_file,
        {
            "evidenceplane_decision": status,
            "evidenceplane_conclusion": conclusion,
            "evidenceplane_risk_score": str(decision["risk_score"]),
            "evidenceplane_review_status": str(decision.get("review_status", "")),
            "evidenceplane_run_id": decision["run_id"],
            "evidenceplane_url": link or "",
        },
    )

    if status == "review":
        print(
            f"::warning::EvidencePlane returned review "
            f"(risk {decision['risk_score']}).",
            file=sys.stderr,
        )
    elif status == "block":
        print(
            f"::error::EvidencePlane returned block "
            f"(risk {decision['risk_score']}).",
            file=sys.stderr,
        )

    print(summary)
    return 1 if should_fail(status, enforce_review=enforce_review, enforce_block=enforce_block) else 0


if __name__ == "__main__":
    raise SystemExit(main())
