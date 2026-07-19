"""Command-line entrypoints for running and inspecting the experiment.

    helixis run --epochs 6              full experiment
    helixis epoch --epoch 2             one epoch
    helixis heldout --epoch 3           held-out eval only
    helixis distill --epoch 2           distillation only
    helixis report                      the learning curve
    helixis wiki                        skill bank state
    helixis tail-policy                 ingest OpenShell denial events
    helixis rehearse                    adversarial containment rehearsal
    helixis task add|list|remove|validate    manage your own tasks
    helixis ingest-real                 real Claw sessions -> episodes
    helixis train-cycle                 ingest -> distill -> pages -> mine
    helixis mine-tasks                  propose tasks from real usage
    helixis proposal list|show|approve|reject    review mined tasks
    helixis preflight                   what each training mode would do now
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import io
import json
import sys
from pathlib import Path
from typing import Any

import yaml

from .backends import OfflineBackend
from .config import SETTINGS, Settings
from .distiller import Distiller
from .llm import LLMClient
from .manifest import (
    Manifest,
    ManifestError,
    TaskEntry,
    load_user_entries,
    validate_entries,
    write_user_manifest,
)
from .pages import regenerate_pages, wiki_snapshot
from .runner import (
    BudgetExceeded,
    EpochRunner,
    Experiment,
    GenerationMismatch,
    warn_skipped_tasks,
)
from .store import EpisodeStore
from .wiki import ExperienceWiki


def build(settings: Settings = SETTINGS) -> tuple[EpisodeStore, ExperienceWiki, Manifest]:
    settings.paths.ensure()
    store = EpisodeStore(settings.paths.db, settings.paths.runs)
    wiki = ExperienceWiki(
        settings.paths.wiki,
        retrieval_mode=settings.retrieval_mode,
        embedding_model=settings.embedding_model,
    )
    manifest = Manifest.load(
        settings.paths.manifest,
        settings.paths.user_manifest,
        root=settings.paths.root,
    )
    return store, wiki, manifest


def _progress(event: str, data: dict[str, Any]) -> None:
    if event == "epoch_start":
        print(
            f"\n=== epoch {data['epoch']} [{data['split']}] — "
            f"{data['n_pending']}/{data['n_tasks']} to run, "
            f"wiki gen {data['wiki_generation']} ({data['n_skills']} skills) ==="
        )
    elif event == "task_done":
        mark = "PASS" if data["passed"] else "fail"
        print(f"  [{mark}] {data['task_id']:<48} {data['partial_credit']:.3f}")
    elif event == "epoch_done":
        print(
            f"--- epoch {data['epoch']} [{data['split']}]: "
            f"mean partial credit {data['mean_partial_credit']:.3f}, "
            f"pass rate {data['pass_rate']:.0%}, ${data['cost_usd']:.2f}"
        )


def _warn_if_simulated(runner: EpochRunner, forced: bool = False) -> None:
    """Say tasks are simulated — and whether that was a choice or a fallback.

    The two cases need different endings: a forced offline run is working as
    asked and needs no fix, while a fallback is a configuration problem the
    operator probably wants to know how to correct.
    """
    if not runner.is_simulated:
        return
    why = (
        "--offline was passed, so tasks are being SIMULATED"
        if forced
        else (
            "AutomationBench or a model endpoint is not configured, so tasks "
            "are being SIMULATED"
        )
    )
    fix = (
        "!! Drop --offline to run against the configured endpoint.\n"
        if forced
        else "!! Set HELIXIS_AGENT_BASE_URL and install automation-bench for real runs.\n"
    )
    print(
        f"\n!! OFFLINE MODE — {why}.\n"
        "!! These numbers exercise the loop but are NOT experimental results.\n"
        f"{fix}",
        file=sys.stderr,
    )


# --------------------------------------------------------------------- commands


async def cmd_run(args: argparse.Namespace) -> int:
    store, wiki, manifest = build()
    runner = EpochRunner(SETTINGS, store, wiki, on_progress=_progress)
    _warn_if_simulated(runner)
    distiller = Distiller(SETTINGS, store, wiki, LLMClient(SETTINGS.distiller))
    experiment = Experiment(SETTINGS, store, wiki, manifest, runner, distiller)

    heldout_at = tuple(int(x) for x in args.heldout_at.split(",") if x.strip())
    try:
        log = await experiment.run(n_epochs=args.epochs, heldout_at=heldout_at)
    except BudgetExceeded as exc:
        print(f"\nBUDGET STOP: {exc}", file=sys.stderr)
        return 2

    out = SETTINGS.paths.runs / "experiment-log.json"
    out.write_text(json.dumps(log, indent=2))
    print(f"\nExperiment log -> {out}")
    _print_report(store, runner.is_simulated)
    return 0


async def cmd_epoch(args: argparse.Namespace) -> int:
    store, wiki, manifest = build()
    # `--offline` forces the stub even when a real endpoint is configured, so
    # "simulated" is a mode you can pick rather than only a thing that happens
    # to you when config is missing. It can only ever make a run cheaper and
    # more fake, never the reverse, which is why it needs no further guard.
    forced_offline = getattr(args, "offline", False)
    backend = OfflineBackend() if forced_offline else None
    runner = EpochRunner(SETTINGS, store, wiki, backend=backend, on_progress=_progress)
    _warn_if_simulated(runner, forced=forced_offline)
    warn_skipped_tasks(manifest, args.split, lambda m: print(m, file=sys.stderr))
    specs = manifest.for_split(args.split)
    try:
        await runner.run_epoch(
            args.epoch,
            specs,
            args.split,
            resume=not args.no_resume,
            allow_rewrite=getattr(args, "allow_rewrite", False),
        )
    except GenerationMismatch as exc:
        print(f"\nREFUSED: {exc}", file=sys.stderr)
        return 3
    except BudgetExceeded as exc:
        print(f"\nBUDGET STOP: {exc}", file=sys.stderr)
        return 2
    return 0


async def cmd_distill(args: argparse.Namespace) -> int:
    store, wiki, _ = build()
    distiller = Distiller(SETTINGS, store, wiki, LLMClient(SETTINGS.distiller))
    result = await distiller.distill(args.epoch)
    if result.gated_out:
        print(f"Distillation gated out: {result.reason}")
        return 0
    print(f"Distilled {len(result.skills)} skill(s) from {result.n_failures} failures:")
    for skill in result.skills:
        print(f"  - {skill.name}: {skill.description}")
    print(f"Wiki generation is now {result.generation}.")
    if result.skills:
        written = regenerate_pages(wiki, store)
        if written:
            print(f"Regenerated pages: {', '.join(written)}")
    return 0


def _readiness(store: EpisodeStore) -> tuple[int, int, bool]:
    """New real episodes since the last distillation, and whether that's enough."""
    n_new = store.new_real_episodes_since_distill()
    threshold = SETTINGS.real_train_threshold
    return n_new, threshold, n_new >= threshold


