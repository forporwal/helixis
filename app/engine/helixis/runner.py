"""The epoch runner — the outer loop of the recursive-improvement experiment.

One epoch: for each task, retrieve top-k skills from the wiki, inject them as an
"Active Skills" block, execute, grade, persist the full trajectory. At epoch end
the distiller mines that epoch's failures into new skills and the generation
counter advances, so the next epoch runs against a strictly larger wiki.

Held-out epochs run the identical flow but their episodes are never fed to the
distiller — that is what makes the held-out curve a generalization measurement
rather than a memorization one.
"""

from __future__ import annotations

import asyncio
import json
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable

from .backends import Attempt, AutomationBenchBackend, OfflineBackend, TaskBackend, TaskSpec
from .config import Settings
from .distiller import Distiller
from .llm import LLMClient
from .manifest import Manifest, ManifestError  # noqa: F401  (re-exported)
from .store import EpisodeResult, EpisodeStore
from .wiki import ExperienceWiki


class BudgetExceeded(RuntimeError):
    pass


class GenerationMismatch(RuntimeError):
    """Re-running an epoch would overwrite it with incomparable scores."""


@dataclass
class EpochSummary:
    epoch: int
    split: str
    n_tasks: int
    n_run: int
    n_skipped: int
    mean_partial_credit: float
    pass_rate: float
    cost_usd: float
    wiki_generation: int
    injected_skill_names: set[str]
    simulated: bool


