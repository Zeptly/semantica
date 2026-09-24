# Phase 6.25A: Implementation Result

**Semantica deployment service (`zeptly-semantica-api`)**

Verified on 2026-09-23 in a Linux container running Docker 29.3.1, with Python 3.12.3 on the host and `python:3.12-slim` in the image.

## Result

The service is implemented, statically checked, tested against a real FalkorDB, and verified end to end on the local Docker Compose stack.

| Check | Result |
|---|---|
| Automated tests | 131 of 131 pass. Warnings are treated as errors. No graph-store mocks are used. |
| Stack verification | 73 of 73 checks pass. |
| Graph state persistence | State survives an API restart, a FalkorDB restart, a SIGKILL of FalkorDB (recovered from AOF), and recreating both containers. |
| Railway | Not deployed, as instructed. |
| Zeptly | Not connected. |

## Files created

All files are in the new directory `zeptly-semantica-api/`. No existing repository file was modified.

```
zeptly-semantica-api/
  app/
    __init__.py            package + service version
    __main__.py            production entrypoint (python -m app), binds 0.0.0.0:${PORT}
    main.py                FastAPI app factory, semantica pin check, error mapping, body-size guard
    config.py              env-driven Settings with fail-closed validation
    auth.py                X-API-Key dependency, constant-time comparison
    graph.py               ProjectionGraph: Semantica GraphStore(falkordb) + fixed Cypher templates
    models.py              strict pydantic request/response models (extra="forbid")
    routes/__init__.py
    routes/health.py       GET /health, GET /ready
    routes/projection.py   /v1 node, edge and relationship-query routes
  tests/
    conftest.py            real-FalkorDB fixtures, unique throwaway graph per session
    test_config.py         configuration posture (unit)
    test_health.py         health, readiness, real-outage readiness, docs disabled
    test_auth.py           missing/invalid/valid key, auth-before-validation, anonymous mode
    test_nodes.py          node CRUD, idempotency, label limit, forbidden fields, validation
    test_edges.py          edge CRUD, endpoint move, detach-on-delete, relationship query
    test_isolation.py      workspace A/B isolation (read, traverse, link, modify, delete)
  scripts/
    lock.sh                regenerates hash-pinned requirements.lock (semantica==0.6.0 appended)
    verify_stack.sh        end-to-end compose verification (restarts, outage, AOF, recreate)
  Dockerfile               multi-stage, slim, hash-verified wheels, non-root, read-only-safe
  .dockerignore            allow-list: app/ + requirements.lock only
  docker-compose.yml       local only: semantica-api + FalkorDB (volume, AOF, password)
  railway.toml             Dockerfile build, /health healthcheck, ON_FAILURE restart
  pyproject.toml           project metadata + pytest/ruff/mypy configuration
  requirements.in          direct runtime deps (exact versions)
  requirements.lock        full runtime closure with sha256 hashes (incl. semantica==0.6.0)
  requirements-dev.txt     pytest, httpx, ruff, mypy
  .env.example             placeholders only
  .gitignore
  README.md
  SECURITY.md
  PHASE-6.25A-IMPLEMENTATION-RESULT.md
```

**Why the service is in a subdirectory.** The repository is a fork of upstream Semantica. Its root already holds Semantica's own `Dockerfile`, `README.md`, `SECURITY.md`, `pyproject.toml`, `docker-compose.yml` and the `semantica/` package at version 0.7.x. Putting the service at the root would overwrite upstream files. It would also let `import semantica` pick up the local 0.7.x source instead of the pinned 0.6.0 wheel. A self-contained directory avoids both problems. On Railway, the service's Root Directory must be set to `zeptly-semantica-api`.

## Semantica version

