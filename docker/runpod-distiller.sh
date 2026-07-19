#!/usr/bin/env bash
# Programmatic lifecycle for the vLLM distiller pod on RunPod.
#
#   ./docker/runpod-distiller.sh up      launch, wait for /v1/models, print env
#   ./docker/runpod-distiller.sh status  is a helixis pod alive?
#   ./docker/runpod-distiller.sh down    delete it and print the balance
#
# A pod bills continuously, unlike serverless — `up` writes the pod id to
# runs/.runpod-pod-id so `down` always has something to delete even if the shell
# that launched it is gone. Bring it up when a judge/distill run needs it, take
# it down afterwards.
#
# Every constant below is a lesson from documentation/runbook.md §1.3:
#   * dockerEntrypoint must be a shell — bare vLLM args get exec'd as a binary
#     and the pod silently crash-loops.
#   * curl does not exist in vllm/vllm-openai; never shell out to it in the
#     start command.
#   * Right-size explicitly or RunPod hands back a $0.79/hr box for a 9B model.
set -euo pipefail

cd "$(dirname "$0")/.."
[ -f .env ] && set -a && . ./.env && set +a

: "${RUNPOD_API_KEY:?RUNPOD_API_KEY is not set (put it in .env)}"
MODEL="${RUNPOD_DISTILLER_MODEL:-nvidia/NVIDIA-Nemotron-Nano-9B-v2}"
SERVED="${HELIXIS_DISTILLER_MODEL:-nemotron-nano-9b}"
TOKEN="${HELIXIS_DISTILLER_API_KEY:-helixis-local}"
ID_FILE="runs/.runpod-pod-id"
API="https://rest.runpod.io/v1/pods"

api() { curl -s -H "Authorization: Bearer $RUNPOD_API_KEY" -H "content-type: application/json" "$@"; }

cmd_up() {
  if [ -s "$ID_FILE" ] && api "$API/$(cat "$ID_FILE")" | grep -q '"id"'; then
    echo "Pod $(cat "$ID_FILE") already exists. Use 'down' first, or 'status'." >&2
    exit 1
  fi

  echo "Launching $MODEL ..." >&2
  local body
  body=$(api -X POST "$API" -d "{
    \"name\":\"helixis-distiller\",\"imageName\":\"vllm/vllm-openai:latest\",
    \"cloudType\":\"SECURE\",\"gpuTypeIds\":[\"NVIDIA RTX A6000\",\"NVIDIA A40\"],
    \"gpuCount\":1,\"containerDiskInGb\":60,\"volumeInGb\":0,
    \"minVCPUPerGPU\":4,\"minRAMPerGPU\":24,\"ports\":[\"8000/http\"],
    \"env\":{\"HF_TOKEN\":\"${HUGGING_FACE_HUB_TOKEN:-}\"},
    \"dockerEntrypoint\":[\"/bin/sh\",\"-lc\"],
    \"dockerStartCmd\":[\"python3 -m vllm.entrypoints.openai.api_server --model $MODEL --served-model-name $SERVED --max-model-len 16384 --gpu-memory-utilization 0.90 --max-num-seqs 32 --trust-remote-code --api-key $TOKEN --host 0.0.0.0 --port 8000\"]
  }")

  local pod_id
  pod_id=$(printf '%s' "$body" | python3 -c 'import json,sys; print(json.load(sys.stdin).get("id",""))' 2>/dev/null || true)
  if [ -z "$pod_id" ]; then
    echo "Launch failed: $body" >&2
    exit 1
  fi
  mkdir -p runs && printf '%s' "$pod_id" > "$ID_FILE"
  echo "Pod $pod_id created. Waiting for vLLM (cold start is ~3-4 min)..." >&2

  local url="https://${pod_id}-8000.proxy.runpod.net/v1"
  # 15 minutes: image pull, then an ~18GB weight download.
  for _ in $(seq 1 90); do
    sleep 10
    local code
    code=$(curl -s -m 10 -o /dev/null -w '%{http_code}' -H "Authorization: Bearer $TOKEN" "$url/models" || true)
    if [ "$code" = "200" ]; then
      echo >&2
      echo "READY. Point the engine at it:" >&2
      echo "  export HELIXIS_DISTILLER_BASE_URL=$url"
      echo "  export HELIXIS_DISTILLER_MODEL=$SERVED"
      echo "  export HELIXIS_DISTILLER_API_KEY=$TOKEN"
      echo "Remember: ./docker/runpod-distiller.sh down" >&2
      return 0
    fi
    printf '.' >&2
  done
  echo >&2
  echo "Timed out waiting for $url/models. The pod is STILL BILLING — check it or run 'down'." >&2
  exit 1
}

cmd_status() {
  [ -s "$ID_FILE" ] || { echo "No pod id recorded."; return 0; }
  api "$API/$(cat "$ID_FILE")" | python3 -c '
import json, sys
try: p = json.load(sys.stdin)
except Exception: print("no such pod"); raise SystemExit
print(p.get("id"), "|", p.get("desiredStatus"), "|", p.get("costPerHr"), "$/hr")'
}

cmd_down() {
  [ -s "$ID_FILE" ] || { echo "No pod id recorded; nothing to delete."; return 0; }
  local pod_id
  pod_id=$(cat "$ID_FILE")
  api -X DELETE "$API/$pod_id" >/dev/null || true
  rm -f "$ID_FILE"
  echo "Deleted pod $pod_id."
  curl -s https://api.runpod.io/graphql -H "content-type: application/json" \
    -H "Authorization: Bearer $RUNPOD_API_KEY" \
    -d '{"query":"query { myself { clientBalance currentSpendPerHr } }"}' \
    | python3 -c 'import json,sys; m=json.load(sys.stdin)["data"]["myself"]; print(f"balance ${m[\"clientBalance\"]:.2f}, now spending ${m[\"currentSpendPerHr\"]:.2f}/hr")' 2>/dev/null || true
}

case "${1:-}" in
  up) cmd_up ;;
  status) cmd_status ;;
  down) cmd_down ;;
  *) echo "usage: $0 {up|status|down}" >&2; exit 2 ;;
esac
