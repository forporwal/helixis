"""Skill distillation and LLM-judge scoring, both on the small-Nemotron/vLLM tier.

This is the recursive step: raw failure trajectories in, reusable skills out.

Two properties matter for the experiment's honesty:

* **Raw evidence.** The distiller reads slices of the actual JSONL trajectories
  (tail-of-context, head-of-response) rather than pre-summarized digests.
* **Support/query separation.** Episodes are stamped with the wiki generation
  that produced them. A given generation's failures can drive distillation
  exactly once, so already-addressed failures never re-trigger it.

The judge is deliberately *not* wired into mocked-tier reward — that stays purely
assertion-based so there is no reward-hacking surface. It is used for real-tier
soft signals and failure-category triage only.
"""

from __future__ import annotations

import asyncio
import json
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .config import Settings
from .llm import BatchStats, LLMClient, Message, extract_json_array
from .store import EpisodeStore
from .wiki import ExperienceWiki, Skill

SKILL_PROMPT = """You are a skill engineer for an autonomous business-automation agent.
Your job: analyze the failed task attempts below and write NEW skills that would have prevented those failures.

---
## Failed Attempts

{failures}

---
## Existing Skills (do NOT duplicate any of these)

{existing}

---
## Instructions

Generate **1 to {max_new_skills}** new skills that directly address the failure patterns above. Prefer concrete, checkable procedure over general advice. A good skill tells the agent what to do differently at a specific decision point; a bad skill restates the task.

Each skill must follow the Claude skill format:
- `name`: a lowercase hyphenated slug (e.g. `verify-list-completeness`). Use `dyn-{next_index:03d}` style only if you cannot find a descriptive name.
- `description`: one sentence — when this skill triggers and what it achieves. This is the retrieval signal, so lead with the trigger condition.
- `content`: 6-15 lines of actionable Markdown containing a heading, numbered steps, a concrete example, and an **Anti-pattern:** section naming the specific mistake observed above.
- `category`: one of {categories}.

**Output:** Return ONLY a valid JSON array. No markdown fences, no prose outside the JSON.

**Example output:**
[
  {{
    "name": "verify-list-completeness",
    "description": "Use when a task requires acting on every record returned by a search. Confirms no items were silently dropped before reporting completion.",
    "content": "## Verify List Completeness\\n\\n1. Record the count of items the search returned.\\n2. Act on each item individually rather than summarizing the batch.\\n3. Re-read the end state and assert the acted-on count matches step 1.\\n\\n**Anti-pattern:** Reporting 'updated all matching records' after acting only on the first page of results.",
    "category": "automation"
  }}
]"""

JUDGE_SYSTEM = """You are a quality reviewer for autonomous agent task attempts.
You will be shown a task instruction and a transcript of what the agent did.
Judge whether the agent substantially completed the task: helpful (+1), unhelpful (-1), or unclear (0).
Use +1 when the agent clearly completed the core requirements.
Use -1 when the agent was off-task, took wrong actions, or left core requirements undone.
Use 0 when the evidence is ambiguous or insufficient.
Think briefly, then end your reply with exactly one of: Score: 1 / Score: -1 / Score: 0"""

CATEGORIES = [
    "automation", "data_handling", "communication", "verification",
    "tool_use", "planning", "common_mistakes", "general",
]


@dataclass
class DistillResult:
    gated_out: bool
    reason: str
    n_failures: int
    skills: list[Skill]
    generation: int
    stats: dict[str, Any]