* **`semantica==0.6.0`** is installed from PyPI with a pinned hash: `sha256:63fda45ad4052d43bbb8d67b71b5180934f3906b588b2715fa1f00db8cb48a4e`.
* It is installed with `--no-deps`. Its published metadata requires the full ML stack (torch, transformers, spacy, faiss, opencv and more). I checked that `semantica.graph_store` imports none of these. The FalkorDB backend only needs the `falkordb` client, which is pinned separately.
* The pin is enforced in three places:
  * `requirements.lock` hashes;
  * an assertion in the Docker build;
  * `app.main._check_semantica_pin()`, which refuses to start on any other version.
* The upstream source is not modified.
* The FalkorDB server is pinned to `falkordb/falkordb-server:v4.14.8`.

Resolved runtime closure:

| Package | Version |
|---|---|
| semantica | 0.6.0 |
| falkordb | 1.7.1 |
| redis | 8.1.0 |
| fastapi | 0.141.1 |
| starlette | 1.7.0 |
| uvicorn | 0.53.0 |
| pydantic | 2.13.5 |
| pydantic-core | 2.46.5 |

The remaining packages are small transitive dependencies. All of them are hash-pinned.

## Architecture implemented

```
(caller, e.g. Zeptly in Phase 6.5) --HTTPS + X-API-Key--> zeptly-semantica-api --private network--> FalkorDB
```

* **API process.** A single uvicorn process, stateless. Synchronous handlers run on FastAPI's threadpool.
* **Persistence path.** `semantica.graph_store.GraphStore(backend="falkordb")` → `FalkorDBStore` → FalkorDB. Every read and write goes through `GraphStore.execute_query` with parameterized Cypher.
* **Not used.** `ContextGraph` and Knowledge Explorer storage.
* **Not present.** No worker, vector DB, RDF or ontology store, MCP service, LLM calls or embeddings. The service owns no PostgreSQL.
* **Documented capability gap.** Semantica 0.6.0's `FalkorDBStore.connect()` does not pass socket timeouts through to the client. `graph.py` therefore builds the `falkordb` client with bounded timeouts (`FALKORDB_TIMEOUT_SECONDS`) and installs it into Semantica's own `FalkorDBClient` wrapper. This touches `GraphStore._store_backend._client`, which is private, but queries still run through `GraphStore`. Semantica's interactive progress tracker is switched off because it printed a progress bar for every query.

## API contract

| Route | Auth | Success | Errors |
|---|---|---|---|
| `GET /health` | none | 200 `{"status":"ok"}` | none |
| `GET /ready` | none | 200 `{"status":"ready"}` | 503 `{"status":"unavailable"}` |
| `PUT /v1/workspaces/{ws}/nodes/{id}` | key | 201 created / 200 updated `{created, node}` | 401, 413, 411, 422, 503 |
| `GET /v1/workspaces/{ws}/nodes/{id}` | key | 200 node | 401, 404, 422, 503 |
| `DELETE /v1/workspaces/{ws}/nodes/{id}` | key | 200 `{deleted, detached_edges}` | 401, 404, 503 |
| `PUT /v1/workspaces/{ws}/edges/{id}` | key | 201 / 200 `{created, edge}` | 401, 409 (endpoint not in workspace), 422, 503 |
| `DELETE /v1/workspaces/{ws}/edges/{id}` | key | 200 `{deleted}` | 401, 404, 503 |
| `POST /v1/workspaces/{ws}/relationships/query` | key | 200 `{node, relationships[], truncated}` | 401, 404, 422, 503 |

**Node payload.**

* Required: `workspace_id`, `canonical_id` and `node_type`.
* Optional: `label` (at most 160 characters), `lifecycle_state`, `is_current`, `valid_from`, `valid_to`, `source_updated_at`, `source_version` and `provenance_refs`.
* Unknown fields are rejected, and every field is pattern-constrained or type-constrained.
* `PUT` is a full replace. The server keeps `projected_at` from the first write and updates `updated_at` on every write.

**Edge payload.**

* Required: `workspace_id`, `edge_id`, `edge_type` (UPPER_SNAKE), `source_node_id` and `target_node_id`.
* Edges carry the same lifecycle, temporal and provenance fields as nodes, and no label.

