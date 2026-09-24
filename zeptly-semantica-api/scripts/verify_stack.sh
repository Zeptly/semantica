#!/usr/bin/env bash
# End-to-end verification of the local docker compose stack (Phase 6.25A/B).
#
#  1. build + start semantica-api and FalkorDB (fresh volume)
#  2. authentication over real HTTP (missing, wrong, empty, duplicate headers)
#  3. Workspace A / Workspace B fixtures
#  4. isolation attacks over real HTTP (guessed IDs, path traversal, malformed
#     IDs, cross-workspace edges, relationship queries)
#  5. restart the API container                    -> state persists
#  6. REBUILD the API image (--no-cache) + recreate -> state persists
#  7. restart FalkorDB (volume kept)               -> state persists
#  8. stop FalkorDB                                -> /ready 503, /health 200
#  9. start FalkorDB                                -> /ready 200, state intact
# 10. SIGKILL FalkorDB (no clean shutdown)          -> state recovered from AOF
# 11. API started while FalkorDB is down (Railway has no depends_on)
#                                                   -> API up, ready once FalkorDB is
# 12. wrong FalkorDB password                       -> not ready, no crash
# 13. docker compose down + up, volume kept         -> state intact
# 14. dual-stack bind, log hygiene
#
# Usage: scripts/verify_stack.sh [--keep]
#   VERIFY_BUILD_CA=/path/ca.pem  pass an extra CA to the image build (TLS-intercepting proxies)
#   VERIFY_SKIP_BUILD=1           reuse an existing zeptly-semantica-api:local for step 1
set -euo pipefail
cd "$(dirname "$0")/.."

export COMPOSE_PROJECT_NAME="zeptly-semantica-verify"
export SEMANTICA_ENV="production"
export SEMANTICA_API_KEY
SEMANTICA_API_KEY="$(python3 -c 'import secrets; print(secrets.token_urlsafe(48))')"
export FALKORDB_PASSWORD
FALKORDB_PASSWORD="$(python3 -c 'import secrets; print(secrets.token_urlsafe(24))')"
IMAGE="zeptly-semantica-api:local"

API="http://127.0.0.1:8080"
A="verify-ws-a"
B="verify-ws-b"
PASS=0
KEEP="${1:-}"

log()  { printf '\n==> %s\n' "$*"; }
ok()   { PASS=$((PASS + 1)); printf '  ok  %s\n' "$*"; }
fail() { printf '  FAIL %s\n' "$*" >&2; docker compose logs --tail 50 >&2 || true; exit 1; }

cleanup() {
  if [[ "$KEEP" != "--keep" ]]; then
    docker compose down -v --remove-orphans >/dev/null 2>&1 || true
  fi
}
trap cleanup EXIT

build_image() {
  if [[ -z "${VERIFY_BUILD_CA:-}" ]]; then
    docker build -q "$@" -t "$IMAGE" . >/dev/null
    return
  fi
  # Behind a TLS-intercepting proxy only: build from a throwaway copy of the
  # Dockerfile whose pip step trusts the extra CA via a BuildKit secret mount.
  # The committed Dockerfile stays plain because Railway's builder rejects
  # secret mounts; the CA never enters an image layer either way.
  local df
  df="$(mktemp)"
  sed 's|^RUN pip install --no-deps|RUN --mount=type=secret,id=build_ca PIP_CERT=/run/secrets/build_ca pip install --no-deps|' Dockerfile > "$df"
  grep -q 'id=build_ca' "$df" || { rm -f "$df"; fail "could not inject build CA into Dockerfile copy"; }
  docker build -q -f "$df" --secret "id=build_ca,src=${VERIFY_BUILD_CA}" "$@" -t "$IMAGE" . >/dev/null
  rm -f "$df"
}

