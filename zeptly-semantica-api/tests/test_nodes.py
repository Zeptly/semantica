"""Semantic node projection against a real FalkorDB via Semantica GraphStore."""

import pytest

from app.graph import NODE_LABEL
from tests.conftest import node_body

pytestmark = pytest.mark.integration


def _count_nodes(graph, ws, node_id):
    rows = graph._store.execute_query(
        f"MATCH (n:{NODE_LABEL}) WHERE n.workspace_id = $ws AND n.canonical_id = $id "
        "RETURN count(n)",
        {"ws": ws, "id": node_id},
    )["records"]
    return rows[0][0]


def test_create_node(client, graph, ws):
    r = client.put(f"/v1/workspaces/{ws}/nodes/mem-1", json=node_body(ws, "mem-1"))
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["created"] is True
    node = body["node"]
    assert node["workspace_id"] == ws and node["canonical_id"] == "mem-1"
    assert node["node_type"] == "memory" and node["label"] == "Node mem-1"
    assert node["lifecycle_state"] == "active" and node["is_current"] is True
    assert node["source_updated_at"] == "2026-01-02T02:04:05Z"  # normalised to UTC
    assert node["provenance_refs"] == ["pg:memories/mem-1"]
    assert "key" not in node  # internal scoped key never leaves the service
    assert _count_nodes(graph, ws, "mem-1") == 1
    # Persisted: stored under the workspace-scoped key.
    rows = graph._store.execute_query(
        f"MATCH (n:{NODE_LABEL} {{key: $k}}) RETURN n.workspace_id", {"k": f"{ws}:mem-1"}
    )["records"]
    assert rows == [[ws]]


def test_idempotent_node_upsert(client, graph, ws):
    url = f"/v1/workspaces/{ws}/nodes/mem-2"
    first = client.put(url, json=node_body(ws, "mem-2"))
    assert first.status_code == 201
    again = client.put(url, json=node_body(ws, "mem-2"))
    assert again.status_code == 200 and again.json()["created"] is False
    assert again.json()["node"]["projected_at"] == first.json()["node"]["projected_at"]

    # Update replaces the projection; omitted optional fields are removed.
    upd = node_body(
        ws,
        "mem-2",
        label="Renamed",
        is_current=False,
        lifecycle_state="superseded",
        source_version=2,
    )
    del upd["valid_from"]
    r = client.put(url, json=upd)
    assert r.status_code == 200
    n = r.json()["node"]
    assert n["label"] == "Renamed" and n["is_current"] is False
    assert n["lifecycle_state"] == "superseded" and n["source_version"] == 2
    assert n["valid_from"] is None
    assert _count_nodes(graph, ws, "mem-2") == 1


def test_retrieve_node(client, ws):
    client.put(
        f"/v1/workspaces/{ws}/nodes/cap-1",
        json=node_body(ws, "cap-1", node_type="capability", label=None),
    )
    r = client.get(f"/v1/workspaces/{ws}/nodes/cap-1")
    assert r.status_code == 200
    assert r.json()["node_type"] == "capability" and r.json()["label"] is None
    assert client.get(f"/v1/workspaces/{ws}/nodes/missing").status_code == 404


def test_delete_node(client, graph, ws):
    client.put(f"/v1/workspaces/{ws}/nodes/d1", json=node_body(ws, "d1"))
    r = client.delete(f"/v1/workspaces/{ws}/nodes/d1")
    assert r.status_code == 200
    assert r.json() == {
        "workspace_id": ws,
        "canonical_id": "d1",
        "deleted": True,
        "detached_edges": 0,
    }
    assert client.get(f"/v1/workspaces/{ws}/nodes/d1").status_code == 404
    assert client.delete(f"/v1/workspaces/{ws}/nodes/d1").status_code == 404
    assert _count_nodes(graph, ws, "d1") == 0


def test_label_at_160_characters_accepted(client, ws):
    r = client.put(f"/v1/workspaces/{ws}/nodes/l160", json=node_body(ws, "l160", label="x" * 160))
    assert r.status_code == 201


