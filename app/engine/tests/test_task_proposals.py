"""Proposal storage and the draft gate that keeps an unreviewed verifier inert.

Two invariants are worth a test each, because both fail silently:

* a decided proposal must never reopen (Req 2.3), and
* a task whose grader is still an LLM draft must never run (Req 2.4) — while
  also never aborting the load of an otherwise healthy manifest.
"""

from __future__ import annotations

from helixis.manifest import (
    Manifest,
    TaskEntry,
    load_user_entries,
    validate_entries,
    write_user_manifest,
)
from helixis.miner import fingerprint


def proposal(task_id: str = "ops.weekly_digest", workflow: str = "compile the digest"):
    return {
        "id": task_id,
        "fingerprint": fingerprint(workflow),
        "status": "pending",
        "title": workflow,
        "domain": "ops",
        "task_type": "real",
        "draft_yaml": "id: ops.weekly_digest\n",
        "verify_draft": "def verify() -> bool:\n    return True\n",
        "source_episode_ids": [1, 2],
        "occurrences": 2,
        "model_id": "test-model",
    }


# --------------------------------------------------------------------- storage


def test_insert_is_idempotent_on_fingerprint(store):
    assert store.insert_task_proposal(proposal()) is True
    # Same cluster, different proposed id — still the same workflow.
    assert store.insert_task_proposal(proposal("ops.other_name")) is False
    assert len(store.task_proposals()) == 1


def test_a_decided_proposal_is_not_reopened_by_a_later_insert(store):
    store.insert_task_proposal(proposal())
    store.resolve_task_proposal("ops.weekly_digest", "rejected", "not useful")

    assert store.insert_task_proposal(proposal("ops.weekly_digest_v2")) is False
    stored = store.get_task_proposal("ops.weekly_digest")
    assert stored["status"] == "rejected"
    assert stored["reason"] == "not useful"
    assert stored["resolved_at"] is not None


def test_repending_clears_the_resolved_timestamp(store):
    """Req 2.2: a failed approval re-pends. `resolved_at` must not linger from
    a decision that did not stick, or the row reads as decided."""
    store.insert_task_proposal(proposal())
    store.resolve_task_proposal("ops.weekly_digest", "approved")
    assert store.get_task_proposal("ops.weekly_digest")["resolved_at"] is not None

    store.resolve_task_proposal("ops.weekly_digest", "pending", "validator said no")
    stored = store.get_task_proposal("ops.weekly_digest")
    assert stored["status"] == "pending"
    assert stored["resolved_at"] is None
    assert stored["reason"] == "validator said no"


def test_fingerprints_span_every_status(store):
    store.insert_task_proposal(proposal("ops.a", "compile the digest"))
    store.insert_task_proposal(proposal("ops.b", "escalate billing complaints"))
    store.resolve_task_proposal("ops.b", "rejected")

    assert store.proposal_fingerprints() == {
        fingerprint("compile the digest"),
        fingerprint("escalate billing complaints"),
    }


def test_mining_ledger_reports_the_latest_watermark(store):
    assert store.mining_watermark() is None
    store.record_mining_run(watermark="2026-01-01T00:00:00Z", n_episodes=3, n_clusters=1, n_proposals=1)
    store.record_mining_run(watermark="2026-02-01T00:00:00Z", n_episodes=2, n_clusters=1, n_proposals=0)
    assert store.mining_watermark() == "2026-02-01T00:00:00Z"


def test_real_episodes_since_respects_the_watermark(store, make_real_episode):
    make_real_episode("claw.s0", user_text="first")
    make_real_episode("claw.s1", user_text="second")
    everything = store.real_episodes_since(None)
    assert len(everything) == 2

    cutoff = everything[0]["finished_at"]
    later = store.real_episodes_since(cutoff)
    assert all(e["finished_at"] > cutoff for e in later)


# ------------------------------------------------------------------ draft gate


def draft_entry(**overrides) -> TaskEntry:
    base = dict(
        id="ops.weekly_digest",
        domain="ops",
        type="real",
        origin="user",
        prompt="Compile the weekly vendor digest.",
        verify="app/real_tier/weekly_digest/verify.py",
        reset="app/real_tier/weekly_digest/reset.py",
        source="miner",
        draft=True,
    )
    base.update(overrides)
    return TaskEntry(**base)


def test_draft_missing_scripts_are_non_fatal(settings):
    issues = validate_entries([draft_entry()], root=settings.paths.root, check_bench=False)
    assert issues, "a draft with no scripts should still be reported"
    assert not any(i.fatal for i in issues), "but never fatally — it would abort every load"
    assert all("DRAFT" in i.message for i in issues)


def test_the_same_entry_without_the_flag_is_fatal(settings):
    issues = validate_entries(
        [draft_entry(draft=False)], root=settings.paths.root, check_bench=False
    )
    assert any(i.fatal for i in issues), "a non-draft real task needs its scripts"


def test_a_draft_task_never_reaches_a_split(settings):
    write_user_manifest(settings.paths.user_manifest, [draft_entry()])
    merged = Manifest.load(
        settings.paths.manifest,
        settings.paths.user_manifest,
        root=settings.paths.root,
        strict=True,  # must NOT raise
        check_bench=False,
    )
    ids = {s.task_id for s in merged.for_split("train", include_real=True)}
    assert "ops.weekly_digest" not in ids
    # include_real=True is not an override for the draft gate: an unreviewed
    # verifier grading real work is the exact failure this prevents.
    assert not merged.get("ops.weekly_digest").runnable_in_epoch
    assert merged.get("ops.weekly_digest") in merged.skipped_in_split("train")


def test_draft_flag_survives_a_write_read_round_trip(settings):
    write_user_manifest(settings.paths.user_manifest, [draft_entry()])
    reloaded = load_user_entries(settings.paths.user_manifest)
    assert reloaded[0].draft is True
    assert reloaded[0].source == "miner"


def test_completed_scripts_with_the_flag_still_set_are_called_out(settings, tmp_path):
    for name in ("verify.py", "reset.py"):
        path = tmp_path / "app" / "real_tier" / "weekly_digest" / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("def x(): ...\n", encoding="utf-8")

    issues = validate_entries([draft_entry()], root=settings.paths.root, check_bench=False)
    # "I wrote the verifier and nothing happened" is the confusion this avoids.
    assert any("still set" in i.message for i in issues)
    assert not any(i.fatal for i in issues)