class Distiller:
    def __init__(
        self,
        settings: Settings,
        store: EpisodeStore,
        wiki: ExperienceWiki,
        client: LLMClient,
    ):
        self.settings = settings
        self.store = store
        self.wiki = wiki
        self.client = client

    # ------------------------------------------------------------------ gating

    def select_failures(self, epoch: int) -> list[dict[str, Any]]:
        """Failed train episodes from the *current* wiki generation only.

        The generation filter is the support/query separation: once generation N
        has produced skills, its failures are spent. Without it the same failures
        would re-trigger distillation every epoch and the wiki would fill with
        near-duplicates of skills that are already deployed.
        """
        episodes = self.store.query_episodes(epoch=epoch, split="train")
        gen = self.wiki.generation
        failures = [
            e
            for e in episodes
            if e["partial_credit"] < 1.0 and e["wiki_generation"] == gen
        ]
        return failures + self.select_real_failures()

    def select_real_failures(self) -> list[dict[str, Any]]:
        """Confidently-judged real failures from the current generation (Req 3.1).

        Three filters, each load-bearing:

        * `judge_passed = 0` — an unjudged episode (the vLLM endpoint was down
          when it was ingested) is not a failure, it is an unknown. Teaching
          from unknowns is how the wiki fills with confident nonsense.
        * `judge_confidence >= threshold` — a 2-1 judge vote is a coin flip
          dressed as a label (Req 3.3).
        * current wiki generation — the same support/query separation the mocked
          path uses, so a real failure drives distillation exactly once.
        """
        episodes = self.store.query_episodes(split="real", wiki_generation=self.wiki.generation)
        return [
            e
            for e in episodes
            if e.get("judge_passed") == 0
            and (e.get("judge_confidence") or 0.0) >= self.settings.judge_min_confidence
        ]

    def should_distill(self, epoch: int, failures: list[dict[str, Any]]) -> tuple[bool, str]:
        episodes = self.store.query_episodes(epoch=epoch, split="train")
        if not failures:
            return False, "no failures in the current wiki generation"
        n_real = sum(1 for f in failures if f.get("tier") == "real")
        if not episodes:
            # No benchmark epoch here, but real usage still produced failures —
            # which is the whole point of spec 03. Gate on the real evidence
            # alone rather than reporting "no episodes for this epoch".
            if n_real:
                return True, f"{n_real} judged-failed real session(s) to learn from"
            return False, "no episodes for this epoch"
        success_rate = sum(e["passed"] for e in episodes) / len(episodes)
        if success_rate < self.settings.distill_success_threshold:
            return True, f"success rate {success_rate:.2f} below threshold"
        if len(failures) >= self.settings.distill_min_failures:
            return True, f"{len(failures)} new failures since last generation"
        return False, (
            f"success rate {success_rate:.2f} healthy and only {len(failures)} "
            f"new failures (need {self.settings.distill_min_failures})"
        )

    # ------------------------------------------------------------------ slicing

    def _failure_block(self, index: int, episode: dict[str, Any]) -> str:
        """Raw trace slice: tail of context + head of response, plus failed assertions.

        Tail-of-context and head-of-response is the Just Talk / MetaClaw slicing —
        the decision point that went wrong is almost always at the boundary
        between what the agent last saw and what it did next.
        """
        path = Path(episode["trajectory_path"])
        records = self.store.read_trajectory(path)
        messages = [r for r in records if r.get("type") == "message"]

        context = ""
        response = ""
        for msg in reversed(messages):
            role = msg.get("role")
            if role == "assistant" and not response:
                response = _text(msg)
            elif role in ("user", "tool") and response and not context:
                context = _text(msg)
            if context and response:
                break
        if not context and messages:
            context = _text(messages[0])

        failed_assertions = []
        for r in records:
            if r.get("type") == "assertions":
                failed_assertions = [
                    a for a in r.get("results", [])
                    if not a.get("passed") and not a.get("excluded")
                ]
                break

        assertion_text = (
            "\n".join(
                f"- {a.get('type')}: {json.dumps(a.get('params', {}))[:200]}"
                for a in failed_assertions[:8]
            )
            or "- (none recorded)"
        )

        if episode.get("tier") == "real":
            # A real session has no assertions to quote — nothing graded it but
            # the judge. Saying so keeps the model from treating an empty
            # assertion list as "nothing went wrong", and the user's own words
            # are the closest thing to a spec that exists here.
            confidence = episode.get("judge_confidence") or 0.0
            request = ""
            for msg in messages:
                if msg.get("role") == "user":
                    request = _text(msg)
                    break
            return (
                f"### Failure {index + 1} — REAL user session `{episode['task_id']}`\n"
                f"This is a real conversation with a user, not a benchmark task. "
                f"An LLM judge rated it unsuccessful (confidence {confidence:.2f}).\n"
                f"**What the user asked (first 400 chars):**\n"
                f"```\n{request[:400]}\n```\n\n"
                f"**Conversation context (last 600 chars):**\n"
                f"```\n...{context[-600:]}\n```\n\n"
                f"**Agent response (first 500 chars):**\n"
                f"```\n{response[:500]}\n```"
            )

        return (
            f"### Failure {index + 1} — task `{episode['task_id']}` "
            f"(domain {episode['domain']}, partial credit {episode['partial_credit']:.2f})\n"
            f"**Conversation context (last 600 chars):**\n"
            f"```\n...{context[-600:]}\n```\n\n"
            f"**Agent response (first 500 chars):**\n"
            f"```\n{response[:500]}\n```\n\n"
            f"**Assertions that failed:**\n{assertion_text}"
        )

    # ------------------------------------------------------------------ distill

    async def distill(self, epoch: int) -> DistillResult:
        failures = self.select_failures(epoch)
        ok, reason = self.should_distill(epoch, failures)
        if not ok:
            self.store.record_distill_run(
                epoch=epoch, generation=self.wiki.generation,
                n_failures=len(failures), n_skills=0, gated_out=True, stats={},
            )
            return DistillResult(True, reason, len(failures), [], self.wiki.generation, {})

        selected = sorted(failures, key=lambda e: e["partial_credit"])[
            : self.settings.max_failures_per_distill
        ]
        blocks = [self._failure_block(i, e) for i, e in enumerate(selected)]
        existing = (
            "\n".join(f"- {s.name}: {s.description}" for s in self.wiki.skills)
            or "(none yet — this is the first generation)"
        )
        prompt = SKILL_PROMPT.format(
            failures="\n\n".join(blocks),
            existing=existing,
            max_new_skills=self.settings.max_new_skills,
            next_index=self.wiki.next_dyn_index(),
            categories=", ".join(f"`{c}`" for c in CATEGORIES),
        )

        messages: list[Message] = [{"role": "user", "content": prompt}]
        completion = await self.client.complete(messages, temperature=0.7, max_tokens=3000)
        raw = extract_json_array(completion.text)

        candidates = self.wiki.finalize_names(
            [c for c in raw if c.get("name") and c.get("description") and c.get("content")]
        )[: self.settings.max_new_skills]

        # Real sessions are not attempts at an epoch's task, so they are cited
        # by session rather than by `epoch-N/`, and `source_tier` records what
        # kind of evidence taught the skill (Req 3.2). A skill learned from a
        # user's actual failure should be auditable as exactly that.
        source_ids = [
            e["task_id"] if e.get("tier") == "real" else f"epoch-{epoch}/{e['task_id']}"
            for e in selected
        ]
        tiers = sorted({str(e.get("tier") or "mocked") for e in selected})
        source_tier = "+".join(tiers)

        added: list[Skill] = []
        for skill in candidates:
            skill.created_epoch = epoch
            skill.source_episodes = source_ids
            skill.source_tier = source_tier
            skill.generation = self.wiki.generation + 1
            if self.wiki.add_skill(skill):
                added.append(skill)
                self.store.register_skill(
                    name=skill.name, description=skill.description,
                    category=skill.category, generation=skill.generation,
                    created_epoch=epoch, source_episodes=source_ids,
                    path=str(skill.path),
                )

        generation = self.wiki.generation
        if added:
            generation = self.wiki.bump_generation()
            self.wiki.append_history({
                "event": "skills_evolved", "epoch": epoch, "generation": generation,
                "skills": [s.name for s in added],
                "source_episodes": source_ids,
                "n_failures_considered": len(failures),
            })

        stats = {
            "tokens_in": completion.tokens_in,
            "tokens_out": completion.tokens_out,
            "latency_s": round(completion.latency_s, 3),
            "model": completion.model,
        }
        self.store.record_distill_run(
            epoch=epoch, generation=generation, n_failures=len(failures),
            n_skills=len(added), gated_out=False, stats=stats,
        )
        return DistillResult(False, reason, len(failures), added, generation, stats)

    # -------------------------------------------------------------------- judge

    async def judge(
        self,
        instruction: str,
        transcript: str,
        votes: int = 3,
        system: str | None = None,
    ) -> dict[str, Any]:
        """Majority-vote LLM judge. Ties resolve to 0 (abstain), never to a guess.

        `system` swaps the rubric. Real Helixis Claw sessions pass their own
        (ingest.REAL_JUDGE_SYSTEM): the benchmark rubric asks whether a *task*
        was completed, which scores an ordinary conversational exchange as a
        failure and would flood the wiki with skills distilled from small talk.
        """
        messages: list[Message] = [
            {"role": "system", "content": system or JUDGE_SYSTEM},
            {
                "role": "user",
                "content": (
                    f"Task instruction:\n{_sanitize(instruction)[:4000]}\n\n"
                    f"Agent transcript:\n{_sanitize(transcript)[:8000]}\n\n"
                    "Did the agent complete the task? End with Score: 1, Score: -1, or Score: 0."
                ),
            },
        ]
        results, stats = await self.client.complete_many(
            [messages] * votes, temperature=0.6, max_tokens=512
        )
        parsed = [_parse_score(r.text) for r in results if r is not None]
        valid = [p for p in parsed if p is not None]
        if not valid:
            return {"score": 0.0, "votes": [], "stats": stats.to_dict()}
        counts = Counter(valid).most_common()
        top = counts[0][1]
        if sum(1 for _, c in counts if c == top) > 1:
            score = 0.0  # genuine tie: abstain rather than break it arbitrarily
        else:
            score = float(counts[0][0])
        return {"score": score, "votes": valid, "stats": stats.to_dict()}

    async def triage_failures(
        self, epoch: int, limit: int = 16
    ) -> tuple[list[dict[str, Any]], BatchStats]:
        """Categorize failures with a concurrent burst — the vLLM batching showcase.

        Every failed episode gets its own request, all in flight at once. The
        returned BatchStats is the throughput evidence for the bounty writeup.
        """
        failures = self.store.query_episodes(epoch=epoch, split="train", passed=False)[:limit]
        if not failures:
            return [], BatchStats()

        batches: list[list[Message]] = []
        for ep in failures:
            records = self.store.read_trajectory(Path(ep["trajectory_path"]))
            msgs = [r for r in records if r.get("type") == "message"]
            transcript = "\n".join(f"[{m.get('role')}] {_text(m)[:400]}" for m in msgs[-12:])
            batches.append([
                {
                    "role": "user",
                    "content": (
                        "Classify why this agent attempt failed, using exactly one of: "
                        "false_success, dropped_list_items, inexact_string, wrong_tool, "
                        "missing_verification, ran_out_of_turns, other.\n\n"
                        f"Task: {ep['task_id']}\n"
                        f"Partial credit: {ep['partial_credit']:.2f}\n\n"
                        f"Transcript tail:\n{transcript[:6000]}\n\n"
                        "Answer with the category word alone."
                    ),
                }
            ])

        # Reasoning models spend their first hundred-odd tokens narrating, so a
        # tight cap truncates before the label is ever emitted.
        results, stats = await self.client.complete_many(
            batches, temperature=0.2, max_tokens=512
        )
        out = []
        for ep, res in zip(failures, results):
            out.append({
                "task_id": ep["task_id"],
                "domain": ep["domain"],
                "partial_credit": ep["partial_credit"],
                "category": _parse_category(res.text) if res else "unknown",
            })
        return out, stats


