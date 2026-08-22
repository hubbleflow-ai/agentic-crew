#!/usr/bin/env bash
# Build all images Crew needs before `docker compose up`.
#
# Run from repo root:
#   ./scripts/build-images.sh
#
# Builds:
#   crew-agent:latest   · shared Python image (control plane, mocks, agents)
#   crew-sandbox:latest · per-task sandbox image (what mcp_sandbox spawns)

set -euo pipefail

cd "$(dirname "$0")/.."

echo "Building crew-agent (control plane / mocks / agents)..."
docker build -t crew-agent:latest -f docker/agent.Dockerfile .

echo "Building crew-sandbox (per-task sandboxes spawned by mcp_sandbox)..."
docker build -t crew-sandbox:latest -f docker/sandbox.Dockerfile .

echo "Done. Now run: docker compose up"
