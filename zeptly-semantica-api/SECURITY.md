# Security — zeptly-semantica-api

This document covers the security model of the `zeptly-semantica-api` service only. The upstream Semantica project's policy is in the repository root `SECURITY.md`.

## Non-authoritative by design

The service holds a **derived projection** of canonical Zeptly PostgreSQL state and nothing else.

* **No authority.** It is not authoritative for identity, lifecycle, policy, authorization, execution events, memory relationships, Tape, deletion or forgetting, model promotion, or orchestration.
* **No decisions based on its answers.** Zeptly must not grant access, delete data or promote anything because of an answer from this service.
* **Loss is recoverable.** Losing or corrupting the graph is an availability issue, not a data-integrity issue. The graph can be dropped and rebuilt by replaying canonical rows through `PUT`.
* **Deletes are projection-only.** `DELETE` routes remove projection state only. They do not perform or authorize forgetting in Zeptly.

## Authentication

* Every `/v1/...` route requires the header `X-API-Key: <SEMANTICA_API_KEY>`. This is server-to-server authentication only. There are no users, accounts or roles, and Zeptly remains the authorization authority.
* The SHA-256 digests of the presented and expected keys are compared with `hmac.compare_digest`, so timing does not depend on the presented key's length or content. Missing, empty, wrong and **duplicated** `X-API-Key` headers all get the same `401`. Duplicates are refused rather than resolved, because proxies disagree about which copy wins. The key is never read from a query string, a cookie or `Authorization`.
* Authentication runs before request validation, so unauthenticated callers learn nothing about the payload schema.
* Fail-closed startup (the process exits with code 2):
  * `SEMANTICA_API_KEY` is unset or blank and anonymous mode is not enabled.
  * `SEMANTICA_ALLOW_ANONYMOUS=true` while `SEMANTICA_ENV` is anything other than `development` or `test`. An unset `SEMANTICA_ENV` counts as `production`.
  * The key is shorter than 32 characters in `production` or `staging`.
  * `FALKORDB_PASSWORD` is unset in `production` or `staging`.
* At request time, a missing key configuration also fails closed with `503`. This covers a mistake when the app is built in code.
* Only `/health` and `/ready` are unauthenticated. They return fixed status strings and never show hostnames, ports, versions, exceptions or tenant data. `/docs` and `/openapi.json` are disabled outside `development` and `test`.
* To rotate the key, set the new value on both Zeptly and this service, then redeploy. There is a single active key.

## Private networking

* FalkorDB must have **no public exposure**: no Railway public domain or TCP proxy. It is reached only over Railway private networking at `*.railway.internal`.
* FalkorDB **must** run with `--requirepass` (`FALKORDB_PASSWORD`). The `falkordb-server` image starts Redis with `protected-mode no` and binds all interfaces (`* -::*`), so without a password anything on the private network could read or flush the graph. The API refuses to start in production or staging without the password. The local compose file does both: FalkorDB is not published to the host and requires a password.
* The API should be private as well unless Phase 6.25B decides otherwise. If it gets a public domain, the API key becomes the only barrier.

## Secrets

* Secrets come only from environment variables: `SEMANTICA_API_KEY` and `FALKORDB_PASSWORD`. They are never in the image, `railway.toml`, compose defaults for production, or the repository. `.env` is git-ignored, and `.env.example` holds placeholders only.
* `Settings.__repr__` redacts both secrets. Logs are JSON lines. Startup records only whether each secret is set. Graph failures record the operation name and exception type, never query text or parameters. Authentication failures record the reason, route template and a running count, never key material. Request bodies and headers are never logged. The Phase 6.25B stack check greps the container logs for both secrets and for payload markers.
* Builds behind a TLS-intercepting proxy pass the CA as a BuildKit secret mount, which is never written to a layer.

## Workspace isolation

