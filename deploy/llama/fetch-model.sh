#!/usr/bin/env sh
# Download one candidate GGUF (Q4_K_M) for the rider-assistant LLM (D-121) into
# /var/lib/vehicle/models, verified against a PINNED SHA-256. The model is never committed.
#
#   sudo sh deploy/llama/fetch-model.sh --list
#   sudo sh deploy/llama/fetch-model.sh qwen2.5-1.5b      # then write /etc/vehicle/llama.env
#
# The URL names a pinned Hugging Face commit (not "main"), so the bytes cannot change under the
# hash. The hash is the file's LFS oid from https://huggingface.co/api/models/<repo>/tree/<rev>.
# An entry whose hash is the literal VERIFY is refused: look the hash up, check it, then pin it.
#
# name | HF repo | revision | file | sha256 | licence
set -eu
MODELS_DIR="${MODELS_DIR:-/var/lib/vehicle/models}"

table() {
  cat <<'TABLE'
qwen2.5-1.5b|Qwen/Qwen2.5-1.5B-Instruct-GGUF|91cad51170dc346986eccefdc2dd33a9da36ead9|qwen2.5-1.5b-instruct-q4_k_m.gguf|6a1a2eb6d15622bf3c96857206351ba97e1af16c30d7a74ee38970e434e9407e|Apache-2.0
gemma3-1b|ggml-org/gemma-3-1b-it-GGUF|f9c28bcd85737ffc5aef028638d3341d49869c27|gemma-3-1b-it-Q4_K_M.gguf|8ccc5cd1f1b3602548715ae25a66ed73fd5dc68a210412eea643eb20eb75a135|Gemma Terms of Use (https://ai.google.dev/gemma/terms)
gemma3-4b|ggml-org/gemma-3-4b-it-GGUF|d0976223747697cb51e056d85c532013931fe52e|gemma-3-4b-it-Q4_K_M.gguf|882e8d2db44dc554fb0ea5077cb7e4bc49e7342a1f0da57901c0802ea21a0863|Gemma Terms of Use (https://ai.google.dev/gemma/terms)
TABLE
}

sha256_of() {
  if command -v sha256sum >/dev/null 2>&1; then sha256sum "$1" | cut -d' ' -f1
  else shasum -a 256 "$1" | cut -d' ' -f1; fi
}

if [ "$#" -ne 1 ]; then echo "usage: fetch-model.sh --list | <name>"; exit 2; fi
if [ "$1" = "--list" ]; then
  table | while IFS='|' read -r name repo _rev file sha licence; do
    status=pinned; [ "$sha" = "VERIFY" ] && status="VERIFY (refused)"
    echo "$name  $repo/$file  $status  licence: $licence"
  done
  exit 0
fi
line=$(table | awk -F'|' -v n="$1" '$1 == n') || true
[ -n "$line" ] || { echo "unknown model '$1' (try --list)"; exit 2; }
IFS='|' read -r name repo rev file sha licence <<EOF2
$line
EOF2
case "$sha" in
  [0-9a-f]*) [ "${#sha}" -eq 64 ] || { echo "$name: malformed pinned hash, refusing"; exit 1; } ;;
  *) echo "$name: hash is '$sha', not pinned: refusing to install an unverified model"; exit 1 ;;
esac
echo "model:   $name ($repo @ $rev)"
echo "licence: $licence"
command -v curl >/dev/null 2>&1 || { echo "curl is required"; exit 1; }
mkdir -p "$MODELS_DIR"
tmp=$(mktemp "$MODELS_DIR/.fetch.XXXXXX")
trap 'rm -f "$tmp"' EXIT
curl --proto '=https' --tlsv1.2 --fail --location --retry 5 --retry-delay 5 --continue-at - \
  --output "$tmp" "https://huggingface.co/$repo/resolve/$rev/$file"
got=$(sha256_of "$tmp")
[ "$got" = "$sha" ] || { echo "SHA-256 mismatch for $file: got $got, pinned $sha"; exit 1; }
chmod 0644 "$tmp"
mv "$tmp" "$MODELS_DIR/$file"
trap - EXIT
echo "ok: $MODELS_DIR/$file (sha256 verified)"
echo "next: write /etc/vehicle/llama.env with"
echo "  LLAMA_MODEL=$MODELS_DIR/$file"
echo "  LLAMA_THREADS=2"
echo "  LLAMA_CTX=2048"
