#!/usr/bin/env bash
# Remove everything. One namespace holds all of it, so this is complete.
set -euo pipefail
kubectl delete namespace crew --wait=false
echo "namespace 'crew' deleting · images remain in minikube's daemon"
echo "redis-data and crew-workspace go with it · projects and transcripts are gone"
