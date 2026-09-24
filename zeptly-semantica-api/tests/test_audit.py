"""Phase 6.25B adversarial tests: authentication edge cases, injection,
payload smuggling, DoS limits, API surface and logging hygiene."""

from __future__ import annotations

import ast
import json
import logging
import pathlib
import uuid

import pytest

from app import graph as graph_module
from app.__main__ import _bind_host
from app.graph import scoped_key
from app.logging_setup import JsonFormatter
from tests.conftest import API_KEY, edge_body, node_body

APP_DIR = pathlib.Path(__file__).resolve().parent.parent / "app"


# -- API surface -------------------------------------------------------------


def _flatten_routes(routes):
    """FastAPI >= 0.14x nests included routers; expand them."""
    for r in routes:
        if hasattr(r, "effective_candidates"):
            for ctx in r.effective_candidates():
                yield ctx.path, ctx.methods
        else:
            yield getattr(r, "path", None), getattr(r, "methods", None) or set()


@pytest.mark.integration
def test_route_inventory_is_exactly_the_phase_625_contract(client):
    routes = sorted(
        (m, path)
        for path, methods in _flatten_routes(client.app.routes)
        for m in set(methods) - {"HEAD"}
        if path not in ("/docs", "/docs/oauth2-redirect", "/openapi.json", "/redoc")
    )
    assert routes == sorted(
        [
            ("GET", "/health"),
            ("GET", "/ready"),
            ("PUT", "/v1/workspaces/{workspace_id}/nodes/{node_id}"),
            ("GET", "/v1/workspaces/{workspace_id}/nodes/{node_id}"),
            ("DELETE", "/v1/workspaces/{workspace_id}/nodes/{node_id}"),
            ("PUT", "/v1/workspaces/{workspace_id}/edges/{edge_id}"),
            ("DELETE", "/v1/workspaces/{workspace_id}/edges/{edge_id}"),
            ("POST", "/v1/workspaces/{workspace_id}/relationships/query"),
        ]
    )


@pytest.mark.integration
@pytest.mark.parametrize(
    "path",
    [
        "/cypher",
        "/query",
        "/v1/cypher",
        "/v1/query",
        "/admin",
        "/v1/graph",
        "/upload",
        "/v1/ingest",
        "/v1/ontology",
        "/v1/llm",
        "/v1/pipeline",
        "/metrics",
        "/debug",
    ],
)
def test_no_admin_or_generic_endpoints(client, path):
    for method in ("GET", "POST", "PUT"):
        assert client.request(method, path, json={}).status_code in (404, 405)


@pytest.mark.integration
def test_no_cors_headers(client):
    r = client.options(
        "/v1/workspaces/ws/nodes/n",
        headers={"Origin": "https://evil.example", "Access-Control-Request-Method": "GET"},
    )
    assert "access-control-allow-origin" not in {k.lower() for k in r.headers}


# -- authentication ----------------------------------------------------------


@pytest.mark.integration
@pytest.mark.parametrize(
    "headers",
    [
        [("X-API-Key", API_KEY), ("X-API-Key", "wrong")],
        [("X-API-Key", "wrong"), ("X-API-Key", API_KEY)],
        [("X-API-Key", API_KEY), ("X-API-Key", API_KEY)],
    ],
)
def test_duplicate_api_key_headers_rejected(anon_client, headers):
    r = anon_client.get("/v1/workspaces/ws-dup/nodes/n", headers=headers)
    assert r.status_code == 401


@pytest.mark.integration
@pytest.mark.parametrize(
    "headers",
    [
        {"X-API-Key": API_KEY + " "},  # exact match only: no trimming
        {"X-Api-Key-": API_KEY},
        {"X_API_KEY": API_KEY},
        {"API-Key": API_KEY},
        {"X-API-Key": API_KEY.upper()},
        {"X-API-Key": ("é" + API_KEY[1:]).encode("latin-1")},
        {"X-API-Key": API_KEY.encode() + b"\xff"},
        {"Cookie": f"X-API-Key={API_KEY}"},
    ],
)
def test_malformed_or_misplaced_keys_rejected_or_exact(anon_client, headers):
    r = anon_client.get("/v1/workspaces/ws-mal/nodes/n", headers=headers)
    assert r.status_code == 401