async def cmd_ingest_real(args: argparse.Namespace) -> int:
    """Turn captured Helixis Claw sessions into tier='real' episodes."""
    from .ingest import RealSessionIngestor

    store, wiki, _ = build()
    # No endpoint configured means no judge rather than a stubbed one: a fake
    # label is worse than an honest absence, because the distiller would treat
    # it as evidence. Episodes still land — they are usage, and the nudge counts
    # them — they are simply unlabeled until a re-run with a live endpoint.
    judge = None
    if not SETTINGS.distiller.is_fake:
        judge = Distiller(SETTINGS, store, wiki, LLMClient(SETTINGS.distiller))
    elif not args.no_judge:
        print(
            "NOTE: the distiller tier is in offline mode, so sessions are being "
            "stored UNLABELED. Point HELIXIS_DISTILLER_BASE_URL at the vLLM "
            "endpoint and re-run with --force to judge them.",
            file=sys.stderr,
        )

    ingestor = RealSessionIngestor(SETTINGS, store, wiki.generation, judge=judge)

    while True:
        report = await ingestor.ingest(force=args.force)
        if report.ingested:
            print(
                f"Ingested {len(report.ingested)} session(s): "
                f"{report.judged} judged, {report.unjudged} unlabeled, "
                f"{report.redactions} redaction(s)."
            )
            for session_id in report.ingested:
                print(f"  + {session_id}")
        elif not args.watch:
            print(
                f"No new sessions to ingest "
                f"({len(report.skipped)} already ingested)."
            )
        for session_id, error in report.quarantined:
            print(f"  ! quarantined {session_id}: {error}", file=sys.stderr)

        n_new, threshold, ready = _readiness(store)
        if report.ingested:
            print(f"Train readiness: {n_new}/{threshold} new real episodes.")

        if ready and SETTINGS.auto_train:
            # Req 4.2: at the threshold, with auto-train explicitly enabled,
            # the same cycle the nudge button fires runs on its own.
            print(f"\nauto_train is on and {n_new} >= {threshold} — running train-cycle.")
            code = await _train_cycle(skip_ingest=True)
            if code != 0:
                return code
        elif ready:
            # Req 4.3: the default path says so and stops.
            print(
                f"Ready to train ({n_new}/{threshold}). auto_train is off, so "
                f"nothing runs until you trigger it: `helixis train-cycle`."
            )

        if not args.watch:
            return 0
        await asyncio.sleep(args.interval)


async def _train_cycle(skip_ingest: bool = False) -> int:
    """ingest-real -> distill -> pages, honoring cost caps between steps.

    The chain is the point: a nudge that only ingested, or only distilled,
    would leave the loop half-closed and the user holding the other half.
    """
    from .ingest import RealSessionIngestor

    store, wiki, _ = build()

    if not skip_ingest:
        judge = (
            None if SETTINGS.distiller.is_fake
            else Distiller(SETTINGS, store, wiki, LLMClient(SETTINGS.distiller))
        )
        ingestor = RealSessionIngestor(SETTINGS, store, wiki.generation, judge=judge)
        report = await ingestor.ingest()
        print(
            f"[1/4] ingest-real: {len(report.ingested)} new, "
            f"{report.judged} judged, {report.redactions} redaction(s)."
        )
    else:
        print("[1/4] ingest-real: already done this pass.")

    # Cost caps abort the cycle between steps rather than mid-distillation, so
    # the wiki is never left half-written (design.md, Error handling).
    total = store.total_cost()
    if total >= SETTINGS.total_cost_cap_usd:
        print(
            f"BUDGET STOP: total spend ${total:.2f} has reached the cap "
            f"${SETTINGS.total_cost_cap_usd:.2f}; skipping distillation.",
            file=sys.stderr,
        )
        return 2

    epoch = store.last_epoch() or 0
    distiller = Distiller(SETTINGS, store, wiki, LLMClient(SETTINGS.distiller))
    result = await distiller.distill(epoch)
    if result.gated_out:
        print(f"[2/4] distill: gated out — {result.reason}")
        print("[3/4] pages: skipped (no new skills).")
        # Mining still runs: it reads real episodes directly and has nothing to
        # do with whether those episodes happened to produce a skill. Gating it
        # on distillation would mean a stretch of successful real work — the
        # case where there is most to learn about what the user DOES — proposed
        # nothing at all.
        await _mine_step(store, label="[4/4]")
        return 0

    print(f"[2/4] distill: {len(result.skills)} new skill(s) from {result.n_failures} failures.")
    for skill in result.skills:
        print(f"  - {skill.name}: {skill.description}")

    written = regenerate_pages(wiki, store)
    print(f"[3/4] pages: {', '.join(written) if written else 'no changes'}.")

    await _mine_step(store, label="[4/4]")

    if result.skills:
        # The completion signal the home feed renders as "N new skills live"
        # (Req 4.4). Written to the wiki's own history so it survives the DB
        # and travels with the wiki that the skills landed in.
        wiki.append_history({
            "event": "real_train_cycle",
            "epoch": epoch,
            "generation": result.generation,
            "skills": [s.name for s in result.skills],
            "n_failures_considered": result.n_failures,
        })
        print(
            f"\n{len(result.skills)} new skill(s) live at wiki generation "
            f"{result.generation}. The nemoclaw wiki-sync loop delivers them to "
            f"Helixis Claw within 30s — no container restart."
        )
    return 0


async def cmd_train_cycle(args: argparse.Namespace) -> int:
    return await _train_cycle()


# ------------------------------------------------------------- task mining
#
# The miner PROPOSES. Every path below stops at a stored proposal — enacting one
# takes `proposal approve`, which goes back through `task add` so there stays
# exactly one writer of tasks.user.yaml (spec 05, design §3).


async def _mine(
    store: EpisodeStore,
    *,
    min_occurrences: int | None = None,
    max_proposals: int | None = None,
) -> Any:
    from .miner import TaskMiner

    manifest = _merged(check_bench=False)
    miner = TaskMiner(SETTINGS, store, manifest, LLMClient(SETTINGS.distiller))
    return await miner.mine(min_occurrences=min_occurrences, max_proposals=max_proposals)


async def _mine_step(store: EpisodeStore, *, label: str) -> None:
    """The train-cycle's attached mining step (Req 3.1).

    Never fails the cycle. Distillation has already landed skills by this point,
    and aborting the run because the miner found nothing to propose would report
    a successful training cycle as a failure.
    """
    if not SETTINGS.mine_on_train_cycle:
        print(f"{label} mine-tasks: disabled (mine_on_train_cycle is off).")
        return
    try:
        result = await _mine(store)
    except Exception as exc:  # noqa: BLE001 — mining is the optional tail of the cycle
        print(f"{label} mine-tasks: failed — {type(exc).__name__}: {exc}", file=sys.stderr)
        return
    if result.aborted:
        print(f"{label} mine-tasks: aborted — {result.reason}")
        return
    if not result.proposals:
        print(f"{label} mine-tasks: nothing new to propose ({result.reason or 'no clusters survived filtering'}).")
        return
    print(
        f"{label} mine-tasks: {len(result.proposals)} task proposal(s) awaiting "
        f"your review on the home feed."
    )
    for p in result.proposals:
        print(f"  ? {p['id']} — {p['title']} (seen {p['occurrences']}x)")


