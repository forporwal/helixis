"""The experience wiki: persistent, file-backed, two-layer memory.

Layout:
    wiki/skills/<slug>/SKILL.md   YAML frontmatter + steps + example + anti-pattern
    wiki/history.jsonl            every evolution event, generation-stamped
    wiki/pages/themes.md          OpenWiki-style distilled overviews
    wiki/pages/open-questions.md
    wiki/pages/<domain>-playbook.md
    wiki/state.json               generation counter + content snapshot hash

Two deviations from the MetaClaw reference this ports:

1. Frontmatter is real YAML. MetaClaw's hand-rolled `partition(":")` parser
   silently truncates any description containing a colon — and descriptions are
   the primary retrieval signal, so that corruption is not cosmetic.
2. Keyword relevance uses a length-normalized score rather than the overlap
   coefficient `|A∩B| / min(|A|,|B|)`, which systematically favors skills with
   very short name+description over more specific ones.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

import yaml

SLUG_RE = re.compile(r"^[a-z][a-z0-9-]{1,}$")

STOP_WORDS = frozenset(
    """
    the and for you are with that this from have has had was were will would
    can could should not but all any its our their they them then than when
    what which who whom how why into over under more most such only same each
    other some very just about after before because been being does did done
    """.split()
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class Skill:
    name: str
    description: str
    content: str
    category: str = "general"
    generation: int = 0
    created_epoch: int = 0
    source_episodes: list[str] = field(default_factory=list)
    # Which kind of evidence produced this skill: 'mocked', 'real', or
    # 'mocked+real' (spec 03, Req 3.2). Defaults to 'mocked' so every skill
    # written before real ingestion existed keeps its true provenance rather
    # than silently claiming real-world origin.
    source_tier: str = "mocked"
    path: Path | None = None

    def to_markdown(self) -> str:
        front = yaml.safe_dump(
            {
                "name": self.name,
                "description": self.description,
                "category": self.category,
                "generation": self.generation,
                "created_epoch": self.created_epoch,
                "source_tier": self.source_tier,
                "source_episodes": self.source_episodes,
            },
            sort_keys=False,
            allow_unicode=True,
        )
        return f"---\n{front}---\n\n{self.content.strip()}\n"

    @classmethod
    def from_markdown(cls, text: str, path: Path | None = None) -> Skill | None:
        if not text.startswith("---"):
            return None
        _, _, rest = text.partition("---")
        front_text, sep, body = rest.partition("\n---")
        if not sep:
            return None
        try:
            meta = yaml.safe_load(front_text) or {}
        except yaml.YAMLError:
            return None
        if not isinstance(meta, dict) or not meta.get("name") or not meta.get("description"):
            return None
        return cls(
            name=str(meta["name"]),
            description=str(meta["description"]),
            content=body.lstrip("\n").rstrip() + "\n",
            category=str(meta.get("category", "general")),
            generation=int(meta.get("generation", 0)),
            created_epoch=int(meta.get("created_epoch", 0)),
            source_tier=str(meta.get("source_tier", "mocked")),
            source_episodes=list(meta.get("source_episodes") or []),
            path=path,
        )


class ExperienceWiki:
    """Skill bank + retrieval + generation counter, all on disk."""

    def __init__(self, root: Path, retrieval_mode: str = "keyword", embedding_model: str = ""):
        self.root = root
        self.skills_dir = root / "skills"
        self.pages_dir = root / "pages"
        self.history_path = root / "history.jsonl"
        self.state_path = root / "state.json"
        self.retrieval_mode = retrieval_mode
        self.embedding_model = embedding_model
        for d in (self.skills_dir, self.pages_dir):
            d.mkdir(parents=True, exist_ok=True)
        self._skills: dict[str, Skill] = {}
        self._embedder: Any = None
        self._embeddings: Any = None
        self.reload()

    # ------------------------------------------------------------------- state

    @property
    def generation(self) -> int:
        return self._state().get("generation", 0)

    def _state(self) -> dict[str, Any]:
        if not self.state_path.exists():
            return {"generation": 0, "snapshot": "", "updated_at": _now()}
        try:
            return json.loads(self.state_path.read_text())
        except json.JSONDecodeError:
            return {"generation": 0, "snapshot": "", "updated_at": _now()}

    def _write_state(self, **updates: Any) -> None:
        state = self._state() | updates | {"updated_at": _now()}
        self.state_path.write_text(json.dumps(state, indent=2))

    def bump_generation(self) -> int:
        gen = self.generation + 1
        self._write_state(generation=gen)
        return gen

    def content_hash(self) -> str:
        """SHA-256 over skill content — the OpenWiki no-op guard for page regen."""
        h = hashlib.sha256()
        for name in sorted(self._skills):
            s = self._skills[name]
            h.update(name.encode())
            h.update(s.description.encode())
            h.update(s.content.encode())
        return h.hexdigest()

    # ------------------------------------------------------------------ skills

    def reload(self) -> None:
        self._skills.clear()
        self._embeddings = None
        for skill_md in sorted(self.skills_dir.glob("*/SKILL.md")):
            skill = Skill.from_markdown(skill_md.read_text(encoding="utf-8"), skill_md)
            if skill:
                self._skills[skill.name] = skill

    @property
    def skills(self) -> list[Skill]:
        return list(self._skills.values())

    @property
    def skill_names(self) -> list[str]:
        return sorted(self._skills)

    def __len__(self) -> int:
        return len(self._skills)

    def add_skill(self, skill: Skill) -> bool:
        """Write a new skill. Returns False if the name is already taken."""
        if skill.name in self._skills:
            return False
        path = self.skills_dir / skill.name / "SKILL.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(skill.to_markdown(), encoding="utf-8")
        skill.path = path
        self._skills[skill.name] = skill
        self._embeddings = None
        return True

    def append_history(self, record: dict[str, Any]) -> None:
        with self.history_path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps({"ts": _now(), **record}, default=str) + "\n")

    def history(self, limit: int = 200) -> list[dict[str, Any]]:
        if not self.history_path.exists():
            return []
        lines = self.history_path.read_text(encoding="utf-8").splitlines()
        out = []
        for line in lines[-limit:]:
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                continue
        return out

    def next_dyn_index(self) -> int:
        """Next free `dyn-NNN` slot, for skills the model failed to name well."""
        seen = [
            int(m.group(1))
            for name in self._skills
            if (m := re.match(r"^dyn-(\d+)$", name))
        ]
        return max(seen, default=0) + 1

    def finalize_names(self, raw: Iterable[dict[str, Any]]) -> list[Skill]:
        """Validate slugs, dedup within batch and against the bank."""
        counter = self.next_dyn_index()
        taken = set(self._skills)
        out: list[Skill] = []
        for item in raw:
            name = str(item.get("name", "")).strip().lower()
            if not SLUG_RE.match(name) or name in taken:
                name = f"dyn-{counter:03d}"
                counter += 1
            taken.add(name)
            out.append(
                Skill(
                    name=name,
                    description=str(item.get("description", "")).strip(),
                    content=str(item.get("content", "")).strip(),
                    category=str(item.get("category", "general")).strip() or "general",
                )
            )
        return out

    # --------------------------------------------------------------- retrieval

    def retrieve(self, task_description: str, top_k: int = 4) -> list[Skill]:
        """Top-k relevant skills. Empty wiki -> empty list (honest epoch-0 baseline)."""
        if not self._skills:
            return []
        if self.retrieval_mode == "embedding":
            hits = self._embedding_retrieve(task_description, top_k)
            if hits is not None:
                return hits
        return self._keyword_retrieve(task_description, top_k)

    def _keyword_retrieve(self, task_description: str, top_k: int) -> list[Skill]:
        query = _tokenize(task_description)
        if not query:
            return self._backfill([], top_k)
        scored: list[tuple[float, Skill]] = []
        for skill in self._skills.values():
            # Name+description carry the trigger condition; body text is weighted
            # lower so a long example section can't dominate the match.
            strong = _tokenize(f"{skill.name} {skill.description}")
            weak = _tokenize(skill.content)
            if not strong:
                continue
            overlap_strong = len(query & strong)
            overlap_weak = len(query & weak)
            if not overlap_strong and not overlap_weak:
                continue
            # Length-normalized: avoids the min()-denominator bias toward terse skills.
            score = (overlap_strong + 0.3 * overlap_weak) / (
                len(query) ** 0.5 * len(strong) ** 0.5
            )
            scored.append((score, skill))
        scored.sort(key=lambda p: (-p[0], p[1].name))
        return self._backfill([s for _, s in scored[:top_k]], top_k)

    def _backfill(self, hits: list[Skill], top_k: int) -> list[Skill]:
        """Top up a thin match with the newest skills.

        Lexical overlap is a weak proxy for applicability: the most valuable
        skills are often the most general ("verify list completeness" applies
        almost everywhere) and precisely those share no vocabulary with any
        single task id. Returning an empty block would make the epoch identical
        to the epoch-0 baseline and silently erase the learning signal, so a
        thin match is topped up rather than left short. Newest-first, because a
        skill distilled from a recent failure is the one least likely to have
        been exercised yet.
        """
        if len(hits) >= top_k:
            return hits
        chosen = {s.name for s in hits}
        rest = sorted(
            (s for s in self._skills.values() if s.name not in chosen),
            key=lambda s: (-s.created_epoch, -s.generation, s.name),
        )
        return hits + rest[: top_k - len(hits)]

    def _embedding_retrieve(self, task_description: str, top_k: int) -> list[Skill] | None:
        try:
            if self._embedder is None:
                from sentence_transformers import SentenceTransformer

                self._embedder = SentenceTransformer(self.embedding_model)
            names = self.skill_names
            if self._embeddings is None:
                texts = [
                    f"{self._skills[n].name}. {self._skills[n].description}. "
                    f"{self._skills[n].content[:200]}"
                    for n in names
                ]
                self._embeddings = self._embedder.encode(
                    texts, normalize_embeddings=True
                )
            q = self._embedder.encode([task_description], normalize_embeddings=True)[0]
            sims = self._embeddings @ q
            order = sorted(range(len(names)), key=lambda i: -float(sims[i]))
            return [self._skills[names[i]] for i in order[:top_k]]
        except Exception:
            # Missing torch/model download failure must degrade to keyword, not crash.
            return None

    @staticmethod
    def format_for_injection(skills: list[Skill]) -> str:
        """The 'Active Skills' block appended to the task's system prompt."""
        if not skills:
            return ""
        parts = [
            "## Active Skills",
            "",
            "Lessons distilled from your own prior failures on tasks like this one. "
            "Apply them before falling back on defaults.",
            "",
        ]
        for s in skills:
            parts += [f"### {s.name}", f"_{s.description}_", "", s.content.strip(), ""]
        return "\n".join(parts).strip()


def _tokenize(text: str) -> set[str]:
    tokens = re.findall(r"[a-zA-Z0-9]+", text.lower())
    return {_stem(t) for t in tokens if len(t) >= 3 and t not in STOP_WORDS}


def _stem(token: str) -> str:
    for suffix in ("ingly", "edly", "ing", "ies", "ers", "ed", "es", "er", "ly", "s"):
        if token.endswith(suffix) and len(token) - len(suffix) >= 3:
            return token[: -len(suffix)]
    return token
