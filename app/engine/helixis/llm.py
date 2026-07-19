"""Provider-agnostic OpenAI-compatible chat client with a deterministic offline stub.

Both model tiers (agent, distiller) speak the same interface. The only thing that
distinguishes Featherless from Fireworks from a local vLLM pod is `base_url` and
which env var holds the key — see `helixis.config.ModelTier`.

`BatchStats` exists for the vLLM bounty writeup: it records wall-clock and
token throughput for a burst of concurrent requests, which is the evidence that
batching is actually being exploited rather than claimed.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import time
from dataclasses import dataclass, field
from typing import Any, Sequence

from openai import AsyncOpenAI

from .config import ModelTier

Message = dict[str, Any]


@dataclass
class Completion:
    text: str
    tokens_in: int
    tokens_out: int
    latency_s: float
    model: str

    @property
    def tokens(self) -> int:
        return self.tokens_in + self.tokens_out


@dataclass
class BatchStats:
    """Throughput evidence for a concurrent burst (vLLM batching showcase)."""

    n_requests: int = 0
    n_failed: int = 0
    concurrency: int = 0
    wall_clock_s: float = 0.0
    tokens_in: int = 0
    tokens_out: int = 0
    latencies: list[float] = field(default_factory=list)

    @property
    def tokens_total(self) -> int:
        return self.tokens_in + self.tokens_out

    @property
    def output_tokens_per_s(self) -> float:
        return self.tokens_out / self.wall_clock_s if self.wall_clock_s else 0.0

    @property
    def requests_per_s(self) -> float:
        return self.n_requests / self.wall_clock_s if self.wall_clock_s else 0.0

    @property
    def mean_latency_s(self) -> float:
        return sum(self.latencies) / len(self.latencies) if self.latencies else 0.0

    @property
    def serial_estimate_s(self) -> float:
        """What the same work would have cost one-at-a-time."""
        return sum(self.latencies)

    @property
    def batching_speedup(self) -> float:
        return self.serial_estimate_s / self.wall_clock_s if self.wall_clock_s else 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "n_requests": self.n_requests,
            "n_failed": self.n_failed,
            "concurrency": self.concurrency,
            "wall_clock_s": round(self.wall_clock_s, 3),
            "tokens_in": self.tokens_in,
            "tokens_out": self.tokens_out,
            "output_tokens_per_s": round(self.output_tokens_per_s, 1),
            "requests_per_s": round(self.requests_per_s, 2),
            "mean_latency_s": round(self.mean_latency_s, 3),
            "serial_estimate_s": round(self.serial_estimate_s, 3),
            "batching_speedup": round(self.batching_speedup, 2),
        }


class LLMClient:
    """Async chat client for one model tier."""

    def __init__(self, tier: ModelTier):
        self.tier = tier
        self._sem = asyncio.Semaphore(tier.max_concurrency)
        self._client: AsyncOpenAI | None = None
        if not tier.is_fake:
            self._client = AsyncOpenAI(
                api_key=tier.api_key or "not-needed",
                base_url=tier.base_url,
                max_retries=4,
                timeout=180.0,
            )

    async def complete(
        self,
        messages: Sequence[Message],
        *,
        temperature: float = 0.6,
        max_tokens: int = 3000,
        response_format: dict[str, Any] | None = None,
    ) -> Completion:
        async with self._sem:
            started = time.perf_counter()
            if self._client is None:
                text, tin, tout = _fake_completion(messages, max_tokens)
                # Simulate enough latency that concurrency is observable in stats.
                await asyncio.sleep(0.05)
            else:
                kwargs: dict[str, Any] = {
                    "model": self.tier.model,
                    "messages": list(messages),
                    "temperature": temperature,
                    "max_tokens": max_tokens,
                }
                if response_format:
                    kwargs["response_format"] = response_format
                resp = await self._client.chat.completions.create(**kwargs)
                text = resp.choices[0].message.content or ""
                usage = resp.usage
                tin = getattr(usage, "prompt_tokens", 0) or 0
                tout = getattr(usage, "completion_tokens", 0) or 0
            return Completion(
                text=text,
                tokens_in=tin,
                tokens_out=tout,
                latency_s=time.perf_counter() - started,
                model=self.tier.model,
            )

    async def complete_many(
        self, batches: Sequence[Sequence[Message]], **kwargs: Any
    ) -> tuple[list[Completion | None], BatchStats]:
        """Fire a burst concurrently. This is the deliberate vLLM batching path.

        Individual failures resolve to None rather than sinking the batch — a
        distillation call that errors must never block the next epoch.
        """
        stats = BatchStats(
            n_requests=len(batches), concurrency=self.tier.max_concurrency
        )
        started = time.perf_counter()
        results = await asyncio.gather(
            *(self.complete(b, **kwargs) for b in batches), return_exceptions=True
        )
        stats.wall_clock_s = time.perf_counter() - started

        out: list[Completion | None] = []
        for r in results:
            if isinstance(r, BaseException):
                stats.n_failed += 1
                out.append(None)
                continue
            stats.tokens_in += r.tokens_in
            stats.tokens_out += r.tokens_out
            stats.latencies.append(r.latency_s)
            out.append(r)
        return out, stats


def extract_json_array(text: str) -> list[dict[str, Any]]:
    """Pull a JSON array out of a model response that may be fenced or chatty."""
    cleaned = text.strip()
    if "```" in cleaned:
        # Drop fences without assuming a language tag.
        parts = cleaned.split("```")
        cleaned = max(parts, key=len)
        if cleaned.lstrip().lower().startswith("json"):
            cleaned = cleaned.lstrip()[4:]
    start, end = cleaned.find("["), cleaned.rfind("]")
    if start == -1 or end == -1 or end < start:
        return []
    try:
        parsed = json.loads(cleaned[start : end + 1])
    except json.JSONDecodeError:
        return []
    return [p for p in parsed if isinstance(p, dict)]


def _fake_completion(
    messages: Sequence[Message], max_tokens: int
) -> tuple[str, int, int]:
    """Deterministic offline stub so the whole loop runs without credentials.

    Keyed on a hash of the prompt so repeated runs are reproducible (Requirement
    1.5: no hidden state outside the wiki).
    """
    blob = json.dumps(list(messages), sort_keys=True, default=str)
    digest = hashlib.sha256(blob.encode()).hexdigest()
    tokens_in = max(1, len(blob) // 4)

    if "skill engineer" in blob.lower():
        idx = int(digest[:4], 16) % 1000
        text = json.dumps(
            [
                {
                    "name": f"dyn-{idx:03d}",
                    "description": (
                        "Use when a task requires acting on every item in a "
                        "retrieved list. Ensures no list items are silently dropped."
                    ),
                    "content": (
                        "## Act On Every Retrieved Item\n\n"
                        "1. Count the items returned by the search before acting.\n"
                        "2. Act on each item individually; do not batch-summarize.\n"
                        "3. Re-read the final state and confirm the count matches.\n\n"
                        "**Anti-pattern:** Narrating 'processed all records' after "
                        "acting on only the first page of results."
                    ),
                    "category": "automation",
                }
            ]
        )
    elif "quality reviewer" in blob.lower():
        text = f"Offline stub verdict.\nScore: {[1, 0, -1][int(digest[:2], 16) % 3]}"
    elif "summarize the workflow" in blob.lower():
        # Task miner, stage 1. Drawn from a small fixed pool rather than made
        # unique per prompt: the miner's job is to CLUSTER these, and a stub
        # that returned a distinct workflow per episode would make every cluster
        # a singleton and the offline path would never exercise the interesting
        # half of the module.
        pool = [
            ("compile a weekly vendor spend digest from invoice emails", "finance"),
            ("triage the support inbox and escalate billing complaints", "support"),
            ("reconcile the shipping tracker against fulfilled orders", "ops"),
        ]
        workflow, domain = pool[int(digest[:4], 16) % len(pool)]
        text = json.dumps(
            {"workflow": workflow, "domain": domain, "entities": [], "note": ""}
        )
    elif "drafting a training task" in blob.lower():
        # Task miner, stage 2. Deliberately emits a NEGATIVE ASSERTION marker
        # and a TODO(human), because those are exactly what the engine-side
        # validator checks for — a stub that skipped them would make the offline
        # path test a validator that always fails.
        idx = int(digest[:4], 16) % 1000
        text = json.dumps({
            "id": f"ops.stub_workflow_{idx:03d}",
            "domain": "ops",
            "prompt": (
                "[offline-stub] Compile the recurring digest described in the "
                "sessions above. Read the source records, produce the summary "
                "document, and file it where the previous ones live."
            ),
            "verify_py": (
                "def verify() -> bool:\n"
                "    # TODO(human): point this at the real end state.\n"
                "    digest = load_digest()\n"
                "    assert digest is not None\n"
                "    # NEGATIVE ASSERTION\n"
                "    assert not digest.contains_placeholder_rows()\n"
                "    return True\n"
            ),
            "reset_py": (
                "def reset() -> None:\n"
                "    # TODO(human): make this idempotent against the real store.\n"
                "    delete_digest_if_present()\n"
            ),
        })
    else:
        text = f"[offline-stub] deterministic response {digest[:12]}"

    return text, tokens_in, max(1, min(max_tokens, len(text) // 4))