# call METHOD PATH [JSON] -> sets $STATUS and $BODY (authenticated)
call() {
  local method="$1" path="$2" data="${3:-}"
  local args=(-sS --path-as-is -o /tmp/verify_body -w '%{http_code}' -X "$method" "$API$path"
              -H "X-API-Key: $SEMANTICA_API_KEY")
  [[ -n "$data" ]] && args+=(-H 'Content-Type: application/json' -d "$data")
  STATUS="$(curl "${args[@]}")"; BODY="$(cat /tmp/verify_body)"
}
# raw PATH [curl args...] -> prints status only; sends no key unless given
raw() { local p="$1"; shift; curl -s --path-as-is -o /tmp/verify_body -w '%{http_code}' "$@" "$API$p"; }
expect() { [[ "$STATUS" == "$1" ]] || fail "$2: expected HTTP $1, got $STATUS: $BODY"; ok "$2 (HTTP $STATUS)"; }
expect_raw() { local want="$1" what="$2" got; shift 2; got="$(raw "$@")"; [[ "$got" == "$want" ]] || fail "$what: expected $want got $got"; ok "$what (HTTP $got)"; }
# check MSG CMD...: run CMD; ok on success, fail (and exit) otherwise.
check() { local msg="$1"; shift; if "$@"; then ok "$msg"; else fail "$msg"; fi; }
jq_py()  { python3 -c "import json,sys; d=json.loads(sys.argv[1]); print($2)" "$BODY"; }
no_b_leak() { for s in "$B" b-secret "B secret" "B shared"; do [[ "$BODY" != *"$s"* ]] || fail "$1 leaked '$s': $BODY"; done; ok "$1 discloses nothing from B"; }

wait_for() {  # wait_for PATH EXPECTED_STATUS
  local code=""
  for _ in $(seq 1 90); do
    code="$(curl -s -o /dev/null -w '%{http_code}' "$API$1" || true)"
    [[ "$code" == "$2" ]] && return 0
    sleep 1
  done
  fail "timed out waiting for $1 -> $2 (last $code)"
}

node() {  # node WS ID LABEL
  printf '{"workspace_id":"%s","canonical_id":"%s","node_type":"memory","label":"%s","lifecycle_state":"active","is_current":true,"valid_from":"2026-01-01T00:00:00Z","source_version":1,"provenance_refs":["pg:memories/%s"]}' "$1" "$2" "$3" "$2"
}
edge() {  # edge WS ID SRC TGT TYPE
  printf '{"workspace_id":"%s","edge_id":"%s","edge_type":"%s","source_node_id":"%s","target_node_id":"%s","is_current":true,"provenance_refs":["pg:memory_links/%s"]}' "$1" "$2" "$5" "$3" "$4" "$2"
}

check_state() {  # verifies the fixture state after restarts
  local phase="$1"
  call GET "/v1/workspaces/$A/nodes/shared"; expect 200 "[$phase] A/shared persisted"
  [[ "$(jq_py "$BODY" 'd["label"]')" == "A shared" ]] || fail "[$phase] A/shared label"
  call GET "/v1/workspaces/$B/nodes/shared"; expect 200 "[$phase] B/shared persisted"
  [[ "$(jq_py "$BODY" 'd["label"]')" == "B shared" ]] || fail "[$phase] B/shared label"
  call GET "/v1/workspaces/$A/nodes/b-secret"; expect 404 "[$phase] A cannot read B/b-secret"
  call POST "/v1/workspaces/$A/relationships/query" "{\"workspace_id\":\"$A\",\"node_id\":\"shared\",\"include_non_current\":true}"
  expect 200 "[$phase] A relationship query"
  [[ "$(jq_py "$BODY" 'sorted(r["edge"]["edge_id"] for r in d["relationships"])')" == "['a-e1', 'a-e2']" ]] \
    || fail "[$phase] A relationships: $BODY"
  no_b_leak "[$phase] A traversal"
  call POST "/v1/workspaces/$B/relationships/query" "{\"workspace_id\":\"$B\",\"node_id\":\"shared\"}"
  [[ "$(jq_py "$BODY" '[r["edge"]["edge_id"] for r in d["relationships"]]')" == "['b-e1']" ]] \
    || fail "[$phase] B relationships: $BODY"
  ok "[$phase] B traversal contains only B edges"
  call GET "/v1/workspaces/$A/nodes/deleted-node"; expect 404 "[$phase] deleted node stays deleted"
}

log "1. build and start the stack (fresh volume)"
docker compose down -v --remove-orphans >/dev/null 2>&1 || true
if [[ "${VERIFY_SKIP_BUILD:-0}" != "1" ]]; then build_image; fi
docker compose up -d --no-build
wait_for /health 200; ok "/health 200"
wait_for /ready 200; ok "/ready 200"
if [[ "$(raw /docs)" == "404" && "$(raw /openapi.json)" == "404" ]]; then ok "/docs and /openapi.json disabled in production"; else fail "/docs and /openapi.json disabled in production"; fi
if [[ "$(docker compose exec -T semantica-api id -u)" == "10001" ]]; then ok "API runs as uid 10001"; else fail "API runs as uid 10001"; fi
check "running container has semantica==0.6.0" \
  docker compose exec -T semantica-api python -c "import importlib.metadata as m; assert m.version('semantica')=='0.6.0'"
