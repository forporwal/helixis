"""Task miner: clustering, filtering, drafting, and the outage path.

The bar these tests hold is Requirement 1.3 and 2.3 — the filters. A miner that
proposes eagerly is worse than no miner: it costs the operator attention on
every run and teaches them to click through the feed without reading it. So most
of what follows is about what the miner declines to propose.
"""

from __future__ import annotations

import json

import pytest

from helixis.manifest import Manifest, TaskEntry, write_user_manifest
from helixis.miner import (
    NEGATIVE_MARKER,
    Cluster,
    TaskMiner,
    WorkflowSummary,
    extract_json_object,
    fingerprint,
    similarity,
)


def summary(episode_id: int, workflow: str, domain: str = "ops") -> WorkflowSummary:
    return WorkflowSummary(
        episode_id=episode_id, task_id=f"claw.s{episode_id}", workflow=workflow, domain=domain
    )


def draft_json(task_id: str = "ops.weekly_digest", **overrides) -> str:
    payload = {
        "id": task_id,
        "domain": "ops",
        "prompt": "Compile the weekly vendor digest from the invoice inbox and file it.",
        "verify_py": (
            "def verify() -> bool:\n"
            "    d = load()\n"
            "    assert d\n"
            f"    {NEGATIVE_MARKER}\n"
            "    assert not d.has_placeholders()\n"
            "    return True\n"
        ),
        "reset_py": "def reset() -> None:\n    clear()\n",
    }
    payload.update(overrides)
    return json.dumps(payload)


# ------------------------------------------------------------------ similarity


def test_similarity_is_symmetric_and_bounded():
    a = {"compile", "weekly", "vendor", "digest"}
    b = {"compile", "weekly", "digest"}
    assert similarity(a, b) == pytest.approx(similarity(b, a))
    assert 0.0 < similarity(a, b) <= 1.0
    assert similarity(a, a) == pytest.approx(1.0)
    assert similarity(a, set()) == 0.0


def test_fingerprint_ignores_word_order():
    # The suppression guarantee rests on this: two runs that phrase the same
    # workflow differently must collide, or a rejected proposal returns.
    assert fingerprint("compile the weekly vendor digest") == fingerprint(
        "weekly digest vendor compile"
    )
    assert fingerprint("compile the weekly vendor digest") != fingerprint(
        "escalate billing complaints"
    )


# ------------------------------------------------------------------ clustering


def test_cluster_groups_similar_and_separates_different(settings, store, manifest):
    miner = TaskMiner(settings, store, manifest, client=None)
    clusters = miner.cluster([
        summary(1, "compile the weekly vendor spend digest from invoice emails"),
        summary(2, "compile a weekly vendor spend digest from invoices"),
        summary(3, "triage the support inbox and escalate billing complaints"),
    ])
    sizes = sorted(c.occurrences for c in clusters)
    assert sizes == [1, 2]


def test_cluster_order_does_not_change_the_result(settings, store, manifest):
    miner = TaskMiner(settings, store, manifest, client=None)
    items = [
        summary(1, "compile the weekly vendor spend digest from invoice emails"),
        summary(2, "compile a weekly vendor spend digest from invoices"),
        summary(3, "triage the support inbox and escalate billing complaints"),
    ]
    forward = {c.fingerprint for c in miner.cluster(items)}
    backward = {c.fingerprint for c in miner.cluster(list(reversed(items)))}
    assert forward == backward


def test_representative_is_stable(settings, store, manifest):
    cluster = Cluster([
        summary(1, "compile the weekly vendor spend digest from invoice emails"),
        summary(2, "compile a weekly vendor spend digest from invoices"),
    ])
    assert cluster.representative.workflow == Cluster(
        list(reversed(cluster.summaries))
    ).representative.workflow


# --------------------------------------------------------------------- filters


def test_single_occurrence_is_filtered(settings, store, manifest):
    miner = TaskMiner(settings, store, manifest, client=None)
    clusters = miner.cluster([summary(1, "reconcile the shipping tracker")])
    survivors, dropped = miner.filter_clusters(clusters)
    assert survivors == []
    assert "seen 1x" in dropped[0]


