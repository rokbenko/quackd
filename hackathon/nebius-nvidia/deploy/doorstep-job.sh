#!/usr/bin/env bash
# What the Serverless Job that serverless-job.sh creates runs, inside its container: the
# postman's onboard computer and the duck's, side by side. vLLM serves ONBOARD_MODEL on this
# machine's GPU, on 127.0.0.1 only, as the postman's brain. Both doorstep acts are then played
# with the postman's pilot asking that server alone and the duck's asking Token Factory, and each
# robot's log is printed when its act ends, so the job's log tells the whole story.
#
# Needs NEBIUS_API_KEY (from the MysteryBox secret), NEBIUS_MODEL (the duck's Token Factory id),
# ONBOARD_MODEL (the postman's Hugging Face id) and ONBOARD_VLLM_ARGS (vLLM's flags, split at
# spaces). QUACKD_BRANCH names the branch or commit to fetch (default main).
set -uo pipefail

work="${DOORSTEP_WORK:-/work}"
port="${ONBOARD_PORT:-8000}"
ready_within="${ONBOARD_READY_WITHIN:-1800}"
act_within="${DOORSTEP_ACT_WITHIN:-1200}"
ref="${QUACKD_BRANCH:-main}"
onboard="http://127.0.0.1:${port}/v1"
: "${NEBIUS_MODEL:?the duck's Token Factory id}" "${ONBOARD_MODEL:?the postman's Hugging Face id}"

echo "== the postman's onboard computer, standing in for its Jetson Thor =="
echo "$(uname -m), $(nvidia-smi --query-gpu=name,driver_version,memory.total --format=csv,noheader 2>&1)"
python3 -c 'import sys, torch; sys.exit(0 if torch.cuda.is_available() else "torch sees no CUDA device")' || exit 1

# Only this folder is taken from quackd's repository, and quackd itself comes from PyPI by the
# folder's own lock.
echo "== this folder, from quackd at ${ref} =="
mkdir -p "$work/folder" || exit 1
curl -fsSL "https://codeload.github.com/rokbenko/quackd/tar.gz/${ref}" \
  | tar -xz -C "$work/folder" --strip-components=3 --wildcards '*/hackathon/nebius-nvidia' \
  || exit 1
cd "$work/folder" || exit 1
python3 -m pip install --quiet --upgrade uv 2> /dev/null \
  || python3 -m pip install --quiet --upgrade --break-system-packages uv || exit 1
uv sync --frozen --quiet || exit 1

# What Token Factory answers is checked before the long wait for the GPU.
echo "== the duck's pilot, on Token Factory =="
uv run passport-nebius models || exit 1
uv run passport-nebius doorstep run --role duck --show --act appointment > /dev/null || exit 1

echo "== the postman's brain, onboard: ${ONBOARD_MODEL} =="
HF_HUB_DISABLE_PROGRESS_BARS=1 hf download "$ONBOARD_MODEL" --local-dir "$work/model" > /dev/null \
  || exit 1
read -r -a flags <<< "${ONBOARD_VLLM_ARGS:-}"
key="$(od -An -tx1 -N24 /dev/urandom | tr -d ' \n')"
# Started from the model's own folder, so a parser plugin its card names by file name is found.
(cd "$work/model" && exec env -u NEBIUS_API_KEY VLLM_API_KEY="$key" vllm serve "$work/model" \
  --served-model-name "$ONBOARD_MODEL" --host 127.0.0.1 --port "$port" ${flags[@]+"${flags[@]}"}) \
  > "$work/vllm.log" 2>&1 &
vllm=$!
trap 'kill "$vllm" 2> /dev/null; wait "$vllm" 2> /dev/null' EXIT

deadline=$((SECONDS + ready_within))
until curl -fs --max-time 5 "http://127.0.0.1:${port}/health" > /dev/null; do
  if ! kill -0 "$vllm" 2> /dev/null; then
    echo "vLLM ended before it was ready. Its last lines:"
    tail -n 60 "$work/vllm.log"
    exit 1
  fi
  if ((SECONDS >= deadline)); then
    echo "vLLM was not ready within ${ready_within} s. Its last lines:"
    tail -n 60 "$work/vllm.log"
    exit 1
  fi
  sleep 2
done
export ONBOARD_API_KEY="$key"
echo "vLLM serves ${ONBOARD_MODEL} at ${onboard}"

failed=0
for act in stranger appointment; do
  echo "== the ${act} act =="
  brake=()
  if [ "$act" = appointment ]; then
    brake=(--pause-after 20)
  fi
  timeout -k 30 "$act_within" uv run passport-nebius doorstep run --act "$act" \
    --onboard "$onboard" --onboard-model "$ONBOARD_MODEL" --print-logs ${brake[@]+"${brake[@]}"} \
    || failed=1
done
echo "== vLLM's last lines =="
tail -n 40 "$work/vllm.log"
exit "$failed"