class EpochRunner:
    def __init__(
        self,
        settings: Settings,
        store: EpisodeStore,
        wiki: ExperienceWiki,
        backend: TaskBackend | None = None,
        on_progress: Callable[[str, dict[str, Any]], None] | None = None,
    ):
        self.settings = settings
        self.store = store
        self.wiki = wiki
        self.backend = backend or self._default_backend()
        self.on_progress = on_progress or (lambda _e, _d: None)

    def _default_backend(self) -> TaskBackend:
        if self.settings.agent.is_fake:
            return OfflineBackend()
        try:
            import automationbench  # noqa: F401
        except ImportError:
            return OfflineBackend()
        return AutomationBenchBackend(
            model=self.settings.agent.model,
            base_url=self.settings.agent.base_url,
            api_key_var=self.settings.agent.api_key_var,
            toolset=self.settings.toolset,
        )

    @property
    def is_simulated(self) -> bool:
        return isinstance(self.backend, OfflineBackend)

    # -------------------------------------------------------------- single task

    async def run_task(self, spec: TaskSpec, epoch: int) -> EpisodeResult:
        generation = self.wiki.generation
        skills = self.wiki.retrieve(_retrieval_query(spec), self.settings.top_k_skills)
        block = self.wiki.format_for_injection(skills)
        started = _now()

        self.on_progress("task_start", {"task_id": spec.task_id, "epoch": epoch})

        try:
            attempt = await asyncio.wait_for(
                self.backend.run(spec, block, self.settings.max_steps),
                timeout=self.settings.task_timeout_s,
            )
        except asyncio.TimeoutError:
            attempt = Attempt(
                0.0, False, [],
                error=f"timeout after {self.settings.task_timeout_s}s",
                simulated=self.is_simulated,
            )
        except Exception as exc:
            attempt = Attempt(
                0.0, False, [], error=f"{type(exc).__name__}: {exc}",
                simulated=self.is_simulated,
            )

        cost = self.settings.agent.cost(attempt.tokens_in, attempt.tokens_out)
        finished = _now()

        path = self.store.write_trajectory(
            epoch=epoch,
            split=spec.split,
            task_id=spec.task_id,
            metadata={
                "task_id": spec.task_id,
                "domain": spec.domain,
                "split": spec.split,
                "epoch": epoch,
                "wiki_generation": generation,
                "injected_skills": [s.name for s in skills],
                "origin": spec.origin,
                "task_type": spec.type,
                "bench_ref": spec.bench_task_id,
                "model": self.settings.agent.model,
                "max_steps": self.settings.max_steps,
                "started_at": started,
                "finished_at": finished,
                "simulated": attempt.simulated,
                "error": attempt.error,
            },
            messages=attempt.messages,
            assertions=attempt.assertions,
            end_state=attempt.end_state,
        )

        result = EpisodeResult(
            epoch=epoch,
            task_id=spec.task_id,
            split=spec.split,  # type: ignore[arg-type]
            domain=spec.domain,
            origin=spec.origin,
            partial_credit=attempt.partial_credit,
            passed=attempt.passed,
            steps=attempt.steps,
            tokens_in=attempt.tokens_in,
            tokens_out=attempt.tokens_out,
            cost_usd=cost,
            wiki_generation=generation,
            injected_skills=[s.name for s in skills],
            model=self.settings.agent.model,
            error=attempt.error,
            started_at=started,
            finished_at=finished,
            trajectory_path=str(path),
        )
        self.store.record_episode(result)
        self.on_progress("task_done", {
            "task_id": spec.task_id, "epoch": epoch,
            "partial_credit": result.partial_credit, "passed": result.passed,
        })
        return result

    def _guard_generation(
        self,
        epoch: int,
        split: str,
        specs: list[TaskSpec],
        resume: bool,
        allow_rewrite: bool,
    ) -> None:
        """Refuse to silently rewrite an epoch under a different wiki generation.

        Re-running epoch 0 after the wiki has grown does not reproduce the
        baseline — it injects today's skills into the row that is supposed to
        represent the empty-wiki starting point, and the recorded score silently
        rises. The curve then understates or erases the very improvement the
        experiment exists to measure, and nothing in the output looks wrong.

        This is a live hazard because re-running an old epoch is exactly what an
        operator does to check run-to-run variance. Comparing variance requires
        holding the wiki fixed, which in practice means re-running the *latest*
        epoch, not an earlier one.
        """
        if allow_rewrite:
            return
        recorded = self.store.recorded_generations(epoch, split)
        current = self.wiki.generation
        stale = recorded - {current}
        if not stale:
            return
        # With resume on, existing episodes are skipped. A fully-complete epoch
        # is therefore a no-op and needs no guard; a partial one would run its
        # remaining tasks under the new generation, mixing two wiki states into
        # a single epoch's mean, which is worse than either alone.
        if resume and {s.task_id for s in specs} <= self.store.completed_task_ids(
            epoch, split
        ):
            return
        raise GenerationMismatch(
            f"epoch {epoch} [{split}] was recorded under wiki generation(s) "
            f"{sorted(stale)}, but the wiki is now at generation {current}. "
            f"Re-running would overwrite those scores with skills the original "
            f"run did not have, which corrupts the baseline.\n"
            f"  - To measure run-to-run variance, re-run the LATEST epoch instead.\n"
            f"  - To reproduce the original numbers, restore an archived DB.\n"
            f"  - If you really intend to overwrite, pass --allow-rewrite."
        )

    # ------------------------------------------------------------------- epoch

    async def run_epoch(
        self,
        epoch: int,
        specs: list[TaskSpec],
        split: str,
        resume: bool = True,
        allow_rewrite: bool = False,
    ) -> EpochSummary:
        self._guard_generation(epoch, split, specs, resume, allow_rewrite)
        done = self.store.completed_task_ids(epoch, split) if resume else set()
        pending = [s for s in specs if s.task_id not in done]
        self.store.start_epoch(epoch, split, len(specs), self.wiki.generation)
        self.on_progress("epoch_start", {
            "epoch": epoch, "split": split,
            "n_tasks": len(specs), "n_pending": len(pending),
            "wiki_generation": self.wiki.generation,
            "n_skills": len(self.wiki),
        })

        # Budget guard is checked between tasks, not just up front, so a runaway
        # epoch stops mid-flight rather than after burning the whole cap.
        sem = asyncio.Semaphore(self.settings.max_concurrent_tasks)
        aborted = False

        async def guarded(spec: TaskSpec) -> EpisodeResult | None:
            nonlocal aborted
            async with sem:
                if aborted:
                    return None
                if self.store.epoch_cost(epoch) >= self.settings.epoch_cost_cap_usd:
                    aborted = True
                    return None
                if self.store.total_cost() >= self.settings.total_cost_cap_usd:
                    aborted = True
                    return None
                return await self.run_task(spec, epoch)

        await asyncio.gather(*(guarded(s) for s in pending))
        self.store.finish_epoch(epoch, split, "aborted_budget" if aborted else "done")

        episodes = self.store.query_episodes(epoch=epoch, split=split)
        injected: set[str] = set()
        for e in episodes:
            injected.update(e["injected_skills"])
        summary = EpochSummary(
            epoch=epoch,
            split=split,
            n_tasks=len(specs),
            n_run=len(pending),
            n_skipped=len(done),
            mean_partial_credit=(
                sum(e["partial_credit"] for e in episodes) / len(episodes) if episodes else 0.0
            ),
            pass_rate=(sum(e["passed"] for e in episodes) / len(episodes) if episodes else 0.0),
            cost_usd=self.store.epoch_cost(epoch),
            wiki_generation=self.wiki.generation,
            injected_skill_names=injected,
            simulated=self.is_simulated,
        )
        self.on_progress("epoch_done", {
            "epoch": epoch, "split": split,
            "mean_partial_credit": summary.mean_partial_credit,
            "pass_rate": summary.pass_rate,
            "cost_usd": summary.cost_usd,
            "aborted": aborted,
        })
        if aborted:
            raise BudgetExceeded(
                f"epoch {epoch} hit the cost cap "
                f"(epoch ${self.store.epoch_cost(epoch):.2f} / "
                f"total ${self.store.total_cost():.2f})"
            )
        return summary


