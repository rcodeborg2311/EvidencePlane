from __future__ import annotations

from tests.conftest import admin_headers, base_receipt, post_receipt


def _review_receipt(key: str) -> dict:
    """Receipt that triggers review decision (code with no passing tests)."""
    r = base_receipt(key)
    r["changed_files"][0]["classification"] = "code"
    return r


def _block_receipt(key: str) -> dict:
    """Receipt that triggers block decision (secret detected)."""
    r = base_receipt(key)
    r["changed_files"][0]["secret_detected"] = True
    return r


def _allow_receipt(key: str) -> dict:
    return base_receipt(key)


# --------------------------------------------------------------------------- #
# Auto-case creation
# --------------------------------------------------------------------------- #

def test_review_decision_creates_open_case(client):
    run_id = post_receipt(client, _review_receipt("case-auto-review")).json()["run_id"]
    resp = client.get(f"/api/v1/runs/{run_id}/case", headers=admin_headers())
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "open"
    assert body["case_type"] == "review_required"
    assert body["run_id"] == run_id


def test_block_decision_creates_open_case(client):
    run_id = post_receipt(client, _block_receipt("case-auto-block")).json()["run_id"]
    body = client.get(f"/api/v1/runs/{run_id}/case", headers=admin_headers()).json()
    assert body["case_type"] == "blocked_change"
    assert body["severity"] == "high"


def test_allow_decision_creates_no_case(client):
    run_id = post_receipt(client, _allow_receipt("case-no-case")).json()["run_id"]
    resp = client.get(f"/api/v1/runs/{run_id}/case", headers=admin_headers())
    assert resp.status_code == 200
    assert resp.json() is None


def test_case_title_contains_repo_and_decision(client):
    run_id = post_receipt(client, _review_receipt("case-title-check")).json()["run_id"]
    body = client.get(f"/api/v1/runs/{run_id}/case", headers=admin_headers()).json()
    assert "docs-site" in body["title"]
    assert "REVIEW" in body["title"]


def test_case_has_created_event(client):
    run_id = post_receipt(client, _review_receipt("case-events-check")).json()["run_id"]
    body = client.get(f"/api/v1/runs/{run_id}/case", headers=admin_headers()).json()
    assert len(body["events"]) == 1
    assert body["events"][0]["event_type"] == "created"
    assert body["events"][0]["actor_identity"] == "system"
    assert body["events"][0]["sequence"] == 1
    assert body["events"][0]["previous_event_sha256"] is None


def test_case_event_has_valid_sha256(client):
    run_id = post_receipt(client, _review_receipt("case-sha-check")).json()["run_id"]
    body = client.get(f"/api/v1/runs/{run_id}/case", headers=admin_headers()).json()
    sha = body["events"][0]["event_sha256"]
    assert len(sha) == 64
    assert all(c in "0123456789abcdef" for c in sha)


# --------------------------------------------------------------------------- #
# List cases
# --------------------------------------------------------------------------- #

def test_list_cases_returns_all(client):
    post_receipt(client, _review_receipt("list-case-a"))
    post_receipt(client, _block_receipt("list-case-b"))
    resp = client.get("/api/v1/cases", headers=admin_headers())
    assert resp.status_code == 200
    assert len(resp.json()) == 2


def test_list_cases_filter_by_status(client):
    post_receipt(client, _review_receipt("filter-status-a"))
    post_receipt(client, _review_receipt("filter-status-b"))
    resp = client.get("/api/v1/cases?status=open", headers=admin_headers())
    assert all(c["status"] == "open" for c in resp.json())


def test_list_cases_filter_by_severity(client):
    post_receipt(client, _block_receipt("filter-sev-high"))
    post_receipt(client, _review_receipt("filter-sev-medium"))
    highs = client.get("/api/v1/cases?severity=high", headers=admin_headers()).json()
    assert all(c["severity"] == "high" for c in highs)


def test_list_cases_filter_by_type(client):
    post_receipt(client, _review_receipt("filter-type-review"))
    post_receipt(client, _block_receipt("filter-type-block"))
    reviews = client.get("/api/v1/cases?case_type=review_required", headers=admin_headers()).json()
    assert all(c["case_type"] == "review_required" for c in reviews)


