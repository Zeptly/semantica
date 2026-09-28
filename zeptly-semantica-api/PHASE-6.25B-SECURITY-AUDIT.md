# Phase 6.25B: Security, Isolation and Deployment Audit

**Scope:** `zeptly-semantica-api/`, the service implemented in Phase 6.25A.

**Method:** an independent review. I re-read every file, inspected the *installed* Semantica 0.6.0 and `falkordb` client source rather than trusting Phase A's descriptions, then attacked the running service in-process, over real HTTP, and on the real Docker/FalkorDB stack.

**Environment:** a Linux sandbox with Docker 29.3.1, `falkordb/falkordb-server:v4.14.8`, and Python 3.12 (host 3.12.3, image 3.12.14).

**Railway:** no Railway tool is connected to this session, so nothing was deployed. Railway behaviour comes from Railway's public documentation, found by web search because `docs.railway.com` itself was blocked by this sandbox's egress proxy. It must be confirmed on Railway in Phase 6.25C.

## 1. Summary

The service does what 6.25A claimed in the areas that matter most:

* **Persistence:** it really does persist through Semantica's `GraphStore` and FalkorDB.
* **Workspace isolation:** it held under every attack attempted.
* **Query injection:** every query is a fixed template.

The audit found **9 defects**. None was an isolation or injection break. They were:

* one authentication ambiguity;
* one private-networking blocker;
* one missing FalkorDB-auth requirement;
* input-hardening, container-surface and observability gaps;
* two defects in 6.25A's own verification tooling, which could have hidden failures.

All nine are fixed and re-verified:

* **pytest:** 258 tests pass, 227 of them against real FalkorDB.
* **Stack verification:** 139 checks pass on the real stack.

The service is suitable for Railway provisioning. **The deployment itself is unverified**, and Phase 6.25C must validate the items listed in §13.

## 2. Findings and fixes

