# zeptly-semantica-api

A small FastAPI service that keeps a **derived semantic projection** of Zeptly's canonical state. It stores the projection in FalkorDB through Semantica's persistent `GraphStore`, pinned to `semantica==0.6.0`.

> **PostgreSQL owns truth. Semantica interprets and relates it.**
>
> Nothing in this service is authoritative. If the service and its graph database are destroyed, Zeptly can rebuild them from canonical PostgreSQL state by replaying `PUT` calls. This service does not own or decide capability identity, implementation or policy, authorization, execution events, memory identity or lifecycle, canonical relationships, Tape, deletion or forgetting, model promotion, or orchestration.

This directory is self-contained. It sits inside a fork of the upstream Semantica repository but does **not** use the repository's own `semantica/` source tree (0.7.x). It consumes the published `semantica==0.6.0` wheel from PyPI.

## Architecture

```
Zeptly (Phase 6.5+, not in this phase)
        │  HTTPS + X-API-Key
        ▼
zeptly-semantica-api   (this service: FastAPI, 1 process, stateless)
        │  Semantica GraphStore(backend="falkordb")
        │  Railway private network, Redis protocol + password
        ▼
FalkorDB               (falkordb/falkordb-server:v4.14.8, volume-backed, AOF on)
```

The topology is only these two services. It has no worker, vector store, RDF server, ontology database, MCP service, LLM calls, embeddings, or database of its own. This service does not use `ContextGraph`, and it does not use the Knowledge Explorer's storage.

### Graph model

It uses a single FalkorDB graph (`FALKORDB_GRAPH`, default `zeptly_semantica`):

```
(:SemanticaNode {key, workspace_id, canonical_id, node_type, label?, lifecycle_state?,
                 is_current, valid_from?, valid_to?, source_updated_at?, source_version?,
                 provenance_refs[], projected_at, updated_at})
   -[:SEMANTIC_EDGE {key, workspace_id, edge_id, edge_type, source_node_id, target_node_id,
                     lifecycle_state?, is_current, valid_*?, source_*?, provenance_refs[],
                     projected_at, updated_at}]->
(:SemanticaNode)
```

* `key = "{workspace_id}:{id}"`. The service derives it from the URL path. Identifiers may not contain `:`, so a key can only come from one (workspace, id) pair.
* Lookups use indexes on `SemanticaNode.key`, `SemanticaNode.workspace_id` and `SEMANTIC_EDGE.key`. The service creates them on first readiness check.
* The client-facing edge type (for example `DERIVED_FROM`) is stored as the `edge_type` **property**. The Cypher relationship type is always the fixed `SEMANTIC_EDGE`, so client input is never put into query text.

## API

Every `/v1/...` route requires exactly one `X-API-Key` header. Duplicate headers are rejected. Identifiers (`workspace_id`, node and edge IDs) must match `^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$`.

| Method | Path | Auth | Purpose |
|---|---|---|---|
| GET | `/health` | none | Process liveness. Always `{"status":"ok"}`. Does not contact FalkorDB. |
| GET | `/ready` | none | `200 {"status":"ready"}` when FalkorDB answers a query. Otherwise `503 {"status":"unavailable"}`. |
| PUT | `/v1/workspaces/{workspace_id}/nodes/{node_id}` | key | Idempotent create or replace. Returns `201` when created, `200` when updated. |
| GET | `/v1/workspaces/{workspace_id}/nodes/{node_id}` | key | Get one node, or `404`. |
| DELETE | `/v1/workspaces/{workspace_id}/nodes/{node_id}` | key | Delete the node and every projection edge attached to it. Returns `detached_edges`, or `404`. |
| PUT | `/v1/workspaces/{workspace_id}/edges/{edge_id}` | key | Idempotent create or replace. Both endpoints must already exist in the same workspace, otherwise `409`. Changing the endpoints moves the edge. |
| DELETE | `/v1/workspaces/{workspace_id}/edges/{edge_id}` | key | Delete one edge, or `404`. |
| POST | `/v1/workspaces/{workspace_id}/relationships/query` | key | Constrained one-hop query (see below). |

The OpenAPI UI (`/docs`) is served only when `SEMANTICA_ENV` is `development` or `test`.

### Node body (`PUT .../nodes/{node_id}`)

```json
{
  "workspace_id": "ws-123",            // required, must equal the path
  "canonical_id": "mem-456",           // required, must equal the path
  "node_type": "memory",               // required, ^[A-Za-z][A-Za-z0-9_.-]{0,63}$
  "label": "Quarterly planning notes", // optional, 1–160 chars, no control chars
  "lifecycle_state": "active",         // optional, ^[a-z][a-z0-9_]{0,31}$
  "is_current": true,                  // default true
  "valid_from": "2026-01-01T00:00:00Z",       // optional, timezone required
  "valid_to": null,                           // optional, >= valid_from
  "source_updated_at": "2026-01-02T00:00:00Z",// optional
  "source_version": 3,                        // optional, >= 0
  "provenance_refs": ["pg:memories/mem-456"]  // <= 16 unique refs, ^[A-Za-z0-9][A-Za-z0-9._:/#@=-]{0,255}$
}
```