@pytest.mark.integration
def test_key_in_query_string_not_accepted(anon_client):
    r = anon_client.get(f"/v1/workspaces/ws-q/nodes/n?api_key={API_KEY}&X-API-Key={API_KEY}")
    assert r.status_code == 401


@pytest.mark.integration
def test_header_name_is_case_insensitive(anon_client):
    r = anon_client.get("/v1/workspaces/ws-case/nodes/n", headers={"x-api-key": API_KEY})
    assert r.status_code == 404


@pytest.mark.integration
def test_auth_failures_are_counted_and_logged_without_credentials(anon_client, caplog):
    counter = anon_client.app.state.auth_failures
    before = sum(counter.counts.values())
    secretish = "attempted-key-" + uuid.uuid4().hex
    with caplog.at_level(logging.WARNING, logger="zeptly_semantica.auth"):
        anon_client.get("/v1/workspaces/ws-log/nodes/n")
        anon_client.get("/v1/workspaces/ws-log/nodes/n", headers={"X-API-Key": secretish})
    assert sum(counter.counts.values()) == before + 2
    formatted = [JsonFormatter().format(r) for r in caplog.records]
    assert len(formatted) == 2
    reasons = [json.loads(line)["reason"] for line in formatted]
    assert reasons == ["missing", "invalid"]
    blob = "\n".join(formatted)
    assert secretish not in blob and API_KEY not in blob
    assert json.loads(formatted[0])["route"] == "/v1/workspaces/{workspace_id}/nodes/{node_id}"


@pytest.mark.integration
def test_request_bodies_never_logged(client, ws, caplog):
    marker = "MARKER" + uuid.uuid4().hex
    with caplog.at_level(logging.DEBUG):
        client.put(f"/v1/workspaces/{ws}/nodes/m", json=node_body(ws, "m", label=marker))
        client.put(f"/v1/workspaces/{ws}/nodes/m2", json=node_body(ws, "m2", content=marker))
    assert marker not in "\n".join(JsonFormatter().format(r) for r in caplog.records)


# -- query-injection ---------------------------------------------------------


def _const_names(tree: ast.Module) -> set[str]:
    return {
        t.id
        for node in tree.body
        if isinstance(node, ast.Assign)
        for t in node.targets
        if isinstance(t, ast.Name)
    }


def test_all_cypher_is_constant_text():
    """Structural proof: module-level f-strings only interpolate other
    module constants, and every _run/execute_query call receives a
    module-level template (or an element of one), never built text."""
    tree = ast.parse((APP_DIR / "graph.py").read_text())
    consts = _const_names(tree)
    allowed_interp = {"NODE_LABEL", "EDGE_TYPE", "_REL_FILTER"}

    templates = [n for n in tree.body if isinstance(n, ast.Assign)]
    assert any(
        isinstance(t.targets[0], ast.Name) and t.targets[0].id == "_Q_UPSERT_EDGE"
        for t in templates
    )
    for node in (n for t in templates for n in ast.walk(t)):
        if isinstance(node, ast.JoinedStr):
            for v in node.values:
                if isinstance(v, ast.FormattedValue):
                    assert isinstance(v.value, ast.Name), ast.dump(v)
                    assert v.value.id in allowed_interp, v.value.id

    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr in ("_run", "execute_query", "query")
        ):
            if node.func.attr == "execute_query":
                # only the generic call inside _run(query, params)
                assert isinstance(node.args[0], ast.Name) and node.args[0].id == "query"
                continue
            if node.func.attr == "query":
                continue
            arg = node.args[0]
            ok = (
                isinstance(arg, ast.Name)
                and (arg.id in consts or arg.id in ("statement", "template"))
            ) or (
                isinstance(arg, ast.Subscript)
                and isinstance(arg.value, ast.Name)
                and arg.value.id in consts
            )
            assert ok, ast.dump(arg)


def test_graph_layer_revalidates_identifiers():
    for bad in ["", "a:b", "x y", "a'b", 'a"b', "a\\b", "a\nb", "é", "x" * 129, "-a"]:
        with pytest.raises(ValueError):
            scoped_key(bad, "ok")
        with pytest.raises(ValueError):
            scoped_key("ok", bad)


