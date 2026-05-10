from __future__ import annotations

from tests.conftest import admin_headers, base_receipt, post_receipt


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #

_DEFAULT_CONFIG = {
    "failed_tests": "block",
    "code_without_passing_tests": "review",
    "network_access": "review",
    "large_protected_branch_change": "review",
    "large_change_threshold": 500,
}


def _create_config(client, repo_name: str | None = None, config: dict | None = None):
    payload = {"repo_name": repo_name, "config": config or _DEFAULT_CONFIG}
    return client.post(
        "/api/v1/policy-configs",
        json=payload,
        headers=admin_headers(),
    )


# --------------------------------------------------------------------------- #
# CRUD
# --------------------------------------------------------------------------- #

def test_create_policy_config_returns_201(client):
    resp = _create_config(client, repo_name="myorg/my-repo")
    assert resp.status_code == 201
    body = resp.json()
    assert body["repo_name"] == "myorg/my-repo"
    assert body["version"] == 1
    assert body["is_active"] is True
    assert body["config"]["failed_tests"] == "block"


def test_create_policy_config_version_increments(client):
    _create_config(client, repo_name="myorg/my-repo")
    resp = _create_config(client, repo_name="myorg/my-repo")
    assert resp.status_code == 201
    assert resp.json()["version"] == 2


def test_list_policy_configs(client):
    _create_config(client, repo_name="myorg/repo-a")
    _create_config(client, repo_name="myorg/repo-b")
    resp = client.get("/api/v1/policy-configs", headers=admin_headers())
    assert resp.status_code == 200
    assert len(resp.json()) == 2


def test_list_policy_configs_filtered_by_repo(client):
    _create_config(client, repo_name="myorg/repo-a")
    _create_config(client, repo_name="myorg/repo-b")
    resp = client.get(
        "/api/v1/policy-configs?repo_name=myorg/repo-a", headers=admin_headers()
    )
    assert resp.status_code == 200
    assert all(c["repo_name"] == "myorg/repo-a" for c in resp.json())


def test_get_policy_config(client):
    created = _create_config(client, repo_name="myorg/my-repo").json()
    config_id = created["config_id"]
    resp = client.get(f"/api/v1/policy-configs/{config_id}", headers=admin_headers())
    assert resp.status_code == 200
    assert resp.json()["config_id"] == config_id


def test_deactivate_policy_config(client):
    config_id = _create_config(client, repo_name="myorg/my-repo").json()["config_id"]
    resp = client.delete(f"/api/v1/policy-configs/{config_id}", headers=admin_headers())
    assert resp.status_code == 204
    detail = client.get(f"/api/v1/policy-configs/{config_id}", headers=admin_headers()).json()
    assert detail["is_active"] is False


def test_create_config_requires_admin(client):
    resp = client.post("/api/v1/policy-configs", json={"config": _DEFAULT_CONFIG})
    assert resp.status_code == 401


# --------------------------------------------------------------------------- #
# Policy config applied to runs
# --------------------------------------------------------------------------- #

def test_run_uses_default_policy_without_config(client):
    receipt = {
        **base_receipt("no-config-run"),
        "tests": [{"name": "test_a", "status": "failed"}],
    }
    resp = post_receipt(client, receipt)
    assert resp.status_code == 200
    assert resp.json()["decision"] == "block"


def test_run_uses_custom_policy_failed_tests_review(client):
    _create_config(
        client,
        repo_name="docs-site",
        config={**_DEFAULT_CONFIG, "failed_tests": "review"},
    )
    receipt = {
        **base_receipt("custom-policy-run"),
        "tests": [{"name": "test_a", "status": "failed"}],
    }
    resp = post_receipt(client, receipt)
    assert resp.status_code == 200
    assert resp.json()["decision"] == "review"


def test_evidence_pack_includes_policy_config_snapshot(client):
    _create_config(
        client,
        repo_name="docs-site",
        config={**_DEFAULT_CONFIG, "failed_tests": "review"},
    )
    receipt = {
        **base_receipt("evidence-config-run"),
        "tests": [{"name": "test_a", "status": "failed"}],
    }
    run_id = post_receipt(client, receipt).json()["run_id"]
    evidence = client.get(f"/api/v1/evidence/{run_id}").json()
    assert evidence["policy_config_snapshot"] is not None
    assert evidence["policy_config_snapshot"]["failed_tests"] == "review"
    assert evidence["policy_config_id"] is not None


def test_evidence_pack_has_null_snapshot_without_config(client):
    run_id = post_receipt(client, base_receipt("no-snap-run")).json()["run_id"]
    evidence = client.get(f"/api/v1/evidence/{run_id}").json()
    assert evidence["policy_config_snapshot"] is None
    assert evidence["policy_config_id"] is None


def test_inactive_config_is_not_applied(client):
    created = _create_config(
        client,
        repo_name="docs-site",
        config={**_DEFAULT_CONFIG, "failed_tests": "review"},
    ).json()
    client.delete(f"/api/v1/policy-configs/{created['config_id']}", headers=admin_headers())

    receipt = {
        **base_receipt("inactive-config-run"),
        "tests": [{"name": "test_a", "status": "failed"}],
    }
    resp = post_receipt(client, receipt)
    assert resp.json()["decision"] == "block"


# --------------------------------------------------------------------------- #
# Dry-run
# --------------------------------------------------------------------------- #

def test_dry_run_shows_would_change(client):
    post_receipt(
        client,
        {**base_receipt("dry-run-base"), "tests": [{"name": "t", "status": "failed"}]},
    )

    resp = client.post(
        "/api/v1/policy-configs/dry-run",
        json={"repo_name": "docs-site", "config": {**_DEFAULT_CONFIG, "failed_tests": "review"}},
        headers=admin_headers(),
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["total_runs"] == 1
    assert body["would_change"] == 1
    assert body["changes"][0]["old_decision"] == "block"
    assert body["changes"][0]["new_decision"] == "review"


def test_dry_run_no_change_when_same_config(client):
    post_receipt(client, base_receipt("dry-run-no-change"))

    resp = client.post(
        "/api/v1/policy-configs/dry-run",
        json={"repo_name": "docs-site", "config": _DEFAULT_CONFIG},
        headers=admin_headers(),
    )
    assert resp.status_code == 200
    assert resp.json()["would_change"] == 0


def test_dry_run_empty_repo(client):
    resp = client.post(
        "/api/v1/policy-configs/dry-run",
        json={"repo_name": "nonexistent/repo", "config": _DEFAULT_CONFIG},
        headers=admin_headers(),
    )
    assert resp.status_code == 200
    assert resp.json()["total_runs"] == 0
    assert resp.json()["would_change"] == 0


# --------------------------------------------------------------------------- #
# Validation
# --------------------------------------------------------------------------- #

def test_invalid_failed_tests_value_rejected(client):
    resp = _create_config(
        client,
        repo_name="myorg/my-repo",
        config={**_DEFAULT_CONFIG, "failed_tests": "allow"},
    )
    assert resp.status_code == 422


def test_invalid_network_access_value_rejected(client):
    resp = _create_config(
        client,
        repo_name="myorg/my-repo",
        config={**_DEFAULT_CONFIG, "network_access": "invalid"},
    )
    assert resp.status_code == 422
