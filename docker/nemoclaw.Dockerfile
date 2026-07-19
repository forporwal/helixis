# Helixis Claw — the pinned NemoClaw sandbox plus ttyd.
#
# The only thing this adds to the upstream image is the ttyd binary, so the
# browser TUI can be served from the same container as the gateway rather than
# from a sidecar. That matters for more than tidiness: the sidecar joined the
# gateway's network namespace with `network_mode: service:nemoclaw`, which pins
# the *container id* — recreating the gateway stranded the sidecar in a dead
# namespace, and it could only be recovered by recreating it too.
#
# Installing at build time (rather than the sidecar's apt-get on every start)
# also drops a network dependency from the container's boot path.
#
# Provenance is unchanged: FROM is the same digest pinned in the NemoClaw
# blueprint (nemoclaw-blueprint/blueprint.yaml upstream), so the sandbox
# underneath is still the audited image.
FROM ghcr.io/nvidia/openshell-community/sandboxes/openclaw@sha256:b3d832b596ab6b7184a9dcb4ae93337ca32851a4f93b00765cc12de26baa3a9a

# Root only for the install; the image's own unprivileged user is restored
# below so the gateway, the TUI, and the agent all run as `sandbox` exactly as
# they did before. Nothing here grants the agent new privileges.
USER root
RUN apt-get update -qq \
    && apt-get install -y -qq --no-install-recommends ttyd \
    && rm -rf /var/lib/apt/lists/*

# The startup scripts, BAKED IN rather than bind-mounted from ./docker.
#
# They used to arrive via `- ./docker:/helixis/bin:ro`. That mount cannot work
# any more: a Landlock-confined process on Docker Desktop for macOS cannot read
# a host bind mount (they surface as the `fakeowner` filesystem, where Landlock
# rules do not grant access), so `bash /helixis/bin/claw-start.sh` died with
# "Permission denied" while `ls -l` on the same file succeeded.
#
# Baking them in also removes a whole class of drift: the script the sandbox
# runs is now the one that was built into the pinned image, not whatever is in
# the host directory at exec time.
COPY docker/claw-start.sh docker/wiki-sync.sh /helixis/bin/
RUN chmod +x /helixis/bin/claw-start.sh /helixis/bin/wiki-sync.sh

# Patch the upstream Control UI so tool results collapse into their tool card
# instead of dumping the whole file into the transcript. See the header of
# patch-control-ui.mjs for the bug; it is upstream in openclaw 2026.3.11, not
# ours. Applied at build time because the sandbox filesystem is ephemeral — an
# edit to the running sandbox is gone on the next recreate.
#
# The script exits non-zero if the guard it rewrites is not found exactly once,
# so a moved FROM digest breaks the build loudly rather than shipping a UI that
# silently reverts to the old behaviour.
COPY docker/patch-control-ui.mjs /helixis/bin/
RUN node /helixis/bin/patch-control-ui.mjs

# Back to the upstream default (uid 998). Confirmed against the pinned image's
# config: User=sandbox, WorkingDir=/sandbox, Entrypoint=/bin/bash — all
# inherited unchanged.
USER sandbox
