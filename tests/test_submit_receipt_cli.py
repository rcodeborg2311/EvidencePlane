from __future__ import annotations

import hashlib
import hmac
import json
import os
from pathlib import Path
import subprocess
import sys

from app.models.schemas import RunReceipt
from app.services.policy import evaluate_policy

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = REPO_ROOT / "scripts" / "submit_receipt.py"
EXAMPLES = REPO_ROOT / "examples" / "receipts"
TEST_SECRET = "submit-receipt-test-secret"


def _run_cli(*args: str, secret: str | None = TEST_SECRET) -> subprocess.CompletedProcess:
    env = os.environ.copy()
    if secret is None:
        env.pop("EVIDENCEPLANE_HMAC_SECRET", None)
    else:
        env["EVIDENCEPLANE_HMAC_SECRET"] = secret
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )


def test_submit_receipt_dry_run_computes_signature():
    receipt = EXAMPLES / "allow.json"
    result = _run_cli("--file", str(receipt), "--dry-run", "--pretty")
    body = receipt.read_bytes()
    expected_signature = hmac.new(
        TEST_SECRET.encode("utf-8"), body, hashlib.sha256
    ).hexdigest()

    assert result.returncode == 0
    output = json.loads(result.stdout)
    assert output["dry_run"] is True
    assert output["target_url"] == "http://127.0.0.1:8000/api/v1/runs"
    assert output["signature"] == expected_signature
    assert TEST_SECRET not in result.stdout
    assert TEST_SECRET not in result.stderr


def test_submit_receipt_missing_secret_exits_nonzero():
    result = _run_cli("--file", str(EXAMPLES / "allow.json"), "--dry-run", secret=None)

    assert result.returncode != 0
    assert "EVIDENCEPLANE_HMAC_SECRET is required" in result.stderr


def test_submit_receipt_invalid_json_file_exits_nonzero(tmp_path):
    invalid = tmp_path / "receipt.json"
    invalid.write_text("{invalid", encoding="utf-8")

    result = _run_cli("--file", str(invalid), "--dry-run")

    assert result.returncode != 0
    assert "receipt file is not valid JSON" in result.stderr


def test_example_receipts_validate_and_produce_expected_decisions():
    expected = {
        "allow.json": ("allow", 12, []),
        "review.json": ("review", 60, ["NO_TEST_EVIDENCE"]),
        "block.json": ("block", 95, ["SECRET_PATTERN_DETECTED"]),
    }

    for filename, (decision, risk_score, violation_codes) in expected.items():
        payload = json.loads((EXAMPLES / filename).read_text(encoding="utf-8"))
        receipt = RunReceipt.model_validate(payload)
        policy = evaluate_policy(receipt)

        assert policy.decision == decision
        assert policy.risk_score == risk_score
        assert [violation.code for violation in policy.violations] == violation_codes