async def cmd_mine_tasks(args: argparse.Namespace) -> int:
    store, _, _ = build()
    result = await _mine(
        store,
        min_occurrences=1 if args.allow_single else args.min_occurrences,
        max_proposals=args.max_proposals,
    )

    if args.json:
        print(json.dumps(result.to_dict(), indent=2))
        return 2 if result.aborted else 0

    if result.aborted:
        print(f"Mining aborted: {result.reason}", file=sys.stderr)
        return 2

    print(
        f"Read {result.n_episodes} real episode(s) -> {result.n_clusters} workflow "
        f"cluster(s) -> {len(result.proposals)} proposal(s)."
    )
    for p in result.proposals:
        print(f"\n  {p['id']}  [{p['domain']}, seen {p['occurrences']}x]")
        print(f"    {p['title']}")
    for reason in result.dropped:
        print(f"  dropped — {reason}", file=sys.stderr)

    if result.proposals:
        print(
            f"\n{len(result.proposals)} proposal(s) are pending your review. "
            f"`helixis proposal list`, or approve from the home feed. Nothing "
            f"enters the curriculum until you say so."
        )
    elif not result.n_episodes:
        print(f"\n{result.reason}.")
    if SETTINGS.distiller.is_fake:
        print(
            "\nNOTE: the distiller tier is in offline mode, so these came from "
            "the deterministic stub, not Nemotron. Point "
            "HELIXIS_DISTILLER_BASE_URL at the vLLM endpoint for real mining.",
            file=sys.stderr,
        )
    return 0


# ------------------------------------------------------------ proposal review


def _proposal_or_exit(store: EpisodeStore, proposal_id: str) -> dict[str, Any] | None:
    proposal = store.get_task_proposal(proposal_id)
    if proposal is None:
        print(f"No task proposal `{proposal_id}`.", file=sys.stderr)
        return None
    return proposal


def cmd_proposal_list(args: argparse.Namespace) -> int:
    store, _, _ = build()
    proposals = store.task_proposals(status=args.status)

    if args.json:
        print(json.dumps({"proposals": proposals}, indent=2))
        return 0

    if not proposals:
        print(f"No {args.status or ''} task proposals.".replace("  ", " "))
        return 0
    print(f"{'id':<40} {'status':>9} {'seen':>5}  workflow")
    for p in proposals:
        print(
            f"{p['id']:<40} {p['status']:>9} {p['occurrences']:>5}  "
            f"{p['title'][:60]}"
        )
    return 0


def cmd_proposal_show(args: argparse.Namespace) -> int:
    from .miner import episode_links

    store, _, _ = build()
    proposal = _proposal_or_exit(store, args.id)
    if proposal is None:
        return 4

    links = episode_links(store, proposal["source_episode_ids"])
    if args.json:
        print(json.dumps({**proposal, "episodes": links}, indent=2))
        return 0

    print(f"{proposal['id']}  [{proposal['status']}]")
    print(f"  workflow    {proposal['title']}")
    print(f"  domain      {proposal['domain']}")
    print(f"  seen        {proposal['occurrences']}x")
    print(f"  drafted by  {proposal['model_id']}")
    print(f"  created     {proposal['created_at']}")
    if proposal["reason"]:
        print(f"  reason      {proposal['reason']}")
    print("\n--- drafted manifest entry ---")
    print(proposal["draft_yaml"])
    print("--- drafted verify.py (NOT reviewed, NOT active) ---")
    print(proposal["verify_draft"])
    print("--- evidence ---")
    for ep in links:
        print(f"  episode {ep['id']}: {ep['task_id']} ({ep['finished_at']})")
    return 0


def cmd_proposal_approve(args: argparse.Namespace) -> int:
    """Enact a proposal by handing it to `task add` (Req 2.2).

    The draft is not written here. It is fed to the same command a human types,
    so a mined task passes exactly the validation a hand-written one does — and
    a validation failure re-pends the proposal with the error attached rather
    than leaving a half-written manifest and a proposal that claims success.
    """
    store, _, _ = build()
    proposal = _proposal_or_exit(store, args.id)
    if proposal is None:
        return 4
    if proposal["status"] == "approved":
        print(f"`{args.id}` is already approved.")
        return 0

    try:
        entry_data = yaml.safe_load(proposal["draft_yaml"]) or {}
    except yaml.YAMLError as exc:
        store.resolve_task_proposal(args.id, "invalid", f"unparseable draft: {exc}")
        print(f"REFUSED: `{args.id}` has an unparseable draft: {exc}", file=sys.stderr)
        return 4
    if not isinstance(entry_data, dict):
        store.resolve_task_proposal(args.id, "invalid", "draft is not a mapping")
        print(f"REFUSED: `{args.id}` has a malformed draft.", file=sys.stderr)
        return 4

    # Scripts BEFORE the manifest write. A manifest entry pointing at drafts
    # that do not exist is a worse state than drafts nobody has referenced yet,
    # and `task add` is the step that can still fail.
    written = _write_script_drafts(entry_data, proposal)

    ns = argparse.Namespace(
        file=None,
        json=json.dumps(entry_data),
        id=None, domain=None, type=None, split=None, heldout=False,
        prompt=None, bench_ref=None, verify=None, reset=None,
    )
    captured = io.StringIO()
    with contextlib.redirect_stdout(captured), contextlib.redirect_stderr(captured):
        try:
            code = cmd_task_add(ns)
        except ManifestError as exc:
            code, captured = 4, io.StringIO(f"MANIFEST ERROR: {exc}")
    output = captured.getvalue().strip()

    if code != 0:
        # Requirement 2.2: never a partial write. `task add` writes atomically
        # or not at all, so the manifest is untouched; the proposal goes back to
        # pending carrying the reason so the operator can see what to fix.
        store.resolve_task_proposal(args.id, "pending", output[:1000] or "task add failed")
        print(output, file=sys.stderr)
        print(
            f"\nREFUSED: `{args.id}` failed validation and stays pending. "
            f"The manifest was not written.",
            file=sys.stderr,
        )
        return 4

    store.resolve_task_proposal(args.id, "approved", None)
    print(output)
    for path in written:
        print(f"  drafted {path}")
    print(
        f"\nApproved `{args.id}`. It is in the manifest and marked `draft: true`, "
        f"so it CANNOT run yet — an LLM wrote those verifier scripts and nothing "
        f"has reviewed them. Finish them, rename off the `.draft` suffix, remove "
        f"`draft: true`, then `helixis task validate`."
    )
    return 0


