#!/usr/bin/env bash
# Verify the host and .env can actually bring the stack up, BEFORE any build.
#
# Every check here exists because its absence produces a failure that looks like
# something else:
#   - a missing AUTH_SECRET still renders /login with a 200; it fails only when
#     you actually submit credentials, as an [auth][error] MissingSecret in the
#     container log and a bounce back to the login page
#   - a missing helixis-nemoclaw image surfaces as a claw-init crash deep in a
#     compose log, several minutes into a build
#   - HELIXIS_ROOT resolving wrong surfaces as an agent with an empty wiki
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

FAIL=0
WARN=0
ok()   { printf '  \033[32mok\033[0m    %s\n' "$*"; }
warn() { printf '  \033[33mwarn\033[0m  %s\n' "$*"; WARN=$((WARN+1)); }
bad()  { printf '  \033[31mFAIL\033[0m  %s\n' "$*"; FAIL=$((FAIL+1)); }

echo "helixis preflight — $ROOT"
echo
echo "host:"

if command -v docker >/dev/null 2>&1; then
  if docker info >/dev/null 2>&1; then
    ok "docker daemon reachable"
  else
    bad "docker is installed but the daemon is not responding (start Docker Desktop / dockerd)"
  fi
else
  bad "docker not found on PATH"
fi

if docker compose version >/dev/null 2>&1; then
  ok "docker compose v2 available"
else
  bad "docker compose v2 plugin missing (the v1 'docker-compose' binary will not work — this file uses the v2 'path:' env_file syntax)"
fi

# Disk. The four images total ~11GB and the sandbox image alone is 5.4GB.
AVAIL_KB=$(df -Pk "$ROOT" | awk 'NR==2 {print $4}')
AVAIL_GB=$((AVAIL_KB / 1024 / 1024))
if [ "$AVAIL_GB" -lt 20 ]; then
  warn "only ${AVAIL_GB}GB free on this filesystem — the image set needs ~15GB"
else
  ok "${AVAIL_GB}GB free"
fi

echo
echo "repo:"

# AutomationBench is a pinned git dependency, not a published package, so the
# engine build needs to be able to reach GitHub.
if git ls-remote --exit-code https://github.com/zapier/AutomationBench HEAD >/dev/null 2>&1; then
  ok "AutomationBench upstream reachable"
else
  bad "cannot reach github.com/zapier/AutomationBench — the engine build installs it from git and will fail"
fi

for f in docker-compose.yml docker/nemoclaw.Dockerfile policy/helixis-claw.yaml policy/helixis-inference-profile.yaml; do
  [ -f "$f" ] && ok "$f" || bad "$f missing"
done

echo
echo "config (.env):"

if [ ! -f .env ]; then
  bad ".env missing — run: cp .env.example .env"