class Experiment:
    """Drives the multi-epoch experiment: run → distill → run → …"""

    def __init__(
        self,
        settings: Settings,
        store: EpisodeStore,
        wiki: ExperienceWiki,
        manifest: Manifest,
        runner: EpochRunner | None = None,
        distiller: Distiller | None = None,
    ):
        self.settings = settings
        self.store = store
        self.wiki = wiki
        self.manifest = manifest
        self.runner = runner or EpochRunner(settings, store, wiki)
        self.distiller = distiller or Distiller(
            settings, store, wiki, LLMClient(settings.distiller)
        )

    async def run(
        self, n_epochs: int = 6, heldout_at: tuple[int, ...] = (0, 3, 6)
    ) -> list[dict[str, Any]]:
        log: list[dict[str, Any]] = []
        for split in ("train", "heldout"):
            warn_skipped_tasks(self.manifest, split, lambda m: print(m, file=sys.stderr))
        for epoch in range(n_epochs):
            if epoch in heldout_at:
                heldout = await self.runner.run_epoch(
                    epoch, self.manifest.heldout, "heldout"
                )
                log.append({"kind": "heldout", **_summary_dict(heldout)})

            train = await self.runner.run_epoch(epoch, self.manifest.train, "train")
            log.append({"kind": "train", **_summary_dict(train)})

            # Distillation failure must never block the next epoch — skills are
            # additive and the last-good wiki stays on disk.
            try:
                result = await self.distiller.distill(epoch)
                log.append({
                    "kind": "distill", "epoch": epoch,
                    "gated_out": result.gated_out, "reason": result.reason,
                    "n_failures": result.n_failures,
                    "skills": [s.name for s in result.skills],
                    "generation": result.generation,
                })
                if result.skills:
                    from .pages import regenerate_pages

                    regenerate_pages(self.wiki, self.store)
            except Exception as exc:
                log.append({"kind": "distill_error", "epoch": epoch, "error": str(exc)})

        final = n_epochs
        if final in heldout_at:
            heldout = await self.runner.run_epoch(final, self.manifest.heldout, "heldout")
            log.append({"kind": "heldout", **_summary_dict(heldout)})
        return log


def _summary_dict(s: EpochSummary) -> dict[str, Any]:
    return {
        "epoch": s.epoch, "split": s.split, "n_tasks": s.n_tasks,
        "n_run": s.n_run, "n_skipped": s.n_skipped,
        "mean_partial_credit": round(s.mean_partial_credit, 4),
        "pass_rate": round(s.pass_rate, 4), "cost_usd": round(s.cost_usd, 4),
        "wiki_generation": s.wiki_generation,
        "n_injected_skills": len(s.injected_skill_names),
        "simulated": s.simulated,
    }


def warn_skipped_tasks(
    manifest: Manifest,
    split: str,
    emit: Callable[[str], None],
) -> None:
    """Say out loud which manifest tasks this epoch will NOT run, and why.

    A mocked epoch cannot execute a `real` task — it needs live credentials and
    the real-tier driver. Dropping them silently would make the epoch's task
    count quietly disagree with the manifest, which is exactly the kind of
    invisible shrink that makes a curve untrustworthy.
    """
    for entry in manifest.skipped_in_split(split):
        emit(
            f"  skipping {entry.id} ({entry.type} task): a mocked epoch cannot "
            f"grade it. Run it through the real-tier driver."
        )
    for issue in manifest.warnings:
        emit(f"  manifest warning — {issue.task_id}: {issue.message}")


def _retrieval_query(spec: TaskSpec) -> str:
    """Task ids are structured (`domain.snake_case_action`) and are the only
    task text available before execution, so they are the retrieval key."""
    _, _, action = spec.task_id.partition(".")
    return f"{spec.domain} {action.replace('_', ' ')}"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()
