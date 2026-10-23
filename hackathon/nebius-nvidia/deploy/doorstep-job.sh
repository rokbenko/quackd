#!/usr/bin/env bash
# What the Serverless Job that serverless-job.sh creates runs, inside its container: quackd at
# QUACKD_BRANCH, this folder synced from its own lock, the NVIDIA models the key can use, then
# both doorstep acts with each robot's log printed, so the job's log tells the whole story.
# Needs NEBIUS_API_KEY (from the MysteryBox secret) and NEBIUS_MODEL in the environment.
set -uo pipefail

branch="${QUACKD_BRANCH:-main}"
git clone --quiet --depth 1 --branch "$branch" https://github.com/rokbenko/quackd.git /work/quackd || exit 1
cd /work/quackd/hackathon/nebius-nvidia || exit 1
uv sync --frozen --quiet || exit 1
uv run passport-nebius models || exit 1

failed=0
for act in stranger appointment; do
  echo "== the ${act} act =="
  uv run passport-nebius doorstep run --act "$act" --print-logs || failed=1
done
exit "$failed"
