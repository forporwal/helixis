# Helixis dashboard — Next.js, reading the experiment's SQLite index read-only.
FROM node:22-slim AS deps

WORKDIR /app

# better-sqlite3 is a native module; without a toolchain the install silently
# falls back to a prebuild that may not match this base image.
RUN apt-get update \
    && apt-get install -y --no-install-recommends python3 make g++ \
    && rm -rf /var/lib/apt/lists/*

RUN corepack enable
COPY app/web/package.json app/web/pnpm-lock.yaml app/web/pnpm-workspace.yaml ./
RUN pnpm install --frozen-lockfile


FROM node:22-slim AS builder
WORKDIR /app
RUN corepack enable
COPY --from=deps /app/node_modules ./node_modules
COPY app/web ./
ENV NEXT_TELEMETRY_DISABLED=1
# Invoke next directly: `pnpm build` re-runs a deps-status check that
# false-positives on a node_modules copied from the deps stage and then
# aborts on the no-TTY purge prompt. The deps are already frozen-lockfile
# installed, so the check adds nothing here.
RUN node_modules/.bin/next build


FROM node:22-slim AS runner
WORKDIR /app

ENV NODE_ENV=production \
    NEXT_TELEMETRY_DISABLED=1 \
    PORT=3000 \
    HELIXIS_DB=/app/runs/helixis.db \
    HELIXIS_WIKI=/app/wiki

RUN corepack enable

# The `openshell` CLI, so approving a policy proposal from the dashboard reaches
# the gateway instead of 503ing on a missing binary (/api/proposals shells out
# to it). The CLI is a thin gRPC client: it needs no Docker socket and no root,
# which is why this container can drive the control plane without holding any
# host privilege of its own.
#
# Pinned to the same v0.0.86 as the gateway service. `openshell rule
# approve|reject` is a hidden command with no stability guarantee, so the pin is
# what keeps this route working across upstream releases.
#
# The installer also tries to start a local gateway via systemd and warns when
# it can't; there is no init in this image and we point at the gateway service
# instead, so that warning is expected and harmless.
RUN apt-get update \
    && apt-get install -y --no-install-recommends curl ca-certificates git \
    && OPENSHELL_VERSION=v0.0.86 sh -c \
       "curl -LsSf https://raw.githubusercontent.com/NVIDIA/OpenShell/main/install.sh | sh" \
    && openshell --version \
    && rm -rf /var/lib/apt/lists/*

# ---------------------------------------------------------------------------
# The `helixis` engine CLI.
#
# WHY IT IS HERE AT ALL. /lab and /tasks do not merely read the database — they
# shell out to the CLI (`helixis preflight --json`, `task list --json`) and
# SPAWN RUNS (`helixis epoch|run|heldout|distill|triage`). Without it every one
# of those routes 503s with "the `helixis` CLI is not on PATH", which is what
# the deployed /lab showed. It works in local dev only because `pnpm start`
# runs on the host, where app/engine/.venv is a sibling directory.
#
# WHY uv AND NOT apt. The engine declares requires-python >=3.13 (AutomationBench
# does too); this image is node:22-slim on Debian bookworm, whose python3 is
# 3.11. uv fetches a standalone 3.13 rather than fighting the distro.
#
# WHY A VENV AND NOT --system. Keeping it out of the system prefix means the
# Python install cannot collide with anything Node's toolchain expects, and
# HELIXIS_CLI (honoured by app/web/lib/cli.ts resolveBin) names the binary
# explicitly, so nothing depends on PATH ordering.
#
# Dependency set mirrors docker/engine.Dockerfile — keep the two in step,
# including AUTOMATIONBENCH_REV.
ARG AUTOMATIONBENCH_REV=a321764ace3cfbe42289e6a13abef2f0f4f56fad
# UV_PYTHON_INSTALL_DIR is the important one. `uv venv --python 3.13` downloads
# a MANAGED interpreter, and by default it lands in the building user's home —
# /root/.local/share/uv/python. The venv then symlinks to it. /root is
# drwx------, so at runtime the unprivileged app user cannot traverse it and
# every invocation dies with:
#
#   sh: /opt/engine/.venv/bin/helixis: Permission denied
#
# which reads like a chmod problem on the CLI itself. It is not: the binary is
# fine and the interpreter behind the symlink is unreachable. Putting the
# interpreter under /opt keeps the whole chain world-traversable.
ENV UV_LINK_MODE=copy \
    UV_PYTHON_INSTALL_DIR=/opt/uv/python \
    HELIXIS_CLI=/opt/engine/.venv/bin/helixis \
    AUTOMATIONBENCH_STRICT_ASSERTIONS=0 \
    PYTHONUNBUFFERED=1

RUN curl -LsSf https://astral.sh/uv/install.sh | env UV_INSTALL_DIR=/usr/local/bin sh \
    && uv --version

# Layout matters: helixis/config.py computes
#   ROOT = Path(__file__).resolve().parents[3]
# so with the package at /opt/engine/app/engine/helixis, ROOT is /opt/engine and
# the manifest, policy and real-tier paths all resolve beneath it. Copying the
# package anywhere else silently moves ROOT and the CLI then looks for
# tasks.yaml in a directory that does not exist.
#
# policy/ is included because Paths.policy defaults to ROOT/policy — the
# containment commands read it.
COPY app/engine /opt/engine/app/engine
COPY app/real_tier /opt/engine/app/real_tier
COPY policy /opt/engine/policy

# `-e` on the final install is load-bearing, not a dev convenience.
# helixis/config.py derives ROOT from its own __file__ (parents[3]). A regular
# install lands the package in site-packages, making ROOT resolve to
# /opt/engine/.venv/lib — so the CLI would look for tasks.yaml and policy/ in
# directories that do not exist. Editable keeps __file__ under
# /opt/engine/app/engine, where ROOT is /opt/engine and every path resolves.
RUN uv venv --python 3.13 /opt/engine/.venv \
    && uv pip install --python /opt/engine/.venv/bin/python --no-cache \
        "automation-bench @ git+https://github.com/zapier/AutomationBench@${AUTOMATIONBENCH_REV}" \
        "openai>=1.60" "pydantic>=2.0" "pyyaml>=6.0" \
        "python-dotenv>=1.2.1" "httpx>=0.27" "rich>=13.0" \
    && uv pip install --python /opt/engine/.venv/bin/python --no-cache --no-deps \
        -e /opt/engine/app/engine \
    && chmod -R a+rX /opt/uv \
    && /opt/engine/.venv/bin/helixis --help >/dev/null \
    && apt-get purge -y curl git && apt-get autoremove -y

COPY --from=deps /app/node_modules ./node_modules
COPY --from=builder /app/.next ./.next
COPY --from=builder /app/public ./public
COPY --from=builder /app/package.json ./package.json

# /opt/engine is chowned too: `pip install -e` leaves an editable install whose
# .pth and egg-link the runtime user must be able to read, and the engine writes
# __pycache__ beside its sources on first import.
RUN useradd --create-home --uid 10002 nextjs \
    && chown -R nextjs:nextjs /app /opt/engine
USER nextjs

# Re-run the smoke test AS THE RUNTIME USER.
#
# The identical check above runs as root and therefore proves very little: root
# traverses /root regardless, so a managed interpreter installed into a
# 0700 home passes at build time and fails on every real request. That exact
# bug shipped once — the deploy reported healthy while /lab answered "the
# helixis CLI is not on PATH", because nothing between the build and the
# browser ever executed the binary as uid 10002.
#
# `report` rather than `--help`: it imports the store and touches the
# filesystem, so it exercises the interpreter, the editable install and ROOT
# resolution together. On a container with no database mounted it prints
# "No episodes recorded yet." and exits 0, which is the correct answer here.
RUN "$HELIXIS_CLI" --help >/dev/null \
    && "$HELIXIS_CLI" report >/dev/null \
    && echo "engine CLI usable as runtime user"

EXPOSE 3000
# Same reasoning as the build stage: bypass pnpm's deps-status check, which
# would try to purge node_modules at container start.
CMD ["node_modules/.bin/next", "start"]
