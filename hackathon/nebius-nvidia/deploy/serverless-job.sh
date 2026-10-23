#!/usr/bin/env bash
# Play both doorstep acts as a Nebius Serverless AI Job: a container on Nebius AI Cloud that
# runs to the end and is billed per second while it runs. No GPU: the robots are simulated and
# their pilots run on Token Factory, so the job takes the smallest CPU preset that fits.
#
#   bash deploy/serverless-job.sh <NVIDIA model id> [branch]
#
# Run it from hackathon/nebius-nvidia with the Nebius CLI installed and configured. The key
# goes in a MysteryBox secret, never on this command line: create a secret named
# nebius-api-key whose payload key is NEBIUS_API_KEY, or name another in NEBIUS_KEY_SECRET.
# Read the outcome with: nebius ai job logs <job id> --follow
set -euo pipefail

model="${1:?usage: bash deploy/serverless-job.sh <NVIDIA model id from passport-nebius models> [branch]}"
branch="${2:-main}"
secret="${NEBIUS_KEY_SECRET:-nebius-api-key}"

cd "$(dirname "$0")/.."

# A CPU-only job needs at least 20 GiB of container disk; 30 leaves room for the sync.
nebius ai job create \
  --name "doorstep-$(date +%Y%m%d-%H%M%S)" \
  --image ghcr.io/astral-sh/uv:python3.12-bookworm \
  --inject-file "deploy/doorstep-job.sh:/work/doorstep-job.sh" \
  --container-command bash \
  --args /work/doorstep-job.sh \
  --platform cpu-d3 \
  --preset 4vcpu-16gb \
  --disk-size 30Gi \
  --timeout 1h \
  --env "NEBIUS_MODEL=${model}" \
  --env "QUACKD_BRANCH=${branch}" \
  --env-secret "NEBIUS_API_KEY=${secret}"
