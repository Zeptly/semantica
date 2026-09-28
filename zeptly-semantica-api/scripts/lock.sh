#!/usr/bin/env sh
# Regenerate requirements.lock (hash-pinned runtime dependencies).
#
# semantica is pinned to exactly 0.6.0 and installed with --no-deps: its
# published metadata declares the full ML stack (torch, transformers, spacy,
# ...), none of which semantica.graph_store needs. The service only uses
# GraphStore + the FalkorDB backend, whose sole third-party requirement is
# the falkordb client (pinned in requirements.in).
set -eu
cd "$(dirname "$0")/.."

SEMANTICA_VERSION="0.6.0"

uv pip compile requirements.in \
  --generate-hashes \
  --python-version 3.12 \
  --no-header \
  --quiet \
  -o requirements.lock

tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT
pip download --quiet --no-deps --only-binary=:all: \
  "semantica==${SEMANTICA_VERSION}" -d "$tmp"
wheel="$(ls "$tmp"/semantica-*.whl)"
hash="$(python3 -c 'import hashlib,sys; print(hashlib.sha256(open(sys.argv[1],"rb").read()).hexdigest())' "$wheel")"

{
  echo "semantica==${SEMANTICA_VERSION} \\"
  echo "    --hash=sha256:${hash}"
  echo "    # installed with --no-deps; see scripts/lock.sh"
} >> requirements.lock

echo "requirements.lock regenerated (semantica==${SEMANTICA_VERSION}, sha256:${hash})"
