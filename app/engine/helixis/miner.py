"""Mining real usage into proposed training tasks.

The last loop to close. Specs 03 and 04 gave Helixis real transcripts and a
mutable curriculum; this module notices what the user actually does with the
agent and drafts tasks from it, so the curriculum converges on their work
instead of on whatever the bench happened to ship.

Two stages, both concurrent on the vLLM endpoint (Req 1.4):

    stage 1  one cheap call per episode -> {workflow, domain, entities}
             cluster the workflow lines by token cosine
             filter: occurrences >= N, not ~= an existing task, not ~= a prior proposal
    stage 2  one call per surviving cluster -> a full task draft + verify outline
             validate engine-side, one repair round, then store as `pending`

**The miner proposes and never enacts.** Nothing here writes `tasks.user.yaml`,
and nothing here can. Approval goes back through `helixis task add` (spec 04),
which stays the single manifest writer — so a mined task passes exactly the
validation a hand-written one does.

Two safety properties are worth stating out loud:

* **Redacted input only** (Req 3.2). Episodes arrive already scrubbed by
  ingestion's fail-closed `Redactor`, and every slice is scrubbed AGAIN on the
  way into a prompt. That is deliberate belt-and-braces: ingestion redacts what
  it knows about at ingest time, and a prompt is the one place a leak leaves the
  machine.
* **Drafted verifiers never grade unreviewed** (Req 2.4). Stage 2 writes a
  `verify.py` outline, but approval stores it as `verify.py.draft` and marks the
  task `draft: true`, which excludes it from every run until a human finishes
  it. An LLM that both proposes the task and grades it is a closed loop with no
  ground truth in it.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

import yaml

from .config import Settings
from .ingest import Redactor, load_honeypot_values
from .llm import BatchStats, LLMClient, Message
from .manifest import TASK_ID_RE, Manifest, TaskEntry, validate_entries
from .store import EpisodeStore
from .wiki import _tokenize

# The marker stage 2 must emit on the assertion that checks something is NOT
# true. Requirement 1.2 wants a negative assertion in every drafted verifier,
# and "the model probably included one" is not a check — an explicit marker is.
# Verifiers that only assert the happy path pass on a no-op agent.
NEGATIVE_MARKER = "# NEGATIVE ASSERTION"

SUMMARY_PROMPT = """You are reading one real conversation between a user and their automation agent.

Summarize the WORKFLOW the user was trying to accomplish — the repeatable job, not this instance of it.

Conversation:
```
{transcript}
```

Rules:
- `workflow` is ONE line, present tense, naming the job generically. Write "compile a weekly vendor spend digest from invoice emails", not "compile the March 14 digest for Acme".
- Strip every specific name, date, amount and identifier out of `workflow`. Those go in `entities`.
- `domain` is one lowercase word: the business area (ops, sales, finance, support, research, eng, personal).
- If the conversation contains no actual task — chit-chat, a question answered from memory, a failed start — set `workflow` to "" and say why in `note`.

Return ONLY a JSON object:
{{"workflow": "...", "domain": "...", "entities": ["..."], "note": ""}}"""

DRAFT_PROMPT = """You are drafting a training task for an automation agent, based on work its user does repeatedly.

## The recurring workflow

{workflow}

Seen {occurrences} times. Domain: {domain}.

## Representative transcript slices

{slices}

## What you are producing

A task specification the agent can be trained and graded on. It must generalize: grading the exact episodes above would teach nothing, so the prompt describes the JOB, and the verifier checks the END STATE that job should produce.

Fields:
- `id`: `{domain}.snake_case_action` — lowercase, digits and underscores, EXACTLY one dot. Name the action, not the instance.
- `domain`: `{domain}`.
- `prompt`: 2-5 sentences instructing the agent to do this job. Written for a fresh agent with no memory of the conversations above. Name the inputs it will find and the output it must produce. Do NOT embed specific names, dates, amounts, credentials or identifiers from the transcripts.
- `verify_py`: a Python `verify.py` OUTLINE checking the end state. It must define `def verify() -> bool:` and contain at least one NEGATIVE assertion — a check that something which must NOT happen did not happen — marked with a `{negative_marker}` comment on the line above it. A verifier that only checks the happy path passes an agent that did nothing.
- `reset_py`: a Python `reset.py` OUTLINE returning the environment to a clean pre-task state. Must be idempotent — running it twice must be the same as running it once.

