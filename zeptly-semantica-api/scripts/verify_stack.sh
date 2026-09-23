#!/usr/bin/env bash
# End-to-end verification of the local docker compose stack.
#
#  1. build + start semantica-api and FalkorDB (fresh volume)
#  2. exercise the API with Workspace A and Workspace B fixtures
#  3. restart the API container            -> state must persist
#  4. restart FalkorDB (volume kept)       -> state must persist
#  5. stop FalkorDB                        -> /ready 503, /health 200
#  6. start FalkorDB                       -> /ready 200, state intact
#  7. SIGKILL FalkorDB (no clean shutdown)  -> state recovered from AOF
#  8. docker compose down + up, volume kept -> state intact in new containers
#
# Usage: scripts/verify_stack.sh [--keep]   (--keep leaves the stack running)
# VERIFY_SKIP_BUILD=1 reuses an already-built zeptly-semantica-api:local image.
set -euo pipefail
cd "$(dirname "$0")/.."

export COMPOSE_PROJECT_NAME="zeptly-semantica-verify"
export SEMANTICA_ENV="production"
export SEMANTICA_API_KEY
SEMANTICA_API_KEY="$(python3 -c 'import secrets; print(secrets.token_urlsafe(48))')"
export FALKORDB_PASSWORD
FALKORDB_PASSWORD="$(python3 -c 'import secrets; print(secrets.token_urlsafe(24))')"

API="http://127.0.0.1:8080"
A="verify-ws-a"
B="verify-ws-b"
PASS=0

log()  { printf '\n==> %s\n' "$*"; }
ok()   { PASS=$((PASS + 1)); printf '  ok  %s\n' "$*"; }
fail() { printf '  FAIL %s\n' "$*" >&2; docker compose logs --tail 50 >&2 || true; exit 1; }

cleanup() {
  if [[ "${1:-}" != "--keep" ]]; then
    docker compose down -v --remove-orphans >/dev/null 2>&1 || true
  fi
}
trap 'cleanup "${KEEP:-}"' EXIT
[[ "${1:-}" == "--keep" ]] && KEEP="--keep"

# call METHOD PATH [JSON] -> sets $STATUS and $BODY
call() {
  local method="$1" path="$2" data="${3:-}" out
  if [[ -n "$data" ]]; then
    out="$(curl -sS -o /tmp/verify_body -w '%{http_code}' -X "$method" "$API$path" \
      -H "X-API-Key: $SEMANTICA_API_KEY" -H 'Content-Type: application/json' -d "$data")"
  else
    out="$(curl -sS -o /tmp/verify_body -w '%{http_code}' -X "$method" "$API$path" \
      -H "X-API-Key: $SEMANTICA_API_KEY")"
  fi
  STATUS="$out"; BODY="$(cat /tmp/verify_body)"
}
expect() { [[ "$STATUS" == "$1" ]] || fail "$2: expected HTTP $1, got $STATUS: $BODY"; ok "$2 (HTTP $STATUS)"; }
jq_py()  { python3 -c "import json,sys; d=json.loads(sys.argv[1]); print($2)" "$BODY"; }

wait_for() {  # wait_for PATH EXPECTED_STATUS
  for _ in $(seq 1 60); do
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
  call POST "/v1/workspaces/$A/relationships/query" "{\"workspace_id\":\"$A\",\"node_id\":\"shared\"}"
  expect 200 "[$phase] A relationship query"
  [[ "$(jq_py "$BODY" 'sorted(r["edge"]["edge_id"] for r in d["relationships"])')" == "['a-e1', 'a-e2']" ]] \
    || fail "[$phase] A relationships: $BODY"
  [[ "$BODY" != *b-secret* ]] || fail "[$phase] B data leaked into A traversal"
  ok "[$phase] A traversal contains only A edges"
  call POST "/v1/workspaces/$B/relationships/query" "{\"workspace_id\":\"$B\",\"node_id\":\"shared\"}"
  [[ "$(jq_py "$BODY" '[r["edge"]["edge_id"] for r in d["relationships"]]')" == "['b-e1']" ]] \
    || fail "[$phase] B relationships: $BODY"
  ok "[$phase] B traversal contains only B edges"
  call GET "/v1/workspaces/$A/nodes/deleted-node"; expect 404 "[$phase] deleted node stays deleted"
}