* Every node and edge carries `workspace_id`. Internal identity is `"{workspace_id}:{id}"`, derived by the service from the **URL path**. Identifiers cannot contain `:`, so keys cannot collide or be forged across workspaces.
* The body's `workspace_id` (and `canonical_id` or `edge_id`) must equal the path, otherwise the request gets `422`. The body cannot move the operation into a different workspace.
* Every Cypher template filters on `workspace_id` for the anchor node, each traversed edge and each neighbour. This holds even where the scoped key would already be enough. A cross-workspace edge written to FalkorDB by any other route is still never followed (tested).
* An edge can only connect two nodes in the same workspace. Otherwise the request gets `409`.
* A request for another workspace's object returns `404`, the same response as for an object that does not exist.
* Scope is enforced by this service, but **which workspaces a caller may access is decided by Zeptly**. Any holder of the service key can address any workspace, so the key must be held only by Zeptly's backend.

## Payload restrictions

* All request models use `extra="forbid"`. Unknown fields are rejected with `422`, including `content`, `prompt`, `messages`, `model_output`, `embedding`, `vector`, `metadata`, `api_key`, `credentials`, `document` and `key`.
* Allowed data:
  * identifiers and types, all regex-constrained;
  * lifecycle and currentness;
  * timezone-aware timestamps;
  * at most 16 provenance references, pattern-constrained with no whitespace and at most 256 characters each;
  * an optional label of 1–160 characters with no control characters (C0 or C1), no bidi embedding, override, isolate or mark characters, and no Unicode line or paragraph separators.
* Request bodies over 64 KiB are rejected with `413`. `POST` and `PUT` requests without a `Content-Length` header, such as chunked uploads, are rejected with `411`, so the size check cannot be bypassed.

## Query-injection controls

* The service does not accept arbitrary Cypher, and there is no generic query endpoint. The relationship query is a fixed one-hop template chosen from a closed enum (`outgoing`, `incoming`, `both`).
* All Cypher is a module-level constant in `app/graph.py`. Client values are passed only as **query parameters** to `GraphStore.execute_query`. The label and relationship type are fixed constants (`SemanticaNode`, `SEMANTIC_EDGE`). The client's `edge_type` is a parameterised property filter, never part of the query text.
* Identifiers are validated twice: by the API models and path parameters, and again in `scoped_key()` in the graph layer.
* `LIMIT` is parameterised and bounded (1–200).
* The `falkordb` Python client does **not** bind parameters on the server. It writes each value into a `CYPHER name=value …` header, as a string literal with `\` and `"` escaped. Phase 6.25B verified that hostile strings round-trip through that encoding unchanged: quote breakouts, trailing backslashes, `$param` text, backticks and `\u0022` text. It also verified that every field that can reach a query rejects NUL bytes. Parameter names and map keys are always server-chosen constants.

## Availability

* Graph calls have bounded socket timeouts (`FALKORDB_TIMEOUT_SECONDS`). While FalkorDB is unavailable, `/ready` returns `503` and projection calls return `503 {"detail":"graph store unavailable"}`. The service recovers automatically when FalkorDB returns.
* The container runs as a non-root user (uid 10001) with a read-only root filesystem. Compose also drops all capabilities and sets `no-new-privileges`.

## Known limitations

* The service has a single shared key: no per-caller identity, key versioning or overlap window for rotation.
* There is no rate limiting. The service is expected to sit behind private networking, called by Zeptly only. uvicorn answers `503` beyond 100 concurrent connections and requests.
* An authenticated caller can still put free text in a `label` (160 characters) and base64url-like text in `provenance_refs` (16 × 256 characters). The API bounds this data but cannot prove what it means, so Zeptly's projection code is responsible for sending only titles and references.
* The runtime image is Debian slim, so `sh`, `bash`, `perl` and `apt-get`/`dpkg` remain. The process runs as a non-root user with read-only application code, so these cannot install or modify anything.

## Reporting

Report vulnerabilities in this service privately to the Zeptly maintainers. Do not open a public issue.
