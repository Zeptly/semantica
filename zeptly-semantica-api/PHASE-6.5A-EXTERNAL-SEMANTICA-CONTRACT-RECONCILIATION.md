# Phase 6.5A: External Semantica Contract Reconciliation

**Programme:** Zeptly Agentic Cortex
**Phase:** 6.5A. This phase is analysis and specification only. It changes no behaviour.
**Date:** 2026-09-24
**Governing rule:** «PostgreSQL owns truth. Semantica interprets and relates it.»

**Repositories inspected:**
* `Zeptly/zeptly-MVP`, HEAD `b92590c` ("Completed semantic projection", 2026-08-20). This is the Zeptly application. It was cloned read-only and **not modified**.
* `Zeptly/semantica` at `zeptly-semantica-api/`, commit `9782931`. This is the Phase 6.25 external service. This document lives here.
* `Zeptly/Social-Channels-Adaptor` was checked. It contains no Semantica, Cortex or semantic-projection code, so it is out of scope.

Everything below is taken from the repositories, not from the phase brief. Where the brief and the repositories disagree, the repositories win, and each disagreement is called out.

---

## 1. Existing Phase 6 architecture discovered

Phase 6 is a **write-side projection seam** (`SemanticaClient`) plus one implementation, `PostgresSemanticProjectionClient`, backed by three Postgres tables. Its pieces:

* a durable-intent **outbox**;
* a full-rebuild **reconciler**;
* a **read module** (`querySemanticContext`) that reads the projection tables directly;
* two **production writers**, called from two edge functions.

Everything runs as **Supabase Edge Functions (Deno)** in Supabase project `ikynpepqqxbmipesxjqh` (`supabase/config.toml`).

```
canonical write (edge function: memory-approve / zmc-cases-promote)
      │  supabase-js, service role
      ▼
canonical row committed (memory_context_refs / zmc_case_execution_refs)
      ▼
projectWithDurableIntent(supabase, client, intent, apply)
      ├─ recordProjectionIntent → semantic_projection_outbox (status=pending)
      ├─ apply(client) → client.projectNode / projectEdge
      │        PostgresSemanticProjectionClient → semantic_nodes / semantic_edges
      └─ mark applied │ on error: pending, attempt_count+1, last_error, next_attempt_at=+60s

reconcileSemanticProjection(supabase, client, ws)   (on demand only; no scheduler)
      rebuilds nodes and edges from canonical tables, then marks every pending/failed intent "applied"

querySemanticContext(supabase, request)             (reads semantic_nodes / semantic_edges directly)
```

Findings that differ from the brief:

1. **`SemanticaClient` has no query method.** Semantic reads do not go through the seam. `querySemanticContext` takes a `supabase` client and reads the local tables directly.
2. **`querySemanticContext` has no production caller.** Only `src/test/cortex-semantic-projection.test.ts` calls it. Semantic recall is not yet on any runtime path.
3. **No production code path tombstones a node.** `tombstoneNode` is called only from tests. Memory "deprecate" (`memory-approve`, `case "deprecate"`) sets `agent_memories.status = 'deprecated'` and projects nothing. The Phase 6 report's §28 claim ("Forgetting a memory tombstones its projected node") holds only when the client is called directly.
4. **Nothing retries automatically.** Nothing reads `next_attempt_at`. The only recovery path is `reconcileSemanticProjection`, which runs on demand.
5. **The outbox write is awaited but not checked.** `recordProjectionIntent` ignores the `{ error }` that supabase-js returns. If the insert fails, no intent is recorded, and nothing reports it.

## 2. Exact repository paths and symbols (`Zeptly/zeptly-MVP`)

| Concern | Path | Symbols |
|---|---|---|
| Contract (source of truth) | `packages/contracts/src/semantic/semantic_projection_v1.ts` | `SEMANTIC_NODE_TYPES`, `SemanticNodeType`, `SemanticNodeReference`, `PLATFORM_WORKSPACE_SCOPE`, `semanticNodeKey`, `parseSemanticNodeKey`, `SEMANTIC_LABEL_MAX_LENGTH`, `truncateSemanticLabel`, `SEMANTIC_RELATION_TYPES`, `SEMANTIC_EDGE_AUTHORITIES`, `SEMANTIC_EDGE_DERIVATIONS`, `SemanticProvenance`, `SemanticNodeRecord`, `SemanticEdgeRecord`, `SEMANTIC_OUTBOX_OPERATIONS`, `SEMANTIC_OUTBOX_STATUSES`, `SemanticOutboxIntent`, `SemanticQueryRequest`, `SemanticQueryEdge`, `SemanticQueryResult` |
| Contract (generated Deno mirror) | `supabase/functions/_shared/contracts/semantic_projection_v1.generated.ts` | the same symbols, guarded by `src/test/shared-contracts-sync.test.ts` |
| Client seam and local substrate | `supabase/functions/_shared/cortex/semantic-projection.ts` | `ProjectNodeInput`, `ProjectEdgeInput`, **`SemanticaClient`**, **`PostgresSemanticProjectionClient`**, `recordProjectionIntent`, `markIntent` (private), **`projectWithDurableIntent`**, `provenanceOf`, **`reconcileSemanticProjection`**, `ReconcileResult` |
| Projection rules | `supabase/functions/_shared/cortex/semantic-projection-map.ts` | `ProjectionRule`, `PROJECTION_MAP`, `CONTEXT_TARGET_RESOLUTION`, `rulesForRelation` |
| Query boundary | `supabase/functions/_shared/cortex/semantic-query.ts` | **`querySemanticContext`**, `SemanticQueryOutcome` |
| Production writers | `supabase/functions/_shared/cortex/memory-relationship-writers.ts` | `writeMemoryEvidenceRefs`, `writeCaseExecutionRef` (both take an optional `client?: SemanticaClient`, defaulting to `new PostgresSemanticProjectionClient(supabase)`) |
| Writer call sites | `supabase/functions/memory-approve/index.ts` (approval, `case "create"`, ~l.101) · `supabase/functions/zmc-cases-promote/index.ts` (l.259, l.492) | `writeMemoryEvidenceRefs(...).catch(console.warn)` · `writeCaseExecutionRef(...)` |
| Schema | `supabase/migrations/20260820211745_e664244e-cb79-4b77-81c1-bd8a26767a5c.sql` | `semantic_nodes`, `semantic_edges`, `semantic_projection_outbox`, with RLS, GRANTs and comments |
| Tests | `src/test/cortex-semantic-projection.test.ts` (23 tests), `src/test/shared-contracts-sync.test.ts` (8 tests) | in-memory Supabase stand-in `makeClient`, `seedDb` |
| Feature flags | `supabase/functions/_shared/feature-flags.ts` | `isFeatureEnabled(supabase, workspaceId, flagName)` backed by `feature_flags`, per workspace, with rollout percentage and a 60 s cache. Unknown flag means `false`. |
| Dependency health pattern | `supabase/functions/service-health/index.ts` | `ServiceStatus { status: 'connected'|'disconnected'|'error'|'not_configured', latency_ms, error? }`, admin-only |
| Outbound HTTP adapter pattern | `supabase/functions/_shared/cortex/adapters/service.ts` | `readEnv` (a Deno/Node-safe env reader), `fetchImpl` injection |
| Configuration convention | edge functions | `Deno.env.get("<VENDOR>_API_KEY")` and `Deno.env.get("<VENDOR>_BASE_URL")` (e.g. `VEXA_BASE_URL` with `VEXA_API_KEY`). `VITE_*` variables are browser-bundled. |
| Governance docs | `docs/agentic-cortex/PHASE-6-COMPLETION-REPORT.md`, `IMPLEMENTATION-STATE.md` (§Phase 6), `DECISIONS.md` (AC-004: Semantica non-authoritative; AC-002: TimeSaver sole orchestrator; AC-012: progressive complexity) | none |

