"""Shared plumbing for the real-credential task tier.

Three invariants hold across every task here, and most of this module exists to
enforce them mechanically rather than by convention:

1. **Credentials come from the environment, never from the repo.** There is no
   file-based credential loader in this module on purpose. `require_env` raises
   a named, actionable error so a missing variable surfaces as a hard failure
   at the top of a run instead of a silent no-op that later reads as a pass.

2. **Every destructive operation is marker-scoped.** A run stamps a unique
   marker (`[helixis-run:<run_id>]`) into every object it creates. verify only
   inspects objects carrying the marker, and reset only deletes objects
   carrying the marker. Pre-existing user data in the same account/channel is
   therefore unreachable by any code path here — that is the whole point.

3. **verify and reset are idempotent.** Re-running verify performs only reads.
   Re-running reset treats "already gone" (404 / not-found) as success, so a
   second invocation neither errors nor double-deletes.

Negative assertions (the "nothing else happened" checks) need a pre-run
baseline, which is why verify scripts support a `--snapshot` mode. The runner
calls that before handing control to the agent. A missing baseline is scored as
a FAILED negative assertion, never as a pass — an unprovable containment claim
is not a satisfied one.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Sequence

import yaml

# <root>/app/real_tier/common.py -> <root>
ROOT = Path(__file__).resolve().parents[2]
STATE_DIR = ROOT / "runs" / "real_tier"


class MissingCredential(RuntimeError):
    """Raised when a required environment variable is absent or blank."""


def require_env(name: str, *, hint: str = "") -> str:
    """Return env var `name` or raise with an actionable message.

    Fail-fast is deliberate: a real-tier task that runs without credentials
    would create nothing, and a verifier that finds nothing could otherwise be
    mistaken for a clean pass. Better to abort loudly.
    """
    value = os.environ.get(name, "").strip()
    if not value:
        suffix = f" {hint}" if hint else ""
        raise MissingCredential(
            f"required environment variable {name} is not set."
            f" Set it in your shell or a local .env file (see"
            f" app/real_tier/.env.example for the expected shape).{suffix}"
        )
    return value


def optional_env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


def check_env(names: Sequence[str]) -> list[str]:
    """Return the subset of `names` that are missing. Used for pre-flight."""
    return [n for n in names if not os.environ.get(n, "").strip()]


# --------------------------------------------------------------------- markers


def new_run_id() -> str:
    return uuid.uuid4().hex[:12]


def marker_for(run_id: str) -> str:
    """The literal string stamped into every object a run creates.

    It must be (a) unique per run, (b) cheap to substring-match in a subject
    line / message body / label, and (c) implausible as user-authored text.
    """
    return f"[helixis-run:{run_id}]"


def has_marker(text: str | None, run_id: str) -> bool:
    return bool(text) and marker_for(run_id) in (text or "")


# ----------------------------------------------------------------- task spec


@dataclass(frozen=True)
class RealTaskSpec:
    """A `task.yaml` on disk."""

    id: str
    service: str
    prompt: str
    allowed_endpoints: list[str] = field(default_factory=list)
    required_env: list[str] = field(default_factory=list)
    timeout_s: int = 600
    assertions: list[dict[str, Any]] = field(default_factory=list)
    directory: Path = ROOT

    @classmethod
    def load(cls, directory: Path | str) -> RealTaskSpec:
        directory = Path(directory)
        path = directory / "task.yaml"
        if not path.exists():
            raise FileNotFoundError(f"no task.yaml in {directory}")
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        return cls(
            id=str(data["id"]),
            service=str(data.get("service", "")),
            prompt=str(data.get("prompt", "")),
            allowed_endpoints=[str(e) for e in data.get("allowed_endpoints", [])],
            required_env=[str(e) for e in data.get("required_env", [])],
            timeout_s=int(data.get("timeout_s", 600)),
            assertions=list(data.get("assertions", [])),
            directory=directory,
        )

    @property
    def verify_script(self) -> Path:
        return self.directory / "verify.py"

    @property
    def reset_script(self) -> Path:
        return self.directory / "reset.py"

    @property
    def negative_assertions(self) -> list[dict[str, Any]]:
        return [a for a in self.assertions if a.get("negative")]

    def render_prompt(self, **context: str) -> str:
        """Substitute `{run_id}` / `{marker}` / task-specific placeholders.

        `str.format` is avoided because prompts contain JSON braces; plain
        replacement keeps the template safe for literal `{...}` examples.
        """
        text = self.prompt
        for key, value in context.items():
            text = text.replace("{" + key + "}", value)
        return text


def discover_tasks(root: Path | str | None = None) -> list[RealTaskSpec]:
    base = Path(root) if root else Path(__file__).resolve().parent
    specs = []
    for child in sorted(base.iterdir()):
        if child.is_dir() and (child / "task.yaml").exists():
            specs.append(RealTaskSpec.load(child))
    return specs


# ---------------------------------------------------------------- assertions


@dataclass
class Assertion:
    """One end-state check. Serializes to the mocked tier's assertion shape.

    `excluded` mirrors AutomationBench: an assertion that could not be
    meaningfully evaluated is excluded from the denominator rather than counted
    as a failure. Negative assertions are NEVER excluded — see `VerifyReport`.
    """

    type: str
    passed: bool
    params: dict[str, Any] = field(default_factory=dict)
    excluded: bool = False
    negative: bool = False
    detail: str = ""

    def to_dict(self) -> dict[str, Any]:
        # Shape matches AutomationBench's `_assertion_results` entries so the
        # distiller and dashboard read real-tier episodes with no special case.
        params = dict(self.params)
        if self.detail:
            params["detail"] = self.detail
        if self.negative:
            params["negative"] = True
        return {
            "type": self.type,
            "passed": bool(self.passed),
            "excluded": bool(self.excluded),
            "params": params,
        }


@dataclass
class VerifyReport:
    assertions: list[Assertion] = field(default_factory=list)
    error: str | None = None

    def add(self, assertion: Assertion) -> Assertion:
        self.assertions.append(assertion)
        return assertion

    def check(
        self,
        type_: str,
        passed: bool,
        *,
        negative: bool = False,
        detail: str = "",
        **params: Any,
    ) -> Assertion:
        return self.add(
            Assertion(
                type=type_,
                passed=passed,
                params=params,
                negative=negative,
                detail=detail,
            )
        )

    @property
    def scored(self) -> list[Assertion]:
        return [a for a in self.assertions if not a.excluded]

    @property
    def partial_credit(self) -> float:
        scored = self.scored
        if not scored:
            return 0.0
        return round(sum(1 for a in scored if a.passed) / len(scored), 4)

    @property
    def passed(self) -> bool:
        """A task passes only if every scored assertion passed.

        Negative assertions are part of this set, so "did the work but also
        sent a real email" scores below 1.0 and never passes.
        """
        scored = self.scored
        return bool(scored) and all(a.passed for a in scored) and self.error is None

    def to_dict(self) -> dict[str, Any]:
        return {
            "assertions": [a.to_dict() for a in self.assertions],
            "partial_credit": self.partial_credit,
            "passed": self.passed,
            "error": self.error,
        }


# -------------------------------------------------------------- run-state I/O


def state_path(task_id: str, run_id: str) -> Path:
    return STATE_DIR / f"{task_id}.{run_id}.json"


def write_state(task_id: str, run_id: str, data: dict[str, Any]) -> Path:
    path = state_path(task_id, run_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, indent=2, default=str), encoding="utf-8")
    tmp.replace(path)  # atomic: a truncated baseline must never look complete
    return path


def output_path(task_id: str, run_id: str) -> Path:
    """Where a task's agent-produced artifact lands.

    Distinct suffix from `state_path` on purpose: the baseline snapshot and the
    agent's output must never be the same file, or the agent would overwrite
    the very evidence the negative assertions are compared against.
    """
    base = optional_env("HELIXIS_REAL_OUTPUT_DIR")
    root = Path(base) if base else STATE_DIR
    return root / f"{task_id}.{run_id}.output.json"


def read_state(task_id: str, run_id: str) -> dict[str, Any] | None:
    path = state_path(task_id, run_id)
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None


# ------------------------------------------------------------- HTTP + Google


def http_json(
    method: str,
    url: str,
    *,
    headers: dict[str, str] | None = None,
    params: dict[str, Any] | None = None,
    json_body: Any = None,
    timeout: float = 30.0,
) -> tuple[int, Any]:
    """Small sync HTTP helper. Returns (status, parsed-or-text).

    Status is returned rather than raised so callers can treat 404 as
    "already deleted" during idempotent resets.
    """
    import httpx

    resp = httpx.request(
        method,
        url,
        headers=headers,
        params=params,
        json=json_body,
        timeout=timeout,
    )
    body: Any
    try:
        body = resp.json()
    except Exception:
        body = resp.text
    return resp.status_code, body


def google_credentials(scopes: Sequence[str]) -> Any:
    """Build OAuth user credentials from env vars.

    Lazy-imported so the engine imports cleanly without the optional
    `realtier` extra installed. A refresh token is used rather than a
    service-account key file because the tier targets a dedicated *user*
    mailbox and because it keeps the secret in the environment, not on disk.
    """
    try:
        from google.oauth2.credentials import Credentials
    except ImportError as exc:  # pragma: no cover - depends on optional extra
        raise MissingCredential(
            "google-auth is not installed. Install the optional extra:"
            " `uv pip install -e 'app/engine[realtier]'`"
        ) from exc

    return Credentials(
        token=None,
        refresh_token=require_env("GOOGLE_OAUTH_REFRESH_TOKEN"),
        client_id=require_env("GOOGLE_OAUTH_CLIENT_ID"),
        client_secret=require_env("GOOGLE_OAUTH_CLIENT_SECRET"),
        token_uri=optional_env(
            "GOOGLE_OAUTH_TOKEN_URI", "https://oauth2.googleapis.com/token"
        ),
        scopes=list(scopes),
    )


def gmail_service() -> Any:
    """Authenticated Gmail API client. Lazy-imported (optional extra)."""
    try:
        from googleapiclient.discovery import build
    except ImportError as exc:  # pragma: no cover - depends on optional extra
        raise MissingCredential(
            "google-api-python-client is not installed. Install the optional"
            " extra: `uv pip install -e 'app/engine[realtier]'`"
        ) from exc

    scopes = [
        "https://www.googleapis.com/auth/gmail.compose",
        "https://www.googleapis.com/auth/gmail.readonly",
    ]
    return build(
        "gmail", "v1", credentials=google_credentials(scopes), cache_discovery=False
    )


def is_not_found(exc: Exception) -> bool:
    """True for HTTP 404/410 from googleapiclient — i.e. already deleted.

    Used by every reset script so a second run is a no-op instead of an error.
    """
    status = getattr(getattr(exc, "resp", None), "status", None)
    return status in (404, 410)


# --------------------------------------------------------------- script entry


def script_args(description: str) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=description)
    parser.add_argument(
        "--run-id",
        default=optional_env("HELIXIS_REAL_RUN_ID"),
        help="run marker id; every object this run touched carries it",
    )
    parser.add_argument(
        "--snapshot",
        action="store_true",
        help="capture the pre-run baseline instead of verifying (verify.py only)",
    )
    parser.add_argument("--json", action="store_true", help="emit JSON (default)")
    args = parser.parse_args()
    if not args.run_id:
        parser.error(
            "--run-id is required (or set HELIXIS_REAL_RUN_ID). Destructive and"
            " verification steps are scoped by run id so they can never touch"
            " pre-existing account data."
        )
    return args


def emit(payload: dict[str, Any]) -> None:
    json.dump(payload, sys.stdout, indent=2, default=str)
    sys.stdout.write("\n")


def fail(message: str, *, kind: str = "error") -> None:
    """Print a machine-readable failure and exit non-zero."""
    emit({kind: message, "assertions": [], "partial_credit": 0.0, "passed": False})
    sys.exit(2)


def sentences(text: str) -> list[str]:
    """Naive sentence split, good enough for grounding checks."""
    out: list[str] = []
    buf: list[str] = []
    for ch in text:
        buf.append(ch)
        if ch in ".!?\n":
            chunk = "".join(buf).strip()
            if chunk:
                out.append(chunk)
            buf = []
    tail = "".join(buf).strip()
    if tail:
        out.append(tail)
    return out


def content_tokens(text: str, *, min_len: int = 4) -> set[str]:
    """Lowercased alphanumeric tokens, stopwords and short words dropped."""
    cleaned = "".join(c.lower() if c.isalnum() else " " for c in text)
    return {t for t in cleaned.split() if len(t) >= min_len and t not in STOPWORDS}


STOPWORDS = frozenset(
    """about above after again against also been before being below between both
    cannot could does doing down during each from further have having here
    hers herself himself into itself more most other ought ours ourselves over
    same should some such than that their theirs them themselves then there
    these they this those through under until very were what when where which
    while with would your yours yourself yourselves""".split()
)


def coverage(claim: str, source_tokens: Iterable[str]) -> float:
    """Fraction of a claim's content tokens present in the source material."""
    tokens = content_tokens(claim)
    if not tokens:
        return 1.0  # nothing substantive asserted -> nothing to ground
    source = set(source_tokens)
    return len(tokens & source) / len(tokens)


def grounded(claim: str, source_tokens: Iterable[str], threshold: float) -> bool:
    """Is a claim traceable to the source material?

    Coverage alone is unfair to short claims: "Based in London" has two content
    tokens, so one connective the scrape never used drops it to 0.5 and a true
    statement reads as a hallucination. Short claims (<= 3 content tokens) are
    therefore allowed one unmatched token, provided at least one token DOES
    match — which still rejects a wholly invented fact, the case that matters.
    """
    tokens = content_tokens(claim)
    if not tokens:
        return True
    source = set(source_tokens)
    matched = len(tokens & source)
    if len(tokens) <= 3:
        return matched >= 1 and (len(tokens) - matched) <= 1
    return matched / len(tokens) >= threshold
