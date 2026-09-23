"""Semantic edges and the constrained relationship query."""

import pytest

from tests.conftest import edge_body, node_body

pytestmark = pytest.mark.integration


@pytest.fixture
def triangle(client, ws):
    for n in ("a", "b", "c"):
        assert (
            client.put(f"/v1/workspaces/{ws}/nodes/{n}", json=node_body(ws, n)).status_code == 201
        )
    return ws


def _edge_count(graph, ws, edge_id):
    return graph._store.execute_query(
        "MATCH ()-[r:SEMANTIC_EDGE {key: $k}]->() RETURN count(r)", {"k": f"{ws}:{edge_id}"}
    )["records"][0][0]


def test_create_edge(client, graph, triangle):
    ws = triangle
    r = client.put(f"/v1/workspaces/{ws}/edges/e1", json=edge_body(ws, "e1", "a", "b"))
    assert r.status_code == 201, r.text
    e = r.json()["edge"]
    assert (
        e["edge_type"] == "RELATES_TO" and e["source_node_id"] == "a" and e["target_node_id"] == "b"
    )
    assert e["workspace_id"] == ws and "key" not in e
    assert _edge_count(graph, ws, "e1") == 1


def test_idempotent_edge_upsert_and_endpoint_move(client, graph, triangle):
    ws = triangle
    url = f"/v1/workspaces/{ws}/edges/e2"
    first = client.put(url, json=edge_body(ws, "e2", "a", "b"))
    assert first.status_code == 201
    again = client.put(url, json=edge_body(ws, "e2", "a", "b", is_current=False))
    assert again.status_code == 200 and again.json()["created"] is False
    assert again.json()["edge"]["is_current"] is False
    assert _edge_count(graph, ws, "e2") == 1
    moved = client.put(url, json=edge_body(ws, "e2", "a", "c"))
    assert moved.status_code == 200 and moved.json()["edge"]["target_node_id"] == "c"
    assert moved.json()["edge"]["projected_at"] == first.json()["edge"]["projected_at"]
    assert _edge_count(graph, ws, "e2") == 1


def test_edge_requires_existing_endpoints(client, graph, triangle):
    ws = triangle
    r = client.put(f"/v1/workspaces/{ws}/edges/e3", json=edge_body(ws, "e3", "a", "nope"))
    assert r.status_code == 409
    assert _edge_count(graph, ws, "e3") == 0


@pytest.mark.parametrize(
    "field,value",
    [
        ("edge_type", "relates_to"),
        ("edge_type", "X]->() DELETE r //"),
        ("source_node_id", "a:b"),
        ("content", "x"),
        ("label", "edges carry no label"),
        ("weight", 0.5),
    ],
)
def test_invalid_edge_payload_rejected(client, triangle, field, value):
    ws = triangle
    r = client.put(
        f"/v1/workspaces/{ws}/edges/bad", json=edge_body(ws, "bad", "a", "b", **{field: value})
    )
    assert r.status_code == 422


def test_delete_edge(client, graph, triangle):
    ws = triangle
    client.put(f"/v1/workspaces/{ws}/edges/e4", json=edge_body(ws, "e4", "a", "b"))
    r = client.delete(f"/v1/workspaces/{ws}/edges/e4")
    assert r.status_code == 200 and r.json() == {
        "workspace_id": ws,
        "edge_id": "e4",
        "deleted": True,
    }
    assert _edge_count(graph, ws, "e4") == 0
    assert client.delete(f"/v1/workspaces/{ws}/edges/e4").status_code == 404
    # Endpoints are untouched.
    assert client.get(f"/v1/workspaces/{ws}/nodes/a").status_code == 200
    assert client.get(f"/v1/workspaces/{ws}/nodes/b").status_code == 200


def test_delete_node_detaches_connected_edges(client, graph, triangle):
    ws = triangle
    client.put(f"/v1/workspaces/{ws}/edges/ab", json=edge_body(ws, "ab", "a", "b"))
    client.put(f"/v1/workspaces/{ws}/edges/ca", json=edge_body(ws, "ca", "c", "a"))
    client.put(f"/v1/workspaces/{ws}/edges/bc", json=edge_body(ws, "bc", "b", "c"))
    r = client.delete(f"/v1/workspaces/{ws}/nodes/a")
    assert r.status_code == 200 and r.json()["detached_edges"] == 2
    assert _edge_count(graph, ws, "ab") == 0 and _edge_count(graph, ws, "ca") == 0
    assert _edge_count(graph, ws, "bc") == 1


def _query(client, ws, **body):
    return client.post(
        f"/v1/workspaces/{ws}/relationships/query", json={"workspace_id": ws, **body}
    )


def test_relationship_query(client, triangle):
    ws = triangle
    client.put(f"/v1/workspaces/{ws}/edges/q1", json=edge_body(ws, "q1", "a", "b"))
    client.put(
        f"/v1/workspaces/{ws}/edges/q2",
        json=edge_body(ws, "q2", "c", "a", edge_type="DERIVED_FROM"),
    )
    client.put(
        f"/v1/workspaces/{ws}/edges/q3", json=edge_body(ws, "q3", "a", "c", is_current=False)
    )

    r = _query(client, ws, node_id="a")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["node"]["canonical_id"] == "a" and body["truncated"] is False
    got = {
        (x["edge"]["edge_id"], x["direction"], x["neighbor"]["canonical_id"])
        for x in body["relationships"]
    }
    assert got == {("q1", "outgoing", "b"), ("q2", "incoming", "c")}  # q3 not current

    r = _query(client, ws, node_id="a", include_non_current=True)
    assert {x["edge"]["edge_id"] for x in r.json()["relationships"]} == {"q1", "q2", "q3"}

    r = _query(client, ws, node_id="a", direction="outgoing")
    assert [x["edge"]["edge_id"] for x in r.json()["relationships"]] == ["q1"]

    r = _query(client, ws, node_id="a", direction="incoming")
    assert [x["edge"]["edge_id"] for x in r.json()["relationships"]] == ["q2"]

    r = _query(client, ws, node_id="a", edge_types=["DERIVED_FROM"])
    assert [x["edge"]["edge_id"] for x in r.json()["relationships"]] == ["q2"]

    r = _query(client, ws, node_id="a", include_non_current=True, limit=2)
    assert len(r.json()["relationships"]) == 2 and r.json()["truncated"] is True


def test_relationship_query_is_constrained(client, triangle):
    ws = triangle
    assert _query(client, ws, node_id="missing").status_code == 404
    for bad in (
        {"node_id": "a", "cypher": "MATCH (n) RETURN n"},
        {"node_id": "a", "query": "MATCH (n) RETURN n"},
        {"node_id": "a", "depth": 5},
        {"node_id": "a", "direction": "any"},
        {"node_id": "a", "limit": 201},
        {"node_id": "a", "limit": 0},
        {"node_id": "a", "edge_types": []},
        {"node_id": "a", "edge_types": ["X' OR 1=1"]},
        {"node_id": "a' OR '1'='1"},
    ):
        assert _query(client, ws, **bad).status_code == 422, bad
    r = client.post(
        f"/v1/workspaces/{ws}/relationships/query", json={"workspace_id": "other", "node_id": "a"}
    )
    assert r.status_code == 422