def test_allow_single_overrides_the_occurrence_floor(settings, store, manifest):
    miner = TaskMiner(settings, store, manifest, client=None)
    clusters = miner.cluster([summary(1, "reconcile the shipping tracker")])
    survivors, _ = miner.filter_clusters(clusters, min_occurrences=1)
    assert len(survivors) == 1


@pytest.mark.parametrize("status", ["pending", "approved", "rejected", "invalid"])
def test_prior_proposal_suppresses_reproposal_in_any_status(
    settings, store, manifest, status
):
    """Requirement 1.3/2.3 — the load-bearing one.

    A rejected proposal that comes back next cycle is not a rejection, and the
    operator would have to decline the same workflow forever.
    """
    workflow = "compile the weekly vendor spend digest from invoice emails"
    store.insert_task_proposal({
        "id": "ops.weekly_digest",
        "fingerprint": fingerprint(workflow),
        "status": status,
    })
    miner = TaskMiner(settings, store, manifest, client=None)
    clusters = miner.cluster([summary(1, workflow), summary(2, workflow)])
    survivors, dropped = miner.filter_clusters(clusters)
    assert survivors == []
    assert "already proposed" in dropped[0]


def test_workflow_matching_an_existing_task_is_filtered(settings, store):
    write_user_manifest(
        settings.paths.user_manifest,
        [
            TaskEntry(
                id="ops.weekly_vendor_digest",
                domain="ops",
                type="real",
                origin="user",
                prompt="Compile the weekly vendor spend digest from invoice emails.",
                verify="v.py",
                reset="r.py",
            )
        ],
    )
    merged = Manifest.load(
        settings.paths.manifest,
        settings.paths.user_manifest,
        root=settings.paths.root,
        strict=False,
        check_bench=False,
    )
    miner = TaskMiner(settings, store, merged, client=None)
    workflow = "compile the weekly vendor spend digest from invoice emails"
    clusters = miner.cluster([summary(1, workflow), summary(2, workflow)])
    survivors, dropped = miner.filter_clusters(clusters)
    assert survivors == []
    assert "similar to an existing task" in dropped[0]


# ---------------------------------------------------------------- json parsing


def test_extract_json_object_survives_python_source_in_a_value():
    # The reason this is not `rfind('}')`: stage 2 returns Python source inside
    # a string, and a dict literal in that source ends a naive scan early.
    raw = '```json\n{"id": "a.b", "verify_py": "d = {}\\nassert not d"}\n```'
    parsed = extract_json_object(raw)
    assert parsed["id"] == "a.b"
    assert "assert not d" in parsed["verify_py"]


def test_extract_json_object_tolerates_chatter_and_returns_empty_on_junk():
    assert extract_json_object('Sure! {"id": "a.b"} hope that helps')["id"] == "a.b"
    assert extract_json_object("no json here") == {}
    assert extract_json_object('{"broken": ') == {}


# -------------------------------------------------------------------- drafting


def test_draft_without_negative_assertion_is_rejected(settings, store, manifest):
    miner = TaskMiner(settings, store, manifest, client=None)
    cluster = Cluster([summary(1, "compile the digest"), summary(2, "compile the digest")])
    draft = json.loads(draft_json(verify_py="def verify() -> bool:\n    assert d\n    return True\n"))
    issues = miner._draft_issues(draft, cluster)
    assert any(NEGATIVE_MARKER in i for i in issues)


def test_draft_with_a_malformed_id_is_rejected(settings, store, manifest):
    miner = TaskMiner(settings, store, manifest, client=None)
    cluster = Cluster([summary(1, "compile the digest")])
    issues = miner._draft_issues(json.loads(draft_json("Weekly Digest")), cluster)
    assert any("snake_case_action" in i for i in issues)


def test_draft_colliding_with_an_existing_task_is_rejected(settings, store, manifest):
    miner = TaskMiner(settings, store, manifest, client=None)
    cluster = Cluster([summary(1, "qualify inbound leads")])
    issues = miner._draft_issues(json.loads(draft_json("sales.qualify_lead")), cluster)
    assert any("already a task" in i for i in issues)


