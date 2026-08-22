#!/usr/bin/env bash
# Push the keys from .env into the cluster as a Secret.
#
# The Secret is built here rather than committed as a manifest · the .env file
# is already gitignored, and this keeps exactly one copy of each key on disk.
set -euo pipefail
cd "$(dirname "$0")/.."

[[ -f .env ]] || { echo "no .env · copy .env.example and fill it in"; exit 1; }

# shellcheck disable=SC1091
set -a; source .env; set +a

kubectl create secret generic crew-secrets \
  --namespace crew \
  --from-literal=GOOGLE_API_KEY="${GOOGLE_API_KEY:-}" \
  --from-literal=E2B_API_KEY="${E2B_API_KEY:-}" \
  --from-literal=LANGSMITH_API_KEY="${LANGSMITH_API_KEY:-}" \
  --dry-run=client -o yaml | kubectl apply -f -

echo "crew-secrets updated · $(kubectl get secret crew-secrets -n crew -o jsonpath='{.data}' | tr ',' '\n' | wc -l | tr -d ' ') keys"