def _write_script_drafts(entry_data: dict[str, Any], proposal: dict[str, Any]) -> list[Path]:
    """Write verify.py.draft / reset.py.draft beside where the real ones go.

    `.draft` is the whole gate (Req 2.4). The manifest points at `verify.py`,
    which does not exist, so the task is inert until a human reads this file,
    decides it grades the right thing, and renames it. Naming it `verify.py`
    directly would make an unreviewed LLM verifier authoritative over real work
    the moment it was approved.
    """
    root = SETTINGS.paths.root
    written: list[Path] = []
    for key, body in (
        ("verify", proposal.get("verify_draft") or ""),
        ("reset", proposal.get("reset_draft") or ""),
    ):
        target = str(entry_data.get(key) or "").strip()
        if not target or not body.strip():
            continue
        path = Path(target)
        path = path if path.is_absolute() else root / path
        draft_path = path.with_name(path.name + ".draft")
        draft_path.parent.mkdir(parents=True, exist_ok=True)
        draft_path.write_text(_DRAFT_HEADER.format(
            task_id=proposal["id"],
            model=proposal.get("model_id", "unknown"),
            occurrences=proposal.get("occurrences", 0),
            final=path.name,
        ) + body.rstrip() + "\n", encoding="utf-8")
        written.append(draft_path)
    return written


_DRAFT_HEADER = """\
# ---------------------------------------------------------------------------
# DRAFT — NOT ACTIVE. Written by the Helixis task miner, not by a human.
#
# Task:     {task_id}
# Drafted:  {model} (from {occurrences} real session(s))
#
# This file grades nothing while it is named `.draft`. The task's manifest entry
# points at `{final}` and carries `draft: true`, so it is excluded from every
# run until you:
#
#   1. Read this and decide whether it checks the right end state.
#   2. Confirm the negative assertion actually fails on an agent that did nothing.
#   3. Resolve every TODO(human) below.
#   4. Rename this file to `{final}` and remove `draft: true` from the entry.
#   5. Run `helixis task validate`.
#
# An LLM proposed this task AND wrote its grader. Nothing in that loop has
# touched ground truth yet — you are the ground truth.
# ---------------------------------------------------------------------------

"""


def cmd_proposal_reject(args: argparse.Namespace) -> int:
    store, _, _ = build()
    proposal = _proposal_or_exit(store, args.id)
    if proposal is None:
        return 4

    reason = (args.reason or "").strip() or "rejected by the operator"
    store.resolve_task_proposal(args.id, "rejected", reason[:500])
    print(f"Rejected `{args.id}`: {reason}")
    print(
        "Its workflow fingerprint is suppressed — mining will not propose this "
        "same cluster again."
    )
    return 0


async def cmd_triage(args: argparse.Namespace) -> int:
    """Concurrent failure triage — also the vLLM batching evidence."""
    store, wiki, _ = build()
    distiller = Distiller(SETTINGS, store, wiki, LLMClient(SETTINGS.distiller))
    results, stats = await distiller.triage_failures(args.epoch, limit=args.limit)
    if not results:
        print(f"No failures recorded for epoch {args.epoch}.")
        return 0
    counts: dict[str, int] = {}
    for r in results:
        counts[r["category"]] = counts.get(r["category"], 0) + 1
    print(f"Failure categories (epoch {args.epoch}):")
    for cat, n in sorted(counts.items(), key=lambda p: -p[1]):
        print(f"  {n:3d}  {cat}")
    print("\nvLLM batch throughput:")
    print(json.dumps(stats.to_dict(), indent=2))
    (SETTINGS.paths.runs / f"triage-epoch-{args.epoch}.json").write_text(
        json.dumps({"results": results, "stats": stats.to_dict()}, indent=2)
    )
    return 0


def cmd_report(args: argparse.Namespace) -> int:
    store, _, _ = build()
    _print_report(store, simulated=False)
    return 0


def _print_curriculum_note(store: EpisodeStore) -> None:
    """Say when the task set moved, and that the curve above ignores it.

    Requirement 3.2: a curve over a curriculum that changed mid-experiment is
    only honest if it is either held to a frozen set or annotated. We do both —
    the numbers are frozen-bench, and any change is named here.
    """
    events = store.curriculum_events()
    if not events:
        return
    by_epoch: dict[Any, list[dict[str, Any]]] = {}
    for e in events:
        by_epoch.setdefault(e["epoch"], []).append(e)
    print(
        "\nCurriculum changed — the curve above is computed over the FROZEN "
        "bench set only, so these tasks do not affect it:"
    )
    for epoch in sorted(by_epoch, key=lambda x: (x is not None, x)):
        where = "before any epoch ran" if epoch is None else f"after epoch {epoch}"
        names = ", ".join(f"{e['action']} {e['task_id']}" for e in by_epoch[epoch])
        print(f"  {where}: {names}")


def _print_report(store: EpisodeStore, simulated: bool) -> None:
    curve = store.epoch_curve()
    if not curve:
        print("No episodes recorded yet.")
        return
    print("\nLearning curve (frozen bench set)")
    print(f"{'epoch':>6} {'split':>8} {'n':>4} {'mean PC':>9} {'pass':>7} {'cost':>9}")
    for row in curve:
        print(
            f"{row['epoch']:>6} {row['split']:>8} {row['n']:>4} "
            f"{row['mean_partial_credit']:>9.3f} {row['pass_rate']:>6.0%} "
            f"${row['cost_usd']:>8.2f}"
        )

    # The headline claim: epoch-0 vs final, train and held-out reported separately
    # so a train-only gain can't be passed off as generalization.
    for split in ("train", "heldout"):
        rows = [r for r in curve if r["split"] == split]
        if len(rows) < 2:
            continue
        first, last = rows[0], rows[-1]
        d_pc = last["mean_partial_credit"] - first["mean_partial_credit"]
        d_pr = last["pass_rate"] - first["pass_rate"]
        print(
            f"\n{split}: epoch {first['epoch']} -> {last['epoch']}  "
            f"partial credit {first['mean_partial_credit']:.3f} -> "
            f"{last['mean_partial_credit']:.3f} ({d_pc:+.3f}), "
            f"pass rate {first['pass_rate']:.0%} -> {last['pass_rate']:.0%} ({d_pr:+.0%})"
        )
    _print_curriculum_note(store)
    print(f"\nTotal spend: ${store.total_cost():.2f}")
    if simulated:
        print("\n!! These figures come from the OFFLINE SIMULATOR, not a graded run.")