**Relationship query.**

* Returns a single anchor node's one-hop explicit relationships.
* `direction` is one of `outgoing`, `incoming` or `both`.
* Optional filters: `edge_types` (1–16), `include_non_current` (default false) and `limit` (1–200, with a `truncated` flag).
* There is no depth parameter, no Cypher input and no generic query endpoint.

## Authentication

* **Header and comparison.** `X-API-Key` is checked against `SEMANTICA_API_KEY` with `hmac.compare_digest`. A missing key and a wrong key get the same 401. Authentication runs before payload validation.
* **Refused startup.** The process exits with code 2 in any of these cases:
  * the key is missing and anonymous access is not enabled;
  * `SEMANTICA_ALLOW_ANONYMOUS=true` outside `development` or `test`;
  * the key is shorter than 32 characters in `production` or `staging`.

  An unset `SEMANTICA_ENV` is treated as `production`. I verified all three refusals against the built image.
* **Runtime guard.** If the key is somehow absent at runtime, requests fail closed with 503.
* **Unauthenticated routes.** `/health` and `/ready` need no key and return only fixed status strings. `/docs` and `/openapi.json` are disabled outside development and test.

## Persistence design

* **Graph and indexes.** Everything lives in one FalkorDB graph, `zeptly_semantica`, configurable with `FALKORDB_GRAPH`. Nodes are labeled `:SemanticaNode` and edges are typed `:SEMANTIC_EDGE`. There are indexes on node `key`, node `workspace_id` and edge `key`. They are created idempotently the first time the service becomes ready.
* **Workspace-scoped identity.** Each object's key is `"{workspace_id}:{id}"`, built by the service from the URL path. Identifiers may not contain `:`, so keys cannot be ambiguous. They are validated in the API and again in `scoped_key()`.
* **Idempotent upserts.** Node upserts use `MERGE` on the scoped key followed by `SET n = $props`. Edge upserts run as one atomic query. It matches both endpoints inside the workspace, removes a stale copy of the edge if its endpoints changed, then `MERGE`s the edge. Deleting a node uses `DETACH DELETE` and reports how many edges were removed.
* **Durability.** FalkorDB runs with AOF (`appendfsync everysec`) plus the default RDB snapshots, on a volume at `/var/lib/falkordb/data`.
* **Rebuildability.** The graph is derived state. Dropping it and replaying canonical rows through `PUT` rebuilds it, because every write is idempotent.

## Test results

### Static checks

```
ruff check app tests          -> All checks passed!
ruff format --check app tests -> 18 files already formatted
mypy                          -> Success: no issues found in 10 source files
```

### pytest

Run against `falkordb/falkordb-server:v4.14.8` with `--requirepass` and AOF enabled, through the real Semantica GraphStore:

```
131 passed in 1.82s
```

The suite was also run against `falkordb/falkordb:v4.14.8` with no password: 130 of 130 passed. That run happened before the chunked-body test was added.

### Coverage of the 17 required cases