Both scripts are OUTLINES for a human to finish. Mark anything you had to guess with a `# TODO(human):` comment. Guessing silently is worse than an honest TODO.

Return ONLY a JSON object:
{{"id": "...", "domain": "...", "prompt": "...", "verify_py": "...", "reset_py": "..."}}"""

REPAIR_PROMPT = """The task draft you just returned was rejected by the validator.

Your draft:
```json
{draft}
```

Problems:
{issues}

Fix exactly these problems and return the corrected JSON object. Same fields, same format. Change nothing else."""


# --------------------------------------------------------------- similarity


def similarity(a: set[str], b: set[str]) -> float:
    """Cosine over binary token sets.

    Symmetric, unlike `wiki._keyword_retrieve`'s query/document score — which is
    right there (a query and a skill are different kinds of thing) and wrong
    here (two workflow summaries are the same kind of thing, so "A is like B"
    must equal "B is like A" or clustering depends on iteration order).
    Length-normalized for the same reason the wiki normalizes: without it a
    verbose summary matches everything.
    """
    if not a or not b:
        return 0.0
    return len(a & b) / ((len(a) ** 0.5) * (len(b) ** 0.5))


def fingerprint(workflow: str) -> str:
    """Stable identity for a workflow cluster, used for suppression (Req 2.3).

    Sorted stemmed tokens rather than a hash of the raw line: two runs that
    summarize the same job with different word order must produce the same
    fingerprint, or a rejected proposal comes back next cycle wearing a hat.
    Kept human-readable so `sqlite3 helixis.db 'select fingerprint...'` is a
    useful thing to type.
    """
    return " ".join(sorted(_tokenize(workflow)))


# ------------------------------------------------------------------- shapes


@dataclass
class WorkflowSummary:
    """Stage-1 output for one episode."""

    episode_id: int
    task_id: str
    workflow: str
    domain: str
    entities: list[str] = field(default_factory=list)
    note: str = ""
    trajectory_path: str = ""

    @property
    def tokens(self) -> set[str]:
        return _tokenize(self.workflow)

    @property
    def usable(self) -> bool:
        return bool(self.workflow.strip()) and bool(self.tokens)


@dataclass
class Cluster:
    """A recurring workflow: several episodes that are the same job."""

    summaries: list[WorkflowSummary] = field(default_factory=list)

    @property
    def occurrences(self) -> int:
        return len(self.summaries)

    @property
    def representative(self) -> WorkflowSummary:
        """The member closest to every other member — the least odd phrasing.

        Ties break on episode id so the choice is deterministic; a cluster that
        picks a different representative on each run produces a different
        fingerprint on each run, which would defeat suppression entirely.
        """
        if len(self.summaries) == 1:
            return self.summaries[0]
        return max(
            self.summaries,
            key=lambda s: (
                sum(similarity(s.tokens, o.tokens) for o in self.summaries if o is not s),
                -s.episode_id,
            ),
        )

    @property
    def workflow(self) -> str:
        return self.representative.workflow

    @property
    def domain(self) -> str:
        """Majority domain, ties broken alphabetically for determinism."""
        counts: dict[str, int] = {}
        for s in self.summaries:
            if s.domain:
                counts[s.domain] = counts.get(s.domain, 0) + 1
        if not counts:
            return "ops"
        return min(counts, key=lambda d: (-counts[d], d))

    @property
    def fingerprint(self) -> str:
        return fingerprint(self.workflow)


@dataclass
class MineResult:
    aborted: bool = False
    reason: str = ""
    n_episodes: int = 0
    n_clusters: int = 0
    proposals: list[dict[str, Any]] = field(default_factory=list)
    dropped: list[str] = field(default_factory=list)
    watermark: str = ""
    stats: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "aborted": self.aborted,
            "reason": self.reason,
            "n_episodes": self.n_episodes,
            "n_clusters": self.n_clusters,
            "n_proposals": len(self.proposals),
            "proposals": [
                {
                    "id": p["id"],
                    "domain": p["domain"],
                    "occurrences": p["occurrences"],
                    "title": p["title"],
                }
                for p in self.proposals
            ],
            "dropped": self.dropped,
            "watermark": self.watermark,
            "stats": self.stats,
        }


# ------------------------------------------------------------------- miner


class TaskMiner:
    def __init__(
        self,
        settings: Settings,
        store: EpisodeStore,
        manifest: Manifest,
        client: LLMClient,
    ):
        self.settings = settings
        self.store = store
        self.manifest = manifest
        self.client = client
        self.redactor = Redactor(
            load_honeypot_values(settings.paths.policy / "honeypot" / "aws_keys.env")
        )

    # ------------------------------------------------------------ transcripts

    def _slice(self, episode: dict[str, Any], *, budget: int = 2400) -> str:
        """A redacted, length-capped view of one real session.

        Head-and-tail rather than a flat truncation: the user's opening request
        says what the job IS, and the tail says how it ended. The middle of a
        long session is mostly tool chatter, and spending the budget on it would
        crowd out both of the parts that carry the workflow.
        """
        raw = str(episode.get("trajectory_path") or "").strip()
        # An empty path becomes Path("."), which is a directory that very much
        # exists — so `read_trajectory`'s existence check passes and the open
        # raises IsADirectoryError. A trajectory that was deleted, or an episode
        # row written without one, must degrade to "no evidence" rather than
        # taking the whole mining run down with it.
        path = Path(raw) if raw else None
        records = (
            self.store.read_trajectory(path) if path is not None and path.is_file() else []
        )
        messages = [r for r in records if r.get("type") == "message"]
        if not messages:
            return "(no messages recorded)"

        lines = []
        for m in messages:
            role = str(m.get("role", "?"))
            content = str(m.get("content") or "").strip()
            if calls := m.get("tool_calls"):
                names = ", ".join(str(c.get("name", "?")) for c in calls)
                content = f"{content}\n[calls: {names}]".strip()
            if content:
                lines.append(f"[{role}] {content[:500]}")

        text = "\n".join(lines)
        if len(text) > budget:
            head = budget // 2
            text = f"{text[:head]}\n...[{len(text) - budget} chars elided]...\n{text[-(budget - head):]}"
        # Ingestion already scrubbed these, and we scrub again: this is the
        # boundary where text leaves the machine (Req 3.2).
        return self.redactor.scrub(text, _NullReport())

    # ---------------------------------------------------------------- stage 1

    async def summarize(
        self, episodes: list[dict[str, Any]]
    ) -> tuple[list[WorkflowSummary], BatchStats]:
        """One concurrent burst, one summary per episode (Req 1.4)."""
        if not episodes:
            return [], BatchStats()

        batches: list[list[Message]] = [
            [
                {
                    "role": "user",
                    "content": SUMMARY_PROMPT.format(transcript=self._slice(ep)),
                }
            ]
            for ep in episodes
        ]
        results, stats = await self.client.complete_many(
            batches, temperature=0.2, max_tokens=700
        )

        out: list[WorkflowSummary] = []
        for ep, res in zip(episodes, results):
            if res is None:
                continue  # one failed call is not a failed run
            parsed = extract_json_object(res.text)
            if not parsed:
                continue
            summary = WorkflowSummary(
                episode_id=int(ep.get("id") or 0),
                task_id=str(ep.get("task_id") or ""),
                workflow=str(parsed.get("workflow") or "").strip(),
                domain=_clean_domain(str(parsed.get("domain") or "")),
                entities=[str(e) for e in (parsed.get("entities") or [])][:12],
                note=str(parsed.get("note") or "").strip(),
                trajectory_path=str(ep.get("trajectory_path") or ""),
            )
            if summary.usable:
                out.append(summary)
        return out, stats

    # --------------------------------------------------------------- clustering

    def cluster(self, summaries: list[WorkflowSummary]) -> list[Cluster]:
        """Greedy single-pass agglomeration by token cosine.

        Deliberately simple. The alternative — real embeddings — buys a better
        clustering of a set that is, in the demo timeframe, a few dozen items,
        and costs a model download the offline path cannot make. `wiki.retrieve`
        makes the same trade in the same direction.

        Sorted by fingerprint first so clustering does not depend on the order
        episodes came out of the database.
        """
        threshold = self.settings.mine_similarity_threshold
        clusters: list[Cluster] = []
        for summary in sorted(summaries, key=lambda s: (s.workflow, s.episode_id)):
            best: tuple[float, Cluster] | None = None
            for c in clusters:
                # Compare against the whole cluster, not just its representative:
                # average linkage keeps a cluster from drifting one loose match
                # at a time into a bucket whose members no longer resemble each
                # other.
                score = sum(
                    similarity(summary.tokens, m.tokens) for m in c.summaries
                ) / len(c.summaries)
                if score >= threshold and (best is None or score > best[0]):
                    best = (score, c)
            if best is None:
                clusters.append(Cluster([summary]))
            else:
                best[1].summaries.append(summary)
        return sorted(clusters, key=lambda c: (-c.occurrences, c.fingerprint))

    # ------------------------------------------------------------------ filters

    def _manifest_signatures(self) -> list[set[str]]:
        """Token sets for every task already in the merged manifest.

        Includes retired ones: a task the operator retired is a task they
        already decided about, and re-proposing it as a discovery would be the
        miner arguing with them.
        """
        out = []
        for e in self.manifest.entries:
            text = f"{e.id.replace('.', ' ').replace('_', ' ')} {e.prompt}"
            if tokens := _tokenize(text):
                out.append(tokens)
        return out

    def filter_clusters(
        self, clusters: list[Cluster], *, min_occurrences: int | None = None
    ) -> tuple[list[Cluster], list[str]]:
        """Requirement 1.3. Returns survivors and a reason per rejection."""
        floor = (
            self.settings.mine_min_occurrences
            if min_occurrences is None
            else min_occurrences
        )
        threshold = self.settings.mine_similarity_threshold
        known_fingerprints = self.store.proposal_fingerprints()
        manifest_sigs = self._manifest_signatures()

        survivors: list[Cluster] = []
        dropped: list[str] = []
        for c in clusters:
            if c.occurrences < floor:
                dropped.append(
                    f"{c.workflow!r}: seen {c.occurrences}x, need {floor}"
                )
                continue
            if c.fingerprint in known_fingerprints:
                # Any status — including rejected. This is the suppression that
                # makes "no" mean no (Req 2.3).
                dropped.append(f"{c.workflow!r}: already proposed once")
                continue
            near = max(
                (similarity(c.representative.tokens, sig) for sig in manifest_sigs),
                default=0.0,
            )
            if near >= threshold:
                dropped.append(
                    f"{c.workflow!r}: {near:.2f} similar to an existing task"
                )
                continue
            survivors.append(c)
        return survivors, dropped

    # ---------------------------------------------------------------- stage 2

    def _draft_issues(self, draft: dict[str, Any], cluster: Cluster) -> list[str]:
        """Validate one stage-2 draft. Empty list means storable.

        Runs the SPEC-04 validator rather than a second implementation of it:
        a proposal that would be refused at `task add` time must never reach the
        feed wearing a green light (design.md §1).
        """
        issues: list[str] = []
        task_id = str(draft.get("id") or "").strip()
        if not TASK_ID_RE.match(task_id):
            issues.append(
                f"`id` is {task_id!r}; it must be `domain.snake_case_action` "
                f"(lowercase, digits and underscores, exactly one dot)."
            )
        if self.manifest.get(task_id) is not None:
            issues.append(f"`{task_id}` is already a task in the manifest; pick another id.")

        verify = str(draft.get("verify_py") or "")
        if NEGATIVE_MARKER not in verify:
            issues.append(
                f"`verify_py` has no assertion marked `{NEGATIVE_MARKER}`. Add a "
                f"check that something which must NOT happen did not happen."
            )
        if "def verify(" not in verify:
            issues.append("`verify_py` must define `def verify() -> bool:`.")
        if not str(draft.get("reset_py") or "").strip():
            issues.append("`reset_py` is empty.")

        entry = self._entry_for(draft, cluster)
        for issue in validate_entries(
            [entry], root=self.settings.paths.root, check_bench=False
        ):
            if issue.fatal:
                issues.append(issue.message)
        return issues

    def _entry_for(self, draft: dict[str, Any], cluster: Cluster) -> TaskEntry:
        """The TaskEntry an approval would write. Built here so the thing we
        validate is the thing we would enact — not a lookalike."""
        task_id = str(draft.get("id") or "").strip()
        slug = task_id.split(".", 1)[-1] if "." in task_id else task_id
        task_dir = f"app/real_tier/{slug}"
        return TaskEntry(
            id=task_id,
            domain=_clean_domain(str(draft.get("domain") or "")) or cluster.domain,
            # Always `train` (Req 1.2). The held-out set is the measuring stick;
            # a miner that could grow it would be changing the ruler and the
            # result in the same motion.
            split="train",
            type="real",
            origin="user",
            prompt=str(draft.get("prompt") or "").strip(),
            verify=f"{task_dir}/verify.py",
            reset=f"{task_dir}/reset.py",
            source="miner",
            draft=True,
        )

    async def draft_one(self, cluster: Cluster) -> tuple[dict[str, Any] | None, str]:
        """One cluster -> one validated proposal, with a single repair round.

        A draft that is still invalid after the repair is DROPPED, not stored as
        pending (design.md, Error handling). Storing it would put an item in
        front of the operator that cannot be approved — the worst kind of feed
        row, because it costs attention and offers no action.
        """
        slices = "\n\n".join(
            f"### Session {i + 1} — `{s.task_id}`\n```\n{self._slice(self._episode(s), budget=1600)}\n```"
            for i, s in enumerate(cluster.summaries[:3])
        )
        prompt = DRAFT_PROMPT.format(
            workflow=cluster.workflow,
            occurrences=cluster.occurrences,
            domain=cluster.domain,
            slices=slices,
            negative_marker=NEGATIVE_MARKER,
        )

        messages: list[Message] = [{"role": "user", "content": prompt}]
        completion = await self.client.complete(messages, temperature=0.4, max_tokens=2500)
        draft = extract_json_object(completion.text)
        model_id = completion.model

        issues = self._draft_issues(draft, cluster) if draft else ["no JSON object returned"]
        if issues:
            repair: list[Message] = messages + [
                {"role": "assistant", "content": completion.text},
                {
                    "role": "user",
                    "content": REPAIR_PROMPT.format(
                        draft=json.dumps(draft or {}, indent=2)[:4000],
                        issues="\n".join(f"- {i}" for i in issues),
                    ),
                },
            ]
            retry = await self.client.complete(repair, temperature=0.2, max_tokens=2500)
            draft = extract_json_object(retry.text) or draft
            model_id = retry.model
            issues = self._draft_issues(draft, cluster) if draft else ["no JSON object returned"]

        if issues or not draft:
            return None, f"{cluster.workflow!r}: invalid after repair — {'; '.join(issues)}"

        entry = self._entry_for(draft, cluster)
        # Scrub the model's own output too. It was asked not to copy identifiers
        # out of the transcripts; this is what makes that a guarantee rather
        # than an instruction (Req 3.2).
        report = _NullReport()
        verify_py = self.redactor.scrub(str(draft.get("verify_py") or ""), report)
        reset_py = self.redactor.scrub(str(draft.get("reset_py") or ""), report)
        entry.prompt = self.redactor.scrub(entry.prompt, report)

        proposal = {
            "id": entry.id,
            "fingerprint": cluster.fingerprint,
            "status": "pending",
            "title": cluster.workflow,
            "domain": entry.domain,
            "task_type": entry.type,
            "draft_yaml": yaml.safe_dump(
                entry.to_yaml_entry(), sort_keys=False, allow_unicode=True
            ),
            "verify_draft": verify_py,
            "reset_draft": reset_py,
            "source_episode_ids": [s.episode_id for s in cluster.summaries],
            "occurrences": cluster.occurrences,
            "model_id": model_id,
        }
        return proposal, ""

    def _episode(self, summary: WorkflowSummary) -> dict[str, Any]:
        return {"trajectory_path": summary.trajectory_path, "task_id": summary.task_id}

    # -------------------------------------------------------------------- mine

    async def mine(
        self,
        *,
        min_occurrences: int | None = None,
        max_proposals: int | None = None,
    ) -> MineResult:
        """The whole pipeline. Advances the ledger only on a clean run."""
        cap = max_proposals or self.settings.max_proposals_per_run

        # Requirement 1.4: mining spends tokens, so it answers to the same cap
        # every other model call does. Checked before the burst rather than
        # during it — aborting halfway would leave the ledger honest but the
        # spend already made.
        total = self.store.total_cost()
        if total >= self.settings.total_cost_cap_usd:
            return MineResult(
                aborted=True,
                reason=(
                    f"total spend ${total:.2f} has reached the cap "
                    f"${self.settings.total_cost_cap_usd:.2f}"
                ),
            )

        watermark = self.store.mining_watermark()
        episodes = self.store.real_episodes_since(
            watermark, limit=self.settings.mine_max_episodes
        )
        if not episodes:
            return MineResult(
                aborted=False,
                reason="no new real episodes since the last mining run",
                watermark=watermark or "",
            )

        summaries, stage1 = await self.summarize(episodes)
        if not summaries:
            # Every summary call failed or returned junk. The endpoint is down
            # or the model is not answering in the requested shape; either way
            # the ledger must not advance past episodes we never actually read.
            return MineResult(
                aborted=True,
                reason=(
                    f"stage 1 produced no usable summaries from {len(episodes)} "
                    f"episode(s) ({stage1.n_failed} call(s) failed) — leaving the "
                    f"ledger where it is so the next run retries them"
                ),
                n_episodes=len(episodes),
                stats={"stage1": stage1.to_dict()},
            )

        clusters = self.cluster(summaries)
        survivors, dropped = self.filter_clusters(
            clusters, min_occurrences=min_occurrences
        )

        proposals: list[dict[str, Any]] = []
        for cluster in survivors:
            if len(proposals) >= cap:
                dropped.append(
                    f"{cluster.workflow!r}: per-run cap of {cap} reached"
                )
                continue
            proposal, why = await self.draft_one(cluster)
            if proposal is None:
                dropped.append(why)
                continue
            if self.store.insert_task_proposal(proposal):
                proposals.append(proposal)
            else:
                # Lost a race with a concurrent run, or the fingerprint check
                # above and the UNIQUE index disagree. Either way the proposal
                # exists; do not report it as new.
                dropped.append(f"{cluster.workflow!r}: already stored")

        new_watermark = max(
            (str(e.get("finished_at") or "") for e in episodes),
            default=watermark or "",
        )
        stats = {
            "stage1": stage1.to_dict(),
            "cost_usd": round(
                self.settings.distiller.cost(stage1.tokens_in, stage1.tokens_out), 6
            ),
        }
        self.store.record_mining_run(
            watermark=new_watermark,
            n_episodes=len(episodes),
            n_clusters=len(clusters),
            n_proposals=len(proposals),
            model_id=self.settings.distiller.model,
            stats=stats,
        )
        return MineResult(
            n_episodes=len(episodes),
            n_clusters=len(clusters),
            proposals=proposals,
            dropped=dropped,
            watermark=new_watermark,
            stats=stats,
        )


# ----------------------------------------------------------------- helpers


class _NullReport:
    """Redaction counts we do not need. `Redactor.scrub` requires a report."""

    def hit(self, label: str, n: int = 1) -> None:  # noqa: D102
        return None


def _clean_domain(raw: str) -> str:
    """First lowercase word. Models like to answer 'ops (operations)'."""
    match = re.search(r"[a-z]+", raw.lower())
    return match.group(0) if match else ""


def extract_json_object(text: str) -> dict[str, Any]:
    """Pull a JSON object out of a possibly-fenced, possibly-chatty response.

    The object counterpart of `llm.extract_json_array`, and it lives here rather
    than there because the miner is the only caller. Braces are matched by
    depth rather than by `rfind('}')`: stage 2 returns Python source inside a
    string value, and a dict literal in that source ends the naive scan early.
    """
    cleaned = text.strip()
    if "```" in cleaned:
        parts = cleaned.split("```")
        cleaned = max(parts, key=len)
        if cleaned.lstrip().lower().startswith("json"):
            cleaned = cleaned.lstrip()[4:]

    start = cleaned.find("{")
    if start == -1:
        return {}
    depth = 0
    in_string = False
    escaped = False
    for i in range(start, len(cleaned)):
        ch = cleaned[i]
        if in_string:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                in_string = False
            continue
        if ch == '"':
            in_string = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                try:
                    parsed = json.loads(cleaned[start : i + 1])
                except json.JSONDecodeError:
                    return {}
                return parsed if isinstance(parsed, dict) else {}
    return {}


def episode_links(
    store: EpisodeStore, episode_ids: Iterable[int]
) -> list[dict[str, Any]]:
    """Resolve stored episode ids into trajectory-viewer coordinates.

    The review view links each piece of evidence back to the conversation it
    came from (Req 2.1) — a proposal you cannot audit is a proposal you can only
    trust, and the whole point of human approval is not having to.
    """
    ids = [int(i) for i in episode_ids]
    if not ids:
        return []
    placeholders = ",".join("?" * len(ids))
    with store.connect() as con:
        rows = con.execute(
            f"SELECT id, epoch, split, task_id, finished_at FROM episodes"
            f" WHERE id IN ({placeholders}) ORDER BY finished_at",
            ids,
        ).fetchall()
    return [dict(r) for r in rows]