**Baseline test run (this phase).** The lockfile is out of sync, so `npm ci` fails with `EUSAGE`. I did not change the repo. Instead I used a scratch `vitest@3.2.7` in a node environment:
* `src/test/cortex-*.test.ts`: **96/96 pass** across 5 files.
* `src/test/shared-contracts-sync.test.ts`: **8/8 pass**.

## 3. Existing `SemanticaClient` contract

```ts
export interface SemanticaClient {
  readonly backend: string;
  projectNode(input: ProjectNodeInput): Promise<void>;
  projectEdge(input: ProjectEdgeInput): Promise<void>;
  tombstoneNode(ref: SemanticNodeReference): Promise<void>;
  wipeWorkspace(workspaceId: string): Promise<void>;
}
```

| Method | Parameters | Returns | Workspace semantics | Error semantics | Idempotency | Authority | Production callers | Test coverage |
|---|---|---|---|---|---|---|---|---|
| `backend` | none | `string` | none | none | not applicable | informational | none | indirect |
| `projectNode` | `ProjectNodeInput { ref, label?, label_source?, status?, currentness?, valid_from?, valid_to?, provenance }` | `void` | `ref.workspace_id` is part of identity | throws `Error("semantic node projection failed: …")` | **full replace** upsert on `(workspace_id,node_type,canonical_id)`. Clears `tombstoned_at`. Sets omitted optionals to `null`. | projection only | inside the `apply` closures of `writeMemoryEvidenceRefs` and `writeCaseExecutionRef`; `reconcileSemanticProjection` | substrate, idempotency, repair and loss-recovery tests |
| `projectEdge` | `ProjectEdgeInput { workspace_id, src, dst, relation, derivation, valid_from?, valid_to?, provenance }` | `void` | `workspace_id` plus both endpoint keys | throws `Error("semantic edge projection failed: …")` | upsert on `(workspace_id,src_node_key,relation,dst_node_key)`. `authority` is forced to `authoritative_explicit`. | projection only | same as `projectNode` | same |
| `tombstoneNode` | `SemanticNodeReference` | `void` | by node key | **swallows** supabase errors | re-tombstone is a no-op | projection only | **none** (tests only) | "tombstones a memory node…" |
| `wipeWorkspace` | `workspaceId` | `void` | deletes the workspace's edges and nodes | swallows errors | yes | projection only | **none** (tests only) | "recovers from total projection loss" |

**The existing abstraction is sufficient for the external write path.** It is **not** sufficient for the external read path, because no query method exists (§10, §24 C3). `SemanticaClient` itself does **not** need to change.

## 4. Local implementation behaviour

* **Identity:** `node_key = workspace_id|node_type|canonical_id`, a generated column that is unique. Edge identity is the tuple `(workspace_id, src_node_key, relation, dst_node_key)`. **There is no edge id.**
* **Labels:** `truncateSemanticLabel` trims the label, returns `null` if it is empty, and truncates it to 160 characters with a trailing `…`. It does **not** remove newlines or control characters. `label_source` falls back to `provenance.source_table`, and `label_generated_at` is set to now.
* **Full-replace upsert:** a later `projectNode` for the same node with no label **clears** a label set earlier. This matters because both writers project their endpoint nodes label-less (`memory-relationship-writers.ts`), so a writer run after a reconcile clears labels and statuses. This is a **latent Phase 6 behaviour**. It is preserved as-is (§25) and reported in §26.
* **Dangling edges are allowed:** there is no foreign key from `semantic_edges` to `semantic_nodes`.
* **Tombstone:** deletes all edges touching the node, then sets `tombstoned_at`. The node row stays. A later `projectNode` resurrects it (`tombstoned_at: null`).
* **Reconcile:**
  * It projects `agent_memories`, then `runtime_runs` along with FK endpoint nodes and FK edges, then `memory_links`, `memory_context_refs` (with endpoint nodes), `runtime_run_memory_refs` and `zmc_case_execution_refs` (with case nodes).
  * It then marks **every** pending or failed intent in the workspace as `applied`.
  * It **never deletes stale projected rows**. The loss-recovery proof calls `wipeWorkspace` first.
  * It processes each workspace sequentially and has no checkpoint.

## 5. Projection and write flow

The flow is exactly the order in §1. Both writers:

1. insert the canonical row, treating `23505` duplicates as success;
2. call `projectWithDurableIntent` with a deterministic key:
   * `memory_context_ref:{ws}:{memory}:{relation}:{target}`
   * `case_execution_ref:{ws}:{case}:based_on:{run}`

   and `operation_type: "project_edge"`;
