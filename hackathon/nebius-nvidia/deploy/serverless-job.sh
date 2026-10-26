#!/usr/bin/env bash
# Play both doorstep acts as a Nebius Serverless AI Job on one GPU: a container on Nebius AI
# Cloud that runs to the end and is billed per second while it runs. It stands in for the
# postman's onboard computer, which on a real Agile ONE SN is an NVIDIA Jetson Thor and here is
# an x86 server with an NVIDIA GPU. vLLM serves a Nemotron on that GPU as the postman's brain,
# and the postman's pilot asks nothing else. The duck's pilot stays on Token Factory, since a
# Microduck's own computer is too small for a model.
#
#   bash deploy/serverless-job.sh <duck's NVIDIA id> <postman's nvidia/ Hugging Face id> \
#     <platform> <preset> <vllm serve flags...>
#
# Run it from hackathon/nebius-nvidia with the Nebius CLI installed and configured. The key
# goes in a MysteryBox secret, never on this command line: create a secret named
# nebius-api-key whose payload key is NEBIUS_API_KEY, or name another in NEBIUS_KEY_SECRET.
# HF_TOKEN_SECRET names a secret holding HF_TOKEN, for a model that needs one. QUACKD_BRANCH
# names the branch or commit the job fetches (default main), VLLM_IMAGE the image.
# Read the outcome with: nebius ai job logs <job id> --follow
set -euo pipefail

usage="usage: bash deploy/serverless-job.sh <duck's NVIDIA id> <postman's nvidia/ Hugging Face id> <platform> <preset> <vllm serve flags...>"
duck_model="${1:?$usage}"
onboard_model="${2:?$usage}"
platform="${3:?$usage}"
preset="${4:?$usage}"
shift 4
secret="${NEBIUS_KEY_SECRET:-nebius-api-key}"

# Each refusal is made here, before a job exists, since a job is billed from its first second.
refuse() {
  echo "error: $1" >&2
  exit 2
}
case "$onboard_model" in
  nvidia/?*) ;;
  *) refuse "the postman's brain is an NVIDIA model: a Hugging Face id under nvidia/, not $onboard_model" ;;
esac
for flag in "$@"; do
  case "$flag" in
    *[[:space:]]*)
      refuse "a vllm flag holds a space ($flag): the job splits the flags at spaces, so write JSON without any" ;;
    --host | --host=* | --port | --port=* | --api-key | --api-key=* | --served-model-name | --served-model-name=*)
      refuse "$flag is the job's own: vLLM serves on 127.0.0.1 with a key the job makes, under the id given" ;;
  esac
done
# quackd asks its pilot with tool_choice auto, which vLLM answers with HTTP 400 unless both are set.
case " $* " in
  *" --enable-auto-tool-choice "*) ;;
  *) refuse "the vllm flags need --enable-auto-tool-choice, since quackd asks with tool_choice auto" ;;
esac
case " $* " in
  *" --tool-call-parser "* | *" --tool-call-parser="*) ;;
  *) refuse "the vllm flags need --tool-call-parser, the one the model's card names" ;;
esac

cd "$(dirname "$0")/.."

extra=()
if [ -n "${HF_TOKEN_SECRET:-}" ]; then
  extra+=(--env-secret "HF_TOKEN=${HF_TOKEN_SECRET}")
fi

nebius ai job create \
  --name "doorstep-onboard-$(date +%Y%m%d-%H%M%S)" \
  --image "${VLLM_IMAGE:-vllm/vllm-openai:v0.32.0}" \
  --inject-file "deploy/doorstep-job.sh:/job/doorstep-job.sh" \
  --container-command bash \
  --args /job/doorstep-job.sh \
  --platform "$platform" \
  --preset "$preset" \
  --disk-size 100Gi \
  --timeout 2h \
  --env "NEBIUS_MODEL=${duck_model}" \
  --env "ONBOARD_MODEL=${onboard_model}" \
  --env "ONBOARD_VLLM_ARGS=$*" \
  --env "QUACKD_BRANCH=${QUACKD_BRANCH:-main}" \
  --env-secret "NEBIUS_API_KEY=${secret}" \
  ${extra[@]+"${extra[@]}"}