| ID | Severity | Finding | Fix | Evidence |
|---|---|---|---|---|
| D1 | **Medium** | **Duplicate `X-API-Key` headers.** Starlette's `headers.get()` returns the first value. A request carrying `X-API-Key: <valid>` followed by `X-API-Key: <anything>` was accepted (HTTP 404, i.e. authenticated), while the reversed order was rejected. Proxies differ on which duplicate they forward, so this is an ambiguity an attacker can use. | `auth.py` now reads `headers.getlist()` and rejects more than one value with the same 401 (reason `ambiguous`). | `test_duplicate_api_key_headers_rejected` (3 orderings). Stack §2: "valid+wrong duplicate (401)" and "wrong+valid duplicate (401)". |
| D2 | **High, for deployment** | **IPv4-only bind.** The service bound `0.0.0.0`. Railway private networking is **IPv6-only in environments created before 16 Oct 2025**, and Railway recommends binding `::`. In such an environment Zeptly could never have reached the API privately, which would have blocked the Phase 6.5 private-only design. | `python -m app` binds dual-stack `::` (with `IPV6_V6ONLY=0`) and falls back to `0.0.0.0` only if the kernel has no IPv6. The chosen host is logged (`event: bind`). | `test_bind_host_selection_logic` (both branches). Stack §14 proves the IPv4 fallback. The `::` branch is **not executable in this sandbox**, because neither the host nor Docker has IPv6. **6.25C must check it** (§13). |
| D3 | **High, if deployed as documented** | **FalkorDB has no authentication by default.** The inspected `falkordb-server` image runs `redis-server --protected-mode no` bound to `* -::*`. The API treated `FALKORDB_PASSWORD` as optional, so a deployment without `--requirepass` would expose the whole graph to any service on the private network. | `FALKORDB_PASSWORD` is **required** when `SEMANTICA_ENV` is production or staging; otherwise startup is refused with exit code 2. The guide makes `--requirepass` mandatory. | `test_falkordb_password_required_outside_dev` (6 cases). Stack §12: a wrong password gives healthy but not ready. The `startup` log shows `falkordb_password_set: true`. |
| D4 | Low | **Timestamp overflow gave a 500.** `9999-12-31T23:59:59-05:00` passed validation and then overflowed in `astimezone(UTC)`, returning HTTP 500. Numeric epoch timestamps were also silently accepted. | Timestamps must be ISO-8601 **strings** with a timezone. UTC conversion happens during validation, and overflow returns 422. | `test_timestamps_must_be_valid_aware_iso_strings` (8 cases). |
| D5 | Low | **Label hardening.** Labels accepted C1 control characters (`\x85`), bidi overrides and isolates (`‮`, `⁦`), and line and paragraph separators, all of which allow deceptive display or line injection in consumers. | The label validator rejects every Unicode `Cc` character (C0 and C1), bidi embedding, override, isolate and mark characters, and ` `/` `. Ordinary Unicode and emoji are still accepted. | `test_label_rejects_c1_controls_bidi_and_separators` (7 cases), `test_label_accepts_ordinary_unicode`. |
| D6 | Medium | **Container surface.** The base image tag `python:3.12-slim` floated rather than being pinned. The runtime image contained: pip and setuptools; **Semantica's console scripts** `semantica-server`, `semantica-explorer`, `semantica-mcp` and `semantica-worker`; the `fastapi` dev-server CLI; and venv activation scripts. The compose FalkorDB image was pinned only by tag. | The base image is pinned by digest (`python:3.12-slim@sha256:2f17fc04…`). pip, setuptools, wheel, idle and pydoc are removed from the runtime image, as are all `semantica*` and `fastapi` scripts and the activation scripts. FalkorDB is pinned by tag and digest. | Image inspection (§8). `/opt/venv/bin` now holds only `python*`, `uvicorn` and `idna`. |
| D7 | Medium | **Observability.** There was no count of authentication failures, and graph outages were logged on *every* failed request, flooding the logs. Each restart logged a misleading `graph query failed` error for "index already exists". Logs were plain text. | JSON-lines logging on the standard library. Events: `startup`, `bind`, `readiness`, `graph_connected` and `graph_unavailable` (logged on transitions only), `auth_rejected` (reason, route template, running total), `graph_operation_failed` (operation name and exception type only), `config_rejected`. Index-exists errors are silenced. uvicorn's ANSI `color_message` is dropped. | `test_auth_failures_are_counted_and_logged_without_credentials`, `test_request_bodies_never_logged`. Stack §14: every API log line parses as JSON; key, password and payload markers are absent. |
| D8 | Medium (tooling) | **6.25A's stack script could pass without checking.** It used `[[ cond ]] && ok`, which under `set -e` **skips silently** when the condition is false. Re-running it during this audit showed two checks being skipped without a failure. | Every assertion now uses `if …; then ok; else fail; fi` or `check`. The script has no `&& ok` left. | The count of printed `ok` lines matches the checks written. The two previously silent checks now print explicit `ok` lines. |
| D9 | Low (tooling) | **Mis-marked test.** 6.25A reported `pytest -m "not integration"` as "tests that need no FalkorDB". In fact `test_health_is_unauthenticated_and_minimal` needed FalkorDB and passed only because it was running. | The test now builds an app pointed at a closed port, which also proves that liveness doesn't depend on the graph. Three new surface tests are correctly marked `integration`. | `pytest -m "not integration"` with FalkorDB stopped: 31 passed, 0 errors. |

Additional hardening:

* uvicorn `limit_concurrency=100` (503 beyond it).
* `railway.toml` corrected for Railway monorepo behaviour: no `dockerfilePath`, and `watchPatterns` scoped to the service directory.
* A `--no-cache` API rebuild added to the stack verification.

## 3. Dependency integrity