HOSTILE_STRINGS = [
    '"}) MATCH (m) DETACH DELETE m //',
    "') MATCH (m) DETACH DELETE m //",
    '\\" MATCH (m) DETACH DELETE m //',
    "x\\",
    "\\\\\\",
    '"',
    "' OR 1=1 --",
    "$ws",
    "`SemanticaNode`",
    "\\u0022) MATCH (m) DELETE m //",
    "CYPHER ws='other'",
    "} RETURN 1 //",
    "日本語 🚀",
]


@pytest.mark.integration
@pytest.mark.parametrize("value", HOSTILE_STRINGS)
def test_driver_parameter_quoting_round_trips(graph, value):
    """falkordb-py sends parameters as a quoted 'CYPHER k=v' header rather
    than binding them server-side. Prove hostile strings stay data."""
    rows = graph._run("RETURN $x, $y", {"x": value, "y": [value, {"k": value}]})
    assert rows[0][0] == value
    assert rows[0][1] == [value, {"k": value}]


@pytest.mark.integration
@pytest.mark.parametrize("value", [v for v in HOSTILE_STRINGS if len(v) <= 160])
def test_hostile_labels_stored_verbatim_without_side_effects(client, graph, ws, value):
    other = "ws-victim-" + uuid.uuid4().hex[:6]
    client.put(f"/v1/workspaces/{other}/nodes/v", json=node_body(other, "v"))
    r = client.put(f"/v1/workspaces/{ws}/nodes/h", json=node_body(ws, "h", label=value))
    assert r.status_code == 201
    assert client.get(f"/v1/workspaces/{ws}/nodes/h").json()["label"] == value
    assert client.get(f"/v1/workspaces/{other}/nodes/v").status_code == 200


@pytest.mark.integration
@pytest.mark.parametrize(
    "field",
    [
        "workspace_id",
        "canonical_id",
        "node_type",
        "label",
        "lifecycle_state",
        "provenance_refs",
        "valid_from",
    ],
)
@pytest.mark.parametrize("payload", ["a\x00b", "a'}) DELETE n //", 'a") DELETE n //', "a b"])
def test_every_string_field_rejects_nul_and_injection_shapes(client, ws, field, payload):
    value = [payload] if field == "provenance_refs" else payload
    r = client.put(f"/v1/workspaces/{ws}/nodes/f", json=node_body(ws, "f", **{field: value}))
    if field == "label" and "\x00" not in payload:
        assert r.status_code == 201  # labels are free text, stored as data (see above)
    else:
        assert r.status_code == 422


@pytest.mark.integration
@pytest.mark.parametrize(
    "field", ["edge_type", "edge_id", "source_node_id", "target_node_id", "workspace_id"]
)
def test_edge_string_fields_reject_injection_shapes(client, ws, field):
    client.put(f"/v1/workspaces/{ws}/nodes/a", json=node_body(ws, "a"))
    client.put(f"/v1/workspaces/{ws}/nodes/b", json=node_body(ws, "b"))
    body = {**edge_body(ws, "e", "a", "b"), field: "X]->() DELETE r //"}
    r = client.put(f"/v1/workspaces/{ws}/edges/e", json=body)
    assert r.status_code == 422


# -- payload policy ------------------------------------------------------------


@pytest.mark.integration
@pytest.mark.parametrize(
    "label",
    ["a\x85b", "a\x9fb", "‮evil", "a⁦b", "line sep", "para sep", "rlm‏mark"],
)
def test_label_rejects_c1_controls_bidi_and_separators(client, ws, label):
    r = client.put(f"/v1/workspaces/{ws}/nodes/l", json=node_body(ws, "l", label=label))
    assert r.status_code == 422


@pytest.mark.integration
def test_label_accepts_ordinary_unicode(client, ws):
    label = "Café planning – Q3 🚀 日本"
    r = client.put(f"/v1/workspaces/{ws}/nodes/u", json=node_body(ws, "u", label=label))
    assert r.status_code == 201 and r.json()["node"]["label"] == label


