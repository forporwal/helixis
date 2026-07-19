#!/bin/sh
# Bridge the host filesystem and the sandbox's named volumes.
#
# WHY THIS EXISTS
#
# The sandbox's wiki and session directories cannot be host bind mounts. A
# Landlock-confined process on Docker Desktop for macOS cannot read one: host
# mounts arrive as the `fakeowner` filesystem, where Landlock rules grant no
# access, so every `open` under the mount returns EACCES while `stat` and
# `ls -l` still succeed — the wiki reads as EMPTY rather than failing. Named
# volumes live on the VM's own filesystem and work correctly under the same
# policy, so the sandbox mounts volumes and this service moves data across the
# boundary.
#
# This container is deliberately NOT a sandbox and holds no policy, so ordinary
# bind mounts work here. It is a data mover, nothing else: it never talks to the
# gateway and holds no credential.
#
# DIRECTIONALITY IS LOAD-BEARING. Each path is one-way, so the two sides can
# never fight over the same file:
#   host ./wiki            -> wiki volume       (agent input, spec 01)
#   sessions volume        -> host runs/        (agent output, spec 03)
set -u

INTERVAL="${HELIXIS_SYNC_INTERVAL:-15}"

echo "claw-sync: bridging host <-> sandbox volumes every ${INTERVAL}s"

# The session volume must be writable by uid 998 (`sandbox`), which is who
# OpenClaw runs as. Docker creates a fresh named volume owned by root, so
# without this the agent's very first turn dies with EACCES trying to create
# its session directory — the same uid-998 problem the old bind mount had, just
# arriving through a different mechanism. Done here because this container runs
# as root and the sandbox deliberately does not.
chown -R 998:998 /vol-sessions 2>/dev/null \
  || echo "claw-sync: WARNING could not chown session volume"

while true; do
  # Wiki: host is the source of truth. --delete so a page removed on the host
  # actually disappears for the agent rather than lingering forever.
  if [ -d /host-wiki ]; then
    rsync -a --delete /host-wiki/ /vol-wiki/ 2>/dev/null \
      || echo "claw-sync: WARNING wiki sync failed"
  fi

  # Sessions: the agent is the source of truth. NO --delete here — the host copy
  # is what spec 03's ingestion reads, and deleting a session the agent has
  # rotated away would destroy history that has not been ingested yet.
  if [ -d /vol-sessions ]; then
    rsync -a /vol-sessions/ /host-sessions/ 2>/dev/null \
      || echo "claw-sync: WARNING session sync failed"
  fi

  sleep "$INTERVAL"
done