| Item | Status |
|---|---|
| Semantica pin | **Exactly `semantica==0.6.0`**, `sha256:63fda45a…a4e`, in `requirements.lock`. The Docker build asserts the version. `create_app()` refuses to start on any other version. The running container reports 0.6.0 (stack §1), and so does the `startup` log. Not upgraded. |
| Reproducible resolution | `requirements.lock` covers the full runtime closure (18 packages, 153 sha256 hashes). It is installed with `--require-hashes --no-deps --only-binary=:all:`. Regenerating it with `scripts/lock.sh` produced a **byte-identical** file. |
| Runtime versions | semantica 0.6.0, falkordb 1.7.1, redis 8.1.0, fastapi 0.141.1, starlette 1.7.0, uvicorn 0.53.0, pydantic 2.13.5, pydantic-core 2.46.5, anyio 4.15.1, h11 0.16.0, click 8.5.0, idna 3.20, python-dateutil 2.9.0.post0, six 1.17.0, typing-extensions 4.16.0, typing-inspection 0.4.4, annotated-types 0.8.0, annotated-doc 0.0.5. |
| Known vulnerabilities | `pip-audit -r requirements.lock`: **no known vulnerabilities found** (2026-09-23). |
| Docker images | API base `python:3.12-slim@sha256:2f17fc044b579bab302c2e8054d3a686e2cb9a83de48e70534b94cd8ebbe06a9` (Python 3.12.14). FalkorDB `falkordb/falkordb-server:v4.14.8@sha256:71afd8c7cc44c15caab5fb35343d46bb0dc6ce3ee80a33f599f5d6647cdb50c4`. No `latest` anywhere. |
| Semantica install shape | `--no-deps`. I re-verified that `semantica.graph_store` imports no third-party module beyond `falkordb`, and `redis` through it. semantica's declared ML dependencies are absent by design. |
| Rollback | Every change is a Git commit, and pinned inputs make rebuilding an old revision reproducible. Railway also keeps prior deployments for one-click rollback (guide, "Rollback"). |

## 4. Graph persistence

**Installed-code review.** `ProjectionGraph` constructs `semantica.graph_store.GraphStore(backend="falkordb")`, and every read and write goes through `GraphStore.execute_query` → `FalkorDBStore.execute_query` → `FalkorDBGraph.query` → `falkordb.Graph.query`. There is no in-memory store, `ContextGraph` or mock in the service path.

The one deviation is the documented timeout gap. The service builds a `falkordb.FalkorDB` client with bounded socket timeouts and places it into Semantica's private `_store_backend._client`, because semantica 0.6.0's `connect()` does not forward timeouts.

**Stack evidence** (`scripts/verify_stack.sh`, final run: `PASS: 139 checks`). The fixture state was re-checked after each event:

| Event | Result |
|---|---|
| API container restart (§5) | state intact |
| **API image rebuilt `--no-cache`** and container recreated (§6), image `e2d236884bd8` → `db89555d5665` | state intact |
| FalkorDB restart with volume retained (§7) | state intact |
| FalkorDB stopped then started (§8–9) | 503 during the outage, recovered **without an API restart**, state intact |
| FalkorDB `SIGKILL`, an unclean stop (§10) | state recovered from AOF |
| API started while FalkorDB was down (§11) | API stays healthy and not ready, then becomes ready when FalkorDB starts |
| `docker compose down` then `up`, volume retained (§13) | state intact in new containers |

The "fixture state" re-checked each time was:

* A's node and B's node, which share a canonical ID;
* A's two edges and B's one edge;
* the deleted node staying deleted;
* the isolation assertions.

## 5. Workspace isolation

**Mechanisms, re-verified in code.**

* **Scoped identity.** The key is `"{workspace_id}:{id}"`, built from the *path*. IDs match `^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$`, so they contain no `:` and keys are unambiguous. `scoped_key()` re-validates both parts in the graph layer.
* **Per-hop filters.** Every template filters `workspace_id` on the anchor node, the edge **and** the neighbour.
* **Endpoint checks.** An edge upsert matches both endpoints inside the workspace.

**Attacks attempted.** All of them failed to cross workspaces, and every response was checked for B's workspace name, IDs and labels:

| Attack | Result |
|---|---|
| Direct lookup of B's node from A (`GET /v1/workspaces/A/nodes/b-secret`) | 404, identical to a nonexistent node |
| Guessed canonical ID shared by both workspaces | Each workspace gets its own node, and labels never cross |
| Path traversal: `…/nodes/..%2F..%2FB%2Fnodes%2Fb-secret`, raw `/../../B/…`, `/workspaces/A/../B/…` (sent with `curl --path-as-is`) | 404, and nothing from B disclosed |
| Malformed workspace IDs: `A:x`, `A\n`, `B\0`, `*`, 129 characters | 422 |
| Node ID carrying another workspace's prefix (`B:b-secret`) | 422 |
| Edge from A's node to B's node | 409, identical to a nonexistent endpoint, with no disclosure |
| Reusing B's `edge_id` inside A | Creates an independent A edge. B's edge is unchanged, and A cannot delete B's edge (404) |
| A deleting B's node or edge | 404, and B is intact |
| Relationship query anchored on B's node from A | 404 |
| Relationship traversal from A's `shared` node (all directions) | Only A's edges are returned |
| Cross-workspace edge **written directly into FalkorDB**, bypassing the API | Never traversed from A or B, in any direction (`test_cross_workspace_edge_written_out_of_band_is_not_traversed`) |
| Body `workspace_id` differing from the path | 422 |
| `cypher` field in the query body | 422 |

**Residual risk (by design).** Any holder of the service key can address any workspace. The service scopes what it touches, but *which* workspaces a caller may use is decided by Zeptly, the authorization authority.

## 6. Authentication

* **Stack results (§2):**
  * missing key: 401;
  * wrong key: 401;
  * **empty** key (`X-API-Key;`): 401;
  * duplicate header (valid then wrong): 401;
  * duplicate header (wrong then valid): 401;
  * `Authorization: Bearer <key>`: 401;
  * key in the query string: 401;
  * valid key: 404, which means it reached the handler and the node is absent;
  * `/health` without a key: 200.
* **Additional tests:**
  * trailing whitespace: 401, because matching is exact;
  * upper-cased key: 401;
  * key in a cookie: 401;
  * wrong header names (`X_API_KEY`, `API-Key`): 401;
  * non-ASCII or `\xff` header bytes: 401;
  * header name in lower case: accepted, as HTTP requires.
* **Fail closed.** Startup is refused with exit code 2 when:
  * the key is missing or blank;
  * anonymous access is enabled outside dev/test;
  * the key is shorter than 32 characters in production or staging;
  * the FalkorDB password is missing in production or staging.

  I confirmed each against the built image. If configuration were bypassed programmatically, requests would fail with 503.
* **Timing.** SHA-256 digests of the presented and expected keys are compared with `hmac.compare_digest`, so comparison time is independent of the presented key's length and content.
* **Ordering.** Authentication runs before validation, so unauthenticated callers never see schema errors.
* **Health checks.** `/health` and `/ready` need no authentication and return fixed bodies.

## 7. Query-injection review

Every Cypher statement is a module-level constant in `app/graph.py`:

`_Q_PING`, `_Q_INDEXES` (3 statements), `_Q_UPSERT_NODE`, `_Q_GET_NODE`, `_Q_DELETE_NODE`, `_Q_UPSERT_EDGE`, `_Q_DELETE_EDGE`, and `_Q_RELATIONSHIPS[outgoing|incoming|both]`.

| User-controlled input | How it reaches FalkorDB |
|---|---|
| IDs and workspace IDs | Only as `$key`, `$ws`, `$src_key` or `$tgt_key` parameters. Regex-validated twice. |
| Labels | Inside the `$props` map value. Free text, stored as data. |
| Relationship (edge) types | Stored as the `edge_type` *property* inside `$props`, and filtered with `r.edge_type IN $edge_types`. They are **never** part of the Cypher relationship type. The graph label (`SemanticaNode`) and relationship type (`SEMANTIC_EDGE`) are fixed constants, which is a stricter scheme than an allowlist. |
| Temporal metadata | Parsed into datetimes, then re-serialised by the server as ISO strings inside `$props`. |
| Provenance references | A list parameter inside `$props`, pattern-validated. |
| Query direction | Chooses between three fixed templates through a closed `Literal` enum. |
| Query limit | `$limit` parameter, an integer between 1 and 200 (plus 1 to detect truncation). |
| Map keys and parameter names | Always server-chosen model field names. Clients cannot add keys because every model uses `extra="forbid"`. |