def cmd_wiki(args: argparse.Namespace) -> int:
    store, wiki, _ = build()
    snapshot = wiki_snapshot(wiki, store)
    if args.json:
        print(json.dumps(snapshot, indent=2))
        return 0
    print(f"Wiki generation {snapshot['generation']} — {snapshot['n_skills']} skills")
    for s in snapshot["skills"]:
        print(f"\n  {s['name']}  [{s['category']}, epoch {s['created_epoch']}]")
        print(f"    {s['description']}")
        if s["source_episodes"]:
            print(f"    from: {', '.join(s['source_episodes'][:3])}")
    return 0


def cmd_pages(args: argparse.Namespace) -> int:
    store, wiki, _ = build()
    written = regenerate_pages(wiki, store, force=args.force)
    print(
        f"Regenerated: {', '.join(written)}"
        if written
        else "No changes since last generation (content snapshot unchanged)."
    )
    return 0


def cmd_tail_policy(args: argparse.Namespace) -> int:
    from .containment import OCSFTailer

    store, _, _ = build()
    log_dir = Path(args.log_dir) if args.log_dir else SETTINGS.ocsf_log_dir
    tailer = OCSFTailer(store, log_dir)
    stats = tailer.poll()
    print(
        f"{log_dir}: scanned {stats.files} file(s), {stats.lines} line(s); "
        f"found {stats.denials} denial(s), recorded {stats.recorded} new "
        f"({stats.honeypot} honeypot)."
    )
    if not stats.files:
        print(
            "No OpenShell logs found. Denials only appear once the gateway and "
            "sandbox are running; see policy/README or --log-dir.",
            file=sys.stderr,
        )
    for err in stats.errors:
        print(f"  parse warning: {err}", file=sys.stderr)
    return 0


async def cmd_rehearse(args: argparse.Namespace) -> int:
    from .adversarial import LLMAgent, run_rehearsal

    store, _, _ = build()
    agent = LLMAgent(client=LLMClient(SETTINGS.agent))
    report = await run_rehearsal(
        agent,
        store=store,
        log_dir=Path(args.log_dir) if args.log_dir else SETTINGS.ocsf_log_dir,
        honeypot_path=SETTINGS.paths.policy / "honeypot" / "aws_keys.env",
        # Without OpenShell running there are no denial events to find, so the
        # no-leakage assertion is the only one that can be evidenced.
        require_denials=not args.no_denials,
    )
    print(json.dumps(report.to_dict(), indent=2))
    if SETTINGS.agent.is_fake:
        print(
            "\nNOTE: the agent tier is in offline mode, so this rehearsed a stub, "
            "not the real model. Point HELIXIS_AGENT_BASE_URL at the live "
            "endpoint before treating this as a containment result.",
            file=sys.stderr,
        )
    return 0 if report.passed else 1


# ------------------------------------------------------------- task management
#
# This group is the SINGLE mutation path for `tasks.user.yaml` (Requirement
# 2.1). The dashboard shells out to it rather than writing YAML itself, so
# validation, atomic writes and curriculum-change bookkeeping happen in exactly
# one place regardless of which surface the operator used.


def _user_path() -> Path:
    return SETTINGS.paths.user_manifest


def _merged(*, check_bench: bool = True) -> Manifest:
    """Load for reading, tolerating invalid entries so `validate` can report.

    `check_bench=False` is for pure reads: resolving bench refs loads every
    AutomationBench domain dataset (~2s), and `task list` is polled by the
    dashboard, so paying that on every read would burn a CPU core in the
    background for as long as the Lab page is open.
    """
    return Manifest.load(
        SETTINGS.paths.manifest,
        _user_path(),
        root=SETTINGS.paths.root,
        strict=False,
        check_bench=check_bench,
    )


def _entry_from_args(args: argparse.Namespace) -> TaskEntry:
    """Build one entry from flags, a `--file`, or a `--json` payload.

    `--json` exists for the dashboard: the web layer stays dumb and forwards a
    payload it never has to understand, and every field lands in the same
    validator the CLI flags do.
    """
    data: dict[str, Any] = {}
    if getattr(args, "file", None):
        raw = yaml.safe_load(Path(args.file).read_text(encoding="utf-8")) or {}
        if not isinstance(raw, dict):
            raise ManifestError(f"{args.file}: expected a single task mapping.")
        data = raw
    elif getattr(args, "json", None):
        raw = json.loads(args.json)
        if not isinstance(raw, dict):
            raise ManifestError("--json must be a single task object.")
        data = raw

    def pick(key: str, default: Any = "") -> Any:
        value = getattr(args, key, None)
        return value if value not in (None, "") else data.get(key, default)

    split = str(pick("split", "train"))
    heldout = bool(getattr(args, "heldout", False) or data.get("heldout", False))
    if heldout:
        split = "heldout"
    # Requirement 1.3: held-out is the stable measuring stick. Landing there is
    # never a side effect of a form default — it takes saying so twice.
    if split == "heldout" and not heldout:
        raise ManifestError(
            "assigning a task to the held-out split changes what the "
            "generalization curve measures. Pass --heldout to confirm; the "
            "change is annotated on the curve as a curriculum event."
        )

    task_type = str(pick("type", "bench"))
    if task_type not in ("bench", "real"):
        raise ManifestError(f"--type must be 'bench' or 'real', got {task_type!r}.")

    task_id = str(pick("id")).strip()
    if not task_id:
        raise ManifestError("`id` is required.")

    bench_ref = str(pick("bench_ref") or "").strip()
    domain = str(pick("domain")).strip()
    if not domain:
        # Prefer the domain AutomationBench actually files this task under. The
        # id's own prefix is only a fallback: a task named `ops.my_lead_qualifier`
        # grading against `sales.qualify_lead` lives in the SALES dataset, and
        # guessing `ops` from the id would look fine here and then raise
        # `Unknown domain` mid-epoch.
        from .manifest import bench_task_domains

        lookup = bench_task_domains() if task_type == "bench" else None
        domain = (lookup or {}).get(bench_ref or task_id) or task_id.split(".", 1)[0]

    return TaskEntry(
        id=task_id,
        domain=domain,
        split=split,
        type=task_type,  # type: ignore[arg-type]
        origin="user",
        prompt=str(pick("prompt") or "").strip(),
        bench_ref=bench_ref,
        verify=str(pick("verify") or "").strip(),
        reset=str(pick("reset") or "").strip(),
        # Only reachable through `--json`/`--file`, which is how an approved
        # proposal arrives (spec 05). There are deliberately no CLI flags for
        # these: a human adding a task by hand has already written the scripts,
        # and `--draft` would just be a way to add a task that never runs.
        draft=bool(data.get("draft", False)),
        source=str(data.get("source", "") or "").strip(),
        added_at=_now_iso(),
    )


def _now_iso() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat()