PUBLISHED="$(docker inspect -f '{{json .HostConfig.PortBindings}}' "$(docker compose ps -q falkordb)")"
if [[ "$PUBLISHED" == "{}" ]]; then ok "FalkorDB has no host-published port (bindings: $PUBLISHED)"; else fail "FalkorDB published: $PUBLISHED"; fi

log "2. authentication over real HTTP"
expect_raw 401 "missing key"               "/v1/workspaces/$A/nodes/x"
expect_raw 401 "wrong key"                 "/v1/workspaces/$A/nodes/x" -H 'X-API-Key: wrong'
expect_raw 401 "empty key"                 "/v1/workspaces/$A/nodes/x" -H 'X-API-Key;'
expect_raw 401 "valid+wrong duplicate"     "/v1/workspaces/$A/nodes/x" -H "X-API-Key: $SEMANTICA_API_KEY" -H 'X-API-Key: wrong'
expect_raw 401 "wrong+valid duplicate"     "/v1/workspaces/$A/nodes/x" -H 'X-API-Key: wrong' -H "X-API-Key: $SEMANTICA_API_KEY"
expect_raw 401 "Bearer instead of key"     "/v1/workspaces/$A/nodes/x" -H "Authorization: Bearer $SEMANTICA_API_KEY"
expect_raw 401 "key in query string"       "/v1/workspaces/$A/nodes/x?X-API-Key=$SEMANTICA_API_KEY"
expect_raw 404 "valid key reaches handler" "/v1/workspaces/$A/nodes/x" -H "X-API-Key: $SEMANTICA_API_KEY"
expect_raw 200 "/health needs no key"      "/health"

log "3. Workspace A / Workspace B fixtures"
call PUT "/v1/workspaces/$A/nodes/shared"  "$(node $A shared 'A shared')";  expect 201 "create A/shared"
call PUT "/v1/workspaces/$A/nodes/shared"  "$(node $A shared 'A shared')";  expect 200 "idempotent upsert A/shared"
call PUT "/v1/workspaces/$A/nodes/a1"      "$(node $A a1 'A one')";         expect 201 "create A/a1"
call PUT "/v1/workspaces/$A/nodes/a2"      "$(node $A a2 'A two')";         expect 201 "create A/a2"
call PUT "/v1/workspaces/$B/nodes/shared"  "$(node $B shared 'B shared')";  expect 201 "create B/shared (same canonical id)"
call PUT "/v1/workspaces/$B/nodes/b-secret" "$(node $B b-secret 'B secret')"; expect 201 "create B/b-secret"
call PUT "/v1/workspaces/$A/edges/a-e1" "$(edge $A a-e1 shared a1 RELATES_TO)";   expect 201 "create A edge a-e1"
call PUT "/v1/workspaces/$A/edges/a-e2" "$(edge $A a-e2 a2 shared DERIVED_FROM)"; expect 201 "create A edge a-e2"
call PUT "/v1/workspaces/$B/edges/b-e1" "$(edge $B b-e1 shared b-secret RELATES_TO)"; expect 201 "create B edge b-e1"
call PUT "/v1/workspaces/$A/edges/tmp" "$(edge $A tmp a1 a2 RELATES_TO)";  expect 201 "create A edge tmp"
call DELETE "/v1/workspaces/$A/edges/tmp"; expect 200 "delete A edge tmp"
call PUT "/v1/workspaces/$A/nodes/deleted-node" "$(node $A deleted-node 'to delete')"; expect 201 "create A/deleted-node"
call DELETE "/v1/workspaces/$A/nodes/deleted-node"; expect 200 "delete A/deleted-node"
call PUT "/v1/workspaces/$A/nodes/big" "$(node $A big "$(printf 'x%.0s' $(seq 1 161))")"; expect 422 "label > 160 rejected"
call PUT "/v1/workspaces/$A/nodes/c" '{"workspace_id":"'$A'","canonical_id":"c","node_type":"memory","content":"secret memory text"}'
expect 422 "forbidden field 'content' rejected"
call PUT "/v1/workspaces/$A/nodes/e" '{"workspace_id":"'$A'","canonical_id":"e","node_type":"memory","embedding":[0.1,0.2]}'
expect 422 "forbidden field 'embedding' rejected"
check_state "initial"

