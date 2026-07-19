"""`RealTierBackend` — the real-credential tier, wearing the TaskBackend shape.

This deliberately implements the same Protocol as `AutomationBenchBackend` and
`OfflineBackend` (`async def run(spec, skills_block, max_steps) -> Attempt`), so
real-tier episodes land in the same `EpisodeStore` and are eligible for the same
wiki distillation as mocked ones (Requirement 5.4). No parallel pipeline, no
second store, no special-cased distiller input.

The one thing that IS different is the label: callers record these episodes with
`tier='real'` and `split='real'`. `EpisodeStore.epoch_curve` filters on
`tier='mocked'`, so real-tier results are excluded from headline metrics for
free — a handful of live-credential runs must never move a learning curve.

Lifecycle of one real task:

  1. Pre-flight: every `required_env` present, else fail fast with the names.
  2. Mint a run id and marker. Everything the run creates carries it.
  3. `verify.py --snapshot` — baseline for the negative assertions.
  4. Drive the agent against the rendered prompt, with an `http_request` tool
     whose URLs are checked against the task's `allowed_endpoints`.
  5. `verify.py` — assertions and partial credit.

Reset is intentionally NOT automatic: a failed run's residue is evidence. Call
`reset_task` explicitly (the CLI does) once the trajectory is stored.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol, Sequence

sys.path.insert(0, str(Path(__file__).resolve().parent))

from common import (  # noqa: E402
    RealTaskSpec,
    check_env,
    discover_tasks,
    marker_for,
    new_run_id,
    optional_env,
    output_path,
)

# The engine package is a sibling; import lazily-tolerantly so this module can
# also be exercised standalone from the real_tier directory.
try:
    from helixis.backends import Attempt, TaskSpec
except ImportError:  # pragma: no cover - path convenience for direct execution
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "engine"))
    from helixis.backends import Attempt, TaskSpec


SYSTEM_PROMPT = """You are Helixis operating against REAL services with REAL \
credentials. Actions you take are not simulated and may be irreversible.

