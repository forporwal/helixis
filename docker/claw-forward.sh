#!/bin/bash
# Publish the sandbox's Control UI and browser TUI on fixed host ports.
#
# WHY THIS SERVICE EXISTS
#
# A Docker-driver sandbox publishes NO ports. The driver sets neither
# `exposed_ports` nor `port_bindings` when it creates the container (the podman
# driver does; the docker one does not), and the sandbox driver_config schema
# accepts exactly two keys — `cdi_devices` and `mounts` — with
# `deny_unknown_fields`, so a `ports` key is rejected outright. There is no
# configuration that reproduces the old `18789:18789` compose mapping.
#
# The supported path is `openshell forward`, which tunnels a local listener to
# the sandbox over the gateway. This container holds those tunnels and is itself
# port-published by compose, so the Control UI and TUI stay reachable at exactly
# the URLs they had before — which is what keeps spec 02's launch cards correct
# without touching them (Req 1.4).
#
# COST, STATED PLAINLY: the front door is now a tunnel through a supervised
# sidecar rather than a kernel-level port binding. If this service is down, the
# UI is unreachable even though the agent is healthy. That is a real regression
# in failure modes versus a compose port mapping, and it is the price of the
# agent being inside the boundary rather than beside it.
set -uo pipefail

SANDBOX="${HELIXIS_SANDBOX:-helixis}"
UI_PORT="${NEMOCLAW_UI_PORT:-18789}"
TUI_PORT="${NEMOCLAW_TUI_PORT:-18790}"

log() { echo "claw-forward: $*"; }

# Bind 0.0.0.0 inside THIS container, not on the host: compose publishes these
# to 127.0.0.1 on the host, so loopback-only exposure is preserved. Binding
# 127.0.0.1 here would make the listener unreachable from the published port.
log "waiting for sandbox $SANDBOX to be ready"
until openshell sandbox get "$SANDBOX" 2>/dev/null | grep -qi ready; do
  sleep 3
done
log "sandbox $SANDBOX ready"

# Supervise rather than fire-and-forget. `forward start --background` tracks a
# PID that can die (sandbox restart, gateway recreate) without anything noticing;
# a dead tunnel and a healthy agent look identical from the outside. This loop is
# what turns "the UI stopped working" into "the tunnel is being re-established".
#
# It also supervises the AGENT, for a related reason. PID 1 in the sandbox is the
# OpenShell supervisor, and the agent is started through `sandbox exec` — so if
# the sandbox container restarts, the supervisor comes back but the agent does
# NOT. Nothing else would notice: the sandbox reports Ready, the tunnels
# re-establish, and the UI simply refuses connections. Restarting it here means a
# container restart is self-healing instead of a manual `claw-init` re-run.
#
# claw-start.sh is idempotent (it returns early when the gateway is already
# running), so calling it on a healthy sandbox costs one exec and changes
# nothing. That is what makes it safe to run on every sweep rather than trying to
# detect the restart edge.
# Match `openclaw-gateway`, NOT the `openclaw gateway run` command line that
# started it: OpenClaw renames the gateway process, so the invocation string is
# gone by the time it is serving. Matching the invocation silently never hits,
# which would make this loop declare a healthy agent dead and restart it every
# sweep — worse than not supervising at all. Verified against `ps -eo args`
# inside the sandbox.
agent_running() {
  openshell sandbox exec -n "$SANDBOX" --no-tty --timeout 20 -- \
    pgrep -f 'openclaw-gateway' >/dev/null 2>&1
}

# `forward list` prints columns — `helixis  0.0.0.0  18789  766  running` — so the
# port must be matched as a FIELD, not as a `:18789` substring. Matching the
# substring never hits, which makes every sweep try to re-establish a healthy
# tunnel and log "Port 18789 is already forwarded" forever: harmless to the
# tunnel, but it buries a genuine failure in noise. Status is checked too, so a
# forward that is listed but dead gets rebuilt rather than trusted.
forward_healthy() {
  openshell forward list 2>/dev/null \
    | awk -v p="$1" '$3 == p && $NF ~ /running/ { found = 1 } END { exit !found }'
}

while true; do
  for spec in "$UI_PORT" "$TUI_PORT"; do
    if ! forward_healthy "$spec"; then
      log "establishing forward for port $spec"
      # Clear any stale entry first; `forward start` refuses outright when the
      # port is already registered, even if its process is gone.
      openshell forward stop "$spec" "$SANDBOX" >/dev/null 2>&1 || true
      openshell forward start "0.0.0.0:${spec}" "$SANDBOX" --background \
        || log "WARNING: forward for $spec failed; retrying next sweep"
    fi
  done

  # Only act when the sandbox is Ready — during a restart it briefly is not, and
  # exec'ing at that moment fails noisily without accomplishing anything.
  if openshell sandbox get "$SANDBOX" 2>/dev/null | grep -qi ready; then
    if ! agent_running; then
      log "agent not running in $SANDBOX — restarting it"
      openshell sandbox exec -n "$SANDBOX" --no-tty --timeout 120 -- \
        bash -c 'setsid bash /helixis/bin/claw-start.sh > /tmp/claw-start.log 2>&1 & sleep 3; tail -2 /tmp/claw-start.log' \
        || log "WARNING: agent restart failed; retrying next sweep"
    fi
  fi

  sleep 15
done