def cmd_task_add(args: argparse.Namespace) -> int:
    entry = _entry_from_args(args)
    path = _user_path()

    merged = _merged()
    existing = merged.get(entry.id)
    if existing is not None and existing.origin == "bench":
        print(
            f"REFUSED: `{entry.id}` is already a task in the frozen bench "
            f"manifest. Ids must be unique across both files — pick another id "
            f"(use --bench-ref {entry.id} to grade against the bench task).",
            file=sys.stderr,
        )
        return 4

    entries = load_user_entries(path)
    if any(e.id == entry.id for e in entries):
        print(
            f"REFUSED: `{entry.id}` is already in {path.name}. Remove it first, "
            f"or edit the file directly.",
            file=sys.stderr,
        )
        return 4

    issues = validate_entries([entry], root=SETTINGS.paths.root)
    fatal = [i for i in issues if i.fatal]
    for issue in issues:
        print(f"  {'error' if issue.fatal else 'note'}: {issue.message}")
    if fatal:
        sys.stdout.flush()  # keep the findings above the verdict, not after it
        print(f"\nREFUSED: `{entry.id}` is not a valid task.", file=sys.stderr)
        return 4

    entries.append(entry)
    write_user_manifest(path, entries)

    store = EpisodeStore(SETTINGS.paths.db, SETTINGS.paths.runs)
    store.record_curriculum_event(
        action="added",
        task_id=entry.id,
        split=entry.split,
        task_type=entry.type,
        detail=(
            f"added to {entry.split} split"
            + (" from a mined proposal" if entry.source == "miner" else "")
            + (" (draft — cannot run yet)" if entry.draft else "")
        ),
    )

    print(f"Added `{entry.id}` ({entry.type}, {entry.split}) to {path}.")
    if entry.draft:
        print(
            "\nNOTE: this task is marked `draft: true` and is EXCLUDED from every "
            "run. Its verify.py/reset.py were drafted by a model and sit beside "
            "the real paths with a `.draft` suffix. Complete them, rename them, "
            "then remove `draft: true` to activate the task."
        )
    if entry.type == "real":
        print(
            "\nNOTE: `real` tasks are graded by their own verify.py/reset.py and "
            "run through the real-tier driver, not a mocked epoch. Like all "
            "real-tier work they are excluded from the headline curve."
        )
    if entry.split == "heldout":
        print(
            "\nNOTE: the held-out set changed. The curve is annotated "
            "'curriculum changed' at this epoch — held-out numbers before and "
            "after are not directly comparable."
        )
    return 0


def cmd_task_list(args: argparse.Namespace) -> int:
    # A listing does not need bench refs resolved — `task validate` is the
    # command that answers "is this task actually runnable?".
    manifest = _merged(check_bench=False)
    entries = [
        e for e in manifest.entries if args.include_retired or not e.retired
    ]
    if args.origin:
        entries = [e for e in entries if e.origin == args.origin]

    if args.json:
        print(
            json.dumps(
                {
                    "tasks": [e.to_dict() for e in entries],
                    "warnings": [i.to_dict() for i in manifest.warnings],
                    "userManifest": str(manifest.user_path),
                    "benchManifest": str(manifest.bench_path),
                },
                indent=2,
            )
        )
        return 0

    if not entries:
        print("No tasks.")
        return 0
    print(f"{'id':<46} {'origin':>7} {'type':>6} {'split':>8}  flags")
    for e in entries:
        flags = []
        if e.retired:
            flags.append("retired")
        if e.bench_ref and e.bench_ref != e.id:
            flags.append(f"-> {e.bench_ref}")
        print(
            f"{e.id:<46} {e.origin:>7} {e.type:>6} {e.split:>8}  "
            f"{', '.join(flags)}"
        )
    n_user = sum(1 for e in entries if e.origin == "user")
    print(f"\n{len(entries)} task(s), {n_user} user-defined.")
    for issue in manifest.warnings:
        print(f"  warning — {issue.task_id}: {issue.message}", file=sys.stderr)
    return 0


def cmd_task_remove(args: argparse.Namespace) -> int:
    path = _user_path()
    entries = load_user_entries(path)
    entry = next((e for e in entries if e.id == args.id), None)
    if entry is None:
        merged = _merged()
        if merged.get(args.id) is not None:
            print(
                f"REFUSED: `{args.id}` is a frozen bench task. The bench "
                f"manifest is never written by tooling.",
                file=sys.stderr,
            )
            return 4
        print(f"No user task `{args.id}`.", file=sys.stderr)
        return 4

    store = EpisodeStore(SETTINGS.paths.db, SETTINGS.paths.runs)
    n_episodes = store.count_episodes_for_task(args.id)

    if n_episodes:
        # Requirement 2.3: history outlives the curriculum. Deleting the entry
        # would orphan trajectories on disk and rows the dashboard still links
        # to, so the task is retired instead — gone from future epochs, fully
        # queryable in every view of the past.
        if entry.retired:
            print(f"`{args.id}` is already retired ({n_episodes} episode(s) kept).")
            return 0
        entry.retired = True
        write_user_manifest(path, entries)
        action = "retired"
        print(
            f"Retired `{args.id}`: excluded from future epochs, but its "
            f"{n_episodes} recorded episode(s) and trajectories are preserved."
        )
    else:
        entries = [e for e in entries if e.id != args.id]
        write_user_manifest(path, entries)
        action = "removed"
        print(f"Removed `{args.id}` (no episodes recorded).")

    store.record_curriculum_event(
        action=action,
        task_id=args.id,
        split=entry.split,
        task_type=entry.type,
        detail=f"{n_episodes} episode(s) recorded",
    )
    return 0


def cmd_task_validate(args: argparse.Namespace) -> int:
    manifest = _merged()
    user = manifest.user_entries
    issues = validate_entries(user, root=SETTINGS.paths.root)
    fatal = [i for i in issues if i.fatal]

    if args.json:
        print(
            json.dumps(
                {
                    "ok": not fatal,
                    "nUserTasks": len(user),
                    "issues": [i.to_dict() for i in issues],
                },
                indent=2,
            )
        )
        return 1 if fatal else 0

    print(f"{len(user)} user task(s) in {manifest.user_path}.")
    for issue in issues:
        print(f"  {'ERROR' if issue.fatal else 'note '}  {issue.task_id}: {issue.message}")

    real = [e for e in user if e.type == "real"]
    if real:
        print(
            f"\n{len(real)} `real` task(s) validate but do NOT run in a mocked "
            f"epoch — they are graded by their own verify.py/reset.py through "
            f"the real-tier driver, and are excluded from the headline curve "
            f"like all real-tier work."
        )
    if fatal:
        print(f"\nINVALID: {len(fatal)} error(s). These tasks would abort the next run.")
        return 1
    print("\nOK — the merged manifest loads cleanly.")
    return 0


# ------------------------------------------------------------------- preflight


def _automationbench_available() -> bool:
    try:
        import automationbench  # noqa: F401
    except ImportError:
        return False
    return True


