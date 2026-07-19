"""Task execution backends.

`AutomationBenchBackend` is the real one: it drives AutomationBench's
`AutomationBenchEnv` programmatically so we can inject skills per-task and keep
the full trajectory, which shelling out to `auto-bench` would not allow.

`OfflineBackend` is a deterministic simulator used when AutomationBench or a
model endpoint isn't available. It exists so the whole recursive loop — execute,
distill, inject, re-run — can be exercised end to end without credentials. Its
scoring responds to injected skills, so a smoke test produces a real curve shape,
but it is never a substitute for a graded run: episodes it produces are marked
`simulated: true` in their metadata and the CLI refuses to report them as
headline results.
"""

from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass, field
from typing import Any, Protocol

# Must precede any import of automationbench.rubric — STRICT_MODE is read at
# module import time, so setting it later has no effect (Requirement 8.4).
os.environ.setdefault("AUTOMATIONBENCH_STRICT_ASSERTIONS", "0")


@dataclass
class TaskSpec:
    task_id: str
    domain: str
    split: str
    # Provenance and grading semantics, carried from the manifest so the runner,
    # the store and the curve all key off one source of truth (spec 04).
    origin: str = "bench"
    type: str = "bench"
    # The id AutomationBench knows this task by. A user task may carry its own
    # id (`ops.qualify_lead_v2`) while grading against an existing bench task.
    bench_ref: str = ""
    retired: bool = False

    @property
    def bench_task_id(self) -> str:
        return self.bench_ref or self.task_id


@dataclass
class Attempt:
    """Raw outcome of one execution, before it becomes an EpisodeResult."""

    partial_credit: float
    passed: bool
    messages: list[dict[str, Any]]
    assertions: list[dict[str, Any]] = field(default_factory=list)
    end_state: dict[str, Any] | None = None
    tokens_in: int = 0
    tokens_out: int = 0
    steps: int = 0
    error: str | None = None
    simulated: bool = False


class TaskBackend(Protocol):
    async def run(self, spec: TaskSpec, skills_block: str, max_steps: int) -> Attempt: ...


class AutomationBenchBackend:
    """Programmatic wrapper over AutomationBenchEnv."""

    def __init__(self, *, model: str, base_url: str, api_key_var: str, toolset: str = "api"):
        self.model = model
        self.base_url = base_url
        self.api_key_var = api_key_var
        self.toolset = toolset
        self._datasets: dict[str, Any] = {}

    def _dataset(self, domain: str) -> Any:
        if domain not in self._datasets:
            from automationbench.domains import get_domain_dataset

            self._datasets[domain] = get_domain_dataset(domain)
        return self._datasets[domain]

    async def run(self, spec: TaskSpec, skills_block: str, max_steps: int) -> Attempt:
        from automationbench.clients import RetryingOpenAIChatCompletionsClient
        from automationbench.rubric import create_rubric
        from automationbench.runner import AutomationBenchEnv
        from verifiers.types import ClientConfig

        # `bench_task_id`, not `task_id`: a user task may be a renamed reference
        # to an existing bench task, and the dataset only knows the bench id.
        bench_id = spec.bench_task_id
        dataset = self._dataset(spec.domain).filter(lambda row: row["task"] == bench_id)
        if len(dataset) == 0:
            return Attempt(0.0, False, [], error=f"task {bench_id} not found")

        if skills_block:
            dataset = dataset.map(_skill_injector(skills_block))

        env = AutomationBenchEnv(
            dataset=dataset,
            rubric=create_rubric(),
            max_turns=max_steps,
            toolset=self.toolset,
        )
        client = RetryingOpenAIChatCompletionsClient(
            ClientConfig(
                api_key_var=self.api_key_var,
                api_base_url=self.base_url,
                extra_headers={},
            )
        )
        try:
            results = await env.evaluate(
                client=client,
                model=self.model,
                sampling_args={},
                num_examples=-1,
                rollouts_per_example=1,
                max_concurrent=1,
                # The only way _assertion_results / _end_state escape the rollout.
                state_columns=["_usage", "_debug", "_assertion_results", "_end_state", "_perf"],
            )
        except Exception as exc:  # a crashed rollout scores 0, trace preserved
            return Attempt(0.0, False, [], error=f"{type(exc).__name__}: {exc}")

        outputs = results.get("outputs") or []
        if not outputs:
            return Attempt(0.0, False, [], error="no outputs returned")
        out = outputs[0]

        messages = [_plain(m) for m in list(out.get("prompt") or [])]
        messages += [_plain(m) for m in list(out.get("completion") or [])]
        tokens_in, tokens_out = _usage_tokens(out)
        return Attempt(
            partial_credit=float(out.get("reward", 0.0)),
            # Read the metric directly rather than inferring pass from reward.
            passed=bool((out.get("metrics") or {}).get("task_completed_correctly", 0.0) == 1.0),
            messages=messages,
            assertions=list(out.get("_assertion_results") or []),
            end_state=out.get("_end_state"),
            tokens_in=tokens_in,
            tokens_out=tokens_out,
            steps=sum(1 for m in messages if m.get("role") == "assistant"),
        )