3. in `apply`, run `projectNode(src)`, then `projectNode(dst)`, then `projectEdge`.

`memory-approve` also wraps the writer in `.catch(console.warn)`. **A projection failure never reaches the canonical caller.** This satisfies the brief's required shape (canonical mutation, then durable intent, then attempt, then success or retry). **No code path makes the canonical write depend on projection.** Phase 6.5 must keep it that way (§25).

## 6. Query flow

`querySemanticContext(supabase, { workspaceId, node, relations?, direction?, limit? })`:

1. The request must have `workspaceId`, `node.canonical_id` and `node.node_type`; otherwise it returns `invalid_reference`.
2. If `node.workspace_id !== workspaceId`, it returns **`workspace_mismatch`**.
3. It reads the anchor node from `semantic_nodes`. If the node is missing or tombstoned, it returns **`not_found`**.
4. For `outbound` and/or `inbound`, it runs one edge query per direction with `.limit(limit)`. The limit defaults to 200 and is clamped to 1–1000, **applied per direction**.
5. It filters out tombstoned edges, filters by relation when a relation list is given, drops targets outside the workspace, and deduplicates.
6. It fetches target labels in a second query.
7. It returns `{ ok: true, result: { node, node_label, edges[], explicit_only: true } }`.

**Supabase errors are ignored.** A database failure on the anchor read returns `data: null`, which surfaces as `not_found`. **Today, an infrastructure failure is indistinguishable from absence** (see §8 and §14).

## 7. Durable-intent behaviour

* **Table:** `semantic_projection_outbox`:
  * `operation_key` UNIQUE;
  * `operation_type` ∈ `project_node | project_edge | tombstone | reconcile`;
  * `status` ∈ `pending | applied | failed | abandoned`;
  * `attempt_count`, `last_error`, `next_attempt_at`, `completed_at`;
  * a partial index on pending and failed rows.
* **Recording:** `recordProjectionIntent` upserts with `ignoreDuplicates: true`, so repeated transitions collapse onto one row. Its error is **not checked** (finding 5).
* **On success:** status becomes `applied` and `completed_at` is set.
* **On failure:**
  * status becomes `pending`;
  * `attempt_count` increments, read back first;
  * `last_error` is truncated to 500 characters;
  * `next_attempt_at` becomes now + 60 s, with no backoff growth and no maximum.
* **Unused states:** `failed` and `abandoned` are never set.
* **Only consumer:** `reconcileSemanticProjection` marks pending and failed intents `applied` after a full rebuild. Intents are **not replayed individually**: `canonical_ref` is informational and has no replay handler.
* **Retention:** the pruning described in the Phase 6 report (§15) is **not implemented** (`IMPLEMENTATION-STATE.md` technical debt).

## 8. Existing failure semantics

| Failure | Current behaviour |
|---|---|
| Canonical write fails | The writer returns `…_write_failed` in `reasons`, and there is no projection attempt. |
| Outbox insert fails | **Silent.** The projection is still attempted, and no intent row exists. |
| Projection throws | The intent stays `pending` and the error is recorded. The writer returns normally, and the canonical row persists. |
| Reconcile throws midway | It stops, and **pending intents are not drained**. That is correct: it drains only after a full pass. |
| Query DB error | It returns `not_found`. Infrastructure failure is confused with absence. |
| Wrong-workspace node | It returns `workspace_mismatch` before any read. |

## 9. External API contract (Phase 6.25, verified from `zeptly-semantica-api/app/`)

Source files: `app/routes/projection.py`, `app/routes/health.py`, `app/models.py`, `app/auth.py`, `app/graph.py`. Deployed on Railway as commit `dd4b5df`, at `https://semantica-api-production.up.railway.app` (a temporary public domain).

| Route | Auth | Request | Responses |
|---|---|---|---|
| `GET /health` | none | none | 200 `{"status":"ok"}` |
| `GET /ready` | none | none | 200 `{"status":"ready"}` / 503 `{"status":"unavailable"}` |
| `PUT /v1/workspaces/{ws}/nodes/{id}` | `X-API-Key` | `NodeUpsert` | 201 `{created:true,node}` / 200 `{created:false,node}` / 401 / 411 / 413 / 422 / 503 |
| `GET /v1/workspaces/{ws}/nodes/{id}` | key | none | 200 `NodeOut` / 404 / 422 / 503 |
| `DELETE /v1/workspaces/{ws}/nodes/{id}` | key | none | 200 `{deleted,detached_edges}` / 404 / 503 |
| `PUT /v1/workspaces/{ws}/edges/{id}` | key | `EdgeUpsert` | 201/200 `{created,edge}` / **409 when an endpoint is missing in the workspace** / 422 / 503 |
| `DELETE /v1/workspaces/{ws}/edges/{id}` | key | none | 200 `{deleted}` / 404 / 503 |
| `POST /v1/workspaces/{ws}/relationships/query` | key | `RelationshipQuery` | 200 `{workspace_id,node,relationships[{direction,edge,neighbor}],truncated}` / 404 when the anchor is missing / 422 / 503 |

**Constraints:**

* **Identifiers** (`ws`, node and edge ids) must match `^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$`. **`:` is not allowed.**
* **Node types** must match `^[A-Za-z][A-Za-z0-9_.-]{0,63}$`.
* **`edge_type`** must match `^[A-Z][A-Z0-9_]{0,63}$`.
* **`lifecycle_state`** must match `^[a-z][a-z0-9_]{0,31}$`.
* **`is_current`** is a boolean, defaulting to true.
* **Timestamps** (`valid_from`, `valid_to`, `source_updated_at`) must be ISO strings with a timezone, and `valid_to ≥ valid_from`.
* **`source_version`** must be an integer ≥ 0.
* **`provenance_refs`**: at most 16, unique, each matching `^[A-Za-z0-9][A-Za-z0-9._:/#@=-]{0,255}$`.
* **`label`**: 1–160 characters, with **no Cc control characters, bidi controls, or U+2028/2029**.
* **Unknown fields** return 422.
* **Request bodies** over 64 KiB are rejected with 413; a body with no Content-Length gets 411.
* **Relationship queries:** `direction` ∈ `outgoing|incoming|both`, `edge_types` has 1–16 entries, `include_non_current` defaults to **false**, `limit` is 1–200, and there is **no depth**.
* **Authentication:** exactly one `X-API-Key` header. A missing, wrong or duplicated key gets 401.
* **Semantics:** `PUT` is a full replace. Node `DELETE` detach-deletes the node's edges. Moving an edge to new endpoints under the same `edge_id` relocates it.

