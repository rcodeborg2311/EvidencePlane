from __future__ import annotations

from tests.conftest import base_receipt, json_body, post_receipt, signed_headers


def violation_codes(response_json: dict) -> list[str]:
    return [violation["code"] for violation in response_json["violations"]]


def test_valid_signature_is_accepted(client):
    response = post_receipt(client, base_receipt("valid-signature"))

    assert response.status_code == 200
    assert response.json()["decision"] == "allow"


def test_invalid_signature_returns_401(client):
    body = json_body(base_receipt("invalid-signature"))
    headers = signed_headers(body)
    headers["X-EvidencePlane-Signature"] = "0" * 64

    response = client.post("/api/v1/runs", content=body, headers=headers)

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "invalid_signature"


def test_payload_over_256_kb_returns_413(client):
    payload = base_receipt("oversized")
    payload["changed_files"][0]["path"] = "a" * (256 * 1024)
    body = json_body(payload)

    response = client.post("/api/v1/runs", content=body, headers=signed_headers(body))

    assert response.status_code == 413
    assert response.json()["error"]["code"] == "payload_too_large"


def test_secret_detected_blocks_with_risk_95(client):
    payload = base_receipt("secret-detected")
    payload["changed_files"][0]["secret_detected"] = True

    response = post_receipt(client, payload)

    assert response.status_code == 200
    data = response.json()
    assert data["decision"] == "block"
    assert data["risk_score"] == 95
    assert violation_codes(data) == ["SECRET_PATTERN_DETECTED"]


def test_failed_test_blocks_with_risk_90(client):
    payload = base_receipt("failed-test")
    payload["tests"] = [{"name": "tests/test_refund.py::test_refund", "status": "failed"}]

    response = post_receipt(client, payload)

    assert response.status_code == 200
    data = response.json()
    assert data["decision"] == "block"
    assert data["risk_score"] == 90
    assert violation_codes(data) == ["FAILED_TESTS"]


def test_code_change_with_zero_passed_tests_reviews_with_risk_60(client):
    payload = base_receipt("no-test-evidence")
    payload["changed_files"][0]["path"] = "src/refunds.py"
    payload["changed_files"][0]["classification"] = "code"
    payload["tests"] = [{"name": "tests/test_placeholder.py::test_stub", "status": "skipped"}]

    response = post_receipt(client, payload)

    assert response.status_code == 200
    data = response.json()
    assert data["decision"] == "review"
    assert data["risk_score"] == 60
    assert violation_codes(data) == ["NO_TEST_EVIDENCE"]


def test_network_access_reviews(client):
    payload = base_receipt("network-tool")
    payload["tool_calls"] = [
        {
            "tool": "curl",
            "command": "curl https://example.invalid",
            "network_access": True,
            "exit_code": 0,
        }
    ]

    response = post_receipt(client, payload)

    assert response.status_code == 200
    data = response.json()
    assert data["decision"] == "review"
    assert data["risk_score"] == 55
    assert violation_codes(data) == ["NETWORK_TOOL_USAGE"]


def test_protected_branch_large_change_reviews(client):
    payload = base_receipt("large-protected")
    payload["changed_files"][0]["additions"] = 501
    payload["policy_context"]["protected_branch"] = True

    response = post_receipt(client, payload)

    assert response.status_code == 200
    data = response.json()
    assert data["decision"] == "review"
    assert data["risk_score"] == 70
    assert violation_codes(data) == ["LARGE_CHANGE_ON_PROTECTED_BRANCH"]


def test_emergency_override_without_approver_blocks_with_risk_100(client):
    payload = base_receipt("override-no-approver")
    payload["policy_context"]["emergency_override"] = True

    response = post_receipt(client, payload)

    assert response.status_code == 200
    data = response.json()
    assert data["decision"] == "block"
    assert data["risk_score"] == 100
    assert violation_codes(data) == ["OVERRIDE_WITHOUT_APPROVER"]


def test_same_idempotency_key_same_payload_returns_same_run_id(client):
    payload = base_receipt("same-payload")

    first = post_receipt(client, payload)
    second = post_receipt(client, payload)

    assert first.status_code == 200
    assert second.status_code == 200
    assert second.json()["run_id"] == first.json()["run_id"]


def test_same_idempotency_key_different_payload_returns_409(client):
    payload = base_receipt("conflicting-payload")
    changed = base_receipt("conflicting-payload")
    changed["actor"] = "different-ci-bot"

    first = post_receipt(client, payload)
    second = post_receipt(client, changed)

    assert first.status_code == 200
    assert second.status_code == 409
    assert second.json()["error"]["code"] == "idempotency_conflict"
