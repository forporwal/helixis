#!/bin/bash
# Provision Helixis Claw as an OpenShell sandbox.
#
# Replaces the old `nemoclaw` compose service. That service ran the agent
# BESIDE the gateway: `openshell sandbox list` reported "No sandboxes found",
# so none of the confinement the project claimed was in the path. This asks the
# gateway to create the sandbox instead, which is what puts the supervisor,
# Landlock/seccomp/netns, and L7 egress in front of the agent.
#
# Idempotent throughout (Req 4.3): `compose up` on a live stack must reconcile,
# never fail and never prompt. Each step is guarded by a `get`/`list` check
# rather than `|| true`, so a real failure still surfaces instead of being
# swallowed.
set -euo pipefail

SANDBOX="${HELIXIS_SANDBOX:-helixis}"
IMAGE="${HELIXIS_SANDBOX_IMAGE:-helixis-nemoclaw:latest}"
ROOT="${HELIXIS_ROOT:?HELIXIS_ROOT must be the absolute host path of the repo}"
# Templates are the version-controlled inputs; the rendered copies under /tmp are
# what actually reach the gateway (see render_templates below).
POLICY_TEMPLATE="${HELIXIS_POLICY:-/policy/helixis-claw.yaml}"
PROFILE_TEMPLATE="${HELIXIS_PROVIDER_PROFILE:-/policy/helixis-inference-profile.yaml}"
POLICY=/tmp/helixis-claw.rendered.yaml
PROFILE=/tmp/helixis-inference-profile.rendered.yaml

log() { echo "claw-init: $*"; }

# ---------------------------------------------------------------------------
# Derive the model host and path prefix from HELIXIS_AGENT_BASE_URL.
#
# This is what keeps a provider switch to ONE variable in .env. Both the policy
# and the provider profile need the host, and the policy also needs the path
# prefix — which is not constant across providers:
#
#   https://api.fireworks.ai/inference/v1  -> host api.fireworks.ai  path /inference/v1
#   https://api.featherless.ai/v1          -> host api.featherless.ai path /v1
#   http://vllm:8000/v1                    -> host vllm              path /v1
#
# Hardcoding either one denies every inference call while the policy still looks
# correct, which is a genuinely hard failure to read. Deriving both means a
# provider change cannot leave them inconsistent with each other.
render_templates() {
  local url="${HELIXIS_AGENT_BASE_URL:-}"
  [ -n "$url" ] || { log "FATAL: HELIXIS_AGENT_BASE_URL is unset — cannot render policy"; exit 1; }

  local rest="${url#*://}"          # strip scheme
  local hostport="${rest%%/*}"      # host[:port]
  MODEL_HOST="${hostport%%:*}"      # drop any :port — the policy states port separately
  local path="/${rest#*/}"
  [ "$rest" = "$hostport" ] && path=""   # no path component at all
  MODEL_PATH="${path%/}"                 # no trailing slash; rules append /chat/completions

  [ -n "$MODEL_HOST" ] || { log "FATAL: could not derive host from $url"; exit 1; }
  log "model host=$MODEL_HOST path=$MODEL_PATH (from HELIXIS_AGENT_BASE_URL)"

  sed -e "s|__MODEL_HOST__|${MODEL_HOST}|g" -e "s|__MODEL_PATH__|${MODEL_PATH}|g" \
      "$POLICY_TEMPLATE" > "$POLICY"
  sed -e "s|__MODEL_HOST__|${MODEL_HOST}|g" \
      "$PROFILE_TEMPLATE" > "$PROFILE"

  # A leftover placeholder means the template drifted from this function.
  # `deny_unknown_fields` would not catch it — the policy would apply cleanly and
  # deny everything, so fail here where the cause is still obvious.
  if grep -q "__MODEL_" "$POLICY" "$PROFILE"; then
    log "FATAL: unsubstituted placeholder remains after rendering"; exit 1
  fi
}

render_templates

# Wait for the gateway to accept connections. `depends_on` only guarantees the
# CONTAINER started, not that the gRPC listener is up, and the gateway spends a
# few seconds loading its JWT bundle and reconciling existing sandboxes. Without
# this, `compose up` after any gateway change fails on "tcp connect error /
# Connection refused" — a race that looks like a real provisioning failure.
log "waiting for gateway at ${OPENSHELL_GATEWAY_ENDPOINT:-unset}"
for _ in $(seq 1 60); do
  openshell status >/dev/null 2>&1 && break
  sleep 2
