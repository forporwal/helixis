"""Distilled overview pages over the skill bank (OpenWiki pattern).

Skills are the operational memory; these pages are the human-legible layer —
what the agent has learned as themes, what it still gets wrong, and a per-domain
playbook. Topic keys are stable across regenerations so a page's identity does
not churn, and regeneration is a no-op when the underlying skill content hash is
unchanged.
"""

from __future__ import annotations

import json
from collections import Counter, defaultdict
from datetime import datetime, timezone
from typing import Any

from .store import EpisodeStore
from .wiki import ExperienceWiki

# Confidence is a function of how much evidence a claim rests on, stated
# explicitly so a reader never has to guess how load-bearing a line is.
CONFIDENCE_BANDS = ((5, "high"), (3, "medium"), (1, "low"))


def _confidence(n: int) -> str:
    for threshold, label in CONFIDENCE_BANDS:
        if n >= threshold:
            return label
    return "speculative"


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


def regenerate_pages(wiki: ExperienceWiki, store: EpisodeStore, force: bool = False) -> list[str]:
    """Rewrite overview pages. Returns the names of pages actually written."""
    snapshot = wiki.content_hash()
    if not force and wiki._state().get("snapshot") == snapshot:
        return []  # content-snapshot guard: nothing changed, don't churn the files

    written = [
        _write(wiki, "themes.md", _themes_page(wiki, store)),
        _write(wiki, "open-questions.md", _open_questions_page(wiki, store)),
    ]
    domains = {e["domain"] for e in store.query_episodes(limit=5000)}
    for domain in sorted(domains):
        written.append(_write(wiki, f"{domain}-playbook.md", _playbook_page(wiki, store, domain)))

    wiki._write_state(snapshot=snapshot)
    wiki.append_history({"event": "pages_regenerated", "pages": written, "snapshot": snapshot[:12]})
    return written


def _write(wiki: ExperienceWiki, name: str, body: str) -> str:
    (wiki.pages_dir / name).write_text(body, encoding="utf-8")
    return name


def _themes_page(wiki: ExperienceWiki, store: EpisodeStore) -> str:
    by_category: dict[str, list[Any]] = defaultdict(list)
    for skill in wiki.skills:
        by_category[skill.category].append(skill)

    lines = [
        "# Themes",
        "",
        f"_Generated {_now()} · wiki generation {wiki.generation} · {len(wiki)} skills_",
        "",
        "What the agent has learned, grouped by the kind of mistake it corrects.",
        "",
    ]
    if not by_category:
        lines += ["_No skills distilled yet. The wiki is empty and epoch 0 runs clean._", ""]
        return "\n".join(lines)

    for category in sorted(by_category, key=lambda c: -len(by_category[c])):
        skills = by_category[category]
        lines += [
            f"## {category.replace('_', ' ').title()}",
            "",
            f"**Skills:** {len(skills)} · **Confidence:** {_confidence(len(skills))}",
            "",
        ]
        for skill in sorted(skills, key=lambda s: s.name):
            origin = (
                f" (from {len(skill.source_episodes)} failed episodes, epoch {skill.created_epoch})"
                if skill.source_episodes
                else ""
            )
            lines.append(f"- **[{skill.name}](../skills/{skill.name}/SKILL.md)** — {skill.description}{origin}")
        lines.append("")
    return "\n".join(lines)


def _open_questions_page(wiki: ExperienceWiki, store: EpisodeStore) -> str:
    episodes = store.query_episodes(limit=5000)
    failures = [e for e in episodes if not e["passed"]]

    # Tasks that keep failing even with skills injected are the honest open
    # questions — the distiller has seen them and has not yet cracked them.
    persistent = Counter(
        e["task_id"] for e in failures if e["injected_skills"]
    )
    never_helped = [
        (task, n) for task, n in persistent.most_common(15) if n >= 2
    ]

    lines = [
        "# Open Questions",
        "",
        f"_Generated {_now()} · wiki generation {wiki.generation}_",
        "",
        "Failures the current skill set has not resolved. These are the targets "
        "for the next distillation pass — and the honest limits of the result.",
        "",
    ]
    if not never_helped:
        lines += ["_No task has failed more than once with skills active._", ""]
    else:
        lines += [
            "| Task | Failures with skills active | Confidence this is a real gap |",
            "|---|---|---|",
        ]
        for task, n in never_helped:
            lines.append(f"| `{task}` | {n} | {_confidence(n)} |")
        lines.append("")

    unused = sorted(set(wiki.skill_names) - {
        s for e in episodes for s in e["injected_skills"]
    })
    if unused:
        lines += [
            "## Skills never retrieved",
            "",
            "Distilled but never selected by retrieval — either the trigger "
            "description is poorly worded for matching, or the situation has not recurred.",
            "",
        ]
        lines += [f"- `{name}`" for name in unused]
        lines.append("")
    return "\n".join(lines)


def _playbook_page(wiki: ExperienceWiki, store: EpisodeStore, domain: str) -> str:
    episodes = store.query_episodes(domain=domain, limit=5000)
    by_epoch: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for e in episodes:
        by_epoch[e["epoch"]].append(e)

    relevant = wiki.retrieve(domain, top_k=10)

    lines = [
        f"# {domain.title()} Playbook",
        "",
        f"_Generated {_now()} · {len(episodes)} episodes recorded_",
        "",
        "## Performance by epoch",
        "",
        "| Epoch | Episodes | Mean partial credit | Pass rate |",
        "|---|---|---|---|",
    ]
    for epoch in sorted(by_epoch):
        eps = by_epoch[epoch]
        mpc = sum(e["partial_credit"] for e in eps) / len(eps)
        pr = sum(e["passed"] for e in eps) / len(eps)
        lines.append(f"| {epoch} | {len(eps)} | {mpc:.3f} | {pr:.0%} |")
    lines.append("")

    if relevant:
        lines += ["## Skills most often retrieved here", ""]
        for skill in relevant:
            lines.append(f"### {skill.name}")
            lines.append(f"_{skill.description}_")
            lines.append("")
            lines.append(skill.content.strip())
            lines.append("")
    else:
        lines += ["_No skills distilled for this domain yet._", ""]
    return "\n".join(lines)


def wiki_snapshot(wiki: ExperienceWiki, store: EpisodeStore) -> dict[str, Any]:
    """Machine-readable wiki state for the dashboard."""
    return {
        "generation": wiki.generation,
        "n_skills": len(wiki),
        "skills": [
            {
                "name": s.name,
                "description": s.description,
                "category": s.category,
                "generation": s.generation,
                "created_epoch": s.created_epoch,
                "source_episodes": s.source_episodes,
            }
            for s in sorted(wiki.skills, key=lambda s: (-s.created_epoch, s.name))
        ],
        "history": wiki.history(limit=100),
        "snapshot": wiki.content_hash()[:12],
    }