FAILURE_CATEGORIES = (
    "false_success", "dropped_list_items", "inexact_string", "wrong_tool",
    "missing_verification", "ran_out_of_turns", "other",
)


def _parse_category(text: str) -> str:
    """Extract the category from a possibly-reasoning response.

    Reasoning models (Nemotron Nano v2 among them) narrate before answering, so
    the first token is prose like "Okay" rather than a label. Scanning for a
    known category and taking the LAST occurrence lands on the conclusion rather
    than a candidate the model considered and rejected mid-thought.
    """
    lowered = text.lower()
    best: tuple[int, str] | None = None
    for category in FAILURE_CATEGORIES:
        idx = lowered.rfind(category)
        if idx >= 0 and (best is None or idx > best[0]):
            best = (idx, category)
    if best:
        return best[1]
    # Some models answer with the spaced form ("dropped list items").
    for category in FAILURE_CATEGORIES:
        if category.replace("_", " ") in lowered:
            return category
    return "unparsed"


def _text(msg: dict[str, Any]) -> str:
    content = msg.get("content")
    if isinstance(content, str):
        base = content
    elif isinstance(content, list):
        base = " ".join(
            p.get("text", "") if isinstance(p, dict) else str(p) for p in content
        )
    else:
        base = "" if content is None else str(content)
    if calls := msg.get("tool_calls"):
        base += "\n" + json.dumps(calls, default=str)
    return base


def _sanitize(text: str) -> str:
    """Neutralize tag-shaped content that trips provider content filters."""
    import re

    text = re.sub(r"<tool_call>.*?</tool_call>", "[tool_call block]", text, flags=re.S)
    return re.sub(r"<(/?)([a-zA-Z_][\w-]*)[^>]*>", r"[\1\2]", text)


def _parse_score(text: str) -> int | None:
    import re

    matches = re.findall(r"Score:\s*([-+]?\d)", text)
    if not matches:
        matches = re.findall(r"\\boxed\{([-+]?\d)\}", text)
    if not matches:
        return None
    try:
        value = int(matches[-1])  # last match: the model's final answer
    except ValueError:
        return None
    return value if value in (1, 0, -1) else None


async def gather_limited(coros: list[Any], limit: int) -> list[Any]:
    sem = asyncio.Semaphore(limit)

    async def run(c: Any) -> Any:
        async with sem:
            return await c

    return await asyncio.gather(*(run(c) for c in coros), return_exceptions=True)
