# The `openshell` CLI, for the services that provision and expose the sandbox.
#
# Separate from the dashboard image on purpose. The dashboard only makes gRPC
# calls (`openshell rule approve|reject`), but `sandbox exec` and `forward start`
# shell out to a real `ssh` binary with a ProxyCommand pointing back at
# `openshell ssh-proxy` — without openssh-client they fail with a bare
# "No such file or directory (os error 2)", which is opaque enough to be worth
# avoiding. Adding ssh to the dashboard would grow the image and hand a
# password-protected web surface a tool it has no reason to hold.
#
# Pinned to the same v0.0.86 as the gateway service and the dashboard CLI; see
# docker-compose.yml for why the versions must move together.
FROM debian:bookworm-slim

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
       curl ca-certificates openssh-client procps \
    && OPENSHELL_VERSION=v0.0.86 sh -c \
       "curl -LsSf https://raw.githubusercontent.com/NVIDIA/OpenShell/main/install.sh | sh" \
    && openshell --version \
    && apt-get purge -y curl && apt-get autoremove -y \
    && rm -rf /var/lib/apt/lists/*

COPY docker/claw-init.sh /usr/local/bin/claw-init.sh
COPY docker/claw-forward.sh /usr/local/bin/claw-forward.sh
RUN chmod +x /usr/local/bin/claw-init.sh /usr/local/bin/claw-forward.sh
