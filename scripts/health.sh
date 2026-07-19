#!/usr/bin/env bash
# Post-bring-up smoke check. Mirrors the demo-day pre-flight in
# documentation/runbook.md §6, plus the sandbox/tunnel checks from §1.6.
#
# Exits non-zero if anything a human would call "the stack is up" is not true,
# so it can gate `make e2e` rather than just printing for a person to read.
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
[ -f .env ] && { set -a; . ./.env 2>/dev/null || true; set +a; }

DASH_PORT="${HELIXIS_DASHBOARD_PORT:-3000}"
UI_PORT="${NEMOCLAW_UI_PORT:-18789}"
TUI_PORT="${NEMOCLAW_TUI_PORT:-18790}"
TOKEN="${NEMOCLAW_GATEWAY_TOKEN:-helixis-local}"

FAIL=0
ok()  { printf '  \033[32mok\033[0m    %s\n' "$*"; }
bad() { printf '  \033[31mFAIL\033[0m  %s\n' "$*"; FAIL=$((FAIL+1)); }

# Poll rather than probe once: claw-forward re-establishes tunnels on a ~15s
# sweep, so a single curl right after `up` reports a false negative.
wait_http() { # url expected_code timeout_s [curl_args...]
  local url="$1" want="$2" timeout="$3"; shift 3
  local deadline=$((SECONDS + timeout)) code=""
  while [ $SECONDS -lt $deadline ]; do
    code=$(curl -s -o /dev/null -w '%{http_code}' --max-time 5 "$@" "$url" 2>/dev/null)
    [ "$code" = "$want" ] && { echo "$code"; return 0; }
    sleep 3
  done
  echo "${code:-000}"; return 1
}

echo "helixis health"
echo
echo "containers:"
DASH_RUNNING=0
# `ps -a`, not `ps`: without it an exited container reports as absent, so a
# service that started and died is indistinguishable from one never created.
for svc in openshell claw-sync claw-forward dashboard; do
  state=$(docker compose ps -a --format '{{.State}}' "$svc" 2>/dev/null | head -1)
  case "$state" in
    running) ok "$svc running"; [ "$svc" = dashboard ] && DASH_RUNNING=1 ;;
    "")      bad "$svc not created" ;;
    *)       bad "$svc is '$state' — docker compose logs $svc" ;;
  esac
done

# One-shots must have SUCCEEDED, not merely have exited.
for svc in openshell-certs claw-init; do
  code=$(docker compose ps -a --format '{{.ExitCode}}' "$svc" 2>/dev/null | head -1)
  if [ "$code" = "0" ]; then ok "$svc completed cleanly"
  elif [ -z "$code" ]; then bad "$svc never ran"
  else bad "$svc exited $code — see: docker compose logs $svc"; fi
done

echo
echo "sandbox:"
# `openshell` is not on the host PATH; the forwarder container carries the CLI.
if phase=$(docker compose exec -T claw-forward openshell sandbox list 2>/dev/null \
             | awk '$1=="helixis"{print $NF}' | sed 's/\x1b\[[0-9;]*m//g'); [ -n "$phase" ]; then
  [ "$phase" = "Ready" ] && ok "sandbox helixis: Ready" || bad "sandbox helixis: $phase (expected Ready)"
else
  bad "sandbox 'helixis' not found — claw-init did not provision it"
fi

# The spec-01 wiki->agent loop. Two distinct facts, and conflating them gives a
# false alarm on every cold start:
#
#   (a) is the mount READABLE?  A Landlock denial surfaces as an empty listing
#       rather than an error, so an empty dir alone proves nothing. Reading the
#       wiki root — which always contains skills/ and pages/ — separates
#       "denied" from "mounted but empty".
#   (b) has anything been LEARNED yet?  Zero skills on a freshly wiped wiki is
#       correct, not broken. Only host-has-N-but-agent-sees-0 is a real fault.
# `openshell sandbox exec` has been observed to hang indefinitely rather than
# error, which would wedge this whole script. Wrapped in a portable watchdog:
# coreutils `timeout` is not present on macOS by default, so the call is
# backgrounded and killed on deadline.
with_timeout() { # seconds cmd...
  local secs="$1"; shift
  local out; out=$(mktemp)
  ( "$@" >"$out" 2>/dev/null ) & local pid=$!
  local waited=0
  while kill -0 "$pid" 2>/dev/null; do
    [ "$waited" -ge "$secs" ] && { kill -9 "$pid" 2>/dev/null; wait "$pid" 2>/dev/null; rm -f "$out"; return 124; }
    sleep 1; waited=$((waited+1))
  done
  wait "$pid" 2>/dev/null
  cat "$out"; rm -f "$out"
}