| # | Required case | Test(s) |
|---|---|---|
| 1 | health endpoint | `test_health_is_unauthenticated_and_minimal` |
| 2 | readiness | `test_ready_when_graph_store_reachable` |
| 3 | missing auth rejected | `test_missing_api_key_rejected` (6 routes) |
| 4 | invalid auth rejected | `test_invalid_api_key_rejected` (6 routes × 5 keys), `test_bearer_header_is_not_accepted` |
| 5 | valid auth accepted | `test_valid_api_key_accepted` |
| 6 | create node | `test_create_node` |
| 7 | idempotent upsert | `test_idempotent_node_upsert`, `test_idempotent_edge_upsert_and_endpoint_move` |
| 8 | retrieve node | `test_retrieve_node` |
| 9 | create edge | `test_create_edge`, `test_edge_requires_existing_endpoints` |
| 10 | relationship query | `test_relationship_query`, `test_relationship_query_is_constrained` |
| 11 | delete edge | `test_delete_edge` |
| 12 | delete node | `test_delete_node`, `test_delete_node_detaches_connected_edges` |
| 13 | label >160 rejected | `test_label_over_160_characters_rejected` (plus the 160-character boundary accepted) |
| 14 | forbidden fields rejected | `test_forbidden_payload_fields_rejected` (21 fields), `test_invalid_edge_payload_rejected` |
| 15 | A cannot retrieve B node | `test_workspace_a_cannot_retrieve_workspace_b_node` |
| 16 | A cannot traverse B relationship | `test_workspace_a_cannot_traverse_workspace_b_relationship`, `test_cross_workspace_edge_written_out_of_band_is_not_traversed` |
| 17 | graph-store outage → not ready | `test_graph_store_outage_is_not_ready` (a real refused connection, not a mock), plus the stack outage in step 6 below |

Additional tests cover:

* configuration posture (10 tests);
* identifier, timestamp and provenance validation;
* body/path mismatch;
* the 64 KiB body limit (413) and chunked bodies without a length (411);
* docs being disabled in production;
* anonymous mode only in the test environment;
* a wrong FalkorDB password producing "not ready".

## Isolation test results

All isolation tests pass. Workspaces A and B each hold a node with the same canonical ID, `shared`, and B also has private nodes and edges.

* **Reading.** A cannot `GET` B's nodes (404). The same canonical ID resolves to each workspace's own node.
* **Traversing.**
  * Anchoring a query from A on B's node returns 404.
  * A's traversal of `shared` returns only A's edge. B's IDs, labels and workspace name never appear in the response.
  * A cross-workspace edge inserted directly into FalkorDB, bypassing the API, is **not** followed in any direction. This proves the per-hop `workspace_id` filters work on their own.
* **Linking.** A cannot create an edge to B's node (409).
* **Modifying and deleting.** A cannot delete B's node or edge (404). Upserting or deleting A's `shared` leaves B's `shared` node and its edge untouched.
* **Identifier independence.** The same `edge_id` in A and B refers to two independent edges.
* **Redirecting through the body.** A body `workspace_id` that differs from the path is rejected (422).

The stack verification repeated the A/B isolation checks after every restart phase.

## Stack verification (`scripts/verify_stack.sh`)

Settings: `SEMANTICA_ENV=production`, a random 64-character API key, and a random FalkorDB password. Every step passed.

1. **Start.** Fresh volume. `/health` returns 200 and `/ready` returns 200. `/docs` returns 404 in production. The API runs as uid 10001.
2. **Authentication.** A missing key returns 401. An invalid key returns 401.
3. **Workspace A and B fixtures.**
   * Creates return 201 and a repeat upsert returns 200.
   * A cross-workspace link returns 409.
   * Edge create and delete work, and node create and delete work.
   * A 161-character label returns 422, and a `content` field returns 422.
   * The state checks pass.
4. **`docker compose restart semantica-api`.** State persisted and isolation still held.
5. **`docker compose restart falkordb`** (volume retained). State persisted.
6. **`docker compose stop falkordb`.**
   * `/ready` returns 503 and `/health` stays 200.
   * `/v1` calls return 503 `{"detail":"graph store unavailable"}`.
   * After `start`, `/ready` recovers and the state is intact.
7. **`docker compose kill -s SIGKILL falkordb` then `start`.** State was recovered from AOF.
8. **`docker compose down` then `up`.** Containers were recreated and the volume kept. State is intact.

Result: `==> PASS: 73 checks`, exit code 0.

### Other image checks

* **User and filesystem.** The image runs as `uid=10001(semantica)`. `/opt/service` and `/opt/venv` are not writable. There is no compiler in the image.
* **Secrets.** No CA certificate or other secret is present.
* **Size.** 249 MB on disk, about 59 MB compressed.
* **Read-only root filesystem.** Running with `--read-only` and `PORT=9099` serves `/health` on 9099. `/ready` returns 503 when FalkorDB is unreachable.
* **Logs.** API and FalkorDB logs contain neither the API key nor the FalkorDB password.

