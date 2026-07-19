"""Shared fixtures for the engine test suite.

Everything here builds a Helixis that lives entirely under `tmp_path`: its own
database, runs directory, manifests and policy tree. Nothing touches the repo's
real `runs/helixis.db` or `tasks.user.yaml`, because a test suite that can
mutate the operator's actual curriculum is a test suite nobody will run twice.
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import Any, Sequence

import pytest
import yaml

from helixis.config import FAKE_BASE_URL, ModelTier, Paths, Settings
from helixis.llm import BatchStats, Completion
from helixis.manifest import Manifest
from helixis.store import EpisodeResult, EpisodeStore


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    tier = ModelTier(
        model="test-model",
        base_url=FAKE_BASE_URL,
        api_key_var="UNUSED",
        max_concurrency=4,
    )
    paths = Paths(
        root=tmp_path,
        runs=tmp_path / "runs",
        wiki=tmp_path / "wiki",
        db=tmp_path / "runs" / "helixis.db",
        manifest=tmp_path / "tasks.yaml",
        user_manifest=tmp_path / "tasks.user.yaml",
        policy=tmp_path / "policy",
        real_tier=tmp_path / "real_tier",
        claw_sessions=tmp_path / "runs" / "claw-sessions",
    )
    paths.ensure()
    # A frozen bench manifest has to exist — `Manifest.load` treats its absence
    # as a fatal misconfiguration, which is right in production and unhelpful
    # here.
    paths.manifest.write_text(
        yaml.safe_dump({"train": [{"id": "sales.qualify_lead", "domain": "sales"}], "heldout": []}),
        encoding="utf-8",
    )
    return Settings(agent=tier, distiller=tier, paths=paths, mine_min_occurrences=2)


@pytest.fixture
def store(settings: Settings) -> EpisodeStore:
    return EpisodeStore(settings.paths.db, settings.paths.runs)


@pytest.fixture
def manifest(settings: Settings) -> Manifest:
    return Manifest.load(
        settings.paths.manifest,
        settings.paths.user_manifest,
        root=settings.paths.root,
        strict=False,
        check_bench=False,
    )


@pytest.fixture
def make_real_episode(settings: Settings, store: EpisodeStore):
    """Record a `tier='real'` episode with a readable trajectory on disk."""

    def _make(task_id: str, *, user_text: str, assistant_text: str = "Done.") -> int:
        path = settings.paths.runs / "real" / f"{task_id}.jsonl"
        store.write_trajectory(
            epoch=0,
            split="real",
            task_id=task_id,
            path=path,
            metadata={"source": "test", "simulated": False},
            messages=[
                {"role": "user", "content": user_text},
                {"role": "assistant", "content": assistant_text, "tool_calls": []},
            ],
            assertions=[],
        )
        return store.record_episode(
            EpisodeResult(
                epoch=0,
                task_id=task_id,
                split="real",
                domain="claw",
                tier="real",
                origin="claw",
                passed=True,
                trajectory_path=str(path),
            )
        )

    return _make


class FakeClient:
    """An LLMClient stand-in with a scripted reply queue.

    Scripted rather than stubbed-by-content because the tests that matter here
    are about what the miner does with a BAD reply — an unparseable draft, one
    missing its negative assertion — and those are exactly the replies a
    content-keyed stub will not produce.
    """

    def __init__(self, replies: Sequence[str], model: str = "test-model"):
        self.replies = list(replies)
        self.model = model
        self.calls: list[list[dict[str, Any]]] = []

    def _next(self) -> str:
        # Repeat the last reply once exhausted, so a test that only cares about
        # the first call does not have to script the repair round too.
        return self.replies.pop(0) if len(self.replies) > 1 else (self.replies[0] if self.replies else "")

    async def complete(self, messages, **kwargs) -> Completion:
        self.calls.append(list(messages))
        return Completion(
            text=self._next(), tokens_in=10, tokens_out=20, latency_s=0.01, model=self.model
        )

    async def complete_many(self, batches, **kwargs) -> tuple[list[Completion | None], BatchStats]:
        stats = BatchStats(n_requests=len(batches), concurrency=4, wall_clock_s=0.1)
        out: list[Completion | None] = []
        for b in batches:
            c = await self.complete(b, **kwargs)
            stats.tokens_in += c.tokens_in
            stats.tokens_out += c.tokens_out
            stats.latencies.append(c.latency_s)
            out.append(c)
        return out, stats


class FailingClient(FakeClient):
    """Every call raises — the vLLM-outage path."""

    async def complete(self, messages, **kwargs) -> Completion:
        raise RuntimeError("endpoint unreachable")

    async def complete_many(self, batches, **kwargs):
        stats = BatchStats(
            n_requests=len(batches), n_failed=len(batches), concurrency=4, wall_clock_s=0.1
        )
        return [None] * len(batches), stats


@pytest.fixture
def fake_client():
    return FakeClient


@pytest.fixture
def failing_client():
    return FailingClient


def settings_with(base: Settings, **overrides: Any) -> Settings:
    return replace(base, **overrides)