agent_ls() {
  with_timeout 30 docker compose exec -T claw-forward \
    openshell sandbox exec -n "${HELIXIS_SANDBOX:-helixis}" --no-tty -- \
    sh -c "ls /helixis/wiki/$1 2>/dev/null | wc -l" | tr -dc '0-9'
}

root_n=$(agent_ls "")
if [ -z "$root_n" ]; then
  bad "could not query the sandbox (openshell sandbox exec timed out or failed) — the agent may still be fine; retry, or: docker compose logs claw-forward"
elif [ "$root_n" -eq 0 ] 2>/dev/null; then
  bad "agent cannot read /helixis/wiki at all — mount missing or Landlock denial"
else
  ok "agent can read /helixis/wiki ($root_n entries)"

  host_n=$(ls -1 wiki/skills 2>/dev/null | wc -l | tr -d ' ')
  seen_n=$(agent_ls skills)
  seen_n=${seen_n:-0}
  if [ "$host_n" -eq 0 ] 2>/dev/null; then
    ok "no skills yet on host or in agent — expected on a cold start; the loop populates after the first train cycle"
  elif [ "$seen_n" -gt 0 ] 2>/dev/null; then
    ok "agent sees $seen_n/$host_n skill(s) at /helixis/wiki/skills"
  else
    bad "host has $host_n skill(s) but the agent sees 0 — wiki->agent loop broken (Landlock denial, or claw-sync has not swept yet; it runs every ${HELIXIS_SYNC_INTERVAL:-15}s)"
  fi
fi

echo
echo "endpoints:"
# 150s, not 60s. Two waits stack after a sandbox RECREATE: claw-forward
# re-establishes its tunnels on a ~15s sweep, and only then does the agent boot
# inside the sandbox — openclaw onboard, config writes, then the gateway itself,
# measured at ~40s. A 60s budget expires mid-boot and reports "tunnel down" for
# a stack that is merely still starting, which is the single most misleading
# line this script can print.
code=$(wait_http "http://localhost:${UI_PORT}/" 200 150) \
  && ok "control UI :${UI_PORT} -> 200" \
  || bad "control UI :${UI_PORT} -> ${code} (agent still booting, or claw-forward tunnel down: docker compose logs claw-forward)"

# The TUI is ttyd, which starts before the gateway does, so it needs less
# budget — but it is behind the same tunnel, so it gets more than the old 30s.
code=$(wait_http "http://localhost:${TUI_PORT}/" 200 90 -u "helixis:${TOKEN}") \
  && ok "TUI :${TUI_PORT} -> 200" \
  || bad "TUI :${TUI_PORT} -> ${code}"

# If the container is not running, something ELSE is answering on this port —
# typically a local `next dev` started by hand. Probing it would report a green
# dashboard while the deployed one is down, so say so plainly instead. This is
# not hypothetical: it is exactly what a stray dev server looks like.
if [ "$DASH_RUNNING" = "0" ]; then
  holder=$(lsof -nP -iTCP:"${DASH_PORT}" -sTCP:LISTEN -Fc 2>/dev/null | sed -n 's/^c//p' | head -1)
  if [ -n "$holder" ]; then
    bad "port ${DASH_PORT} is served by '${holder}', NOT the dashboard container (which is not running) — skipping endpoint checks, they would test the wrong process"
  else
    bad "dashboard container is not running and nothing is listening on ${DASH_PORT}"
  fi
else

code=$(wait_http "http://localhost:${DASH_PORT}/login" 200 90) \
  && ok "dashboard :${DASH_PORT}/login -> 200" \
  || bad "dashboard :${DASH_PORT}/login -> ${code}"