**Driver finding: parameters are not bound on the server.** Inspecting the installed `falkordb` 1.7.1 client showed it builds a text header, `CYPHER \`name\`=<literal> …`, in front of the query. String literals are produced by `quote_string()`, which escapes `\` and `"`. So "parameterised" here means *client-side quoting*.

I verified this path directly:

* **Direct round-trip.** `test_driver_parameter_quoting_round_trips` sends 13 hostile strings through `RETURN $x` as scalar, list and map values. They include `"}) MATCH (m) DETACH DELETE m //`, a trailing `\`, `\\\`, `\"`, `"` text, `$ws`, a `CYPHER ws='other'` prefix and backticks. All came back unchanged.
* **Through the API.** `test_hostile_labels_stored_verbatim_without_side_effects` checks labels are stored exactly and that another workspace's data is untouched.
* **NUL bytes.** A NUL byte could truncate the C-level query text. Every field that can reach the database rejects it: IDs, types and lifecycle by pattern, labels by the `Cc` rule, and timestamps by parsing (`test_every_string_field_rejects_nul_and_injection_shapes`).
* **Structural test.** `test_all_cypher_is_constant_text` parses `graph.py`. It asserts that the template f-strings interpolate only `NODE_LABEL`, `EDGE_TYPE` and `_REL_FILTER`, and that every `_run` call receives a module constant.
* **Semantica's own helpers.** `FalkorDBStore.create_node` and `create_index` do f-string property and label names. The service **does not call them**. It uses only `execute_query`.

**Conclusion:** none of the user-controlled inputs can break out into arbitrary Cypher execution.

## 8. Container review

| Check | Result |
|---|---|
| User | Runs as uid 10001 (`semantica`) with a `nologin` shell and no home directory. The FalkorDB image runs as root; that is upstream's default and it has no public networking. |
| Writable code | `/opt/service` and `/opt/venv` are owned by root and not writable by uid 10001. Runs under `--read-only` (compose `read_only: true`, `cap_drop: ALL`, `no-new-privileges`). |
| Packages and tools | No pip, setuptools, compilers, `make`, curl, wget or nc. `/opt/venv/bin` holds only `python*`, `uvicorn` and `idna`. **Remaining from Debian slim:** `sh`, `bash`, `perl` (essential) and `apt-get`/`dpkg`, which can't be used for installation because the process is not root (§12). |
| Secrets | None in the image. No build-time CA or other secret is referenced by the committed Dockerfile. (Post-audit correction, Phase 6.25C: the optional `--mount=type=secret` used for this sandbox's TLS proxy was removed because Railway's builder rejects secret mounts; local proxy builds now use a throwaway Dockerfile copy generated by `scripts/verify_stack.sh`.) `.env` is git-ignored. `git grep` found no key-like values beyond test placeholders. |
| Dev server | None. `python -m app` starts `uvicorn.run(workers=1)` with no reload. The `fastapi dev` CLI is removed. |
| Front-end and admin UIs | None. The `falkordb-server` image has no browser. `/docs` and `/openapi.json` return 404 in production. The Semantica Explorer, MCP, server and worker console scripts are removed and nothing imports those modules. |
| Size | 232 MB on disk. |

## 9. API exposure

The route inventory is asserted exactly (`test_route_inventory_is_exactly_the_phase_625_contract`):

```
GET    /health
GET    /ready
PUT    /v1/workspaces/{workspace_id}/nodes/{node_id}
GET    /v1/workspaces/{workspace_id}/nodes/{node_id}
DELETE /v1/workspaces/{workspace_id}/nodes/{node_id}
PUT    /v1/workspaces/{workspace_id}/edges/{edge_id}
DELETE /v1/workspaces/{workspace_id}/edges/{edge_id}
POST   /v1/workspaces/{workspace_id}/relationships/query
```

The service has none of the following:

* a `/cypher` endpoint or generic graph administration;
* file upload;
* ontology authoring or document ingestion;
* LLM calls, shell or code execution, or pipeline execution;
* metrics or debug endpoints;
* CORS.

I probed 13 such paths with GET, POST and PUT. All returned 404 or 405, both in tests and over HTTP.

## 10. Payload policy and denial-of-service limits

**Payload policy.** Every request model uses `extra="forbid"`. The following tests confirm unknown fields are rejected with 422:

* `test_forbidden_payload_fields_rejected` covers 21 field names: `content`, `messages`, `conversation`, `prompt`, `system_prompt`, `model_input`, `model_output`, `completion`, `embedding`, `vector`, `api_key`, `credentials`, `password`, `secret`, `provider_config`, `document`, `text`, `metadata`, `properties`, `key` and `memory_payload`.
* Stack §3 repeats the check for `content` and `embedding`.

Other payload rules:

* **Type confusion** (a label given as an object or list, a numeric `node_type`, and similar) returns 422.
* **Labels** are 1–160 characters with the character rules from D5.

**Limits.**

| Surface | Limit |
|---|---|
| Request body | 64 KiB, else 413. A `POST` or `PUT` without `Content-Length` gets 411. |
| Identifier length | 128 characters |
| `node_type`, `edge_type` | 64 characters |
| Provenance references | at most 16, each at most 256 characters |
| Query depth | fixed at one hop, and there is no depth parameter (422 if one is sent) |
| Query result count | `limit` between 1 and 200, with a `truncated` flag. Proven with a 205-edge hub node. |
| Edge-type filter | 1–16 types |
| Concurrency | uvicorn `limit_concurrency=100` |
| Graph calls | socket timeouts (`FALKORDB_TIMEOUT_SECONDS`, 5 s), and FalkorDB's own `TIMEOUT 1000` ms and `MAX_QUEUED_QUERIES 25` |

**Left to Zeptly or the network boundary:**

* per-workspace quotas, meaning the number of nodes and edges;
* request rate limiting;
* making sure `label` and `provenance_refs` carry only titles and references;
* keeping the key server-side only.

## 11. Logging

* **Never logged:** API keys, the FalkorDB password, `Authorization` or any other header, and request or response bodies. Stack §14 greps the full API and FalkorDB logs for the generated key, the generated password and three payload markers, and finds none.
* **Access log:** method, path (which contains workspace and node IDs; these are not secrets) and status.
* **Diagnostic events available:** startup configuration posture, bind address, initial readiness, graph connect and loss (on transitions), authentication failures with a running count, write and query failures by operation, index-creation failures and scope conflicts.
* **Format:** JSON lines, which Railway parses using the `level` and `message` fields.

## 12. Operational behaviour

* **`/health`** reflects process liveness only. It is proven not to depend on FalkorDB or authentication.
* **`/ready`** runs `RETURN 1` against FalkorDB and makes sure the indexes exist. It returns 503 when FalkorDB is stopped, unreachable or given a wrong password, and 200 again after recovery.
* **No crash on graph loss.** The API keeps running (stack §8 and §11) and reconnects on the next request. redis-py's default retry and 30 s health-check pings refresh stale connections.
* **Startup ordering.** Railway has no `depends_on`. The API starts, stays healthy, and becomes ready once FalkorDB appears, without restarting (stack §11).

**Railway configuration (`railway.toml`):**

* Dockerfile builder;
* `watchPatterns = ["/zeptly-semantica-api/**"]`;
* healthcheck `/health` with a 60 s timeout;
* `ON_FAILURE` restart, 10 retries;
* 1 replica.

Railway reads this file only if the service's config path is set to `/zeptly-semantica-api/railway.toml`. Railway's docs say the config file does not follow the Root Directory.

## 13. Remaining risks and items for Phase 6.25C

| # | Item | Why it is open |
|---|---|---|
| R1 | **The dual-stack `::` bind path has not been exercised.** *Superseded in Phase 6.25C:* on Railway, `host="::"` passed to uvicorn produced a listener that the IPv4 deploy healthcheck and TCP probe could never reach. `_bind_host()` had verified dual-stack on a separate probe socket with `IPV6_V6ONLY=0`, but uvicorn's own socket inherited the runtime default. The default is now `0.0.0.0`. `SEMANTICA_BIND_HOST=::` builds an explicitly dual-stack socket and hands it to uvicorn. | This sandbox has no IPv6. 6.25C must confirm the `bind` log shows `"host": "::"`, and, when Phase 6.5 needs it, that the private URL is reachable from another Railway service. |
| R2 | Railway-specific behaviour is unverified. | Specifically: whether a digest-pinned image reference is accepted, whether `railway.toml` is picked up from the absolute config path, `watchPatterns` semantics, and the volume mount on `/var/lib/falkordb/data`. All come from docs, not from use. |
| R3 | `/health` as the Railway healthcheck (a specified requirement). | A deploy with a wrong FalkorDB password still passes and replaces the previous deployment. Mitigation: always check `/ready` after a deploy (guide step 14). Alternative for later: use `/ready` as the healthcheck. |
| R4 | Single shared key, with no rotation overlap. | Rotation needs coordinated redeploys. |
| R5 | Semantic smuggling by a trusted caller. | Bounded to about 4 KB per object (§10). |
| R6 | Debian base tools (`sh`, `perl`, `apt`) remain. | Mitigated by running as non-root with read-only code. A distroless Python 3.12 image is not available upstream. |
| R7 | Private-attribute timeout workaround. | Reaches into `GraphStore._store_backend._client`. Must be revisited on any Semantica upgrade. |
| R8 | No image vulnerability scan was run. | Trivy or Grype are not available here. `pip-audit` of the Python closure is clean. |
| R9 | No rate limiting. | This is Zeptly's and the network boundary's job. Only the concurrency cap exists. |

## 14. Outstanding manual Railway actions (for 6.25C)

These follow `RAILWAY-DEPLOYMENT-GUIDE.md`:

1. Create the project `zeptly-semantica`, and note the environment's creation date for R1.
2. Create the `falkordb` service from the image `falkordb/falkordb-server:v4.14.8@sha256:71afd8c7…`, or from the tag with the digest recorded.
3. Attach a volume at `/var/lib/falkordb/data`.
4. Generate `FALKORDB_PASSWORD` and set `REDIS_ARGS=--appendonly yes --appendfsync everysec --requirepass ${{FALKORDB_PASSWORD}}`.
5. Confirm `falkordb` has **no** public domain and no TCP proxy, then record `RAILWAY_PRIVATE_DOMAIN`.
6. Create `semantica-api` from `Zeptly/semantica`:
   * Root Directory: `/zeptly-semantica-api`
   * Config path: `/zeptly-semantica-api/railway.toml`
7. Set the API variables:
   * `SEMANTICA_ENV=production`
   * `SEMANTICA_API_KEY` (generated)
   * `SEMANTICA_ALLOW_ANONYMOUS=false`
   * `FALKORDB_HOST=${{falkordb.RAILWAY_PRIVATE_DOMAIN}}`
   * `FALKORDB_PORT=6379`
   * `FALKORDB_PASSWORD=${{falkordb.FALKORDB_PASSWORD}}`
8. Deploy, then confirm the build, the `startup`, `bind` and `readiness` events, `/health` and `/ready`.
9. Generate a temporary public domain for `semantica-api` if external validation is needed. Plan its removal in Phase 6.5.

## 15. Verification commands (this audit)

```sh
cd zeptly-semantica-api && . .venv/bin/activate
ruff check app tests && ruff format --check app tests && mypy          # clean
docker run -d --name falkor-test -p 127.0.0.1:6380:6379 \
  -e REDIS_ARGS="--requirepass test-pass --appendonly yes" falkordb/falkordb-server:v4.14.8
FALKORDB_PORT=6380 FALKORDB_PASSWORD=test-pass python -m pytest -q     # 258 passed
python -m pytest -q -m "not integration"                               # 31 passed (FalkorDB not needed)
./scripts/lock.sh && git diff --exit-code requirements.lock             # identical
pip-audit --no-deps --disable-pip -r requirements.lock                  # no known vulnerabilities
VERIFY_BUILD_CA=/root/.ccr/ca-bundle.crt ./scripts/verify_stack.sh      # PASS: 139 checks
```

`VERIFY_BUILD_CA` is needed only because this sandbox intercepts TLS. It is not needed on Railway or on a normal network.

READY FOR PHASE 6.25C — RAILWAY PROVISIONING AND VALIDATION
