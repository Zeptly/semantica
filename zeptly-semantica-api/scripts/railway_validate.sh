#!/usr/bin/env bash
# Phase 6.25C validation against the deployed Railway service.
# Run on YOUR machine, one stage at a time, and paste the output back.
#
# Setup (the key is read silently and never printed or passed on argv):
#   read -rs SEMANTICA_API_KEY; export SEMANTICA_API_KEY
#   export BASE="https://semantica-api-<suffix>.up.railway.app"
#
# Stages:
#   health        GET /health and /ready
#   auth          missing / invalid / empty / duplicate / valid key
#   fixtures      create A1, A2, A1->A2 (workspace A) and B1, B2, B1->B2 (workspace B)
#   query         intended relationships + cross-workspace attempts
#   check         persistence check (run after each restart)
#   cleanup       delete test edges and nodes in both workspaces
#   verify-clean  prove no Phase 6.25 fixture data remains
#
# Output contains HTTP status codes and fixture metadata only - never the key.
set -uo pipefail

: "${BASE:?export BASE=https://<your semantica-api domain>}"
: "${SEMANTICA_API_KEY:?read -rs SEMANTICA_API_KEY; export SEMANTICA_API_KEY}"
BASE="${BASE%/}"

A="phase625-workspace-a"
B="phase625-workspace-b"
FAILS=0
STATUS=""
BODY=""

pass() { printf 'PASS  %s\n' "$*"; }
failm() { printf 'FAIL  %s\n' "$*"; FAILS=$((FAILS + 1)); }

# req METHOD PATH [JSON] -> sets STATUS, BODY. Key sent via stdin header file.
req() {
  local method="$1" path="$2" data="${3:-}" args
  args=(-sS --max-time 20 -o /tmp/p625_body -w '%{http_code}' -X "$method" "$BASE$path" -H @-)
  [[ -n "$data" ]] && args+=(-H 'Content-Type: application/json' -d "$data")
  STATUS="$(printf 'X-API-Key: %s\n' "$SEMANTICA_API_KEY" | curl "${args[@]}")" || STATUS="curl-error"
  BODY="$(cat /tmp/p625_body 2>/dev/null)"; rm -f /tmp/p625_body
}
# anon METHOD PATH [extra curl args...] -> status only (no key); clears BODY
anon() { BODY=""; local m="$1" p="$2"; shift 2; curl -sS --max-time 20 -o /dev/null -w '%{http_code}' -X "$m" "$@" "$BASE$p" || echo curl-error; }

# Refuse to run when BASE cannot be reached at all (DNS, proxy, firewall):
# a network block must never be recorded as a test result.
preflight() {
  local code
  code="$(curl -s --max-time 20 -o /dev/null -w '%{http_code}' "$BASE/health" 2>/dev/null)" || code="000"
  if [[ "$code" == "000" ]]; then
    echo "UNREACHABLE  cannot connect to $BASE from this machine (network/proxy/DNS)."
    echo "             No test was run and nothing was recorded. Run from a machine with direct internet access."
    exit 3
  fi
}
want() {  # want EXPECTED DESCRIPTION
  if [[ "$STATUS" == "$1" ]]; then pass "$2 -> HTTP $STATUS"; else failm "$2 -> expected $1, got $STATUS: ${BODY:0:300}"; fi
}
field() { python3 -c "import json,sys; d=json.loads(sys.argv[1]); print($2)" "$BODY" 2>/dev/null; }

node() {  # node WS ID LABEL
  printf '{"workspace_id":"%s","canonical_id":"%s","node_type":"phase625_fixture","label":"%s","lifecycle_state":"active","is_current":true,"valid_from":"2026-01-01T00:00:00Z","source_version":1,"provenance_refs":["test:phase625/%s"]}' "$1" "$2" "$3" "$2"
}
edge() {  # edge WS ID SRC TGT
  printf '{"workspace_id":"%s","edge_id":"%s","edge_type":"PHASE625_LINK","source_node_id":"%s","target_node_id":"%s","is_current":true,"provenance_refs":["test:phase625/%s"]}' "$1" "$2" "$3" "$4" "$2"
}
rels() {  # rels WS NODE -> prints "edge:neighbor" list
  req POST "/v1/workspaces/$1/relationships/query" "{\"workspace_id\":\"$1\",\"node_id\":\"$2\",\"include_non_current\":true}"
}

