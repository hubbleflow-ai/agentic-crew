#!/usr/bin/env bash
# Build the images into minikube's daemon and apply every manifest.
#
# Safe to re-run · everything here is declarative, and the rollout restarts
# pick up rebuilt images that kept the same tag (which `kubectl apply` alone
# will not do, because the pod spec text has not changed).
set -euo pipefail
cd "$(dirname "$0")/.."

command -v minikube >/dev/null || { echo "minikube not found"; exit 1; }
minikube status >/dev/null 2>&1 || { echo "minikube is not running · 'minikube start'"; exit 1; }

echo "==> building images into minikube's docker daemon"
eval "$(minikube docker-env)"
docker build -q -f docker/base.Dockerfile    -t crew-base:dev    . >/dev/null
echo "    crew-base:dev"
docker build -q -f docker/browser.Dockerfile -t crew-browser:dev . >/dev/null
echo "    crew-browser:dev"

echo "==> applying manifests"
kubectl apply -f deploy/ >/dev/null
echo "    namespace, rbac, quota, workspace, config, services, observability"

echo "==> pushing secrets from .env"
./scripts/k8s-secrets.sh >/dev/null
echo "    crew-secrets"

echo "==> restarting deployments so they pick up the rebuilt images"
kubectl rollout restart -n crew deployment >/dev/null
kubectl rollout status  -n crew deployment/control-plane --timeout=180s >/dev/null
echo "    ready"

echo
kubectl get pods -n crew --no-headers -o custom-columns=NAME:.metadata.name,STATUS:.status.phase
cat <<'EOF'

Port-forward what you want to look at:

  kubectl port-forward -n crew svc/control-plane 8000:8000   # the API
  kubectl port-forward -n crew svc/grafana       3000:3000   # logs
  kubectl port-forward -n crew svc/phoenix       6006:6006   # model traces
  kubectl port-forward -n crew svc/loki          3100:3100   # log queries

Then start a project:

  curl -X POST localhost:8000/projects -H 'content-type: application/json' \
       -d '{"request":"Add a /healthz endpoint to the payments service."}'
EOF
