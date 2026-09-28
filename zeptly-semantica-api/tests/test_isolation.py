"""Workspace isolation: workspace A must never read, modify or traverse B."""

import uuid

import pytest

from app.graph import EDGE_TYPE, NODE_LABEL
from tests.conftest import edge_body, node_body

pytestmark = pytest.mark.integration


@pytest.fixture
def two_workspaces(client):
    a = "ws-a-" + uuid.uuid4().hex[:8]
    b = "ws-b-" + uuid.uuid4().hex[:8]
    # Same canonical id "shared" in both workspaces, plus B-only nodes.
    for ws, nid, label in [
        (a, "shared", "A shared"),
        (a, "a-only", "A only"),
        (b, "shared", "B shared"),
        (b, "b-only", "B only"),
        (b, "b-secret", "B secret"),
    ]:
        assert (
            client.put(
                f"/v1/workspaces/{ws}/nodes/{nid}", json=node_body(ws, nid, label=label)
            ).status_code
            == 201
        )
    assert (
        client.put(
            f"/v1/workspaces/{b}/edges/b-edge", json=edge_body(b, "b-edge", "shared", "b-secret")
        ).status_code
        == 201
    )
    assert (
        client.put(
            f"/v1/workspaces/{a}/edges/a-edge", json=edge_body(a, "a-edge", "shared", "a-only")
        ).status_code
        == 201
    )
    return a, b


def test_workspace_a_cannot_retrieve_workspace_b_node(client, two_workspaces):
    a, b = two_workspaces
    assert client.get(f"/v1/workspaces/{a}/nodes/b-only").status_code == 404
    assert client.get(f"/v1/workspaces/{a}/nodes/b-secret").status_code == 404
    # Same canonical id resolves to each workspace's own node.
    assert client.get(f"/v1/workspaces/{a}/nodes/shared").json()["label"] == "A shared"
    assert client.get(f"/v1/workspaces/{b}/nodes/shared").json()["label"] == "B shared"


def test_workspace_a_cannot_traverse_workspace_b_relationship(client, two_workspaces):
    a, b = two_workspaces
    # Anchoring on a B-only node from A is simply "not found".
    r = client.post(
        f"/v1/workspaces/{a}/relationships/query", json={"workspace_id": a, "node_id": "b-only"}
    )
    assert r.status_code == 404
    # Anchoring on the shared canonical id in A yields only A's edge.
    r = client.post(
        f"/v1/workspaces/{a}/relationships/query",
        json={"workspace_id": a, "node_id": "shared", "include_non_current": True},
    )
    assert r.status_code == 200
    rels = r.json()["relationships"]
    assert [(x["edge"]["edge_id"], x["neighbor"]["canonical_id"]) for x in rels] == [
        ("a-edge", "a-only")
    ]
    assert "b-secret" not in r.text and "B shared" not in r.text and b not in r.text


def test_cross_workspace_edge_written_out_of_band_is_not_traversed(client, graph, two_workspaces):
    """Defence in depth: even if a cross-workspace edge existed in FalkorDB
    (it cannot be created through the API), queries never follow it."""
    a, b = two_workspaces
    graph._store.execute_query(
        f"MATCH (x:{NODE_LABEL} {{key: $ak}}), (y:{NODE_LABEL} {{key: $bk}}) "
        f"CREATE (x)-[:{EDGE_TYPE} {{key: $ek, workspace_id: $a, edge_id: 'rogue', "
        "edge_type: 'RELATES_TO', is_current: true, source_node_id: 'shared', "
        "target_node_id: 'b-secret', projected_at: 'x', updated_at: 'x'}]->(y)",
        {"ak": f"{a}:shared", "bk": f"{b}:b-secret", "ek": f"{a}:rogue", "a": a},
    )
    for direction in ("outgoing", "incoming", "both"):
        r = client.post(
            f"/v1/workspaces/{a}/relationships/query",
            json={
                "workspace_id": a,
                "node_id": "shared",
                "direction": direction,
                "include_non_current": True,
            },
        )
        assert r.status_code == 200
        assert "b-secret" not in r.text and "rogue" not in r.text
    r = client.post(
        f"/v1/workspaces/{b}/relationships/query",
        json={"workspace_id": b, "node_id": "b-secret", "include_non_current": True},
    )
    assert "rogue" not in r.text and a not in r.text


def test_workspace_a_cannot_link_to_workspace_b_node(client, two_workspaces):
    a, b = two_workspaces
    r = client.put(f"/v1/workspaces/{a}/edges/x", json=edge_body(a, "x", "a-only", "b-secret"))
    assert r.status_code == 409


def test_workspace_a_cannot_modify_or_delete_workspace_b(client, two_workspaces):
    a, b = two_workspaces
    assert client.delete(f"/v1/workspaces/{a}/nodes/b-only").status_code == 404
    assert client.delete(f"/v1/workspaces/{a}/edges/b-edge").status_code == 404
    # Upserting "shared" in A never touches B's "shared".
    client.put(f"/v1/workspaces/{a}/nodes/shared", json=node_body(a, "shared", label="A renamed"))
    assert client.get(f"/v1/workspaces/{b}/nodes/shared").json()["label"] == "B shared"
    # Deleting A's "shared" leaves B's node and edge intact.
    assert client.delete(f"/v1/workspaces/{a}/nodes/shared").json()["detached_edges"] == 1
    assert client.get(f"/v1/workspaces/{b}/nodes/shared").status_code == 200
    r = client.post(
        f"/v1/workspaces/{b}/relationships/query", json={"workspace_id": b, "node_id": "shared"}
    )
    assert [x["edge"]["edge_id"] for x in r.json()["relationships"]] == ["b-edge"]


def test_same_edge_id_is_independent_per_workspace(client, two_workspaces):
    a, b = two_workspaces
    assert (
        client.put(
            f"/v1/workspaces/{a}/edges/same", json=edge_body(a, "same", "shared", "a-only")
        ).status_code
        == 201
    )
    assert (
        client.put(
            f"/v1/workspaces/{b}/edges/same", json=edge_body(b, "same", "shared", "b-only")
        ).status_code
        == 201
    )
    assert client.delete(f"/v1/workspaces/{a}/edges/same").status_code == 200
    r = client.post(
        f"/v1/workspaces/{b}/relationships/query", json={"workspace_id": b, "node_id": "b-only"}
    )
    assert [x["edge"]["edge_id"] for x in r.json()["relationships"]] == ["same"]


def test_body_workspace_cannot_redirect_scope(client, two_workspaces):
    a, b = two_workspaces
    r = client.put(f"/v1/workspaces/{a}/nodes/b-only", json=node_body(b, "b-only"))
    assert r.status_code == 422
    r = client.post(
        f"/v1/workspaces/{a}/relationships/query", json={"workspace_id": b, "node_id": "shared"}
    )
    assert r.status_code == 422