done
openshell status >/dev/null 2>&1 || { log "FATAL: gateway unreachable"; exit 1; }
log "gateway reachable"

# ---------------------------------------------------------------------------
# 1. Provider + placeholder credential (Req 3.1)
#
# Registered BEFORE the sandbox, because `sandbox create --provider` attaches it
# at creation time. The real key is handed to the gateway here and never to the
# sandbox; the sandbox receives only the placeholder, and the L7 proxy
# substitutes the real value at egress (Req 3.2).
# ---------------------------------------------------------------------------
PROVIDER="${HELIXIS_PROVIDER:-helixis-inference}"
PROVIDER_ARGS=()

if [ -n "${HELIXIS_AGENT_API_KEY:-}" ]; then
  # The profile must exist before the provider can name it as --type. OpenShell
  # ships profiles for aws-bedrock, deepinfra, google-vertex-ai and nvidia only,
  # none of which is the agent's actual endpoint, so we register a custom one.
  #
  # `import` is NOT an upsert — it fails with "custom provider profile already
  # exists" on the second run — so the two paths are split explicitly rather
  # than swallowed with `|| true`, which would also hide a genuine lint failure.
  if RV=$(openshell provider profile export helixis-inference 2>/dev/null \
            | sed -n 's/^resource_version:[[:space:]]*//p'); [ -n "${RV:-}" ]; then
    # `update` is optimistic-concurrency checked: it rejects a file without a
    # non-zero resource_version ("export the current profile before editing
    # it"). The version-controlled profile deliberately does NOT carry one — it
    # would be stale the moment anything else touched the profile — so the
    # CURRENT version is read back and stamped onto a copy at apply time.
    log "provider profile exists at resource_version $RV — updating from $PROFILE"
    sed '/^resource_version:/d' "$PROFILE" > /tmp/profile.yaml
    echo "resource_version: $RV" >> /tmp/profile.yaml
    openshell provider profile update helixis-inference --file /tmp/profile.yaml
  else
    log "importing provider profile $PROFILE"
    openshell provider profile import --file "$PROFILE"
  fi

  if openshell provider get "$PROVIDER" >/dev/null 2>&1; then
    log "provider $PROVIDER exists — updating credential"
    openshell provider update "$PROVIDER" \
      --credential "api_key=${HELIXIS_AGENT_API_KEY}"
  else
    log "creating provider $PROVIDER"
    openshell provider create --name "$PROVIDER" --type helixis-inference \
      --credential "api_key=${HELIXIS_AGENT_API_KEY}"
  fi
  PROVIDER_ARGS=(--provider "$PROVIDER")
else
  log "WARNING: HELIXIS_AGENT_API_KEY unset — no provider registered, agent will run unconfigured"
fi

# ---------------------------------------------------------------------------
# 2. The sandbox itself (Req 1.1, 1.2)
#
# --approval-mode manual is load-bearing: it routes agent-authored policy
# proposals to the human-approval flow the dashboard already renders, rather
# than auto-approving them.
#
# Mounts go through --driver-config-json because `sandbox create` has no
# --mount/--volume flag.
#
# NAMED VOLUMES, NOT HOST BIND MOUNTS, and this is not a style preference.
# A Landlock-confined process on Docker Desktop for macOS cannot read a host
# bind mount: the host fs arrives as `fakeowner` (Docker Desktop's macOS mount
# shim) where Landlock rules do not grant access, so every `open` under the
# mount returns EACCES even though the mount itself is correct and `stat`
# succeeds. Measured both ways on this stack — a bind mount at /helixis/wiki was
# denied, a named volume at the same target under the same policy read fine.
#
# The host still needs to see this data, so the `claw-sync` compose service
# bridges volume and host: it holds ordinary bind mounts (it is not confined by
# Landlock) and copies host wiki -> volume, and sessions volume -> host. That is
# the cost of this workaround: the wiki reaches the agent on a sync interval
# rather than instantly.
# ---------------------------------------------------------------------------
MOUNTS=$(cat <<JSON
{"docker":{"mounts":[
  {"type":"volume","source":"${HELIXIS_WIKI_VOLUME:-helixis-wiki}","target":"/helixis/wiki","read_only":true},
  {"type":"volume","source":"${HELIXIS_SESSIONS_VOLUME:-helixis-claw-sessions}","target":"/sandbox/.openclaw/agents","read_only":false}
]}}
JSON
)