## 10. Operation mapping

| Phase 6 operation | External operation | Classification |
|---|---|---|
| `SemanticaClient.projectNode` | `PUT …/nodes/{encodeNodeId(ref)}` | **Mapping requiring translation** (identity §11, payload §12). |
| `SemanticaClient.projectEdge` | `PUT …/edges/{edgeIdFor(input)}` | **Mapping requiring translation.** Needs a deterministic edge id (§11). A 409 for a missing endpoint needs placeholder-endpoint handling, because Phase 6 allows dangling edges (§14). |
| `SemanticaClient.tombstoneNode` | `DELETE …/nodes/{id}` | **Translation.** A 404 counts as success. The observable result matches: the node isn't found and its edges are gone. |
| `SemanticaClient.wipeWorkspace` | none | **Unsupported externally.** There is no workspace-wide delete. Production doesn't call it (§3). See §22 for rebuild. |
| `querySemanticContext` (not on the seam) | `POST …/relationships/query` | **Translation, plus a new seam** (§24 C3). |
| `reconcileSemanticProjection` | a sequence of `PUT`s | Unchanged. It already goes through `SemanticaClient`. |
| none | `GET /v1/…/nodes/{id}` | **External capability not required by Phase 6.** It's used only by the parity harness. |
| none | `DELETE /v1/…/edges/{id}` | **External capability not required by Phase 6.** Phase 6 has no edge-level delete. |
| none | `GET /health`, `GET /ready` | Used only for dependency health (§17). |

Phase 6 contract surface to expose as a result of this mapping: **none**.

## 11. Identity mapping

Zeptly's canonical identity `(workspace_id, node_type, canonical_id)` stays the only authority. The external ids are a **reversible encoding** of it, and the external service never mints ids.

| Concept | Phase 6 | External | Translation |
|---|---|---|---|
| Workspace | `workspace_id` (uuid; platform scope `00000000-…`) | path `{workspace_id}` plus body `workspace_id` | Used verbatim. UUIDs and the platform scope match the id pattern. Never omitted. |
| Node identity | `(workspace_id, node_type, canonical_id)` | path `{node_id}` = body `canonical_id` | `encodeNodeId(ref) = ref.node_type + "." + ref.canonical_id`, e.g. `memory.aaaaaaaa-…`, at most 62 characters. `.` is legal in ids and cannot occur in `node_type`. Decoding splits on the first `.` and must agree with the returned `node_type`. **The external `canonical_id` field therefore holds the encoded id, not the bare uuid.** Without `node_type` in the id, `memory X` and `case X` would collide, which Phase 6's key forbids. |
| Node type | `SemanticNodeType` (7 lowercase values) | `node_type` | Verbatim. All values match the pattern. |
| Edge identity | tuple `(workspace, src_key, relation, dst_key)`, no id | path `{edge_id}` | `edgeIdFor = "e" + hex(sha256(src_key + "\|" + relation + "\|" + dst_key))`, which is 65 characters. It is deterministic, stable across rebuilds and collision-resistant. Because the endpoints are part of the hash, the external "move edge" behaviour can never trigger. |
| Relation | `SemanticRelationType` (18 lowercase values) | `edge_type` | `relation.toUpperCase()` (e.g. `derived_from` becomes `DERIVED_FROM`). Decoding lowercases and must pass `isSemanticRelationType`; otherwise the edge is rejected as `unexpected_response`. |
| Source/target | `SemanticNodeReference` | `source_node_id` / `target_node_id` | `encodeNodeId` of each. |
| Lifecycle | `status` (free text; actually `proposed/active/deprecated` for memories and the `runtime_run_status` enum for runs) | `lifecycle_state` | Verbatim when it matches the pattern (all known values do). If not, the request is classified as `malformed` and **nothing is altered silently**. |
| Currentness | `currentness` (`null` \| `"superseded"`) | `is_current` (boolean) | `is_current = currentness == null`. The exact string is also carried as the provenance ref `currentness=<value>`, so it round-trips losslessly if new values appear. |
| Temporal | `valid_from`, `valid_to` | same fields | Normalised to UTC `…Z` strings. |
| Provenance | `SemanticProvenance { source_table, source_row_id, workspace_id, origin_type, derivation, observed_at, valid_from, valid_to }` | `provenance_refs[]` + `source_updated_at` | See §12. |
| Authority / derivation (edges) | `authority` (always `authoritative_explicit`), `derivation` | none | Carried as the refs `authority=…` and `derivation=…`. |

## 12. Payload mapping

**Node body:**
* `workspace_id`, `canonical_id` (encoded) and `node_type`;
* `label`: sanitised, see below;
* `lifecycle_state` ← `status`, and `is_current` ← `currentness`;
* `valid_from` and `valid_to`;
* `source_updated_at` ← `provenance.observed_at`;
* `provenance_refs`, see below.

**Edge body:**
* `workspace_id`, `edge_id`, `edge_type`, `source_node_id` and `target_node_id`;
* `valid_from` and `valid_to`;
* `source_updated_at` ← `provenance.observed_at`;
* `provenance_refs`.

**Provenance refs encoding** (`key=value`, all characters legal):
* `source_table=<t>`
* `source_row_id=<uuid>` (omitted if null)
* `origin_type=<asserted|observed|derived>` (omitted if null)
* `derivation=<stored_edge|deterministic_fk>`
* `authority=authoritative_explicit` (edges only)
* `prov_valid_from=<Z-ts>` and `prov_valid_to=<Z-ts>` (only when set)
* `currentness=<v>` (only when non-null)
* `label_source=<t.c>` (only when a label is present)

That is at most 9 refs, under the limit of 16. `provenance.workspace_id` is not encoded; it is always the path workspace, and decoding verifies it. `label_generated_at` is **dropped**: it is a timestamp, not a fact, and parity excludes it.

