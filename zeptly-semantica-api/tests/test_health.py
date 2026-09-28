"""Health and readiness, including a real graph-store outage."""

import socket

import pytest
from fastapi.testclient import TestClient

from app.graph import ProjectionGraph
from app.main import create_app
from tests.conftest import API_KEY, make_settings, node_body


def _closed_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()  # nothing listens here any more
    return port


def test_health_is_unauthenticated_minimal_and_graph_independent():
    """Liveness must not depend on FalkorDB or on authentication."""
    settings = make_settings(FALKORDB_HOST="127.0.0.1", FALKORDB_PORT=str(_closed_port()))
    with TestClient(create_app(settings, ProjectionGraph(settings))) as c:
        r = c.get("/health")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}


@pytest.mark.integration
def test_ready_when_graph_store_reachable(anon_client):
    r = anon_client.get("/ready")
    assert r.status_code == 200
    assert r.json() == {"status": "ready"}


def test_graph_store_outage_is_not_ready():
    """Point the service at a FalkorDB address with nothing listening: a real
    connection failure through Semantica GraphStore, not a mock."""
    settings = make_settings(
        FALKORDB_HOST="127.0.0.1", FALKORDB_PORT=str(_closed_port()), FALKORDB_GRAPH="outage"
    )
    with TestClient(create_app(settings, ProjectionGraph(settings))) as c:
        assert c.get("/health").status_code == 200

        r = c.get("/ready")
        assert r.status_code == 503
        assert r.json() == {"status": "unavailable"}
        # Nothing about hosts, ports or exceptions leaks.
        assert "127.0.0.1" not in r.text and "Error" not in r.text

        r = c.put(
            "/v1/workspaces/ws-a/nodes/n1",
            json=node_body("ws-a", "n1"),
            headers={"X-API-Key": API_KEY},
        )
        assert r.status_code == 503
        assert r.json() == {"detail": "graph store unavailable"}


def test_docs_disabled_outside_development():
    settings = make_settings(
        SEMANTICA_ENV="production", FALKORDB_PORT=str(_closed_port()), FALKORDB_PASSWORD="pw"
    )
    with TestClient(create_app(settings, ProjectionGraph(settings))) as c:
        assert c.get("/docs").status_code == 404
        assert c.get("/openapi.json").status_code == 404