def test_drafted_entry_is_always_train_split_and_marked_draft(settings, store, manifest):
    miner = TaskMiner(settings, store, manifest, client=None)
    cluster = Cluster([summary(1, "compile the digest")])
    entry = miner._entry_for(json.loads(draft_json()), cluster)
    # Held-out is the measuring stick; a miner that could grow it would be
    # changing the ruler and the result in one motion.
    assert entry.split == "train"
    assert entry.draft is True
    assert entry.source == "miner"


async def test_draft_one_repairs_an_invalid_first_attempt(
    settings, store, manifest, fake_client
):
    client = fake_client([
        draft_json(verify_py="def verify() -> bool:\n    return True\n"),  # no negative
        draft_json(),  # repaired
    ])
    miner = TaskMiner(settings, store, manifest, client)
    cluster = Cluster([summary(1, "compile the digest"), summary(2, "compile the digest")])

    proposal, why = await miner.draft_one(cluster)
    assert proposal is not None, why
    assert len(client.calls) == 2, "expected exactly one repair round-trip"
    assert NEGATIVE_MARKER in proposal["verify_draft"]


async def test_draft_still_invalid_after_repair_is_dropped_not_stored(
    settings, store, manifest, fake_client
):
    bad = draft_json(verify_py="def verify() -> bool:\n    return True\n")
    miner = TaskMiner(settings, store, manifest, fake_client([bad, bad]))
    cluster = Cluster([summary(1, "compile the digest"), summary(2, "compile the digest")])

    proposal, why = await miner.draft_one(cluster)
    assert proposal is None
    assert "invalid after repair" in why
    assert store.task_proposals() == []


# ------------------------------------------------------------------ full runs


async def test_mine_end_to_end_stores_pending_proposals(
    settings, store, manifest, make_real_episode, fake_client
):
    for i in range(2):
        make_real_episode(f"claw.s{i}", user_text="Compile this week's vendor digest.")

    summary_reply = json.dumps({
        "workflow": "compile the weekly vendor spend digest from invoice emails",
        "domain": "finance",
        "entities": [],
        "note": "",
    })
    client = fake_client([summary_reply, summary_reply, draft_json()])
    result = await TaskMiner(settings, store, manifest, client).mine()

    assert not result.aborted
    assert len(result.proposals) == 1
    stored = store.task_proposals("pending")
    assert [p["id"] for p in stored] == ["ops.weekly_digest"]
    assert stored[0]["occurrences"] == 2
    assert store.mining_watermark(), "a successful run must advance the ledger"


async def test_outage_aborts_without_advancing_the_ledger(
    settings, store, manifest, make_real_episode, failing_client
):
    """design.md, Error handling: retried next cycle, not skipped.

    Advancing the watermark past episodes we never read would lose them
    permanently — they would sit forever behind a mark claiming they were done.
    """
    make_real_episode("claw.s0", user_text="Compile this week's vendor digest.")
    result = await TaskMiner(settings, store, manifest, failing_client([])).mine()

    assert result.aborted
    assert store.mining_watermark() is None
    assert store.task_proposals() == []


async def test_per_run_cap_is_enforced(
    settings, store, manifest, make_real_episode, fake_client
):
    from dataclasses import replace

    capped = replace(settings, max_proposals_per_run=1, mine_min_occurrences=1)
    for i in range(2):
        make_real_episode(f"claw.s{i}", user_text=f"Task {i}")

    replies = [
        json.dumps({"workflow": "compile the weekly vendor digest", "domain": "ops"}),
        json.dumps({"workflow": "escalate billing complaints to finance", "domain": "support"}),
        draft_json("ops.weekly_digest"),
        draft_json("support.escalate_billing"),
    ]
    result = await TaskMiner(capped, store, manifest, fake_client(replies)).mine()

    assert len(result.proposals) == 1
    assert any("cap of 1 reached" in d for d in result.dropped)