**Label sanitisation** (`sanitizeExternalLabel`):
1. Replace Cc control, bidi and U+2028/2029 characters with a space.
2. Collapse runs of spaces and trim.
3. Apply `truncateSemanticLabel`.
4. If the result is empty, use `null`.

This is **required**. `agent_memories.title` is free text and may contain newlines, and the external API rejects those with a non-retryable 422, which would permanently fail every projection involving that memory.

**Restriction proof.** The external adapter only ever builds bodies from the typed fields above. `ProjectNodeInput` and `ProjectEdgeInput` contain no content, prompt, model I/O, credential, document or embedding field, and neither do the canonical columns that reconcile reads:
* `title`, `status`, `origin_type` and the validity and superseded columns (memories);
* status, outcome, FK ids and times (runs);
* link and ref ids and relations.

The remaining gatekeeper is the external service's `extra="forbid"`: an unknown key returns 422 and is never stored.

**Phase 6 fields that would violate the restriction: none.** The only free-text field is `label`, which is ≤160 characters by contract, and sanitisation keeps it compliant. **Nothing semantically necessary is dropped.** `label_generated_at` is the only field not carried, and it is not a canonical fact.

## 13. Workspace isolation mapping

* **Where `workspace_id` comes from:**
  * writers: `input.workspaceId`:
    * `memory-approve` passes the authenticated context's `workspaceId` (`validateAuth`, admin role check);
    * `zmc-cases-promote` passes `body.workspace_id`, which is enforced by `assertWorkspaceMatchFromRequest(__auth.context, req)` plus an explicit `workspace_members` membership check before any write;
  * reconcile: its `workspaceId` argument;
  * query: `request.workspaceId`.
* **The adapter must:**
  * put `workspace_id` into **every** URL path and body, and never issue an unscoped request (the external API has no unscoped route);
  * refuse, **before sending**, any node or edge whose endpoint `ref.workspace_id` differs from the operation's workspace (a `workspace_violation` error);
  * for queries, keep Phase 6's pre-check `node.workspace_id !== workspaceId → workspace_mismatch` before any network call;
  * validate every returned node and edge: its `workspace_id` must equal the request workspace. On mismatch, it discards the whole response as `unexpected_response`, **never infers a workspace from data**, and logs a security event;
  * never query more than one workspace per call.
* **RLS and authorization are unchanged.** The API key is service-to-service only. Which workspace a user may act in is still decided by Zeptly's existing auth (`validateAuth`, `is_workspace_member`) before any writer or query runs.

## 14. Error mapping

This introduces a typed error `SemanticaBackendError { kind, retryable, status?, message }`. Messages never include headers, the key or request bodies.

| External signal | `kind` | Retryable | Write handling (outbox) | Query handling |
|---|---|---|---|---|
| DNS, connect or TLS failure; 502/503/504; `/ready` returns 503 | `unavailable` | yes | stays `pending` with backoff | `blocked: "backend_unavailable"` |
| client timeout (default 5 s) | `timeout` | yes | `pending` with backoff | `backend_unavailable` |
| 401/403 | `auth_config` | **no** (until configuration changes) | marked `failed`; the error is alerted | `backend_unavailable` (logged as `auth_config`) |
| 400/411/413/422 | `malformed` | **no** | marked `failed` (a poison intent) | `invalid_reference` |
| adapter pre-check that the workspace differs | `workspace_violation` | **no** | marked `failed`; security log | `workspace_mismatch` |
| 409 on edge PUT (endpoint missing) | `conflict` | handled | create placeholder endpoint nodes (`PUT` with no label), then retry the edge **once** | not applicable |
| 404 on node or edge DELETE | none (success) | not applicable | `applied` | not applicable |
| 404 on query | none | not applicable | not applicable | `blocked: "not_found"` (matches Phase 6) |
| other 5xx, or non-JSON / schema-invalid 2xx | `unexpected_response` | yes, but bounded | `pending` with backoff, then `failed` at the attempt limit | `backend_unavailable` |

**Retry policy (external intents only):**
* Exponential backoff: 60 s × 2^(n−1), capped at 1 h.
* `SEMANTICA_MAX_ATTEMPTS = 8`, after which the intent is marked `failed`.
* A reconcile drains `failed` intents, so there is no infinite retry.

**Local intents keep today's behaviour exactly** (§25).

## 15. Configuration requirements

These follow the existing `<VENDOR>_BASE_URL` / `<VENDOR>_API_KEY` convention (e.g. `VEXA_*`). All are **Supabase Edge Function secrets**. **No `VITE_` prefix is allowed** (that would bundle the value to the browser).

| Name | Kind | Meaning |
|---|---|---|
| `SEMANTICA_BASE_URL` | config | `https://semantica-api-production.up.railway.app` (see §16). If unset, the external backend is disabled everywhere. |
| `SEMANTICA_API_KEY` | **secret** | The shared service key (≥32 characters, 64 generated). It is sent only in `X-API-Key`. |
| `SEMANTICA_TIMEOUT_MS` | config (optional) | Default 5000. |

**Mode selection** reuses the existing `feature_flags` table through `isFeatureEnabled(supabase, workspaceId, flag)`, so it is per workspace and supports rollout percentages:
* `cortex_semantica_shadow`: send projection to the external service as well.
* `cortex_semantica_query`: read semantic context from the external service.

If a flag is unknown, it is off. **If `SEMANTICA_BASE_URL` or `SEMANTICA_API_KEY` is missing, the effective mode is `local` whatever the flags say** (fail safe). I rejected a global `SEMANTIC_BACKEND=local|external` environment variable because the repository already has a per-workspace flag mechanism, which allows staged rollout.

## 16. Networking requirements

* **Where the caller runs:** `ExternalSemanticaClient` runs in **Supabase Edge Functions** (`memory-approve`, `zmc-cases-promote`, and any future reconcile or query function). They run on Supabase's infrastructure, not on Railway.
* **Consequence:** Railway private networking (`semantica-api.railway.internal`) is **not reachable** from the caller. The only workable endpoint is the **public HTTPS domain** with `X-API-Key`. Phase 6.25 treated the public domain as temporary. For Phase 6.5 it is **required** unless a Railway-hosted relay or worker is introduced, and that would be new infrastructure, out of scope here.
* **Endpoint form:** `https://<railway-public-domain>` over TLS, which Railway terminates. Paths are the `/v1/workspaces/{ws}/…` routes from §9.
* **Never connect to FalkorDB.** It has no public exposure, and Zeptly holds no FalkorDB credential.
* **The Railway change is deferred:** the domain stays in place. Retiring or replacing it is an explicit decision (§26 Q1).

