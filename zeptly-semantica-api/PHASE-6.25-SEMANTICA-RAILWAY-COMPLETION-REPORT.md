# Phase 6.25: Semantica on Railway, Completion Report

This report records what was deployed and what was actually observed. Evidence comes from three sources:

- the Railway dashboard and logs, read through the Railway connector;
- the operator's run of `scripts/railway_validate.sh` from an unrestricted machine on 2026-09-24;
- the repository itself.

Each validation item is marked **Proven**, **Not tested** or **Failed**. No secret values appear in this report.

## 1. Semantica version deployed

- `semantica==0.6.0`, installed from a hash-locked `requirements.lock` with `--no-deps`.
- Proven from the Railway startup log of deployment `7f279309`: `semantica_version "0.6.0"`, `service_version "0.1.0"`.
- The upstream 0.7.x source at the repository root is not used by the service.

## 2. Repository used

| Item | Value |
|---|---|
| Repository | `Zeptly/semantica` |
| Service directory | `/zeptly-semantica-api` |
| Branch | `claude/intelligent-feynman-g4czua` |
| Deployed commit | `dd4b5df` (fix: bind 0.0.0.0 by default; make the `::` override truly dual-stack) |
| Pull request | https://github.com/Zeptly/semantica/pull/1 (draft, not merged) |

## 3. Railway topology

| Item | Value |
|---|---|
| Project | `devoted-magic`, id `db108e5d-6bec-43f1-9b25-e88c83903abe` |
| Environment | `production` |
| Region | `ams` |

