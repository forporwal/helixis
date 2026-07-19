# Helixis engine — runs epochs, distillation, and the containment tooling.
FROM python:3.13-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    AUTOMATIONBENCH_STRICT_ASSERTIONS=0

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends git curl ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# The `openshell` CLI that helixis/containment.py drives (policy set, sandbox
# create, tail-policy). Same v0.0.86 pin as the gateway service and the
# dashboard image — all three have to agree on the command surface.
RUN OPENSHELL_VERSION=v0.0.86 sh -c \
      "curl -LsSf https://raw.githubusercontent.com/NVIDIA/OpenShell/main/install.sh | sh" \
    && openshell --version

# AutomationBench is not on any index, so it is installed straight from the
# upstream commit pinned in app/engine/pyproject.toml. Keep this rev in sync
# with the `[tool.uv.sources]` entry there.
ARG AUTOMATIONBENCH_REV=a321764ace3cfbe42289e6a13abef2f0f4f56fad
COPY app/engine/pyproject.toml /app/app/engine/pyproject.toml

RUN pip install --no-cache-dir \
        "automation-bench @ git+https://github.com/zapier/AutomationBench@${AUTOMATIONBENCH_REV}" \
    && pip install --no-cache-dir \
        "openai>=1.60" "pydantic>=2.0" "pyyaml>=6.0" \
        "python-dotenv>=1.2.1" "httpx>=0.27" "rich>=13.0"

COPY app/engine /app/app/engine
COPY app/real_tier /app/app/real_tier

RUN pip install --no-cache-dir --no-deps -e /app/app/engine

# The engine writes here; compose mounts host dirs over them so state survives.
RUN mkdir -p /app/runs /app/wiki

# Run unprivileged — this process holds credentials and is the thing OpenShell
# is containing, so it should not also be root inside its own container.
RUN useradd --create-home --uid 10001 helixis \
    && chown -R helixis:helixis /app
USER helixis

ENTRYPOINT ["helixis"]
CMD ["report"]