## 17. Health and readiness integration

* `ExternalSemanticaClient.readiness(): Promise<{ status: 'connected'|'disconnected'|'error'|'not_configured', latency_ms, detail }>`. It calls `GET {base}/ready` without a key and with a 2 s timeout. **It performs no semantic query and writes nothing.** The shape matches `service-health`'s `ServiceStatus`.
  * 200 `{"status":"ready"}` → `connected`.
  * 503 → `disconnected` (FalkorDB unreachable).
  * Network error → `error`.
  * Missing configuration → `not_configured`.
* **Wiring:** add a `semantica` entry to `supabase/functions/service-health/index.ts`, which is admin-only (§24 C8).
* **Readiness and provider selection:** readiness does **not** switch backends automatically. It is reported, and it feeds the query degradation described in §21. It is not called per request.

## 18. Local/external parity test plan

The harness drives both backends through the **same contract** and never compares storage internals.

* **Backends:**
  * A: `PostgresSemanticProjectionClient` plus `querySemanticContext`, over the existing in-memory Supabase stand-in (`makeClient`, `seedDb`).
  * B: `ExternalSemanticaClient` plus the external query backend, pointed at either:
    1. a **deterministic in-process fake** of the Phase 6.25 HTTP contract, for CI; or
    2. the real service (the local `docker compose` stack from Phase 6.25, via `SEMANTICA_PARITY_BASE_URL`), for manual or CI-optional runs.
* **Fixtures:** `seedDb()` extended with a second workspace, a `memory_links` pair, a label containing a newline and a bidi character, a superseded memory, and a dangling `runtime_run_memory_refs` edge.
* **Comparison:** the normalised `SemanticQueryOutcome`. Edges are sorted by `(direction, relation, target key)`. `label_generated_at` and `projected_at` are excluded. Labels are compared after sanitisation.

| Case | Expected parity |
|---|---|
| node upsert | the query anchor has the same label, status and currentness |
| idempotent upsert ×2 | identical outcome; external returns `created:false` |
| node lookup | same `node_label` |
| edge upsert | same edge set |
| relationship query, all directions, relation filter | same set, with directions mapped `outgoing`→`outbound` and `incoming`→`inbound` |
| deletion (`tombstoneNode`) | both return `not_found`, and the neighbours lose the edge |
| missing node | both return `not_found` |
| missing relationship | both return `ok` with 0 edges |
| workspace isolation | both return `workspace_mismatch` or `not_found` identically, and a same-id node in the other workspace never appears |
| temporal and currentness | `valid_*` and `currentness` round-trip |
| provenance | the decoded `SemanticProvenance` is equal |
| lifecycle | `status` round-trips |
| error classification | external only: a fake that returns 401/422/503/timeout yields the `kind` in §14; the local side is unaffected |
| **known, accepted differences** | external `limit ≤ 200` merged across directions versus local `limit` per direction; local dangling edges versus external placeholder endpoint nodes (placeholders have `label=null`, so the query is equal) |

## 19. Shadow-mode design

* **Trigger:** `cortex_semantica_shadow` is on **and** the configuration is present.
* **Writers:** they run exactly as today against the local client, with the same intent key and outbox row. **After** that call returns, they call `projectWithDurableIntent` a second time with `ExternalSemanticaClient` and the key **`ext:` + original key**. This gives a separate intent, a separate status and no coupling.
* **Isolation:** the external attempt runs after the local one and its result is ignored by the caller. **External results never influence runtime behaviour**, and queries stay local.
* **Latency:** in shadow mode the external call adds bounded latency (≤ `SEMANTICA_TIMEOUT_MS`) to the edge function after the canonical write has already committed. If that is unacceptable, the external attempt can be record-only (intent without an attempt) and reconcile can drain it. That is decision §26 Q3.
* **Evidence:** outbox rows under `ext:` and their `status` / `last_error`, plus a parity job that runs `reconcile` against the external backend and then compares queries (§18) on sampled nodes.

## 20. External activation design

* **Trigger:** `cortex_semantica_query` is on (it implies shadow writes) **and** the configuration is present **and** 6.5B's activation gates (§27) pass.
* **Writes:** they go to **both** backends, as in shadow mode. The local substrate stays current and is **not replaced**.
* **Reads:** `resolveSemanticQueryBackend(supabase, ws)` returns the external backend. `querySemanticContext`'s signature and local behaviour are untouched, and the local backend just delegates to it.
* **Canonical truth:** unchanged. Reconcile always reads PostgreSQL.

## 21. Fallback design

* **Phase 6 today:** there is no fallback concept, and query errors are confused with `not_found` (§6).
* **Proposal:**
  1. The external query backend returns an explicit `blocked: "backend_unavailable"`. It **never returns an empty result for an infrastructure failure**.
  2. Query fallback is **opt-in per call**: `{ fallback: "local" | "none" }`, with `"none"` as the default. When the fallback is used, the outcome carries `backend: "postgres_local_projection"` and `fallback_from: "external"`, so it is observable.
  3. **Writes never fall back:** an external write failure stays a pending `ext:` intent for reconciliation. The local write happens anyway, but independently, not as a fallback.
  4. Readiness does not auto-switch providers.
* Fallback reads only projection tables and never writes, so it **cannot create canonical events or mutate authoritative state**.
* Because `querySemanticContext` has no production caller yet, **no runtime consumer is affected** by choosing this default.

## 22. Reconciliation and rebuild design

