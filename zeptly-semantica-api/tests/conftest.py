"""Test fixtures.

Integration tests (marker ``integration``) run against a real FalkorDB at
FALKORDB_HOST:FALKORDB_PORT (default localhost:6379) using the pinned
Semantica GraphStore. No graph-store mocks are used anywhere. Each test
session writes to a unique, throwaway graph that is deleted afterwards.
"""

from __future__ import annotations

import os
import uuid
from typing import Iterator

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.graph import ProjectionGraph
from app.main import create_app

API_KEY = "test-key-" + "x" * 40
FALKORDB_HOST = os.environ.get("FALKORDB_HOST", "localhost")
FALKORDB_PORT = os.environ.get("FALKORDB_PORT", "6379")
FALKORDB_PASSWORD = os.environ.get("FALKORDB_PASSWORD", "")


def make_settings(**overrides: str) -> Settings:
    env = {
        "SEMANTICA_ENV": "test",
        "SEMANTICA_API_KEY": API_KEY,
        "SEMANTICA_ALLOW_ANONYMOUS": "false",
        "FALKORDB_HOST": FALKORDB_HOST,
        "FALKORDB_PORT": FALKORDB_PORT,
        "FALKORDB_PASSWORD": FALKORDB_PASSWORD,
        "FALKORDB_TIMEOUT_SECONDS": "2",
    }
    env.update(overrides)
    return Settings.from_env(env)


@pytest.fixture(scope="session")
def graph_name() -> str:
    return "test_" + uuid.uuid4().hex[:12]


@pytest.fixture(scope="session")
def settings(graph_name: str) -> Settings:
    return make_settings(FALKORDB_GRAPH=graph_name)


@pytest.fixture(scope="session")
def graph(settings: Settings) -> Iterator[ProjectionGraph]:
    g = ProjectionGraph(settings)
    if not g.check_ready():
        pytest.fail(
            f"FalkorDB not reachable at {FALKORDB_HOST}:{FALKORDB_PORT}; start it "
            "(docker compose up -d falkordb) or deselect with -m 'not integration'"
        )
    yield g
    g._store._store_backend.delete_graph(settings.graph_name)
    g.close()


@pytest.fixture(scope="session")
def client(settings: Settings, graph: ProjectionGraph) -> Iterator[TestClient]:
    with TestClient(create_app(settings, graph)) as c:
        c.headers.update({"X-API-Key": API_KEY})
        yield c


@pytest.fixture
def anon_client(settings: Settings, graph: ProjectionGraph) -> Iterator[TestClient]:
    """Same app, no credentials attached."""
    with TestClient(create_app(settings, graph)) as c:
        yield c


@pytest.fixture
def ws() -> str:
    """A fresh workspace id per test so tests never observe each other."""
    return "ws-" + uuid.uuid4().hex[:10]


def node_body(ws: str, node_id: str, **extra: object) -> dict:
    body = {
        "workspace_id": ws,
        "canonical_id": node_id,
        "node_type": "memory",
        "label": f"Node {node_id}",
        "lifecycle_state": "active",
        "is_current": True,
        "valid_from": "2026-01-01T00:00:00Z",
        "source_updated_at": "2026-01-02T03:04:05+01:00",
        "source_version": 1,
        "provenance_refs": [f"pg:memories/{node_id}"],
    }
    body.update(extra)
    return body


def edge_body(ws: str, edge_id: str, src: str, tgt: str, **extra: object) -> dict:
    body = {
        "workspace_id": ws,
        "edge_id": edge_id,
        "edge_type": "RELATES_TO",
        "source_node_id": src,
        "target_node_id": tgt,
        "is_current": True,
        "provenance_refs": [f"pg:memory_relationships/{edge_id}"],
    }
    body.update(extra)
    return body
