#!/bin/bash
# Helixis Claw startup, run INSIDE the OpenShell sandbox.
#
# This is the old `command:` block from the `nemoclaw` compose service, moved
# into the image. It had to move: in a driver-created sandbox the OpenShell
# supervisor is PID 1 and the driver explicitly clears the image CMD
# ("Clear the image CMD so Docker does not append inherited args to the
# supervisor entrypoint" — openshell-driver-docker/src/lib.rs:2327-2332), so a
# compose-style command list has nowhere to attach. claw-init.sh execs this
# script through `openshell sandbox exec` once the sandbox reports Ready.
#
# Ordering below is load-bearing in both directions, unchanged from the compose
# version:
#   - the gateway.* token settings must come AFTER `onboard`, which would
#     otherwise overwrite auth.token;
#   - they must come BEFORE the gateway starts, because setting auth.token on a
#     RUNNING gateway sends it SIGUSR1, and its "full process restart" would
#     take down the whole container when the gateway is the main process.
#
# Idempotent: claw-init.sh may re-run this after a sandbox restart, and the
# guard below makes a second invocation a no-op rather than a port conflict.
set -u

TOKEN="${NEMOCLAW_GATEWAY_TOKEN:-helixis-local}"

# `openclaw-gateway` is the RUNNING process name; OpenClaw renames it from the
# `openclaw gateway run` invocation below. Guarding on the invocation string
# never matches, so a second call would re-run the whole chain and race the
# live gateway for port 18789.
if pgrep -f "openclaw-gateway" >/dev/null 2>&1; then
  echo "claw-start: gateway already running — nothing to do"
  exit 0
fi

echo "claw-start: onboarding openclaw"
openclaw onboard --non-interactive --accept-risk --mode local || true

openclaw config set gateway.auth.token "$TOKEN"
openclaw config set gateway.remote.url 'ws://127.0.0.1:18789'
openclaw config set gateway.remote.token "$TOKEN"
# The Control UI checks the browser's Origin header against this list and
# refuses anything unlisted with:
#
#   origin not allowed (open the Control UI from the gateway host or allow it
#   in gateway.controlUi.allowedOrigins)
#
# The loopback pair covers `make tunnel`, where the browser really is at
# localhost:18789. A PUBLIC deployment serves the same UI from its own
# hostname (https://ui.example.com), which is a different origin and is
# rejected until it is added here.
#
# NEMOCLAW_UI_ORIGIN carries that hostname in. It is set on the sandbox at
# CREATE time by claw-init.sh, so changing it needs a sandbox recreate — an
# `openclaw config set` on a running gateway is not enough on its own.
#
# Origins only: scheme://host[:port], no path. Anything else is silently
# ignored by the matcher, which looks exactly like the deny above.
UI_ORIGINS='"http://localhost:18789","http://127.0.0.1:18789"'
if [ -n "${NEMOCLAW_UI_ORIGIN:-}" ]; then
  UI_ORIGINS="${UI_ORIGINS},\"${NEMOCLAW_UI_ORIGIN}\""
  echo "claw-start: allowing Control UI origin ${NEMOCLAW_UI_ORIGIN}"
fi
openclaw config set gateway.controlUi.allowedOrigins "[${UI_ORIGINS}]" --json
openclaw config set gateway.controlUi.dangerouslyDisableDeviceAuth true --json

# Model config points at the OpenShell PLACEHOLDER, never a real key.
#
# `$api_key` is injected by OpenShell itself when the provider is attached to
# the sandbox — the variable name comes from `credentials[].name` in
# policy/helixis-inference-profile.yaml. Its value is a placeholder token
# (verified: it does NOT start with the real key's `fw_` prefix); the L7 proxy
# at $HTTPS_PROXY substitutes the real credential into the Authorization header
# on the way out, scoped to api.fireworks.ai.
#
# So HELIXIS_AGENT_API_KEY is absent from this environment entirely (Req 3.3)
# and `openclaw config get models.providers.helixis` holds no credential
# (Req 3.4), while the agent's requests still authenticate.
if [ -n "${HELIXIS_AGENT_MODEL:-}" ] && [ -n "${api_key:-}" ]; then
  HELIXIS_AGENT_PLACEHOLDER="$api_key"
  echo "claw-start: configuring model provider with placeholder credential"
  openclaw config set models.providers.helixis \
    "{\"baseUrl\":\"${HELIXIS_AGENT_BASE_URL}\",\"api\":\"openai-completions\",\"apiKey\":\"${HELIXIS_AGENT_PLACEHOLDER}\",\"models\":[{\"id\":\"${HELIXIS_AGENT_MODEL}\",\"name\":\"${HELIXIS_AGENT_MODEL}\",\"contextWindow\":131072,\"maxTokens\":8192}]}" \
    --json
  openclaw config set agents.defaults.model.primary "helixis/${HELIXIS_AGENT_MODEL}"
else
  echo "claw-start: no model/placeholder configured — leaving openclaw defaults"
fi

# Wiki sync: same 30s cadence as before. The mount is read-only, so this only
# ever reads from it.
bash /helixis/bin/wiki-sync.sh || true
( while sleep 30; do bash /helixis/bin/wiki-sync.sh || true; done ) &

# Browser TUI.
ttyd --writable --port 18790 \
  --credential "helixis:${TOKEN}" \
  openclaw tui --url ws://127.0.0.1:18789 --token "$TOKEN" &

# The gateway. NOT `exec` — unlike the compose version this is not PID 1 (the
# supervisor is), and it runs detached from the exec session that started it so
# claw-init can exit without killing it.
openclaw gateway run --bind lan --auth token \
  --token "$TOKEN" --port 18789 &

echo "claw-start: started (gateway 18789, ttyd 18790)"