* **Canonical sources:** `agent_memories`, `runtime_runs` (along with the `capability_id`, `capability_implementation_id` and `parent_run_id` FKs), `memory_links`, `memory_context_refs` (resolved through `CONTEXT_TARGET_RESOLUTION`), `runtime_run_memory_refs` and `zmc_case_execution_refs`. These are exactly what `reconcileSemanticProjection` already reads.
* **Existing intent records:** `semantic_projection_outbox`. **No new ledger is needed.**
* **Ordering:** nodes before the edges that use them. Reconcile already does this; `ExternalSemanticaClient`'s placeholder handling for 409 covers any dangling edge.
* **Idempotency:** external `PUT`s are idempotent by construction, and edge ids are deterministic. Re-running is safe.
* **Draining:** reconcile must drain only the intents of **its own backend namespace** (local: keys without `ext:`; external: `ext:` keys). Otherwise an external reconcile would mark local intents applied, and vice versa (§24 C5).
* **Deletion and tombstones:**
  * Reconcile never removes stale external nodes: rows whose canonical source was deleted, or tombstones missed while the service was down.
  * `wipeWorkspace` is unsupported externally.
  * **Proposal:**
    1. `ExternalSemanticaClient.wipeWorkspace` throws `unsupported` explicitly.
    2. A full external rebuild relies on idempotent re-projection.
    3. Stale-row removal is an explicit follow-up needing either a Phase 6.25 workspace-purge endpoint or a list endpoint for diffing (§26 Q4).

    Until then, a stale external node is harmless for truth: it is non-authoritative and scoped to its workspace. It can, however, make external and local queries disagree, and parity evidence will surface that.
* **Checkpoint and resume:**
  * The Phase 6 reconcile is one pass with no checkpoint. Against HTTP, it makes one request per node and per edge.
  * **Proposal:** the external reconcile runs **per workspace, per source table**, with a cursor (`after id`) stored in a `reconcile` outbox row's `canonical_ref`. `operation_type: "reconcile"` already exists, so there is no schema change. It is resumable after an edge-function timeout.
  * Workspaces are batched one at a time. Rate is bounded by `SEMANTICA_TIMEOUT_MS` and sequential requests (the API has no batch endpoint, §26 Q5).

## 23. Security requirements

* **Secrets:**
  * `SEMANTICA_API_KEY` is a Supabase function secret only, with no `VITE_` prefix, and is never in client bundles.
  * It is sent **only** as `X-API-Key`, never in a URL, query string or log.
  * `last_error` and every log line are built from `kind` and status only.
* **Transport:** HTTPS to the public domain is mandatory. The adapter refuses a non-`https://` `SEMANTICA_BASE_URL` except `http://localhost` and `http://127.0.0.1`, which the parity tests use.
* **Workspace scoping:** see §13.
* **Request size:**
  * Bodies are built from bounded fields: roughly 2 KB worst case, well below the 64 KiB server limit.
  * Query `limit` is clamped to 200.
* **Queries:** there is no arbitrary Cypher. The adapter only calls the eight routes in §9, and the server has no generic query route (proven in 6.25B).
* **Topology:** no direct FalkorDB access and no browser access; the client module is imported only by edge functions.
* **Logging:** no payload logging. Labels and provenance are never logged, only ids, workspace, kind and status.
* **Changes required before activation:**
  1. Record the public-domain decision (§16) and rotate the key if it was ever shared outside a password manager.
  2. Put the key into Supabase secrets.
  3. Complete Phase 6.25C Stages 4–8 against the deployed service (§26 Q2).

## 24. Required implementation changes (Phase 6.5B)

All paths are in `Zeptly/zeptly-MVP`. "Local unchanged" means existing tests pass unmodified.

| ID | File | Symbol | Reason | Before | After | Tests |
|---|---|---|---|---|---|---|
| C1 | `supabase/functions/_shared/cortex/semantica-external-codec.ts` (new) | `encodeNodeId`, `decodeNodeId`, `edgeIdFor`, `encodeProvenanceRefs`, `decodeProvenanceRefs`, `toEdgeType`, `fromEdgeType`, `sanitizeExternalLabel`, `toUtcIso` | identity and payload translation (§11, §12) | none | pure, deterministic functions | round-trip property tests for every node type and relation; label sanitisation cases; a test that the id charset matches the server regex |
| C2 | `supabase/functions/_shared/cortex/semantica-external-client.ts` (new) | `ExternalSemanticaClient implements SemanticaClient`, `SemanticaBackendError`, `classifyResponse`, `readiness()` | the external write path (§10, §14, §17) | none | HTTP adapter with injected `fetchImpl`, timeout, 409 placeholder retry, 404-as-success on delete, `wipeWorkspace` throws `unsupported` | fetch-mocked tests for every row of the §14 table; a test that no request ever lacks a workspace; a test that the key is never present in thrown messages |
| C3 | `supabase/functions/_shared/cortex/semantic-query-backend.ts` (new) | `SemanticQueryBackend { backend; query(req) }`, `LocalSemanticQueryBackend` (delegates to `querySemanticContext`), `ExternalSemanticQueryBackend` | the external read path. This is **additive**: `SemanticaClient` is untouched | reads bypass the seam | reads go through a backend object; local is identical | local backend equals `querySemanticContext` output on the existing fixtures; external mapping tests |
| C4 | `supabase/functions/_shared/cortex/semantic-query.ts` | `SemanticQueryOutcome` | explicit infrastructure failure (§21) | `blocked: not_found \| invalid_reference \| workspace_mismatch` | adds `backend_unavailable` to the union, plus optional `backend` and `fallback_from` on `ok` results. **`querySemanticContext`'s body is unchanged.** | the existing 23 tests pass unmodified; type test |
| C5 | `supabase/functions/_shared/cortex/semantic-projection.ts` | `projectWithDurableIntent`, `reconcileSemanticProjection` | bounded retries and namespace-scoped draining for external intents (§14, §22) | fixed 60 s, unbounded, and drain-all | when the intent key starts with `ext:` **and** the error is a `SemanticaBackendError`: exponential backoff, `failed` on non-retryable errors or after `MAX_ATTEMPTS`. Reconcile drains only the namespace matching `client.backend`. **Keys without `ext:` follow the old code path, byte for byte.** | the existing tests pass; new tests for backoff, the attempt limit and namespace isolation |
| C6 | `supabase/functions/_shared/cortex/semantic-backend.ts` (new) | `resolveSemanticProjectionTargets(supabase, ws)`, `resolveSemanticQueryBackend(supabase, ws, opts)` | mode selection (§15, §19–§21) | none | `local` / `shadow` / `external` from env and `isFeatureEnabled`; fail-safe `local` | a matrix test over flags × config presence |
| C7 | `supabase/functions/_shared/cortex/memory-relationship-writers.ts` | `writeMemoryEvidenceRefs`, `writeCaseExecutionRef` | send shadow projection | local projection only | **if and only if** the `client` argument is absent: resolve targets (C6); run the local call exactly as today; if a shadow target exists, run a second `projectWithDurableIntent` with the `ext:` key | the existing writer tests (which pass `client`) are unchanged; new tests: flags off means zero external calls; shadow on and external failing leaves the canonical write, the local projection and exactly one `ext:` pending row |
| C8 | `supabase/functions/service-health/index.ts` | `checkSemantica` (new) | dependency visibility (§17) | none | a `semantica` `ServiceStatus` entry | mocked test: ready, unavailable and not configured |
| C9 | `src/test/cortex-semantica-parity.test.ts` (new) | parity harness | §18 | none | in-process fake by default; live mode via `SEMANTICA_PARITY_BASE_URL` | the harness itself |
| C10 | `docs/agentic-cortex/IMPLEMENTATION-STATE.md`, `DECISIONS.md` | new AC entries: external identity encoding, public-endpoint necessity, query seam, shadow namespace | governance | none | recorded decisions | none |

