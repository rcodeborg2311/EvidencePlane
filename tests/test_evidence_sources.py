from __future__ import annotations

from tests.conftest import admin_headers


def _create_source(client, source_type: str = "gitlab_ci", display_name: str = "GitLab Prod", **kwargs):
    payload = {"source_type": source_type, "display_name": display_name, **kwargs}
    return client.post("/api/v1/sources", json=payload, headers=admin_headers())


# --------------------------------------------------------------------------- #
# CRUD
# --------------------------------------------------------------------------- #

def test_create_source_returns_201(client):
    resp = _create_source(client)
    assert resp.status_code == 201
    body = resp.json()
    assert body["source_type"] == "gitlab_ci"
    assert body["display_name"] == "GitLab Prod"
    assert body["enabled"] is True
    assert body["trust_level"] == "ci_verified"
    assert "source_id" in body


def test_create_source_with_all_fields(client):
    resp = _create_source(
        client,
        source_type="jenkins",
        display_name="Jenkins Enterprise",
        signing_secret="supersecret1234567890",
        trust_level="ci_verified",
        repo_name="myorg/backend",
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["repo_name"] == "myorg/backend"


def test_list_sources_returns_created(client):
    _create_source(client, source_type="gitlab_ci", display_name="A")
    _create_source(client, source_type="buildkite", display_name="B")
    resp = client.get("/api/v1/sources", headers=admin_headers())
    assert resp.status_code == 200
    assert len(resp.json()) == 2


def test_list_sources_excludes_disabled_by_default(client):
    src_id = _create_source(client).json()["source_id"]
    client.delete(f"/api/v1/sources/{src_id}", headers=admin_headers())
    resp = client.get("/api/v1/sources", headers=admin_headers())
    assert resp.status_code == 200
    assert len(resp.json()) == 0


def test_list_sources_includes_disabled_when_asked(client):
    src_id = _create_source(client).json()["source_id"]
    client.delete(f"/api/v1/sources/{src_id}", headers=admin_headers())
    resp = client.get("/api/v1/sources?include_disabled=true", headers=admin_headers())
    assert resp.status_code == 200
    assert len(resp.json()) == 1
    assert resp.json()[0]["enabled"] is False


def test_get_source(client):
    src_id = _create_source(client).json()["source_id"]
    resp = client.get(f"/api/v1/sources/{src_id}", headers=admin_headers())
    assert resp.status_code == 200
    assert resp.json()["source_id"] == src_id


def test_get_source_404(client):
    import uuid
    resp = client.get(f"/api/v1/sources/{uuid.uuid4()}", headers=admin_headers())
    assert resp.status_code == 404


def test_disable_source(client):
    src_id = _create_source(client).json()["source_id"]
    resp = client.delete(f"/api/v1/sources/{src_id}", headers=admin_headers())
    assert resp.status_code == 204
    detail = client.get(f"/api/v1/sources/{src_id}", headers=admin_headers()).json()
    assert detail["enabled"] is False


def test_create_source_requires_admin(client):
    resp = client.post("/api/v1/sources", json={"source_type": "gitlab_ci", "display_name": "X"})
    assert resp.status_code == 401


def test_invalid_source_type_rejected(client):
    resp = _create_source(client, source_type="fax_machine")
    assert resp.status_code == 422


def test_invalid_trust_level_rejected(client):
    resp = _create_source(client, trust_level="super_trusted")
    assert resp.status_code == 422


# --------------------------------------------------------------------------- #
# Source linked to run via source_id in receipt
# --------------------------------------------------------------------------- #

def test_run_links_to_source(client):
    from tests.conftest import base_receipt, post_receipt
    src_id = _create_source(client).json()["source_id"]
    receipt = {**base_receipt("source-link-run"), "source_id": src_id}
    resp = post_receipt(client, receipt)
    assert resp.status_code == 200
    run_id = resp.json()["run_id"]
    # source linking is internal; verify the run stored correctly via evidence
    evidence = client.get(f"/api/v1/evidence/{run_id}").json()
    assert evidence["normalized_input"]["source_id"] == src_id


def test_run_with_unknown_source_id_still_succeeds(client):
    from tests.conftest import base_receipt, post_receipt
    import uuid
    receipt = {**base_receipt("unknown-source-run"), "source_id": str(uuid.uuid4())}
    resp = post_receipt(client, receipt)
    assert resp.status_code == 200


def test_last_seen_updated_after_run(client):
    from tests.conftest import base_receipt, post_receipt
    src_id = _create_source(client).json()["source_id"]
    assert client.get(f"/api/v1/sources/{src_id}", headers=admin_headers()).json()["last_seen_at"] is None
    post_receipt(client, {**base_receipt("last-seen-run"), "source_id": src_id})
    detail = client.get(f"/api/v1/sources/{src_id}", headers=admin_headers()).json()
    assert detail["last_seen_at"] is not None