else
  ok ".env present"

  # Duplicate assignments are LAST-WINS in both shell sourcing and compose's
  # env_file. A trailing empty duplicate therefore silently overrides a
  # filled-in value above it, and nothing logs the override — the symptom is a
  # variable that is "obviously set" in the file but empty at runtime. Checked
  # before sourcing, because sourcing is what hides the problem.
  dupes=$(grep -oE '^[A-Z_][A-Z0-9_]*=' .env | sort | uniq -d | tr -d '=')
  if [ -n "$dupes" ]; then
    for d in $dupes; do
      last=$(grep -E "^${d}=" .env | tail -1 | cut -d= -f2-)
      if [ -z "$last" ]; then
        bad "$d is assigned more than once in .env and the LAST one is empty — it overrides the value above it"
      else
        warn "$d is assigned more than once in .env — the last one wins"
      fi
    done
  else
    ok "no duplicate assignments in .env"
  fi

  set -a; . ./.env 2>/dev/null || true; set +a

  # THE gap that breaks sign-in, and it is invisible from the outside:
  # next-auth v5 throws MissingSecret when AUTH_SECRET is unset, but /login
  # still returns 200. Verified against this image — the failure appears only
  # on submit, so a port check or an HTTP probe both report the app "healthy"
  # while nobody can actually log in.
  if [ -z "${HELIXIS_DASHBOARD_AUTH_SECRET:-}" ]; then
    bad "HELIXIS_DASHBOARD_AUTH_SECRET is unset — compose maps it to AUTH_SECRET; /login renders but sign-in fails with MissingSecret. Fix: make auth-secret"
  else
    ok "HELIXIS_DASHBOARD_AUTH_SECRET set"
  fi

  # Placeholders ship in .env.example in an unusable state.
  case "${HELIXIS_AUTH_EMAIL:-}" in
    ""|"[EMAIL_ADDRESS]") bad "HELIXIS_AUTH_EMAIL unset or still the placeholder — sign-in fails closed" ;;
    *) ok "HELIXIS_AUTH_EMAIL set" ;;
  esac
  case "${HELIXIS_AUTH_PASSWORD:-}" in
    ""|"[PASSWORD]") bad "HELIXIS_AUTH_PASSWORD unset or still the placeholder — sign-in fails closed" ;;
    *) ok "HELIXIS_AUTH_PASSWORD set" ;;
  esac

  # claw-init hard-exits on an unset base URL: it cannot render the policy
  # without deriving the model host from it.
  if [ -z "${HELIXIS_AGENT_BASE_URL:-}" ]; then
    bad "HELIXIS_AGENT_BASE_URL unset — claw-init.sh exits 1 (it derives the egress policy host from this)"
  elif [ "${HELIXIS_AGENT_BASE_URL}" = "fake://offline" ]; then
    warn "HELIXIS_AGENT_BASE_URL=fake://offline — engine runs offline; the interactive agent will not answer"
  else
    ok "HELIXIS_AGENT_BASE_URL=${HELIXIS_AGENT_BASE_URL}"
  fi

  if [ -z "${HELIXIS_AGENT_API_KEY:-}" ]; then
    warn "HELIXIS_AGENT_API_KEY unset — claw-init registers no provider and the agent runs unconfigured"
  else
    ok "HELIXIS_AGENT_API_KEY set (${#HELIXIS_AGENT_API_KEY} chars)"
  fi

  [ -n "${HELIXIS_AGENT_MODEL:-}" ] && ok "HELIXIS_AGENT_MODEL=${HELIXIS_AGENT_MODEL}" \
    || warn "HELIXIS_AGENT_MODEL unset"
fi

echo
echo "ports:"
# Bound ports are the most common cause of a confusing `compose up` failure,
# and port 3000 in particular is routinely held by a stray `next start`.
for spec in "${HELIXIS_DASHBOARD_PORT:-3000}:dashboard" "${OPENSHELL_PORT:-8080}:openshell" \
            "${OPENSHELL_HEALTH_PORT:-8081}:openshell-health" "${NEMOCLAW_UI_PORT:-18789}:control-ui" \
            "${NEMOCLAW_TUI_PORT:-18790}:tui"; do
  port="${spec%%:*}"; name="${spec##*:}"
  if lsof -nP -iTCP:"$port" -sTCP:LISTEN >/dev/null 2>&1; then
    # Ours or a stranger's? A running helixis stack is fine; anything else is not.
    holder=$(lsof -nP -iTCP:"$port" -sTCP:LISTEN -Fc 2>/dev/null | sed -n 's/^c//p' | head -1)
    # Docker Desktop publishes ports via `com.docker.backend`; Linux via
    # `dockerd`/`docker-proxy`. All of them mean "a container holds this",
    # which compose reconciles rather than fails on.
    case "$holder" in
      com.docker.*|docker|dockerd|docker-proxy|vpnkit*)
        ok "port $port ($name) held by docker — existing stack, will be reconciled" ;;
      *)
        bad "port $port ($name) is held by '$holder', not docker — compose up will fail" ;;
    esac
  else
    ok "port $port ($name) free"
  fi
done

echo
echo "images:"
if docker image inspect helixis-nemoclaw:latest >/dev/null 2>&1; then
  ok "helixis-nemoclaw:latest built"
else
  warn "helixis-nemoclaw:latest not built — NO compose service builds it; 'make build' does. claw-init fails without it."
fi

echo
if [ "$FAIL" -gt 0 ]; then
  printf '\033[31m%d check(s) failed\033[0m, %d warning(s)\n' "$FAIL" "$WARN"
  exit 1
fi
printf '\033[32mpreflight passed\033[0m (%d warning(s))\n' "$WARN"