# The dashboard SPAWNS the engine (app/web/lib/runner.ts) for /lab and /tasks,
# so "serving" and "can actually do anything" are separate facts. Checked as the
# container's own user, because the failure this catches — a managed Python
# installed under a 0700 home, unreachable by the runtime uid — passes every
# root-run check and every HTTP probe, and surfaces only as
# "the `helixis` CLI is not on PATH" inside the page.
if [ "$DASH_RUNNING" = "1" ]; then
  if out=$(with_timeout 30 docker compose exec -T dashboard \
             sh -c 'exec "${HELIXIS_CLI:-helixis}" report' 2>/dev/null) && [ -n "$out" ]; then
    ok "engine CLI runnable from the dashboard container"
  else
    bad "dashboard cannot run the engine CLI — /lab and /tasks will report 'not on PATH'. Check: docker compose exec dashboard sh -c 'ls -l \$HELIXIS_CLI && \$HELIXIS_CLI report'"
  fi
fi

# Actually SIGN IN, rather than trusting the 200 above. Measured on this image:
# with AUTH_SECRET unset /login still returns 200 and only the submit fails
# (MissingSecret), so serving-is-healthy and login-works are separate facts and
# only the second one matters to an operator. next-auth requires a CSRF token
# and its cookie, so this is a two-step exchange.

# Sign in against the SAME ORIGIN the dashboard is configured for, not blindly
# at localhost. Once AUTH_COOKIE_DOMAIN scopes the session cookie to
# `.example.com`, a login driven at http://localhost:3000 can never work: the
# browser (and curl) will not store or send a cookie for a domain that does not
# match the request host. Testing localhost there reports "sign-in FAILED" on a
# deployment where signing in is, in fact, fine — a false alarm that sends you
# hunting a bug that does not exist.
AUTH_BASE="${HELIXIS_AUTH_URL:-http://localhost:${DASH_PORT}}"

if [ -n "${HELIXIS_AUTH_EMAIL:-}" ] && [ -n "${HELIXIS_AUTH_PASSWORD:-}" ]; then
  JAR=$(mktemp)
  csrf=$(curl -s -c "$JAR" --max-time 10 "${AUTH_BASE}/api/auth/csrf" \
         | sed -n 's/.*"csrfToken":"\([^"]*\)".*/\1/p')
  if [ -z "$csrf" ]; then
    bad "could not fetch a CSRF token — auth routes are not responding"
  else
    curl -s -o /dev/null -b "$JAR" -c "$JAR" --max-time 10 \
      -d "csrfToken=$csrf" \
      --data-urlencode "email=${HELIXIS_AUTH_EMAIL}" \
      --data-urlencode "password=${HELIXIS_AUTH_PASSWORD}" \
      "${AUTH_BASE}/api/auth/callback/credentials" 2>/dev/null
    if curl -s -b "$JAR" --max-time 10 "${AUTH_BASE}/api/auth/session" \
         2>/dev/null | grep -q '"user"'; then
      ok "dashboard sign-in succeeds as ${HELIXIS_AUTH_EMAIL} (via ${AUTH_BASE})"
    else
      bad "dashboard sign-in FAILED — check HELIXIS_DASHBOARD_AUTH_SECRET (run: make auth-secret) and the email/password pair; then: docker compose logs dashboard | grep auth"
    fi
  fi
  rm -f "$JAR"
else
  bad "HELIXIS_AUTH_EMAIL/PASSWORD unset — sign-in fails closed, dashboard is unusable"
fi

fi  # DASH_RUNNING

echo
if [ "$FAIL" -gt 0 ]; then
  printf '\033[31m%d check(s) failed\033[0m\n' "$FAIL"
  echo "logs: docker compose logs --tail=50 <service>"
  exit 1
fi
printf '\033[32mstack healthy\033[0m\n'
echo
echo "  dashboard    http://localhost:${DASH_PORT}/"
echo "  control UI   http://localhost:${UI_PORT}/#token=${TOKEN}"
echo "  TUI          http://localhost:${TUI_PORT}/   (helixis / ${TOKEN})"