# --------------------------------------------------------------------------- #
# Resolve
# --------------------------------------------------------------------------- #

def test_resolve_case(client):
    run_id = post_receipt(client, _review_receipt("resolve-case")).json()["run_id"]
    case_id = client.get(f"/api/v1/runs/{run_id}/case", headers=admin_headers()).json()["case_id"]

    resp = client.post(
        f"/api/v1/cases/{case_id}/resolve",
        json={"resolved_by": "security@example.com", "resolution_note": "Reviewed and approved."},
        headers=admin_headers(),
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "resolved"
    assert body["resolved_by"] == "security@example.com"
    assert body["resolution_note"] == "Reviewed and approved."
    assert body["resolved_at"] is not None


def test_resolve_case_appends_event(client):
    run_id = post_receipt(client, _review_receipt("resolve-events")).json()["run_id"]
    case_id = client.get(f"/api/v1/runs/{run_id}/case", headers=admin_headers()).json()["case_id"]
    client.post(
        f"/api/v1/cases/{case_id}/resolve",
        json={"resolved_by": "reviewer@example.com"},
        headers=admin_headers(),
    )
    events = client.get(f"/api/v1/cases/{case_id}", headers=admin_headers()).json()["events"]
    assert len(events) == 2
    assert events[1]["event_type"] == "resolved"
    assert events[1]["previous_event_sha256"] == events[0]["event_sha256"]


def test_resolve_already_resolved_case_returns_409(client):
    run_id = post_receipt(client, _review_receipt("resolve-twice")).json()["run_id"]
    case_id = client.get(f"/api/v1/runs/{run_id}/case", headers=admin_headers()).json()["case_id"]
    payload = {"resolved_by": "someone@example.com"}
    client.post(f"/api/v1/cases/{case_id}/resolve", json=payload, headers=admin_headers())
    resp = client.post(f"/api/v1/cases/{case_id}/resolve", json=payload, headers=admin_headers())
    assert resp.status_code == 409


# --------------------------------------------------------------------------- #
# Dismiss
# --------------------------------------------------------------------------- #

def test_dismiss_case(client):
    run_id = post_receipt(client, _review_receipt("dismiss-case")).json()["run_id"]
    case_id = client.get(f"/api/v1/runs/{run_id}/case", headers=admin_headers()).json()["case_id"]
    resp = client.post(
        f"/api/v1/cases/{case_id}/dismiss",
        json={"resolved_by": "admin@example.com", "resolution_note": "False positive."},
        headers=admin_headers(),
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "dismissed"


# --------------------------------------------------------------------------- #
# Messages
# --------------------------------------------------------------------------- #

def test_add_message(client):
    run_id = post_receipt(client, _review_receipt("msg-case")).json()["run_id"]
    case_id = client.get(f"/api/v1/runs/{run_id}/case", headers=admin_headers()).json()["case_id"]
    resp = client.post(
        f"/api/v1/cases/{case_id}/messages",
        json={"body": "I checked the diff — looks clean.", "author_identity": "alice@example.com"},
        headers=admin_headers(),
    )
    assert resp.status_code == 200
    msgs = resp.json()["messages"]
    assert len(msgs) == 1
    assert msgs[0]["body"] == "I checked the diff — looks clean."
    assert len(msgs[0]["message_sha256"]) == 64


def test_add_message_appends_event(client):
    run_id = post_receipt(client, _review_receipt("msg-event-case")).json()["run_id"]
    case_id = client.get(f"/api/v1/runs/{run_id}/case", headers=admin_headers()).json()["case_id"]
    client.post(
        f"/api/v1/cases/{case_id}/messages",
        json={"body": "Checking...", "author_identity": "bob@example.com"},
        headers=admin_headers(),
    )
    events = client.get(f"/api/v1/cases/{case_id}", headers=admin_headers()).json()["events"]
    assert any(e["event_type"] == "message_added" for e in events)


# --------------------------------------------------------------------------- #
# External links
# --------------------------------------------------------------------------- #

def test_add_slack_link(client):
    run_id = post_receipt(client, _block_receipt("link-case")).json()["run_id"]
    case_id = client.get(f"/api/v1/runs/{run_id}/case", headers=admin_headers()).json()["case_id"]
    resp = client.post(
        f"/api/v1/cases/{case_id}/links",
        json={
            "provider": "slack",
            "url": "https://slack.com/archives/C123/p456",
            "link_type": "thread",
            "created_by": "ops@example.com",
        },
        headers=admin_headers(),
    )
    assert resp.status_code == 200
    links = resp.json()["external_links"]
    assert len(links) == 1
    assert links[0]["provider"] == "slack"
    assert links[0]["link_type"] == "thread"


def test_add_jira_link(client):
    run_id = post_receipt(client, _block_receipt("jira-case")).json()["run_id"]
    case_id = client.get(f"/api/v1/runs/{run_id}/case", headers=admin_headers()).json()["case_id"]
    resp = client.post(
        f"/api/v1/cases/{case_id}/links",
        json={
            "provider": "jira",
            "url": "https://myorg.atlassian.net/browse/SEC-42",
            "external_id": "SEC-42",
            "link_type": "issue",
        },
        headers=admin_headers(),
    )
    assert resp.status_code == 200
    assert resp.json()["external_links"][0]["external_id"] == "SEC-42"


def test_invalid_link_provider_rejected(client):
    run_id = post_receipt(client, _block_receipt("bad-provider")).json()["run_id"]
    case_id = client.get(f"/api/v1/runs/{run_id}/case", headers=admin_headers()).json()["case_id"]
    resp = client.post(
        f"/api/v1/cases/{case_id}/links",
        json={"provider": "fax_machine", "url": "https://example.com", "link_type": "issue"},
        headers=admin_headers(),
    )
    assert resp.status_code == 422


# --------------------------------------------------------------------------- #
# Evidence pack includes case
# --------------------------------------------------------------------------- #

def test_evidence_pack_includes_case_for_review(client):
    run_id = post_receipt(client, _review_receipt("evidence-case")).json()["run_id"]
    evidence = client.get(f"/api/v1/evidence/{run_id}").json()
    assert evidence["case"] is not None
    assert evidence["case"]["status"] == "open"
    assert evidence["case"]["case_type"] == "review_required"


def test_evidence_pack_has_null_case_for_allow(client):
    run_id = post_receipt(client, _allow_receipt("evidence-no-case")).json()["run_id"]
    evidence = client.get(f"/api/v1/evidence/{run_id}").json()
    assert evidence["case"] is None


def test_evidence_pack_case_updates_after_resolve(client):
    run_id = post_receipt(client, _review_receipt("evidence-resolve-case")).json()["run_id"]
    case_id = client.get(f"/api/v1/runs/{run_id}/case", headers=admin_headers()).json()["case_id"]
    client.post(
        f"/api/v1/cases/{case_id}/resolve",
        json={"resolved_by": "reviewer@example.com"},
        headers=admin_headers(),
    )
    evidence = client.get(f"/api/v1/evidence/{run_id}").json()
    assert evidence["case"]["status"] == "resolved"


def test_evidence_sha256_unchanged_after_case_created(client):
    """Case data is advisory — must not alter the sealed evidence_sha256."""
    run_id = post_receipt(client, _review_receipt("sha-stable")).json()["run_id"]
    resp1 = client.get(f"/api/v1/evidence/{run_id}").json()
    sha = resp1["evidence_sha256"]
    # Resolve the case
    case_id = client.get(f"/api/v1/runs/{run_id}/case", headers=admin_headers()).json()["case_id"]
    client.post(f"/api/v1/cases/{case_id}/resolve", json={"resolved_by": "r@example.com"}, headers=admin_headers())
    resp2 = client.get(f"/api/v1/evidence/{run_id}").json()
    assert resp2["evidence_sha256"] == sha


# --------------------------------------------------------------------------- #
# Auth
# --------------------------------------------------------------------------- #

def test_list_cases_requires_auth(client):
    resp = client.get("/api/v1/cases")
    assert resp.status_code == 401


def test_resolve_case_requires_auth(client):
    import uuid
    resp = client.post(f"/api/v1/cases/{uuid.uuid4()}/resolve", json={"resolved_by": "x"})
    assert resp.status_code == 401