def test_label_over_160_characters_rejected(client, graph, ws):
    r = client.put(f"/v1/workspaces/{ws}/nodes/l161", json=node_body(ws, "l161", label="x" * 161))
    assert r.status_code == 422
    assert _count_nodes(graph, ws, "l161") == 0


@pytest.mark.parametrize("label", ["", "   ", "line\nbreak", "tab\there", "nul\x00"])
def test_blank_or_control_char_labels_rejected(client, ws, label):
    r = client.put(f"/v1/workspaces/{ws}/nodes/lbl", json=node_body(ws, "lbl", label=label))
    assert r.status_code == 422


FORBIDDEN_FIELDS = {
    "content": "full memory payload",
    "memory_payload": {"text": "..."},
    "conversation": [{"role": "user", "content": "hi"}],
    "messages": [],
    "prompt": "You are...",
    "system_prompt": "x",
    "model_input": "x",
    "model_output": "x",
    "completion": "x",
    "embedding": [0.1, 0.2],
    "vector": [0.1],
    "api_key": "sk-...",
    "credentials": {"user": "x"},
    "password": "x",
    "secret": "x",
    "provider_config": {"model": "x"},
    "document": "x",
    "text": "x",
    "metadata": {"anything": "goes"},
    "properties": {"x": 1},
    "key": "other-ws:node",
}


@pytest.mark.parametrize("field,value", list(FORBIDDEN_FIELDS.items()))
def test_forbidden_payload_fields_rejected(client, graph, ws, field, value):
    body = node_body(ws, "fb", **{field: value})
    r = client.put(f"/v1/workspaces/{ws}/nodes/fb", json=body)
    assert r.status_code == 422, field
    assert _count_nodes(graph, ws, "fb") == 0


@pytest.mark.parametrize(
    "field,value",
    [
        ("node_type", "Memory) DETACH DELETE n //"),
        ("lifecycle_state", "ACTIVE"),
        ("provenance_refs", ["has space"]),
        ("provenance_refs", ["dup", "dup"]),
        ("provenance_refs", [f"r{i}" for i in range(17)]),
        ("is_current", "yes"),
        ("source_version", -1),
        ("source_version", "1"),
        ("valid_from", "2026-01-01T00:00:00"),  # naive datetime
    ],
)
def test_invalid_field_values_rejected(client, ws, field, value):
    r = client.put(f"/v1/workspaces/{ws}/nodes/iv", json=node_body(ws, "iv", **{field: value}))
    assert r.status_code == 422, field


def test_valid_to_before_valid_from_rejected(client, ws):
    body = node_body(ws, "t1", valid_from="2026-02-01T00:00:00Z", valid_to="2026-01-01T00:00:00Z")
    assert client.put(f"/v1/workspaces/{ws}/nodes/t1", json=body).status_code == 422


def test_body_must_match_path(client, ws):
    assert (
        client.put(f"/v1/workspaces/{ws}/nodes/p1", json=node_body("other-ws", "p1")).status_code
        == 422
    )
    assert client.put(f"/v1/workspaces/{ws}/nodes/p1", json=node_body(ws, "p2")).status_code == 422
    body = node_body(ws, "p1")
    del body["workspace_id"]
    assert client.put(f"/v1/workspaces/{ws}/nodes/p1", json=body).status_code == 422


@pytest.mark.parametrize("bad_id", ["a:b", "a%3Ab", "..", "-x", "a b", "a'b", "x" * 129])
def test_invalid_identifiers_rejected(client, ws, bad_id):
    r = client.get(f"/v1/workspaces/{ws}/nodes/{bad_id}")
    assert r.status_code in (404, 422)
    assert r.status_code != 200


def test_oversized_body_rejected(client, ws):
    r = client.put(
        f"/v1/workspaces/{ws}/nodes/big",
        content=b"{" + b" " * (70 * 1024) + b"}",
        headers={"Content-Type": "application/json"},
    )
    assert r.status_code == 413


def test_chunked_body_without_length_rejected(client, ws):
    def gen():
        yield b'{"workspace_id": "x"}'

    r = client.put(
        f"/v1/workspaces/{ws}/nodes/chunked",
        content=gen(),
        headers={"Content-Type": "application/json"},
    )
    assert r.status_code == 411