The project is named `devoted-magic` (Railway's generated name), not `zeptly-semantica` as the brief asked. Renaming it is cosmetic and optional.

```
devoted-magic (production, ams)
├── semantica-api   service d2237bb7…   GitHub source, Dockerfile build
└── falkordb        service 52e1c2e9…   pinned image
    └── volume falkordb-data (5 GB) at /var/lib/falkordb/data
```

There is no worker, no vector database and no other persistence service.

## 4. Service configuration

**semantica-api**

| Setting | Value |
|---|---|
| Builder | `DOCKERFILE` |
| Root directory | `/zeptly-semantica-api` |
| Config-as-code | `/zeptly-semantica-api/railway.toml` |
| Watch pattern | `/zeptly-semantica-api/**` |
| Source | Branch `claude/intelligent-feynman-g4czua`, pinned to commit `dd4b5df` |
| Health check | `/health`, 60 s timeout |
| Restart policy | `ON_FAILURE`, up to 10 retries |
| Replicas | 1 |
| Deployment | `7f279309`, SUCCESS |

**falkordb**

| Setting | Value |
|---|---|
| Image | `falkordb/falkordb-server:v4.14.8`, pinned by digest `sha256:71afd8c7…` |
| Persistence | AOF enabled through `REDIS_ARGS` |
| Password | Required through `FALKORDB_PASSWORD` |
| Public domains or TCP proxies | None |
| Deployment | `6de7dc7a`, SUCCESS |

## 5. Start command / Docker configuration

- **Image stages:** two stages. The base image is `python:3.12-slim`, pinned by digest.
- **Hardening:** runs as the non-root user `10001:10001` and works with a read-only filesystem. pip and the CLI tools are removed from the runtime image.
- **Start command:** `ENTRYPOINT ["python", "-m", "app"]`. This runs uvicorn programmatically on `PORT`; Railway supplies 8080.
- **Bind address:** `0.0.0.0` by default. `SEMANTICA_BIND_HOST=::` switches to an explicit dual-stack socket.
- **Docker healthcheck:** probes `/health` every 30 s. Railway uses its own deploy-time `/health` check.
- **Build secrets:** there is no `--mount=type=secret`, because Railway's builder rejects it. Local builds inject a CA certificate through a throwaway Dockerfile copy in `scripts/verify_stack.sh`.

## 6. Required environment variables

Names only; the values live in Railway variables.

| Variable | Service | Kind | Notes |
|---|---|---|---|
| `SEMANTICA_API_KEY` | semantica-api | Secret, user-generated | At least 32 characters in production. Startup is refused otherwise. |
| `SEMANTICA_ENV` | semantica-api | Setting | `production`. Treated as `production` if unset. |
| `SEMANTICA_ALLOW_ANONYMOUS` | semantica-api | Setting | `false`. `true` is refused outside development and test. |
| `FALKORDB_HOST` | semantica-api | Copied from falkordb | `falkordb.railway.internal` |
| `FALKORDB_PORT` | semantica-api | Setting | `6379` |
| `FALKORDB_PASSWORD` | both | Secret, user-generated | Required in production. |
| `REDIS_ARGS` | falkordb | Setting | Enables AOF and `--requirepass`. |
| `PORT` | semantica-api | Supplied by Railway | 8080 |
| `FALKORDB_GRAPH`, `FALKORDB_TIMEOUT_SECONDS`, `SEMANTICA_BIND_HOST` | semantica-api | Optional | Not set. Defaults: `zeptly_semantica`, 5 s, `0.0.0.0`. |

## 7. Persistence design

- FalkorDB keeps the graph `zeptly_semantica` on the Railway volume at `/var/lib/falkordb/data`. It uses append-only file (AOF) persistence plus an RDB base snapshot.
- On restart, FalkorDB reloads from the AOF. The Railway log from 11:31:29Z shows: "DB loaded from append only file".
- The graph is a **derived, non-authoritative projection**. Canonical truth stays in Zeptly's PostgreSQL. A FalkorDB backup is an operational convenience, not a source of truth.
- **Recovery model:**
  1. Canonical Zeptly PostgreSQL.
  2. The Phase 6 projection contracts.
  3. `ExternalSemanticaClient`, in Phase 6.5.
  4. Replay/rebuild of the Semantica projection.

  None of this is implemented yet, as the brief intended.

## 8. Networking

- **FalkorDB:** reachable only on private networking at `falkordb.railway.internal:6379`. It has no public domain and no TCP proxy. Proven through the connector and through the API's successful `graph_connected` over the private host.
- **semantica-api:**
  - Railway public domain: `https://semantica-api-production.up.railway.app`. It was kept for Phase 6.25 validation and is protected by the API key.
  - Private endpoint for later private-only use: expected to be `semantica-api.railway.internal:8080`. This is not verified; confirm it in the dashboard.
- **Keeping the public domain:** Supabase Edge Functions cannot reach Railway's private network (6.5A §26 Q1). The public domain therefore has to stay while Zeptly calls the service from Supabase, protected by the API key. That is a decision for before shadow activation in Phase 6.5. If it is later removed, the service keeps working privately.
- **Bind fix:** the IPv6-only bind defect found at first deploy was fixed in `dd4b5df`. Railway's IPv4 health check now reaches the service.

## 9. Authentication

- Every `/v1/…` route requires exactly one `X-API-Key` header, compared by digest in constant time.
- The following all get 401: a missing, empty, invalid or duplicated header, and `Authorization: Bearer` instead of `X-API-Key`.
- `/health` and `/ready` are unauthenticated and disclose nothing.
- Authentication failures are logged with a reason and a running count. The key is never logged.
- Anonymous operation is disabled. The startup log shows `auth_required true, anonymous_allowed false`.

## 10. Workspace isolation strategy

- Every node and edge key is `{workspace_id}:{id}`, built from the URL path.
- Each Cypher query also filters on `workspace_id` for the anchor node, every edge and every neighbour.
- If the body's `workspace_id` differs from the path, the request is rejected with 422.
- An edge whose endpoint lies in another workspace gets the same 409 as an edge to a node that doesn't exist, so nothing about the other workspace is revealed.
- Clients supply no Cypher.

## 11. Health checking

- **`GET /health`:** process liveness. It has no dependencies and is used by the Railway deploy check.
- **`GET /ready`:** checks FalkorDB connectivity. It returns 200 `{"status":"ready"}` or 503 `{"status":"unavailable"}`.
- **Startup:** the service retries FalkorDB, because Railway has no `depends_on`. It reports `initial readiness` in its logs.

## 12. Observability

- The API writes JSON log lines. Railway parses their fields as attributes.
- Events: startup (version, environment, auth mode, FalkorDB host and port, whether a password is set), bind, `graph_connected`, readiness transitions, `auth_rejected` with a reason and total, operation failures, and a shutdown summary.
- Access logs record the method, path and status.
- No keys and no payloads are logged. The Phase 6.25B audit tested this, and the Railway logs reviewed here are consistent with it.

## 13. Security configuration

| Control | Status |
|---|---|
| FalkorDB has no public endpoint | Proven (connector: no domains, no TCP proxies) |
| No secrets in Git | Proven (6.25B audit; secrets exist only as Railway variables) |
| API key authentication enabled | Proven (startup log plus the 6 auth checks) |
| Anonymous projection disabled | Proven (startup log) |
| No browser-side credential | Proven (there is no frontend; the key is server-to-server only) |
| No unrestricted Cypher endpoint | Proven (route table: nodes, edges, relationships/query only) |
| No admin or debug endpoint | Proven (`/docs` and `/openapi.json` return 404 in production) |
| No production Zeptly connection | Proven (no Zeptly configuration points at this service) |
| Payload limited to metadata and a short label | Proven (see §14) |
| Workspace isolation tests passed | Proven (see §15) |

## 14. Payload policy

- Request models forbid unknown fields. A `content` field returned 422 on the deployed service.
- Labels are 1–160 characters, with no control, bidi or line-separator characters. A 161-character label returned 422 on the deployed service.
- Timestamps must include a timezone. There are at most 16 provenance references.
- Request bodies are limited to 64 KiB (413), and a body without a Content-Length gets 411.
- The service does not store full memory content, prompts, embeddings or credentials.

## 15. Validation results

Operator run from a laptop against `https://semantica-api-production.up.railway.app` on 2026-09-24, cross-checked against the Railway logs.

| Area | Evidence | Status |
|---|---|---|
| **Boot** | Deployment `7f279309` SUCCESS. Build OK. Startup log: semantica 0.6.0, production, authentication required, `graph_connected`, `ready=true`, bind `0.0.0.0:8080`. | **Proven** |
| **Health** | `GET /health` → 200 `{"status":"ok"}`; Railway health check 200. | **Proven** |
| **Readiness** | `GET /ready` → 200 `{"status":"ready"}`. | **Proven** |
| **Authentication** | No, invalid, empty and duplicate key, and Bearer instead of `X-API-Key`, all → 401. A valid key reaches the handler (404 for an absent probe node). | **Proven** (6/6) |
| **Node write/read** | A1, A2, B1, B2 PUT → 201. Idempotent re-PUT of A1 → 200. GET A1 → 200 with the correct label, type and workspace. | **Proven** |
| **Edge write/read** | A1→A2 and B1→B2 PUT → 201. The relationship query returns exactly the intended edge for A1 and for B1. | **Proven** |
| **Workspace isolation** | See the separate breakdown below. | **Proven** (12/12 query-stage checks) |
| **Persistence: API restart** | Fixtures created at 11:30:03Z. The API restarted at 11:31:02–07Z: same deployment, new process, `graph_connected`, `ready=true`. | **Proven** |
| **Persistence: FalkorDB restart** | FalkorDB restarted at 11:31:29Z with the volume retained: SIGTERM, AOF fsync, restart, "DB loaded from append only file", same deployment `6de7dc7a`. The access log shows no writes between 11:30:08 and 11:31:42. At 11:31:42–43 all six fixture DELETEs returned **200**; DELETE of an absent object returns 404, as `verify-clean` showed. So every fixture survived both restarts. | **Proven** |
| **Deletion** | Edge and node DELETE → 200 in both workspaces. Afterwards, GET, the relationship query and DELETE of the same objects → 404. | **Proven** |
| **Test-data cleanup** | `verify-clean`: 11/11 absent, in both rounds (11:31:45Z and 11:38:22Z). | **Proven** |

**Workspace isolation checks:**
- workspace A retrieving B1 → 404;
- workspace A relationship query anchored on B1 → 404;
- A's query leaks no B nodes or edges;
- the A1→B1 edge → 409, identical to the control edge to a nonexistent node;
- body workspace B under path A → 422;
- encoded path traversal → 404;
- malformed workspace ID → 422.

**Persistence method disclosure.** Survival after the restarts was shown by the cleanup DELETEs returning 200, not by a separate GET after each restart: the `check` stage the operator meant to run after the restarts was mistyped (`check.`) and rejected. The evidence is conclusive, because nothing wrote to the graph between the two restarts and the deletes. The second round (11:38Z) repeated `check` three times, but the Railway logs show no restarts during that round, so it counts only as repeat-read evidence.

**Not tested:**
- private-only operation (no traffic over `semantica-api.railway.internal` yet);
- behaviour under load;
- key rotation.

**Failed:** none.

## 16. Test-data cleanup

- Both runs used only `phase625-workspace-a` and `phase625-workspace-b`, with synthetic labels such as "Phase 6.25 fixture A1".
- All test edges and nodes were deleted. `verify-clean` confirmed that no Phase 6.25 fixture data remains, including the auth probe and cross-workspace edge IDs.
- No real Zeptly or customer data was ever written.

## 17. Current cost footprint

- **Observed usage: not captured.** The Railway usage and metrics view was not recorded during this phase, and the Railway connector was unavailable when this report was written.
- **Estimate (not observed):** two always-on small services, the API (one Python process) and FalkorDB, plus a 5 GB volume.
- Observed data size is tiny: the RDB base snapshot reported 2.43 MB of memory when created.
- Cost is dominated by the baseline RAM/CPU allocation of two services, not by data volume.
- Check the actual figure in Railway → project `devoted-magic` → Usage before Phase 6.5 shadow activation.

## 18. Upgrade strategy

- **Semantica:** pinned to `0.6.0`. Before any upgrade, re-verify the `FalkorDBStore.connect()` timeout workaround described in the 6.25A notes. Then regenerate `requirements.lock` with `scripts/lock.sh`, run the full test suite and `verify_stack.sh`, and deploy a new pinned commit.
- **FalkorDB:** change the digest-pinned image deliberately, after confirming AOF compatibility on a volume snapshot.
- **Base image:** bump the Python digest in the Dockerfile through the same test gate.

## 19. Rollback strategy

- **API:** redeploy a previous successful deployment in Railway, or re-pin the service source to a previous commit. The service is stateless.
- **FalkorDB image:** re-pin the previous digest. Do not delete or recreate the volume.
- **Data:** the graph is derived. If it is corrupted, the recovery path is a rebuild from canonical PostgreSQL (§7) once Phase 6.5 exists, not a restore that treats FalkorDB as the source of truth.

## 20. Known limitations

- **Single API key:** there is no overlap during rotation. A rotation needs coordinated changes in Railway and Supabase (6.25B R4).
- **No rate limiting or IP allowlisting:** the public domain relies on the API key alone (6.25B R4/R9).
- **Public dependency:** Supabase Edge Functions cannot use Railway private networking, so the public domain remains a dependency for Zeptly (6.5A Q1).
- **No batch or workspace-wide operations:** there is no batch endpoint and no workspace-wide delete. A rebuild is one call per node and per edge (6.5A Q4, Q5).
- **Relationship query scope:** one hop only, at most 200 results, 1–16 edge types, and no depth.
- **Persistence method:** see §15. Persistence was proven through DELETE-200 existence checks rather than a separate read after each restart.
- **Project name:** the Railway project is `devoted-magic` rather than `zeptly-semantica`.
- **Costs:** not measured (§17).

## 21. Required Phase 6.5 connection details

| Item | Value |
|---|---|
| Service endpoint (public, current) | `https://semantica-api-production.up.railway.app` |
| Private hostname (Railway-internal only) | Expected `semantica-api.railway.internal`. **Not verified**; confirm it in the service's Settings → Networking. |
| Port | 443 publicly (TLS at Railway's edge); 8080 on the private network |
| API style | JSON over HTTPS REST; `PUT` is a full replace |
| Authentication header | `X-API-Key` (exactly one) |
| Credential variable name | `SEMANTICA_API_KEY`. Zeptly must store its own copy as a server-side Supabase secret, never in the browser or Git. |
| API version | `/v1` |
| Supported semantic operations | `PUT/GET/DELETE /v1/workspaces/{ws}/nodes/{id}`; `PUT/DELETE /v1/workspaces/{ws}/edges/{id}`; `POST /v1/workspaces/{ws}/relationships/query` |
| Health endpoint | `GET /health` (no authentication) |
| Readiness endpoint | `GET /ready` (no authentication; 503 while FalkorDB is unavailable) |
| Workspace scoping rule | The workspace is always in the path, and must equal the body's `workspace_id` if one is present. Nothing crosses workspaces: cross-workspace edges get 409 and cross-workspace reads get 404. |

**Operational constraints:**
- IDs match `^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$`, which does not allow `:`.
- Edge types are upper-case.
- Labels are at most 160 characters.
- Requests are limited to 64 KiB and must carry a Content-Length.
- Queries return at most 200 results, one hop.
- An edge PUT gets 409 when an endpoint node is missing.
- A node DELETE detach-deletes the node's edges.
- There is no workspace wipe.

The full mapping is in `PHASE-6.5A-EXTERNAL-SEMANTICA-CONTRACT-RECONCILIATION.md`.

## 22. Verdict

Every mandatory item is proven on the deployed Railway service, and none failed:

- boot;
- persistence (API restart and FalkorDB restart);
- health and readiness;
- authentication;
- node write/read and edge write/read;
- deletion;
- workspace isolation;
- test-data cleanup.

The open limitations (§20) are operational follow-ups, not blockers.

READY FOR PHASE 6.5 — EXTERNAL SEMANTICA ADAPTER