No migration is needed: outbox statuses, operation types and `canonical_ref` already support everything above. The contract file `semantic_projection_v1.ts` is **unchanged**. `SemanticQueryOutcome` lives in `semantic-query.ts`, not in the shared contract.

## 25. Explicit non-changes

Phase 6.5 does **not** change:
* canonical PostgreSQL ownership;
* Capability Identity, Policy or Implementation;
* `runtime_runs`;
* the Memory Cortex ontology or memory lifecycle;
* Tape;
* the authorization authority, RLS policies or GRANTs;
* model promotion;
* orchestration (TimeSaver, AC-002).

**It also does not change:**
* the `SemanticaClient` interface;
* `PostgresSemanticProjectionClient`;
* `querySemanticContext`'s behaviour;
* `PROJECTION_MAP`;
* the contract file;
* the local outbox semantics for non-`ext:` keys;
* the full-replace, label-clearing upsert behaviour (reported in §26, **not** fixed);
* the absence of production tombstone and retention paths (reported, not fixed).

The local semantic backend is **not deleted**. Phase 7 is **not** started. The Phase 6.25 service needs **no changes** for 6.5B.

## 26. Risks and open questions

| # | Item | Proposed owner or decision |
|---|---|---|
| Q1 | **The public endpoint becomes a permanent dependency.** Supabase edge functions cannot use Railway private networking. Options: (a) keep the public domain with API-key authentication (the minimum); (b) add a custom domain; (c) move the semantic caller to a Railway worker (new infrastructure). The API has no IP allowlisting or rate limiting (6.25B R4/R9). | Decision before shadow activation. |
| Q2 | **Phase 6.25C is incomplete on the deployed service.** On Railway, only boot, health and readiness are proven (deployment `7f279309`). Authentication, node and edge CRUD, workspace isolation, persistence across restarts and cleanup are proven **locally** (6.25A/B: 258 tests and 139 stack checks) but not against the deployed instance: Stages 4–8 are blocked because both operator sandboxes block `*.up.railway.app`. The 6.5A brief states these are proven. **The repository evidence does not support that for the deployed instance.** | Must finish before shadow activation. It does not block 6.5B implementation, which can be tested against the local stack. |
| Q3 | Shadow mode adds up to `SEMANTICA_TIMEOUT_MS` of latency after the commit in `memory-approve` and `zmc-cases-promote`. | Choose between an inline attempt and record-only with reconcile drain. |
| Q4 | No stale-row removal externally (no wipe, and reconcile doesn't delete). | Later: a Phase 6.25 purge or list endpoint, or accept the drift, which parity will surface. |
| Q5 | Rebuild cost: one HTTP call per node and per edge, with no batch endpoint. Large workspaces need the cursor from C5 and may hit edge-function time limits. | Measure in shadow mode; a batch endpoint is a possible 6.25 follow-up. |
| Q6 | Latent Phase 6 behaviour: writer endpoint upserts clear labels and statuses (full replace). The external service mirrors this because `PUT` is a full replace. | A Phase 6 owner decision; out of 6.5 scope. |
| Q7 | Latent Phase 6 gaps: no production tombstone on deprecate, no automatic retry consumer, no outbox pruning, outbox insert errors not checked. | Report to the Phase 6 owner; C5 does not fix local behaviour. |
| Q8 | Single shared API key with no rotation overlap (6.25B R4). Rotation needs coordinated Railway and Supabase secret changes. | Accept for 6.5B; dual-key support is a 6.25 follow-up. |
| Q9 | The Zeptly lockfile is out of sync (`npm ci` fails). 6.5B CI must be able to install. | Repository hygiene before 6.5B merges. |
| Q10 | Accepted parity differences: the query limit (200 merged versus per direction), and placeholder endpoint nodes versus dangling edges. | Documented in §18. |

## 27. Phase 6.5B implementation plan

1. **Codec (C1)** with property tests. Pure functions, no I/O.
2. **External client (C2)** against a fetch mock that implements the §9 contract, covering every §14 error row.
3. **Query seam (C3, C4)**, local delegate first. Prove the existing 23 plus 8 tests still pass unmodified.
4. **Outbox namespacing and bounded retries (C5)** for `ext:` keys only. Regression-prove the local path is untouched.
5. **Mode resolver (C6)** and **writer shadow hook (C7)**, off by default. Flags-off tests show zero external calls.
6. **Health (C8).**
7. **Parity harness (C9):** the in-process fake in CI, plus one manual live run against the Phase 6.25 local Docker stack. The deployed instance is used only once Q2 is closed.
8. **Docs (C10).** Stop before any production configuration change.

**Activation gates** (after 6.5B, before any flag is turned on):
* Q2 closed: Phase 6.25C Stages 4–8 are evidenced on the deployed service.
* Q1 decided.
* Secrets are set in Supabase.
* Shadow runs for at least one workspace with a parity report.
* The query flag is enabled only after a clean shadow parity run.

READY FOR PHASE 6.5B — EXTERNAL SEMANTICA CLIENT IMPLEMENTATION