def _usage_tokens(out: dict[str, Any]) -> tuple[int, int]:
    """Pull token counts out of a rollout, tolerating naming drift.

    AutomationBench reports `_usage` as `{input_tokens, output_tokens}`, not the
    OpenAI `prompt_tokens`/`completion_tokens` spelling. Getting this wrong is
    silent and expensive: usage reads as zero, every episode costs $0.00, and
    the per-epoch and total budget caps can never fire — the runner would happily
    burn an entire credit balance while reporting no spend. Both spellings are
    accepted so a bench or client upgrade cannot quietly disarm the caps again.
    """
    for key in ("_usage", "token_usage", "usage"):
        usage = out.get(key)
        if not isinstance(usage, dict):
            continue
        tokens_in = usage.get("input_tokens", usage.get("prompt_tokens", 0)) or 0
        tokens_out = usage.get("output_tokens", usage.get("completion_tokens", 0)) or 0
        if tokens_in or tokens_out:
            return int(tokens_in), int(tokens_out)
    return 0, 0


def _skill_injector(skills_block: str):
    """Append the Active Skills block to the task's system message.

    Editing the dataset's `prompt` column is the stable seam: the task literals
    put the system prompt at index 0 and verifiers uses the column verbatim.
    Injecting here also means the block lands in the captured trajectory, so
    every episode records exactly which guidance the agent was given.
    """

    def inject(row: dict[str, Any]) -> dict[str, Any]:
        messages = [dict(m) for m in row["prompt"]]
        if messages and messages[0].get("role") == "system":
            messages[0]["content"] = f"{messages[0]['content']}\n\n{skills_block}"
        else:
            messages.insert(0, {"role": "system", "content": skills_block})
        return {"prompt": messages}

    return inject


class OfflineBackend:
    """Deterministic simulator. No network, no credentials, reproducible."""

    def __init__(self, seed: str = "helixis"):
        self.seed = seed

    async def run(self, spec: TaskSpec, skills_block: str, max_steps: int) -> Attempt:
        # Base difficulty is a stable function of the task id, so re-running an
        # epoch with the same wiki state reproduces the same score.
        base = _unit_hash(f"{self.seed}:{spec.task_id}")
        # Most tasks land mid-band, which is where a learning curve is visible.
        difficulty = 0.15 + 0.7 * base

        n_skills = skills_block.count("### ") if skills_block else 0
        # Diminishing returns, capped: skills help, but never trivialize the task.
        lift = 0.28 * (1 - 0.55**n_skills) if n_skills else 0.0
        # Relevance jitter: not every retrieved skill applies to every task.
        relevance = _unit_hash(f"{spec.task_id}:{n_skills}:relevance")
        score = min(1.0, difficulty + lift * (0.4 + 0.6 * relevance))
        passed = score >= 0.995

        messages = [
            {"role": "system", "content": f"[offline] {spec.task_id}\n\n{skills_block}"},
            {"role": "user", "content": f"Simulated prompt for {spec.task_id}."},
            {
                "role": "assistant",
                "content": (
                    f"[offline-sim] Attempted {spec.task_id} with {n_skills} active "
                    f"skills; scored {score:.2f}."
                ),
            },
        ]
        n_assertions = 4
        n_passed = round(score * n_assertions)
        assertions = [
            {
                "type": ["record_exists", "field_equals", "count_matches", "no_extra_actions"][i],
                "passed": i < n_passed,
                "excluded": False,
                "params": {"task": spec.task_id, "index": i},
            }
            for i in range(n_assertions)
        ]
        return Attempt(
            partial_credit=round(score, 4),
            passed=passed,
            messages=messages,
            assertions=assertions,
            end_state={"simulated": True},
            tokens_in=1200,
            tokens_out=400,
            steps=3,
            simulated=True,
        )


def _unit_hash(text: str) -> float:
    digest = hashlib.sha256(text.encode()).hexdigest()
    return int(digest[:8], 16) / 0xFFFFFFFF


def _plain(msg: Any) -> dict[str, Any]:
    if hasattr(msg, "model_dump"):
        return msg.model_dump()
    if isinstance(msg, dict):
        return dict(msg)
    return {"role": "unknown", "content": str(msg)}