Unknown fields are **rejected** with `422`, including `content`, `prompt`, `embedding`, `metadata` and `api_key`. `PUT` replaces the whole projection: optional fields left out of the body are removed. `projected_at` (first write) and `updated_at` (last write) are set by the server.

### Edge body (`PUT .../edges/{edge_id}`)

```json
{
  "workspace_id": "ws-123", "edge_id": "link-9",
  "edge_type": "DERIVED_FROM",          // ^[A-Z][A-Z0-9_]{0,63}$
  "source_node_id": "mem-456", "target_node_id": "mem-111",
  "lifecycle_state": "active", "is_current": true,
  "valid_from": null, "valid_to": null, "source_updated_at": null, "source_version": 1,
  "provenance_refs": ["pg:memory_links/link-9"]
}
```

### Relationship query

```json
POST /v1/workspaces/ws-123/relationships/query
{
  "workspace_id": "ws-123",
  "node_id": "mem-456",
  "direction": "both",            // outgoing | incoming | both
  "edge_types": ["DERIVED_FROM"], // optional filter, 1–16 types
  "include_non_current": false,   // default: only is_current=true edges
  "limit": 50                     // 1–200
}
```

The response contains the anchor node, a list of `{direction, edge, neighbor}` entries, and `truncated`. The query is one hop and has no depth parameter. The service does not accept Cypher and has no generic query endpoint.

## Configuration

| Variable | Required | Default | Notes |
|---|---|---|---|
| `SEMANTICA_ENV` | no | `production` | One of `production`, `staging`, `development` or `test`. Unset is treated as production. |
| `SEMANTICA_API_KEY` | yes* | none | Shared service key. Must be at least 32 characters in production or staging. *Optional only when anonymous mode is enabled. |
| `SEMANTICA_ALLOW_ANONYMOUS` | no | `false` | `true` is accepted only in `development` or `test`. In any other environment the process refuses to start. |
| `FALKORDB_HOST` | no | `localhost` | On Railway, use the FalkorDB service's private domain. |
| `FALKORDB_PORT` | no | `6379` | |
| `FALKORDB_PASSWORD` | yes in production/staging | none | Must match FalkorDB's `--requirepass`. The service refuses to start without it in production or staging, because `falkordb-server` runs with protected-mode off. |
| `FALKORDB_GRAPH` | no | `zeptly_semantica` | Graph name. Letters, digits and `_` only. |
| `FALKORDB_TIMEOUT_SECONDS` | no | `5` | Socket connect and read timeout for graph calls. |
| `PORT` | no | `8080` | Railway injects this value. The server binds `0.0.0.0:${PORT}`. |
| `SEMANTICA_BIND_HOST` | no | `0.0.0.0` | Set to `::` only for a legacy Railway environment (created before 16 Oct 2025) whose private network is IPv6-only. The service then creates an explicitly dual-stack socket (`IPV6_V6ONLY=0`), so it still answers Railway's IPv4 healthcheck, and exits with code 2 if IPv6 is unavailable. Only `0.0.0.0` and `::` are accepted. |

If configuration is invalid or unsafe, the process logs the reason and exits with code 2.

## Local setup

Requires Python 3.12, plus Docker for FalkorDB.

```sh
cd zeptly-semantica-api
python3.12 -m venv .venv && . .venv/bin/activate
pip install --no-deps --require-hashes -r requirements.lock   # exact runtime closure
pip install -r requirements-dev.txt                           # pytest, httpx, ruff, mypy

docker run -d --name falkordb -p 127.0.0.1:6379:6379 falkordb/falkordb-server:v4.14.8

SEMANTICA_ENV=development SEMANTICA_API_KEY=dev-key python -m app
curl -s localhost:8080/ready
```

`pip check` reports semantica's declared ML dependencies as missing. This is expected: see [Version pinning](#version-pinning).

## Docker

```sh
docker build -t zeptly-semantica-api:local .
cp .env.example .env    # then edit the values
docker compose up -d    # semantica-api on 127.0.0.1:8080, FalkorDB on the compose network only
```

The image is multi-stage and based on `python:3.12-slim`, pinned by digest. pip, setuptools, the `fastapi` CLI and semantica's console scripts are removed from the runtime image. It installs only hash-verified wheels (no compilers) into `/opt/venv`, owned by root. It runs as uid `10001` and starts with `python -m app`. Nothing needs to be written at runtime, so it runs with `--read-only` (compose sets `read_only: true`, `cap_drop: ALL`, `no-new-privileges`). No secrets are baked in.