def _preflight(settings: Settings = SETTINGS) -> dict[str, Any]:
    """What each training mode would actually do, right now.

    The dashboard renders mode cards from this rather than re-deriving backend
    selection in TypeScript: `EpochRunner._default_backend` is the only thing
    that decides simulated-vs-real, so anything that predicts it elsewhere is a
    second implementation waiting to disagree with the first.
    """
    from .ingest import discover_sessions

    store, _wiki, manifest = build(settings)

    ab = _automationbench_available()
    agent_configured = not settings.agent.is_fake
    distiller_configured = not settings.distiller.is_fake

    # Mirrors _default_backend: fake endpoint OR missing automationbench -> stub.
    benchmark_blockers: list[str] = []
    if not agent_configured:
        benchmark_blockers.append(
            "No agent endpoint configured — set HELIXIS_AGENT_BASE_URL to an "
            "OpenAI-compatible /v1 URL."
        )
    if not ab:
        benchmark_blockers.append(
            "AutomationBench is not importable — install it in the engine "
            "environment (`pip install -e app/engine[bench]`)."
        )

    try:
        ledger = store.real_session_ledger()
    except Exception:  # a fresh install has no ledger table yet
        ledger = {}
    try:
        candidates = discover_sessions(
            settings.paths.claw_sessions, settings.claw_quiescent_after_s
        )
    except (OSError, FileNotFoundError):
        candidates = []
    pending = [c for c in candidates if c.session_id not in ledger]

    # A cycle over zero NEW sessions is still useful when real episodes are
    # already on record — it re-distills them — so that case is a warning, not
    # a blocker. Only "nothing has ever been captured" actually blocks.
    real_blockers: list[str] = []
    real_warnings: list[str] = []
    real_episodes_total = len(ledger)
    if not candidates and not real_episodes_total:
        real_blockers.append(
            "No Claw sessions have been captured yet — use the agent, and its "
            f"sessions land in {settings.paths.claw_sessions}."
        )
    elif not pending:
        real_warnings.append(
            f"No new sessions to ingest ({real_episodes_total} already on "
            "record). A cycle now re-distills existing real episodes."
        )

    if not distiller_configured:
        real_warnings.append(
            "No distiller endpoint — sessions will be stored UNLABELED and "
            "mining falls back to a stub. Point HELIXIS_DISTILLER_BASE_URL at vLLM."
        )

    active = "benchmark" if not benchmark_blockers else "simulated"

    train_specs = manifest.for_split("train")
    heldout_specs = manifest.for_split("heldout")
    drafts = [e for e in manifest.entries if e.draft and not e.retired]

    last = store.last_epoch()
    return {
        "activeMode": active,
        "modes": {
            "simulated": {
                "available": True,
                "blockers": [],
                "warnings": (
                    []
                    if active == "simulated"
                    else [
                        "A real agent endpoint is configured, so this mode now "
                        "has to be asked for explicitly — it is no longer what "
                        "an unqualified `helixis epoch` would run."
                    ]
                ),
                "estimatedCostUsd": 0.0,
            },
            "benchmark": {
                "available": not benchmark_blockers,
                "blockers": benchmark_blockers,
                "warnings": [],
                "estimatedCostUsd": None,
            },
            "real": {
                "available": not real_blockers,
                "blockers": real_blockers,
                "warnings": real_warnings,
                "estimatedCostUsd": None,
            },
        },
        "agent": {
            "model": settings.agent.model,
            "baseUrl": settings.agent.base_url,
            "configured": agent_configured,
            "automationbench": ab,
        },
        "distiller": {
            "model": settings.distiller.model,
            "baseUrl": settings.distiller.base_url,
            "configured": distiller_configured,
        },
        "real": {
            "sessionsDir": str(settings.paths.claw_sessions),
            "pendingSessions": len(pending),
            "totalSessions": len(candidates),
            "ingestedSessions": len(ledger),
            "newRealEpisodes": store.new_real_episodes_since_distill(),
            "threshold": settings.real_train_threshold,
            "autoTrain": settings.auto_train,
        },
        "budget": {
            "epochCapUsd": settings.epoch_cost_cap_usd,
            "totalCapUsd": settings.total_cost_cap_usd,
            "totalSpentUsd": store.total_cost(),
            "epochSpentUsd": store.epoch_cost(last) if last is not None else 0.0,
        },
        "tasks": {
            "train": len(train_specs),
            "heldout": len(heldout_specs),
            "draftExcluded": len(drafts),
        },
        "lastEpoch": last,
        "nextEpoch": 0 if last is None else last + 1,
    }


def cmd_preflight(args: argparse.Namespace) -> int:
    info = _preflight()
    if args.json:
        print(json.dumps(info, indent=2))
        return 0

    active = info["activeMode"]
    print(f"Active mode for `helixis epoch`: {active.upper()}")
    if active == "simulated":
        print("  Episodes will be SIMULATED and marked simulated:true — not results.")
    for name, mode in info["modes"].items():
        mark = "ok " if mode["available"] else "BLOCKED"
        print(f"\n[{mark}] {name}")
        for b in mode["blockers"]:
            print(f"    - {b}")
        for w in mode["warnings"]:
            print(f"    ~ {w}")
    r = info["real"]
    print(
        f"\nReal sessions: {r['pendingSessions']} pending / {r['totalSessions']} captured; "
        f"{r['newRealEpisodes']} new episodes since last distill (threshold {r['threshold']})."
    )
    b = info["budget"]
    print(
        f"Budget: ${b['totalSpentUsd']:.2f} / ${b['totalCapUsd']:.2f} total, "
        f"${b['epochSpentUsd']:.2f} / ${b['epochCapUsd']:.2f} this epoch."
    )
    return 0