stage_health() {
  STATUS="$(anon GET /health)"; BODY="$(curl -sS --max-time 20 "$BASE/health")"; want 200 "GET /health"; echo "      body: $BODY"
  STATUS="$(anon GET /ready)";  BODY="$(curl -sS --max-time 20 "$BASE/ready")";  want 200 "GET /ready";  echo "      body: $BODY"
  STATUS="$(anon GET /docs)"; want 404 "GET /docs (disabled in production)"
  STATUS="$(anon GET /openapi.json)"; want 404 "GET /openapi.json (disabled in production)"
}

stage_auth() {
  local p="/v1/workspaces/$A/nodes/phase625-auth-probe"
  STATUS="$(anon GET "$p")";                            want 401 "no key"
  STATUS="$(anon GET "$p" -H 'X-API-Key: wrong-key')";  want 401 "invalid key"
  STATUS="$(anon GET "$p" -H 'X-API-Key;')";            want 401 "empty key"
  BODY=""; STATUS="$(printf 'X-API-Key: %s\nX-API-Key: wrong\n' "$SEMANTICA_API_KEY" | curl -sS --max-time 20 -o /dev/null -w '%{http_code}' -H @- "$BASE$p")"
  want 401 "duplicate key headers (valid + wrong)"
  STATUS="$(printf 'Authorization: Bearer %s\n' "$SEMANTICA_API_KEY" | curl -sS --max-time 20 -o /dev/null -w '%{http_code}' -H @- "$BASE$p")"
  want 401 "Bearer header instead of X-API-Key"
  req GET "$p";                                         want 404 "valid key (reaches handler; probe node absent)"
}

stage_fixtures() {
  req PUT "/v1/workspaces/$A/nodes/phase625-a1" "$(node $A phase625-a1 'Phase 6.25 fixture A1')"; want 201 "create A1"
  req PUT "/v1/workspaces/$A/nodes/phase625-a2" "$(node $A phase625-a2 'Phase 6.25 fixture A2')"; want 201 "create A2"
  req PUT "/v1/workspaces/$A/edges/phase625-a1-a2" "$(edge $A phase625-a1-a2 phase625-a1 phase625-a2)"; want 201 "create edge A1->A2"
  req PUT "/v1/workspaces/$B/nodes/phase625-b1" "$(node $B phase625-b1 'Phase 6.25 fixture B1')"; want 201 "create B1"
  req PUT "/v1/workspaces/$B/nodes/phase625-b2" "$(node $B phase625-b2 'Phase 6.25 fixture B2')"; want 201 "create B2"
  req PUT "/v1/workspaces/$B/edges/phase625-b1-b2" "$(edge $B phase625-b1-b2 phase625-b1 phase625-b2)"; want 201 "create edge B1->B2"
  req PUT "/v1/workspaces/$A/nodes/phase625-a1" "$(node $A phase625-a1 'Phase 6.25 fixture A1')"; want 200 "idempotent re-put A1"
  req GET "/v1/workspaces/$A/nodes/phase625-a1"; want 200 "read A1"
  echo "      A1: label=$(field "$BODY" 'd["label"]') type=$(field "$BODY" 'd["node_type"]') ws=$(field "$BODY" 'd["workspace_id"]')"
  req PUT "/v1/workspaces/$A/nodes/phase625-x" '{"workspace_id":"'$A'","canonical_id":"phase625-x","node_type":"t","content":"not allowed"}'
  want 422 "payload policy: 'content' field rejected"
  req PUT "/v1/workspaces/$A/nodes/phase625-x" "$(node $A phase625-x "$(printf 'x%.0s' $(seq 1 161))")"
  want 422 "payload policy: 161-char label rejected"
}