The Dockerfile uses no BuildKit secret or other mounts, because Railway's builder rejects `--mount=type=secret`. To build locally behind a TLS-intercepting proxy, run `VERIFY_BUILD_CA=/path/to/ca.pem scripts/verify_stack.sh`. It builds from a throwaway copy of the Dockerfile that trusts the extra CA through a secret mount, so the CA never enters the repository or an image layer.

`docker-compose.yml` is for local validation only.

## Testing

```sh
# unit + integration (integration needs a reachable FalkorDB; no graph-store mocks are used)
FALKORDB_HOST=localhost FALKORDB_PORT=6379 python -m pytest
python -m pytest -m "not integration"      # tests that need no FalkorDB
ruff check app tests && ruff format --check app tests && mypy

# full stack: build, fixtures for Workspace A and B, API restart, FalkorDB restart,
# SIGKILL/AOF recovery, outage and readiness, compose down/up with the volume retained
scripts/verify_stack.sh   # VERIFY_BUILD_CA=/path/ca.pem behind a TLS-intercepting proxy
```

Integration tests write to a throwaway graph (`test_<random>`) and delete it at the end of the session.

## Observability

Logs are JSON lines on stdout. Railway parses the `level` and `message` fields. Events:

| Event | When |
|---|---|
| `startup` | Once at boot. Records the semantica version, env, `auth_required` and `anonymous_allowed`, and whether the FalkorDB password is set. It never includes the password itself. |
| `bind` | Once at boot. Records the host and port the server listens on. |
| `readiness` | Once at boot, after the first FalkorDB check. |
| `graph_connected` | Whenever connectivity to FalkorDB is established or restored. |
| `graph_unavailable` | Whenever connectivity to FalkorDB is lost. |
| `auth_rejected` | Every rejected request. Records the reason (`missing`, `invalid` or `ambiguous`), the route template and a running total. It never includes key material. |
| `graph_operation_failed` | A write or query failure. Records the operation name and exception type, never query parameters. |
| `config_rejected` | Startup refused because of unsafe configuration. |

Request bodies and headers are never logged.

## Railway deployment

See [RAILWAY-DEPLOYMENT-GUIDE.md](RAILWAY-DEPLOYMENT-GUIDE.md) for step-by-step dashboard instructions. The summary below is kept for reference.

Two native Railway services in one project:

1. **FalkorDB**
   * Image `falkordb/falkordb-server:v4.14.8`, which has no browser UI.
   * A volume mounted at `/var/lib/falkordb/data`.
   * Variable `REDIS_ARGS="--appendonly yes --appendfsync everysec --requirepass ${{FALKORDB_PASSWORD}}"`.
   * No public domain or TCP proxy. It is reachable only at `<service>.railway.internal:6379`.
2. **zeptly-semantica-api**
   * Built from this repository with Root Directory `zeptly-semantica-api`. It uses `railway.toml`: Dockerfile build, healthcheck `/health`, restart `ON_FAILURE`.
   * Variables: `SEMANTICA_ENV=production`, `SEMANTICA_API_KEY` (generated, 48+ characters), `SEMANTICA_ALLOW_ANONYMOUS=false`, `FALKORDB_HOST=<falkordb>.railway.internal`, `FALKORDB_PORT=6379`, `FALKORDB_PASSWORD` (a reference to the FalkorDB service variable).
   * Public exposure is a Phase 6.25B decision. Prefer private networking only.

The deployment healthcheck is `/health`, which does not depend on FalkorDB. Use `/ready` for monitoring graph connectivity.

## Version pinning

* **Semantica is pinned to exactly `0.6.0`**, with a sha256 hash, in `requirements.lock`. `app.main` refuses to start if the installed version is anything else.
* semantica 0.6.0's package metadata declares the full ML and NLP stack (torch, transformers, spacy, faiss, opencv, and more). `semantica.graph_store` imports none of it. The FalkorDB backend needs only the `falkordb` client. The lock therefore holds the exact runtime closure, and it is installed with `pip install --no-deps --require-hashes`. This keeps the image small (about 60 MB compressed) and avoids shipping unused LLM and embedding code. The image build verifies the pin and the `GraphStore` import.
* The FalkorDB server image is pinned to `v4.14.8@sha256:71afd8c7…`, and the Python base image is pinned by digest. All Python dependencies are pinned with hashes. To regenerate the lock, run `scripts/lock.sh`, which needs `uv` and `pip`.
* **One narrow capability gap in Semantica 0.6.0:** `FalkorDBStore.connect()` does not forward socket timeouts. `app/graph.py` builds the `falkordb` client with bounded timeouts and passes it to Semantica's own `FalkorDBClient` wrapper. Every query still runs through `GraphStore.execute_query`. Revisit this if the Semantica pin changes.
