from __future__ import annotations

from tests.conftest import admin_headers, base_receipt, post_receipt


def _review_receipt(key: str) -> dict:
    r = base_receipt(key)
    r["changed_files"][0]["classification"] = "code"
    return r


def _block_receipt(key: str) -> dict:
    r = base_receipt(key)
    r["changed_files"][0]["secret_detected"] = True
    return r


def test_dashboard_empty(client):
    resp = client.get("/api/v1/dashboard", headers=admin_headers())
    assert resp.status_code == 200
    body = resp.json()
    assert body["total_runs"] == 0
    assert body["decisions"]["allow"] == 0
    assert body["decisions"]["review"] == 0
    assert body["decisions"]["block"] == 0
    assert body["pending_reviews"] == 0
    assert body["cases"]["open"] == 0
    assert body["top_violations"] == []


def test_dashboard_counts_decisions(client):
    post_receipt(client, base_receipt("dash-allow-1"))
    post_receipt(client, base_receipt("dash-allow-2"))
    post_receipt(client, _review_receipt("dash-review-1"))
    post_receipt(client, _block_receipt("dash-block-1"))

    body = client.get("/api/v1/dashboard", headers=admin_headers()).json()
    assert body["total_runs"] == 4
    assert body["decisions"]["allow"] == 2
    assert body["decisions"]["review"] == 1
    assert body["decisions"]["block"] == 1


def test_dashboard_pending_reviews(client):
    post_receipt(client, _review_receipt("dash-pending-1"))
    post_receipt(client, _review_receipt("dash-pending-2"))
    body = client.get("/api/v1/dashboard", headers=admin_headers()).json()
    assert body["pending_reviews"] == 2


def test_dashboard_cases_breakdown(client):
    run_id = post_receipt(client, _review_receipt("dash-case-open")).json()["run_id"]
    case_id = client.get(f"/api/v1/runs/{run_id}/case", headers=admin_headers()).json()["case_id"]

    body = client.get("/api/v1/dashboard", headers=admin_headers()).json()
    assert body["cases"]["open"] == 1
    assert body["cases"]["resolved"] == 0

    client.post(
        f"/api/v1/cases/{case_id}/resolve",
        json={"resolved_by": "reviewer@example.com"},
        headers=admin_headers(),
    )
    body2 = client.get("/api/v1/dashboard", headers=admin_headers()).json()
    assert body2["cases"]["open"] == 0
    assert body2["cases"]["resolved"] == 1


def test_dashboard_top_violations(client):
    post_receipt(client, _review_receipt("dash-viol-1"))
    post_receipt(client, _block_receipt("dash-viol-2"))
    body = client.get("/api/v1/dashboard", headers=admin_headers()).json()
    assert len(body["top_violations"]) >= 1
    codes = [v["code"] for v in body["top_violations"]]
    assert "CODE_WITHOUT_PASSING_TESTS" in codes or "SECRET_PATTERN_DETECTED" in codes


def test_dashboard_top_violations_sorted_by_count(client):
    # Create multiple runs with code violations (higher count)
    for i in range(3):
        post_receipt(client, _review_receipt(f"dash-sort-{i}"))
    post_receipt(client, _block_receipt("dash-sort-block"))

    body = client.get("/api/v1/dashboard", headers=admin_headers()).json()
    violations = body["top_violations"]
    if len(violations) > 1:
        for i in range(len(violations) - 1):
            assert violations[i]["count"] >= violations[i + 1]["count"]


def test_dashboard_requires_auth(client):
    resp = client.get("/api/v1/dashboard")
    assert resp.status_code == 401