log "4. isolation attacks over real HTTP"
call GET "/v1/workspaces/$A/nodes/b-secret"; expect 404 "guessed B canonical id from A"
no_b_leak "guessed-id response"
call GET "/v1/workspaces/$A/nodes/..%2F..%2F$B%2Fnodes%2Fb-secret"
[[ "$STATUS" == 404 || "$STATUS" == 422 ]] || fail "encoded traversal $STATUS"; no_b_leak "encoded path traversal ($STATUS)"
call GET "/v1/workspaces/$A/nodes/../../$B/nodes/b-secret"
[[ "$STATUS" != 200 ]] || fail "raw ../ traversal returned 200"; no_b_leak "raw ../ path traversal ($STATUS)"
call GET "/v1/workspaces/$A/../$B/nodes/b-secret"
[[ "$STATUS" != 200 ]] || fail "workspace ../ traversal returned 200"; no_b_leak "workspace ../ traversal ($STATUS)"
call GET "/v1/workspaces/$A:x/nodes/b-secret";               expect 422 "workspace id with ':'"
call GET "/v1/workspaces/$A%0A/nodes/b-secret";              expect 422 "workspace id with newline"
call GET "/v1/workspaces/$B%00/nodes/b-secret";              expect 422 "workspace id with NUL"
call GET "/v1/workspaces/%2A/nodes/b-secret";                expect 422 "workspace id '*'"
call GET "/v1/workspaces/$(printf 'w%.0s' $(seq 1 129))/nodes/x"; expect 422 "workspace id > 128 chars"
call GET "/v1/workspaces/$A/nodes/$B:b-secret";              expect 422 "node id carrying another workspace prefix"
call PUT "/v1/workspaces/$A/edges/x" "$(edge $A x a1 b-secret RELATES_TO)"; expect 409 "A edge to B endpoint rejected"
no_b_leak "cross-workspace edge response"
call PUT "/v1/workspaces/$A/edges/x" "$(edge $A x a1 no-such-node RELATES_TO)"; expect 409 "A edge to nonexistent endpoint (same response)"
call PUT "/v1/workspaces/$A/edges/b-e1" "$(edge $A b-e1 a1 a2 RELATES_TO)"; expect 201 "reusing B's edge id in A creates an independent A edge"
call POST "/v1/workspaces/$B/relationships/query" "{\"workspace_id\":\"$B\",\"node_id\":\"shared\"}"
[[ "$(jq_py "$BODY" '[r["neighbor"]["canonical_id"] for r in d["relationships"]]')" == "['b-secret']" ]] || fail "B edge altered: $BODY"
ok "B's b-e1 unchanged by A's same-id edge"
call DELETE "/v1/workspaces/$A/edges/b-e1"; expect 200 "delete A's b-e1"
call DELETE "/v1/workspaces/$A/edges/b-e1"; expect 404 "A cannot delete B's b-e1"
call DELETE "/v1/workspaces/$A/nodes/b-secret"; expect 404 "A cannot delete B node"
call POST "/v1/workspaces/$A/relationships/query" "{\"workspace_id\":\"$A\",\"node_id\":\"b-secret\"}"; expect 404 "A query anchored on B node"
call POST "/v1/workspaces/$A/relationships/query" "{\"workspace_id\":\"$B\",\"node_id\":\"shared\"}"; expect 422 "body workspace cannot redirect scope"
call POST "/v1/workspaces/$A/relationships/query" "{\"workspace_id\":\"$A\",\"node_id\":\"shared\",\"cypher\":\"MATCH (n) RETURN n\"}"; expect 422 "cypher field rejected"
for p in /cypher /v1/cypher /v1/query /admin /v1/graph /upload /v1/ingest /metrics; do
  [[ "$(raw "$p" -X POST -H "X-API-Key: $SEMANTICA_API_KEY" -H "Content-Type: application/json" -d "{}")" =~ ^40[45]$ ]] || fail "unexpected endpoint $p"
done; ok "no cypher/admin/upload/ingest endpoints"
check_state "after attacks"

log "5. restart the API container"
docker compose restart semantica-api
wait_for /ready 200; ok "API back and ready"
check_state "after API restart"

log "6. rebuild the API image from scratch and recreate the container"
OLD_ID="$(docker image inspect -f '{{.Id}}' "$IMAGE")"
build_image --no-cache
NEW_ID="$(docker image inspect -f '{{.Id}}' "$IMAGE")"
docker compose up -d --no-build --force-recreate --no-deps semantica-api
wait_for /ready 200; ok "rebuilt API ready (image ${OLD_ID:7:12} -> ${NEW_ID:7:12})"
check_state "after API rebuild"

log "7. restart FalkorDB (volume retained)"
docker compose restart falkordb
wait_for /ready 200; ok "API ready after FalkorDB restart"
check_state "after FalkorDB restart"