[ -f "$POLICY" ] || { log "FATAL: rendered policy $POLICY missing"; exit 1; }

if openshell sandbox get "$SANDBOX" >/dev/null 2>&1; then
  log "sandbox $SANDBOX already exists — reconciling"

  # Re-apply policy (Req 2.5). On a LIVE sandbox the gateway accepts only
  # additive filesystem changes and rejects any landlock/process change at all
  # ("cannot be changed on a live sandbox (applied at startup)"), so this
  # succeeds precisely because it is the same file the sandbox was created with.
  # An edited policy that removes a path will fail loudly here — which is the
  # correct outcome: the running agent is still confined by the OLD policy, and
  # a silent success would misreport that. Recreate the sandbox to widen or
  # narrow the filesystem or landlock stanzas.
  log "re-applying policy $POLICY to $SANDBOX"
  openshell policy set "$SANDBOX" --policy "$POLICY" --wait
else
  # --policy at CREATE time, not after. filesystem_policy, landlock and process
  # are applied at sandbox startup; setting them afterwards is rejected outright
  # for landlock/process, and for filesystem the gateway's own comment is that
  # "the enriched paths only take effect on the next restart". Creating with the
  # policy is the only way the agent's first process is confined by it.
  # `-- true` is not decoration. With no trailing command `sandbox create`
  # defaults to an INTERACTIVE SHELL once the sandbox is ready, so a
  # non-interactive run hangs at "Waiting for supervisor relay" forever and
  # `compose up` never returns (Req 4.3). A command that exits immediately lets
  # create return; the agent is started separately by the exec below, detached,
  # so its lifetime is not tied to this one-shot container.
  # The --env values below are the sandbox's ENTIRE environment contribution,
  # and every one is deliberately non-secret: an endpoint URL, a model name, and
  # the local Control-UI token that is a published dev default. The agent's
  # actual credential never appears here — OpenShell injects a placeholder as
  # `api_key` when the provider is attached, and claw-start.sh reads that.
  # Anything added to this list is a decision that the agent may read it; if
  # that is ever in doubt, the answer is no.
  #
  # They are set at CREATE time because `sandbox exec` does not carry the
  # caller's environment — the earlier attempt to pass them through exec left
  # the agent silently unconfigured.
  log "creating sandbox $SANDBOX from $IMAGE with policy $POLICY"
  openshell sandbox create \
    --name "$SANDBOX" \
    --from "$IMAGE" \
    --approval-mode manual \
    --policy "$POLICY" \
    --driver-config-json "$MOUNTS" \
    --env "HELIXIS_AGENT_BASE_URL=${HELIXIS_AGENT_BASE_URL:-}" \
    --env "HELIXIS_AGENT_MODEL=${HELIXIS_AGENT_MODEL:-}" \
    --env "NEMOCLAW_GATEWAY_TOKEN=${NEMOCLAW_GATEWAY_TOKEN:-helixis-local}" \
    --env "NEMOCLAW_UI_ORIGIN=${NEMOCLAW_UI_ORIGIN:-}" \
    ${PROVIDER_ARGS[@]+"${PROVIDER_ARGS[@]}"} \
    --no-tty \
    -- true
fi

# ---------------------------------------------------------------------------
# 4. Start the agent (Req 1.4)
#
# claw-start.sh guards on an already-running gateway, so re-running this after a
# sandbox restart restarts the agent rather than double-starting it.
#
# NO `< /dev/null` here, deliberately. The obvious daemonizing idiom fails with
# "bash: /dev/null: Permission denied" because Landlock is applied to this
# process and /dev is not in the policy's allowed paths. Redirecting stdout and
# stderr to /tmp (which IS read_write) is enough to detach the process; adding
# /dev to the policy just to satisfy an idiom would widen the sandbox for no
# gain. `setsid` is what actually detaches it from this exec session.
# ---------------------------------------------------------------------------
log "starting agent inside $SANDBOX"
openshell sandbox exec -n "$SANDBOX" --no-tty -- \
  bash -lc 'setsid bash /helixis/bin/claw-start.sh > /tmp/claw-start.log 2>&1 & sleep 3; cat /tmp/claw-start.log'

log "done — sandbox $SANDBOX provisioned"
