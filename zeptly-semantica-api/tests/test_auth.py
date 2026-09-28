"""Server-to-server API key authentication on /v1 routes."""

import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from tests.conftest import API_KEY, make_settings, node_body

pytestmark = pytest.mark.integration

ROUTES = [
    ("put", "/v1/workspaces/ws-auth/nodes/n1"),
    ("get", "/v1/workspaces/ws-auth/nodes/n1"),
    ("delete", "/v1/workspaces/ws-auth/nodes/n1"),
    ("put", "/v1/workspaces/ws-auth/edges/e1"),
    ("delete", "/v1/workspaces/ws-auth/edges/e1"),
    ("post", "/v1/workspaces/ws-auth/relationships/query"),
]


@pytest.mark.parametrize("method,path", ROUTES)
def test_missing_api_key_rejected(anon_client, method, path):
    r = anon_client.request(method.upper(), path, json={})
    assert r.status_code == 401
    assert r.json() == {"detail": "invalid or missing API key"}


@pytest.mark.parametrize("method,path", ROUTES)
@pytest.mark.parametrize("key", ["wrong", API_KEY + "x", API_KEY[:-1], "", " " + API_KEY])
def test_invalid_api_key_rejected(anon_client, method, path, key):
    r = anon_client.request(method.upper(), path, json={}, headers={"X-API-Key": key})
    assert r.status_code == 401


def test_bearer_header_is_not_accepted(anon_client):
    r = anon_client.get(
        "/v1/workspaces/ws-auth/nodes/n1", headers={"Authorization": f"Bearer {API_KEY}"}
    )
    assert r.status_code == 401


def test_valid_api_key_accepted(anon_client, ws):
    r = anon_client.put(
        f"/v1/workspaces/{ws}/nodes/n1",
        json=node_body(ws, "n1"),
        headers={"X-API-Key": API_KEY},
    )
    assert r.status_code == 201
    r = anon_client.get(f"/v1/workspaces/{ws}/nodes/n1", headers={"X-API-Key": API_KEY})
    assert r.status_code == 200


def test_auth_checked_before_payload_validation(anon_client):
    """Unauthenticated callers learn nothing about the payload schema."""
    r = anon_client.put("/v1/workspaces/ws-auth/nodes/n1", json={"content": "x"})
    assert r.status_code == 401


def test_anonymous_mode_only_in_test_env(graph):
    settings = make_settings(
        SEMANTICA_API_KEY="",
        SEMANTICA_ALLOW_ANONYMOUS="true",
        FALKORDB_GRAPH=graph._settings.graph_name,
    )
    with TestClient(create_app(settings, graph)) as c:
        r = c.get("/v1/workspaces/ws-anon/nodes/missing")
        assert r.status_code == 404  # authenticated as anonymous, node absent