stage_query() {
  rels "$A" phase625-a1; want 200 "A1 relationship query"
  local got; got="$(field "$BODY" '[(r["direction"], r["edge"]["edge_id"], r["neighbor"]["canonical_id"], r["neighbor"]["workspace_id"]) for r in d["relationships"]]')"
  echo "      A1 relationships: $got"
  [[ "$got" == "[('outgoing', 'phase625-a1-a2', 'phase625-a2', '$A')]" ]] && pass "A1 returns exactly A1->A2" || failm "A1 relationships unexpected"
  rels "$B" phase625-b1; want 200 "B1 relationship query"
  got="$(field "$BODY" '[(r["direction"], r["edge"]["edge_id"], r["neighbor"]["canonical_id"], r["neighbor"]["workspace_id"]) for r in d["relationships"]]')"
  echo "      B1 relationships: $got"
  [[ "$got" == "[('outgoing', 'phase625-b1-b2', 'phase625-b2', '$B')]" ]] && pass "B1 returns exactly B1->B2" || failm "B1 relationships unexpected"

  echo "  -- cross-workspace attempts --"
  req GET "/v1/workspaces/$A/nodes/phase625-b1"; want 404 "workspace A retrieving B1"
  echo "      body: $BODY"
  rels "$A" phase625-b1; want 404 "workspace A relationship query anchored on B1"
  echo "      body: $BODY"
  rels "$A" phase625-a1
  if [[ "$BODY" != *"$B"* && "$BODY" != *phase625-b* ]]; then pass "workspace A relationship query returns no B nodes/edges"; else failm "B data in A query: $BODY"; fi
  req PUT "/v1/workspaces/$A/edges/phase625-cross" "$(edge $A phase625-cross phase625-a1 phase625-b1)"; want 409 "workspace A creating edge A1->B1"
  echo "      body: $BODY"
  req PUT "/v1/workspaces/$A/edges/phase625-cross" "$(edge $A phase625-cross phase625-a1 phase625-missing)"; want 409 "(control) edge to nonexistent node gives same response"
  req POST "/v1/workspaces/$A/relationships/query" "{\"workspace_id\":\"$B\",\"node_id\":\"phase625-b1\"}"; want 422 "body workspace_id=B under path A"
  req GET "/v1/workspaces/$A/nodes/..%2F..%2F$B%2Fnodes%2Fphase625-b1"
  [[ "$STATUS" == 404 || "$STATUS" == 422 ]] && [[ "$BODY" != *phase625-b1* || "$BODY" == *'"detail"'* ]] && pass "encoded path traversal -> HTTP $STATUS" || failm "path traversal -> $STATUS $BODY"
  req GET "/v1/workspaces/$A:x/nodes/phase625-b1"; want 422 "malformed workspace id"
}

stage_check() {
  req GET "/v1/workspaces/$A/nodes/phase625-a1"; want 200 "A1 still present"
  req GET "/v1/workspaces/$A/nodes/phase625-a2"; want 200 "A2 still present"
  rels "$A" phase625-a1
  [[ "$(field "$BODY" '[r["edge"]["edge_id"] for r in d["relationships"]]')" == "['phase625-a1-a2']" ]] && pass "edge A1->A2 still present" || failm "edge A1->A2 missing: $BODY"
  req GET "/v1/workspaces/$B/nodes/phase625-b1"; want 200 "B1 still present"
  echo "      A1 projected_at=$(req GET "/v1/workspaces/$A/nodes/phase625-a1"; field "$BODY" 'd["projected_at"]')"
}

stage_cleanup() {
  req DELETE "/v1/workspaces/$A/edges/phase625-a1-a2"; want 200 "delete edge A1->A2"
  req DELETE "/v1/workspaces/$B/edges/phase625-b1-b2"; want 200 "delete edge B1->B2"
  for n in phase625-a1 phase625-a2; do req DELETE "/v1/workspaces/$A/nodes/$n"; want 200 "delete $n"; echo "      detached_edges=$(field "$BODY" 'd["detached_edges"]')"; done
  for n in phase625-b1 phase625-b2; do req DELETE "/v1/workspaces/$B/nodes/$n"; want 200 "delete $n"; echo "      detached_edges=$(field "$BODY" 'd["detached_edges"]')"; done
}

stage_verify_clean() {
  for n in phase625-a1 phase625-a2 phase625-x phase625-auth-probe; do req GET "/v1/workspaces/$A/nodes/$n"; want 404 "A/$n absent"; done
  for n in phase625-b1 phase625-b2; do req GET "/v1/workspaces/$B/nodes/$n"; want 404 "B/$n absent"; done
  rels "$A" phase625-a1; want 404 "A1 relationship query -> node gone"
  rels "$B" phase625-b1; want 404 "B1 relationship query -> node gone"
  for e in phase625-a1-a2 phase625-cross; do req DELETE "/v1/workspaces/$A/edges/$e"; want 404 "A edge $e absent"; done
  req DELETE "/v1/workspaces/$B/edges/phase625-b1-b2"; want 404 "B edge phase625-b1-b2 absent"
}

case "${1:-}" in
  health|auth|fixtures|query|check|cleanup|verify-clean) preflight ;;
esac

case "${1:-}" in
  health) stage_health ;;
  auth) stage_auth ;;
  fixtures) stage_fixtures ;;
  query) stage_query ;;
  check) stage_check ;;
  cleanup) stage_cleanup ;;
  verify-clean) stage_verify_clean ;;
  *) echo "usage: $0 health|auth|fixtures|query|check|cleanup|verify-clean"; exit 2 ;;
esac
echo "---- stage ${1}: $FAILS failure(s) at $(date -u +%Y-%m-%dT%H:%M:%SZ)"
exit $((FAILS > 0))