## Remaining issues

These are for the Phase 6.25B audit to consider. None of them is a failing check.

1. **Private-attribute use in Semantica.** The socket-timeout workaround reaches into `GraphStore._store_backend._client`. It is safe under the exact 0.6.0 pin and covered by tests, but it has to be rechecked on any Semantica upgrade.
2. **pip check warnings.** `pip check` reports semantica's undeclared ML dependencies as missing. This is expected from the deliberate `--no-deps` install.
3. **Single shared API key.** There is no per-caller identity, key versioning or overlap window for rotation. Any holder of the key can address any workspace, because Zeptly is the authorization authority.
4. **No rate limiting.** The service relies on private networking.
5. **Healthcheck choice.** The Railway healthcheck uses `/health` as specified, which does not check FalkorDB. Graph connectivity must be monitored through `/ready`.
6. **Railway FalkorDB settings are documentation only.** The volume at `/var/lib/falkordb/data`, AOF, `--requirepass` and no public exposure are described in README but not deployed. They must be applied and audited in 6.25B.
7. **Out-of-order writes.** `source_version` is stored but not enforced, so the last write wins. If ordering guarantees are needed, they belong to Zeptly's projection publisher in Phase 6.5.
8. **Local build needs a CA secret.** In this sandbox, the image build needs `--secret id=build_ca,...` because outbound TLS is intercepted. That is why the stack script ran with `VERIFY_SKIP_BUILD=1` against the image built immediately before it. A plain `docker build .` needs no secret on Railway or on a normal network.

## Exact verification commands

```sh
# Docker daemon (sandbox only)
nohup dockerd > /tmp/claude-0/dockerd.log 2>&1 &

cd zeptly-semantica-api

# 1. install dependencies
python3.12 -m venv .venv && . .venv/bin/activate
pip install --upgrade pip
pip install --no-deps --require-hashes -r requirements.lock
pip install -r requirements-dev.txt

# 2. static / type checks
ruff check app tests
ruff format --check app tests
mypy

# 3 + 4. unit + integration tests against local FalkorDB (password + AOF)
docker run -d --name falkor-test -p 127.0.0.1:6380:6379 \
  -e REDIS_ARGS="--requirepass test-pass --appendonly yes" falkordb/falkordb-server:v4.14.8
FALKORDB_PORT=6380 FALKORDB_PASSWORD=test-pass python -m pytest -q     # 131 passed
python -m pytest -q -m "not integration"                               # tests that need no FalkorDB

# image build (sandbox needs the proxy CA as a build secret; not needed elsewhere)
docker build --secret id=build_ca,src=/root/.ccr/ca-bundle.crt -t zeptly-semantica-api:local .

# image posture
docker run --rm --entrypoint id zeptly-semantica-api:local
docker run --rm --read-only zeptly-semantica-api:local                            # exit 2: no key
docker run --rm --read-only -e SEMANTICA_API_KEY=$(python3 -c 'print("k"*40)') \
  -e SEMANTICA_ALLOW_ANONYMOUS=true zeptly-semantica-api:local                    # exit 2
docker run --rm --read-only -e SEMANTICA_API_KEY=short zeptly-semantica-api:local # exit 2

# 5–10. full stack, A/B fixtures, API restart, FalkorDB restart, outage, SIGKILL, recreate
VERIFY_SKIP_BUILD=1 ./scripts/verify_stack.sh                           # PASS: 73 checks
```

No Railway deployment was performed. Zeptly was not connected. No Zeptly PostgreSQL was touched. `ExternalSemanticaClient`, Phase 6.5 and Phase 7 were not implemented.

READY FOR PHASE 6.25B — SECURITY AND DEPLOYMENT AUDIT