@pytest.mark.integration
@pytest.mark.parametrize(
    "value",
    [
        "9999-12-31T23:59:59-05:00",
        "0001-01-01T00:00:00+05:00",
        1700000000,
        1.5,
        True,
        "2026-01-01",
        "not-a-date",
        "2026-01-01T00:00:00",
    ],
)
def test_timestamps_must_be_valid_aware_iso_strings(client, ws, value):
    r = client.put(f"/v1/workspaces/{ws}/nodes/t", json=node_body(ws, "t", valid_from=value))
    assert r.status_code == 422


@pytest.mark.integration
@pytest.mark.parametrize(
    "body_patch",
    [
        {"label": {"text": "nested"}},
        {"label": ["list"]},
        {"node_type": 1},
        {"provenance_refs": "not-a-list"},
        {"provenance_refs": [{"ref": "x"}]},
        {"is_current": 1},
    ],
)
def test_type_confusion_rejected(client, ws, body_patch):
    r = client.put(f"/v1/workspaces/{ws}/nodes/tc", json=node_body(ws, "tc", **body_patch))
    assert r.status_code == 422


@pytest.mark.integration
def test_non_object_bodies_rejected(client, ws):
    for raw in (b"[]", b'"x"', b"null", b"1", b"{", b""):
        r = client.put(
            f"/v1/workspaces/{ws}/nodes/nb",
            content=raw,
            headers={"Content-Type": "application/json"},
        )
        assert r.status_code == 422, raw


# -- DoS limits ------------------------------------------------------------------


@pytest.mark.integration
def test_relationship_query_result_is_capped(client, ws):
    client.put(f"/v1/workspaces/{ws}/nodes/hub", json=node_body(ws, "hub", label=None))
    for i in range(205):
        client.put(f"/v1/workspaces/{ws}/nodes/s{i}", json=node_body(ws, f"s{i}", label=None))
        client.put(f"/v1/workspaces/{ws}/edges/e{i}", json=edge_body(ws, f"e{i}", "hub", f"s{i}"))
    r = client.post(
        f"/v1/workspaces/{ws}/relationships/query",
        json={"workspace_id": ws, "node_id": "hub", "limit": 200},
    )
    assert r.status_code == 200
    assert len(r.json()["relationships"]) == 200 and r.json()["truncated"] is True


@pytest.mark.integration
def test_query_limits_enforced(client, ws):
    q = f"/v1/workspaces/{ws}/relationships/query"
    too_many_types = [f"T{i}" for i in range(17)]
    assert (
        client.post(
            q, json={"workspace_id": ws, "node_id": "a", "edge_types": too_many_types}
        ).status_code
        == 422
    )
    assert (
        client.post(q, json={"workspace_id": ws, "node_id": "a", "limit": 10**9}).status_code == 422
    )
    assert client.post(q, json={"workspace_id": ws, "node_id": "a", "depth": 2}).status_code == 422


# -- bind address ------------------------------------------------------------------


class _FakeSocket:
    fail = False

    def __init__(self, *a, **k):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def setsockopt(self, *a):
        pass

    def bind(self, addr):
        if _FakeSocket.fail:
            raise OSError(97, "Address family not supported by protocol")


@pytest.mark.parametrize("ipv6_works,expected", [(True, "::"), (False, "0.0.0.0")])  # noqa: S104
def test_bind_host_selection_logic(monkeypatch, ipv6_works, expected):
    """Selection logic only (this sandbox has no IPv6 to bind for real)."""
    import app.__main__ as entry

    monkeypatch.setattr(entry.socket, "has_ipv6", True)
    monkeypatch.setattr(entry.socket, "socket", _FakeSocket)
    _FakeSocket.fail = not ipv6_works
    assert entry._bind_host() == expected


def test_bind_host_is_dual_stack_or_ipv4_fallback():
    assert _bind_host() in ("::", "0.0.0.0")  # noqa: S104


def test_graph_module_labels_are_constants():
    assert graph_module.NODE_LABEL == "SemanticaNode"
    assert graph_module.EDGE_TYPE == "SEMANTIC_EDGE"


def test_dockerfile_is_railway_buildable():
    """Railway's builder only accepts type=cache mounts and rejects
    --mount=type=secret outright, so the committed Dockerfile uses none."""
    dockerfile = (APP_DIR.parent / "Dockerfile").read_text()
    assert "--mount" not in dockerfile
    assert "PIP_CERT" not in dockerfile