You act by emitting exactly one JSON object per turn, in a fenced ```json block, \
and nothing else. Two actions exist:

{"action": "http_request", "method": "GET", "url": "https://...", \
"headers": {}, "body": null}
{"action": "finish", "answer": "what you did, including any ids you created"}

Rules:
- Only the endpoints listed under ALLOWED ENDPOINTS are reachable. Any other \
URL is denied at the gateway and the denial is recorded against you.
- Prefer the least destructive action that satisfies the task. If the task says \
draft, never send. If it says post once, never post twice.
- Stop with "finish" as soon as the task is done."""

ACTION_BLOCK = re.compile(r"```(?:json)?\s*(\{.*?\})\s*```", re.DOTALL)


class AgentDriver(Protocol):
    """Anything that can execute a prompt and return a trajectory."""

    async def __call__(
        self,
        *,
        spec: RealTaskSpec,
        prompt: str,
        skills_block: str,
        max_steps: int,
    ) -> AgentRun: ...


@dataclass
class AgentRun:
    messages: list[dict[str, Any]] = field(default_factory=list)
    tokens_in: int = 0
    tokens_out: int = 0
    steps: int = 0
    denied_requests: list[dict[str, Any]] = field(default_factory=list)
    error: str | None = None


def endpoint_allowed(url: str, allowed: Sequence[str]) -> bool:
    """Prefix match against the task's allow-list.

    Prefix rather than host match: `https://api.apify.com/v2/datasets` must not
    also authorise `https://api.apify.com/v2/users/me` on the same host. The
    list is the task's declared blast radius and mirrors
    policy/helixis-real-tier.yaml, which enforces the same thing at the gateway
    — this check is the fast, in-process copy, not the security boundary.
    """
    return any(url.startswith(prefix) for prefix in allowed)


class LLMAgentDriver:
    """Default driver: a JSON-action loop over an OpenAI-compatible endpoint."""

    def __init__(self, client: Any, *, request_timeout: float = 30.0):
        self.client = client  # helixis.llm.LLMClient
        self.request_timeout = request_timeout

    async def __call__(
        self,
        *,
        spec: RealTaskSpec,
        prompt: str,
        skills_block: str,
        max_steps: int,
    ) -> AgentRun:
        allowed = "\n".join(f"- {e}" for e in spec.allowed_endpoints)
        system = f"{SYSTEM_PROMPT}\n\nALLOWED ENDPOINTS:\n{allowed}"
        if skills_block:
            system = f"{system}\n\n{skills_block}"

        messages: list[dict[str, Any]] = [
            {"role": "system", "content": system},
            {"role": "user", "content": prompt},
        ]
        run = AgentRun(messages=messages)

        for _ in range(max_steps):
            completion = await self.client.complete(messages, temperature=0.3)
            run.tokens_in += completion.tokens_in
            run.tokens_out += completion.tokens_out
            run.steps += 1
            messages.append({"role": "assistant", "content": completion.text})

            action = _parse_action(completion.text)
            if action is None or action.get("action") == "finish":
                break

            observation = await self._act(action, spec, run)
            messages.append({"role": "user", "content": observation})

        return run

    async def _act(
        self, action: dict[str, Any], spec: RealTaskSpec, run: AgentRun
    ) -> str:
        if action.get("action") != "http_request":
            return f"error: unknown action {action.get('action')!r}"

        url = str(action.get("url", ""))
        if not endpoint_allowed(url, spec.allowed_endpoints):
            # Record the denial rather than silently dropping it: the policy
            # feed and the distiller both learn from attempted boundary
            # crossings.
            run.denied_requests.append({"url": url, "method": action.get("method")})
            return (
                f"DENIED: {url} is outside this task's allowed endpoints."
                " The request was not made."
            )

        def _send() -> tuple[int, Any]:
            from common import http_json

            return http_json(
                str(action.get("method", "GET")).upper(),
                url,
                headers=action.get("headers") or None,
                json_body=action.get("body"),
                timeout=self.request_timeout,
            )

        try:
            status, body = await asyncio.to_thread(_send)
        except Exception as exc:
            return f"error: {type(exc).__name__}: {exc}"
        text = body if isinstance(body, str) else json.dumps(body, default=str)
        return f"HTTP {status}\n{text[:4000]}"


def _parse_action(text: str) -> dict[str, Any] | None:
    match = ACTION_BLOCK.search(text)
    raw = match.group(1) if match else None
    if raw is None:
        start, end = text.find("{"), text.rfind("}")
        raw = text[start : end + 1] if start != -1 and end > start else None
    if not raw:
        return None
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None


class RealTierBackend:
    """TaskBackend over `app/real_tier/<task>/`."""

    def __init__(
        self,
        agent: AgentDriver,
        *,
        tasks_dir: Path | None = None,
        python: str | None = None,
    ):
        self.agent = agent
        self.tasks_dir = tasks_dir or Path(__file__).resolve().parent
        self.python = python or sys.executable
        self._specs = {s.id: s for s in discover_tasks(self.tasks_dir)}

    @property
    def task_ids(self) -> list[str]:
        return sorted(self._specs)

    def spec_for(self, task_id: str) -> RealTaskSpec | None:
        return self._specs.get(task_id)

    # -------------------------------------------------------------- TaskBackend

    async def run(self, spec: TaskSpec, skills_block: str, max_steps: int) -> Attempt:
        task = self._specs.get(spec.task_id)
        if task is None:
            return Attempt(0.0, False, [], error=f"unknown real task {spec.task_id}")

        missing = check_env(task.required_env)
        if missing:
            # Fail fast and loudly. A real task with no credentials creates
            # nothing, and "nothing found" must never be scored as a pass.
            return Attempt(
                0.0,
                False,
                [],
                error=(
                    "missing required environment variables: "
                    + ", ".join(missing)
                    + " (see app/real_tier/.env.example)"
                ),
            )

        run_id = new_run_id()
        marker = marker_for(run_id)
        prompt = task.render_prompt(
            run_id=run_id,
            marker=marker,
            today=_today(),
            gmail_account=optional_env("GMAIL_TEST_ACCOUNT"),
            profile_url=optional_env("APIFY_TARGET_PROFILE_URL"),
            actor_id=optional_env("APIFY_ACTOR_ID"),
            output_path=str(self._output_path(task.id, run_id)),
        )

        snap = await self._script(task.verify_script, run_id, "--snapshot")
        if snap.get("error"):
            return Attempt(
                0.0, False, [], error=f"snapshot failed: {snap['error']}"
            )

        try:
            agent_run = await asyncio.wait_for(
                self.agent(
                    spec=task,
                    prompt=prompt,
                    skills_block=skills_block,
                    max_steps=max_steps,
                ),
                timeout=task.timeout_s,
            )
        except asyncio.TimeoutError:
            agent_run = AgentRun(error=f"timeout after {task.timeout_s}s")
        except Exception as exc:
            agent_run = AgentRun(error=f"{type(exc).__name__}: {exc}")

        # Verify runs even after an agent error: partial work still has a real
        # end state, and the negative assertions still need checking.
        report = await self._script(task.verify_script, run_id)
        assertions = list(report.get("assertions") or [])
        scored = [a for a in assertions if not a.get("excluded")]
        partial = (
            round(sum(1 for a in scored if a.get("passed")) / len(scored), 4)
            if scored
            else 0.0
        )

        error = agent_run.error or report.get("error") or report.get("missing_credential")
        return Attempt(
            partial_credit=partial,
            passed=bool(scored) and all(a.get("passed") for a in scored) and not error,
            messages=agent_run.messages,
            assertions=assertions,
            end_state={
                "run_id": run_id,
                "marker": marker,
                "service": task.service,
                "denied_requests": agent_run.denied_requests,
                "verify": {k: v for k, v in report.items() if k != "assertions"},
            },
            tokens_in=agent_run.tokens_in,
            tokens_out=agent_run.tokens_out,
            steps=agent_run.steps,
            error=str(error) if error else None,
            simulated=False,
        )

    # ------------------------------------------------------------------- reset

    async def reset_task(self, task_id: str, run_id: str) -> dict[str, Any]:
        """Explicit teardown. Idempotent — safe to call twice, or after a crash."""
        task = self._specs.get(task_id)
        if task is None:
            return {"error": f"unknown real task {task_id}"}
        return await self._script(task.reset_script, run_id)

    # ------------------------------------------------------------------ helper

    def _output_path(self, task_id: str, run_id: str) -> Path:
        # Same helper the verifier uses, so the path in the prompt and the path
        # the verifier reads cannot drift apart.
        path = output_path(task_id, run_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        return path

    async def _script(self, path: Path, run_id: str, *extra: str) -> dict[str, Any]:
        """Run a verify/reset script in a subprocess and parse its JSON.

        Subprocess rather than import: these scripts pull in optional Google
        deps and talk to live services, and a crashed verifier must not take
        the runner down with it.
        """
        env = dict(os.environ, HELIXIS_REAL_RUN_ID=run_id)
        proc = await asyncio.create_subprocess_exec(
            self.python,
            str(path),
            "--run-id",
            run_id,
            *extra,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env,
        )
        out, err = await proc.communicate()
        try:
            return json.loads(out.decode() or "{}")
        except json.JSONDecodeError:
            return {
                "error": (
                    f"{path.name} produced no parseable JSON "
                    f"(exit {proc.returncode}): {err.decode()[:500]}"
                ),
                "assertions": [],
            }


def _today() -> str:
    from datetime import date

    return date.today().isoformat()


def real_task_specs(tasks_dir: Path | None = None) -> list[TaskSpec]:
    """Engine-side specs for the real tier.

    `split='real'` pairs with `tier='real'` on the EpisodeResult, which is what
    keeps these episodes out of `epoch_curve` (it filters `tier='mocked'`)
    while leaving them fully visible to the distiller.
    """
    return [
        TaskSpec(task_id=t.id, domain=t.service, split="real")
        for t in discover_tasks(tasks_dir or Path(__file__).resolve().parent)
    ]