log "1. build and start the stack (fresh volume)"
docker compose down -v --remove-orphans >/dev/null 2>&1 || true
if [[ "${VERIFY_SKIP_BUILD:-0}" != "1" ]]; then
  docker compose build semantica-api
fi
docker compose up -d --no-build
wait_for /health 200; ok "/health 200"
wait_for /ready 200; ok "/ready 200"
[[ "$(curl -s -o /dev/null -w '%{http_code}' "$API/docs")" == "404" ]] && ok "/docs disabled in production"
[[ "$(docker compose exec -T semantica-api id -u)" == "10001" ]] && ok "API runs as uid 10001"

log "2. authentication"
[[ "$(curl -s -o /dev/null -w '%{http_code}' "$API/v1/workspaces/$A/nodes/x")" == "401" ]] && ok "missing key -> 401"
[[ "$(curl -s -o /dev/null -w '%{http_code}' -H 'X-API-Key: wrong' "$API/v1/workspaces/$A/nodes/x")" == "401" ]] && ok "invalid key -> 401"

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
call PUT "/v1/workspaces/$A/edges/x" "$(edge $A x a1 b-secret RELATES_TO)"; expect 409 "A cannot link to B node"
call PUT "/v1/workspaces/$A/edges/tmp" "$(edge $A tmp a1 a2 RELATES_TO)";  expect 201 "create A edge tmp"
call DELETE "/v1/workspaces/$A/edges/tmp"; expect 200 "delete A edge tmp"
call PUT "/v1/workspaces/$A/nodes/deleted-node" "$(node $A deleted-node 'to delete')"; expect 201 "create A/deleted-node"
call DELETE "/v1/workspaces/$A/nodes/deleted-node"; expect 200 "delete A/deleted-node"
call PUT "/v1/workspaces/$A/nodes/big" "$(node $A big "$(printf 'x%.0s' $(seq 1 161))")"; expect 422 "label > 160 rejected"
call PUT "/v1/workspaces/$A/nodes/c" '{"workspace_id":"'$A'","canonical_id":"c","node_type":"memory","content":"secret memory text"}'
expect 422 "forbidden field 'content' rejected"
check_state "initial"

log "4. restart the API container"
docker compose restart semantica-api
wait_for /ready 200; ok "API back and ready"
check_state "after API restart"

log "5. restart FalkorDB (volume retained)"
docker compose restart falkordb
wait_for /ready 200; ok "API ready after FalkorDB restart"
check_state "after FalkorDB restart"

log "6. FalkorDB outage"
docker compose stop falkordb
wait_for /ready 503; ok "/ready 503 while FalkorDB is down"
[[ "$(curl -s -o /dev/null -w '%{http_code}' "$API/health")" == "200" ]] && ok "/health still 200"
call GET "/v1/workspaces/$A/nodes/shared"; expect 503 "projection call -> 503 during outage"
[[ "$BODY" == '{"detail":"graph store unavailable"}' ]] && ok "outage response leaks nothing"
docker compose start falkordb
wait_for /ready 200; ok "/ready recovers after FalkorDB returns"
check_state "after FalkorDB outage"

log "7. SIGKILL FalkorDB (unclean shutdown; AOF recovery)"
sleep 2  # appendfsync everysec: let the last write reach disk
docker compose kill -s SIGKILL falkordb
docker compose start falkordb
wait_for /ready 200; ok "/ready after unclean FalkorDB restart"
check_state "after FalkorDB SIGKILL"

log "8. docker compose down + up (containers recreated, volume retained)"
docker compose down
docker compose up -d --no-build
wait_for /ready 200; ok "recreated stack ready"
check_state "after stack recreate"

log "PASS: $PASS checks"