# ------------------------------------------------------------------------ main


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="helixis", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("run", help="run the full multi-epoch experiment")
    p.add_argument("--epochs", type=int, default=6)
    p.add_argument("--heldout-at", default="0,3,6")
    p.set_defaults(fn=cmd_run, is_async=True)

    p = sub.add_parser("epoch", help="run a single epoch")
    p.add_argument("--epoch", type=int, required=True)
    p.add_argument("--split", choices=["train", "heldout"], default="train")
    p.add_argument("--no-resume", action="store_true")
    p.add_argument(
        "--allow-rewrite",
        action="store_true",
        help="overwrite episodes recorded under a different wiki generation",
    )
    p.add_argument(
        "--offline",
        action="store_true",
        help="force the deterministic stub even if a model endpoint is configured",
    )
    p.set_defaults(fn=cmd_epoch, is_async=True)

    p = sub.add_parser("heldout", help="run a held-out evaluation")
    p.add_argument("--epoch", type=int, required=True)
    p.add_argument("--allow-rewrite", action="store_true")
    p.add_argument("--offline", action="store_true")
    p.set_defaults(fn=cmd_epoch, is_async=True, split="heldout", no_resume=False)

    p = sub.add_parser("distill", help="distill skills from an epoch's failures")
    p.add_argument("--epoch", type=int, required=True)
    p.set_defaults(fn=cmd_distill, is_async=True)

    p = sub.add_parser(
        "ingest-real", help="turn captured Helixis Claw sessions into real episodes"
    )
    p.add_argument(
        "--force", action="store_true",
        help="re-ingest sessions already in the ledger (also retries quarantined ones)",
    )
    p.add_argument(
        "--watch", action="store_true",
        help="keep polling for new sessions; with HELIXIS_AUTO_TRAIN=1 this is the auto-train loop",
    )
    p.add_argument("--interval", type=float, default=60.0, help="--watch poll seconds")
    p.add_argument(
        "--no-judge", action="store_true",
        help="store episodes unlabeled even when a distiller endpoint is configured",
    )
    p.set_defaults(fn=cmd_ingest_real, is_async=True)

    p = sub.add_parser(
        "train-cycle", help="ingest real sessions, distill, regenerate pages, mine tasks"
    )
    p.set_defaults(fn=cmd_train_cycle, is_async=True)

    p = sub.add_parser(
        "mine-tasks", help="propose training tasks from recurring real workflows"
    )
    p.add_argument(
        "--min-occurrences", type=int, default=None,
        help=f"times a workflow must recur to count (default {SETTINGS.mine_min_occurrences})",
    )
    p.add_argument(
        "--allow-single", action="store_true",
        help="propose from one-off workflows too — DEMO ONLY, and clearly weaker "
             "evidence: a workflow seen once is not yet a workflow",
    )
    p.add_argument(
        "--max-proposals", type=int, default=None,
        help=f"per-run cap (default {SETTINGS.max_proposals_per_run})",
    )
    p.add_argument("--json", action="store_true")
    p.set_defaults(fn=cmd_mine_tasks, is_async=True)

    p = sub.add_parser("triage", help="categorize failures (concurrent vLLM batch)")
    p.add_argument("--epoch", type=int, required=True)
    p.add_argument("--limit", type=int, default=16)
    p.set_defaults(fn=cmd_triage, is_async=True)

    p = sub.add_parser(
        "preflight", help="what each training mode would do right now"
    )
    p.add_argument("--json", action="store_true")
    p.set_defaults(fn=cmd_preflight, is_async=False)

    p = sub.add_parser("report", help="print the learning curve")
    p.set_defaults(fn=cmd_report, is_async=False)

    p = sub.add_parser("wiki", help="show the skill bank")
    p.add_argument("--json", action="store_true")
    p.set_defaults(fn=cmd_wiki, is_async=False)

    p = sub.add_parser("pages", help="regenerate wiki overview pages")
    p.add_argument("--force", action="store_true")
    p.set_defaults(fn=cmd_pages, is_async=False)

    p = sub.add_parser("tail-policy", help="ingest OpenShell denial events")
    p.add_argument("--log-dir", help="override the OCSF log directory")
    p.set_defaults(fn=cmd_tail_policy, is_async=False)

    p = sub.add_parser("rehearse", help="run the adversarial containment rehearsal")
    p.add_argument("--log-dir", help="override the OCSF log directory")
    p.add_argument(
        "--no-denials",
        action="store_true",
        help="skip the denial-trail assertion (use when OpenShell is not running)",
    )
    p.set_defaults(fn=cmd_rehearse, is_async=True)

    # ---------------------------------------------------------------- task group
    p = sub.add_parser("task", help="manage your own tasks (tasks.user.yaml)")
    tsub = p.add_subparsers(dest="task_command", required=True)

    t = tsub.add_parser("add", help="add a task to the user manifest")
    t.add_argument("--id", help="task id, `domain.snake_case_action`")
    t.add_argument("--domain", help="defaults to the id's domain segment")
    t.add_argument("--type", choices=["bench", "real"], help="grading semantics")
    t.add_argument("--split", choices=["train", "heldout"], help="default: train")
    t.add_argument(
        "--heldout",
        action="store_true",
        help="required to assign to the held-out split — it changes what the "
        "generalization curve measures",
    )
    t.add_argument("--prompt", help="task prompt (`real` tasks)")
    t.add_argument("--bench-ref", dest="bench_ref", help="AutomationBench task id")
    t.add_argument("--verify", help="path to verify.py (`real` tasks)")
    t.add_argument("--reset", help="path to reset.py (`real` tasks)")
    t.add_argument("--file", help="read the task from a YAML file instead of flags")
    t.add_argument("--json", help="read the task from a JSON object instead of flags")
    t.set_defaults(fn=cmd_task_add, is_async=False)

    t = tsub.add_parser("list", help="list the merged manifest")
    t.add_argument("--json", action="store_true")
    t.add_argument("--include-retired", action="store_true")
    t.add_argument("--origin", choices=["bench", "user"])
    t.set_defaults(fn=cmd_task_list, is_async=False)

    t = tsub.add_parser("remove", help="retire (or delete) a user task")
    t.add_argument("--id", required=True)
    t.set_defaults(fn=cmd_task_remove, is_async=False)

    t = tsub.add_parser("validate", help="check the user manifest")
    t.add_argument("--json", action="store_true")
    t.set_defaults(fn=cmd_task_validate, is_async=False)

    # ------------------------------------------------------------ proposal group
    p = sub.add_parser("proposal", help="review tasks the miner proposed")
    psub = p.add_subparsers(dest="proposal_command", required=True)

    t = psub.add_parser("list", help="list task proposals")
    t.add_argument(
        "--status", choices=["pending", "approved", "rejected", "invalid"], default=None
    )
    t.add_argument("--json", action="store_true")
    t.set_defaults(fn=cmd_proposal_list, is_async=False)

    t = psub.add_parser("show", help="show one proposal with its evidence")
    t.add_argument("--id", required=True)
    t.add_argument("--json", action="store_true")
    t.set_defaults(fn=cmd_proposal_show, is_async=False)

    t = psub.add_parser("approve", help="add a proposed task to the user manifest")
    t.add_argument("--id", required=True)
    t.set_defaults(fn=cmd_proposal_approve, is_async=False)

    t = psub.add_parser("reject", help="reject a proposal and suppress its cluster")
    t.add_argument("--id", required=True)
    t.add_argument("--reason", default="")
    t.set_defaults(fn=cmd_proposal_reject, is_async=False)

    args = parser.parse_args(argv)
    try:
        return asyncio.run(args.fn(args)) if args.is_async else args.fn(args)
    except ManifestError as exc:
        # A bad manifest is a configuration error with a known fix, not a crash.
        # Name the file and the entry; never let a run proceed on a task set we
        # could not fully resolve.
        print(f"\nMANIFEST ERROR: {exc}", file=sys.stderr)
        return 4


if __name__ == "__main__":
    raise SystemExit(main())