log "8. FalkorDB outage"
docker compose stop falkordb
wait_for /ready 503; ok "/ready 503 while FalkorDB is down"
if [[ "$(raw /health)" == "200" ]]; then ok "/health still 200"; else fail "/health still 200"; fi
call GET "/v1/workspaces/$A/nodes/shared"; expect 503 "projection call -> 503 during outage"
if [[ "$BODY" == '{"detail":"graph store unavailable"}' ]]; then ok "outage response leaks nothing"; else fail "outage response leaks nothing"; fi
if [[ -n "$(docker compose ps --status running -q semantica-api)" ]]; then ok "API process did not crash"; else fail "API process did not crash"; fi

log "9. FalkorDB returns"
docker compose start falkordb
wait_for /ready 200; ok "/ready recovers without restarting the API"
check_state "after FalkorDB outage"

log "10. SIGKILL FalkorDB (unclean shutdown; AOF recovery)"
sleep 2  # appendfsync everysec: let the last write reach disk
docker compose kill -s SIGKILL falkordb
docker compose start falkordb
wait_for /ready 200; ok "/ready after unclean FalkorDB restart"
check_state "after FalkorDB SIGKILL"

log "11. API starts while FalkorDB is down (no depends_on on Railway)"
docker compose stop falkordb semantica-api
docker compose up -d --no-build --no-deps semantica-api
wait_for /health 200; ok "API healthy with FalkorDB absent"
wait_for /ready 503; ok "API not ready with FalkorDB absent"
sleep 3
if [[ "$(raw /health)" == "200" ]]; then ok "API stays up (no crash loop)"; else fail "API stays up (no crash loop)"; fi
docker compose start falkordb
wait_for /ready 200; ok "API becomes ready once FalkorDB starts (no API restart)"
check_state "after late FalkorDB start"

log "12. wrong FalkorDB password"
docker compose stop semantica-api >/dev/null
FALKORDB_PASSWORD="wrong-password" docker compose up -d --no-build --no-deps --force-recreate semantica-api
wait_for /health 200; wait_for /ready 503; ok "wrong password: healthy but not ready"
docker compose up -d --no-build --no-deps --force-recreate semantica-api
wait_for /ready 200; ok "correct password restored: ready"

log "13. docker compose down + up (containers recreated, volume retained)"
docker compose down
docker compose up -d --no-build
wait_for /ready 200; ok "recreated stack ready"
check_state "after stack recreate"

log "14. network bind and log hygiene"
BIND_HOST="$(docker compose logs --no-color --no-log-prefix semantica-api | python3 -c "
import json, sys
print([json.loads(l) for l in sys.stdin if '\"bind\"' in l][-1]['host'])")"
if [[ "$BIND_HOST" == "::" ]]; then
  check "API answers on IPv4 and IPv6 (dual-stack [::] bind)" docker compose exec -T semantica-api python -c "
import urllib.request
for u in ('http://127.0.0.1:8080/health', 'http://[::1]:8080/health'):
    assert urllib.request.urlopen(u, timeout=3).status == 200, u"
else
  check "no IPv6 in this container runtime: fell back to 0.0.0.0 and answers on IPv4" docker compose exec -T semantica-api python -c "
import urllib.request
assert urllib.request.urlopen('http://127.0.0.1:8080/health', timeout=3).status == 200"
fi
LOGS="$(docker compose logs --no-color semantica-api falkordb 2>&1)"
if [[ "$LOGS" != *"$SEMANTICA_API_KEY"* ]]; then ok "API key absent from logs"; else fail "API key absent from logs"; fi
if [[ "$LOGS" != *"$FALKORDB_PASSWORD"* ]]; then ok "FalkorDB password absent from logs"; else fail "FalkorDB password absent from logs"; fi
for marker in "secret memory text" "B secret" "A shared"; do
  [[ "$LOGS" != *"$marker"* ]] || fail "payload content '$marker' found in logs"
done; ok "payload contents absent from logs"
API_LOGS="$(docker compose logs --no-color --no-log-prefix semantica-api 2>&1)"
if python3 - "$API_LOGS" <<'PY'; then ok "API logs are JSON lines with startup/readiness/bind events"; else fail "structured log check"; fi
import json, sys
events = [json.loads(l) for l in sys.argv[1].splitlines() if l.strip()]  # every line must be JSON
names = {e.get("event") for e in events}
assert {"startup", "readiness", "bind"} <= names, names
start = next(e for e in events if e.get("event") == "startup")
assert start["semantica_version"] == "0.6.0" and start["auth_required"] is True
assert start["anonymous_allowed"] is False and start["falkordb_password_set"] is True
PY

log "PASS: $PASS checks"
