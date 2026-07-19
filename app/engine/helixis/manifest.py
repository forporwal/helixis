"""The task manifest: the frozen bench demo set plus the operator's own tasks.

`tasks.yaml` is the 30-task AutomationBench curriculum and is FROZEN — nothing
in this module ever writes it. `tasks.user.yaml` sits beside it, is git-ignored,
and is the only file the `helixis task` CLI mutates. `load()` merges the two and
is the single place task ids are resolved, so a duplicate id fails once, loudly,
at load rather than silently shadowing a bench task mid-epoch.

Two task types exist and they differ only in who grades them:

  bench — references an AutomationBench task id (`bench_ref`, defaulting to the
          task's own id) and is graded by that task's assertions. Executes
          through `AutomationBenchBackend` with no special case.
  real  — real-tier style: a prompt plus `verify.py` end-state checks and an
          idempotent `reset.py`. Graded by those scripts, run by the real-tier
          driver, and — like all real-tier work — excluded from the headline
          curve.

Retirement rather than deletion is deliberate (Requirement 2.3): a task with
recorded episodes still owns trajectories on disk and rows in the store, so
`remove` marks it `retired: true` and the loader drops it from future epochs
while every historical query still resolves.
"""

from __future__ import annotations

import os
import re
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Literal

import yaml

from .backends import TaskSpec

TaskType = Literal["bench", "real"]
Origin = Literal["bench", "user"]

# `domain.snake_case_action` — the shape the retrieval query and the bench's own
# task ids both assume (`_retrieval_query` splits on the dot).
TASK_ID_RE = re.compile(r"^[a-z][a-z0-9_]*\.[a-z][a-z0-9_]*$")

HEADER = """\
# Helixis user tasks — MANAGED FILE.
#
# Written by `helixis task add/remove`; hand edits are re-validated on load, so
# a malformed entry aborts the next run rather than silently shrinking the task
# set. This file is git-ignored: it is your curriculum, not the frozen bench
# demo set in tasks.yaml (which tooling never writes).
#
#   helixis task add --id ops.weekly_digest --domain ops --type real \\
#       --prompt "..." --verify app/real_tier/weekly_digest/verify.py \\
#       --reset app/real_tier/weekly_digest/reset.py
#   helixis task list
#   helixis task validate
"""


class ManifestError(RuntimeError):
    """A manifest that cannot be trusted. Always names the file and the entry."""


@dataclass
class TaskEntry:
    """One row of the merged manifest."""

    id: str
    domain: str
    split: str = "train"
    type: TaskType = "bench"
    origin: Origin = "bench"
    prompt: str = ""
    bench_ref: str = ""
    verify: str = ""
    reset: str = ""
    retired: bool = False
    added_at: str = ""
    # Who authored this entry: "" for a hand-added task, "miner" for one the
    # task miner drafted and the operator approved (spec 05, Req 3.3). Recorded
    # so the Tasks grid can badge mined work — a curriculum that grew itself
    # should say so rather than looking hand-written.
    source: str = ""
    # An approved `real` proposal whose verify.py/reset.py are still LLM drafts
    # (spec 05, Req 2.4). The entry is in the manifest and visible everywhere,
    # but it can never run: an unreviewed drafted verifier grading real work is
    # exactly the failure mode the human-in-the-loop gate exists to prevent.
    # A human completes the scripts and deletes this flag.
    draft: bool = False

    @property
    def bench_task_id(self) -> str:
        """The id AutomationBench knows this task by."""
        return self.bench_ref or self.id

    @property
    def runnable_in_epoch(self) -> bool:
        """Can a mocked epoch execute this?

        `real` tasks need live credentials and the real-tier driver, so an epoch
        skips them with a warning instead of handing them to the bench backend,
        which would score a perfectly valid task 0.0 for "task not found".
        """
        return not self.retired and not self.draft and self.type == "bench"

    def to_spec(self) -> TaskSpec:
        return TaskSpec(
            task_id=self.id,
            domain=self.domain,
            split=self.split,
            origin=self.origin,
            type=self.type,
            bench_ref=self.bench_ref,
            retired=self.retired,
        )

    def to_yaml_entry(self) -> dict[str, Any]:
        """Serialize for `tasks.user.yaml` — omit defaults to keep it readable."""
        out: dict[str, Any] = {"id": self.id, "type": self.type, "domain": self.domain}
        out["split"] = self.split
        if self.prompt:
            out["prompt"] = self.prompt
        if self.bench_ref and self.bench_ref != self.id:
            out["bench_ref"] = self.bench_ref
        if self.verify:
            out["verify"] = self.verify
        if self.reset:
            out["reset"] = self.reset
        if self.retired:
            out["retired"] = True
        if self.draft:
            out["draft"] = True
        if self.source:
            out["source"] = self.source
        if self.added_at:
            out["added_at"] = self.added_at
        return out

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "domain": self.domain,
            "split": self.split,
            "type": self.type,
            "origin": self.origin,
            "prompt": self.prompt,
            "bench_ref": self.bench_task_id,
            "verify": self.verify,
            "reset": self.reset,
            "retired": self.retired,
            "draft": self.draft,
            "source": self.source,
            "added_at": self.added_at,
        }


@dataclass
class Issue:
    """A validation finding. `fatal` ones make the manifest unloadable."""

    task_id: str
    message: str
    fatal: bool = True

    def to_dict(self) -> dict[str, Any]:
        return {"task_id": self.task_id, "message": self.message, "fatal": self.fatal}


# --------------------------------------------------------------------- parsing


def _parse_bench(path: Path) -> list[TaskEntry]:
    """The frozen file: `train:`/`heldout:` lists of `{id, domain}`."""
    if not path.exists():
        raise ManifestError(f"{path}: the frozen task manifest is missing.")
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    entries: list[TaskEntry] = []
    for split in ("train", "heldout"):
        for raw in data.get(split) or []:
            if not isinstance(raw, dict) or "id" not in raw:
                raise ManifestError(f"{path}: entry in `{split}` is missing an `id`.")
            entries.append(
                TaskEntry(
                    id=str(raw["id"]),
                    domain=str(raw.get("domain", "")),
                    split=split,
                    type="bench",
                    origin="bench",
                )
            )
    return entries


def _parse_user(path: Path) -> list[TaskEntry]:
    """The mutable file: a flat list, or `{tasks: [...]}`. Split is per-entry."""
    if not path.exists():
        return []
    raw_doc = yaml.safe_load(path.read_text(encoding="utf-8"))
    if raw_doc in (None, ""):
        return []
    if isinstance(raw_doc, dict):
        rows = raw_doc.get("tasks")
        if rows is None:
            raise ManifestError(
                f"{path}: expected a `tasks:` list at the top level, "
                f"got keys {sorted(raw_doc)}."
            )
    else:
        rows = raw_doc
    if not isinstance(rows, list):
        raise ManifestError(f"{path}: `tasks` must be a list, got {type(rows).__name__}.")

    entries: list[TaskEntry] = []
    for i, raw in enumerate(rows):
        if not isinstance(raw, dict):
            raise ManifestError(f"{path}: entry #{i} is not a mapping.")
        task_id = str(raw.get("id", "")).strip()
        if not task_id:
            raise ManifestError(f"{path}: entry #{i} is missing an `id`.")
        task_type = str(raw.get("type", "bench")).strip() or "bench"
        if task_type not in ("bench", "real"):
            raise ManifestError(
                f"{path}: `{task_id}` has type {task_type!r}; expected 'bench' or 'real'."
            )
        split = str(raw.get("split", "train")).strip() or "train"
        if split not in ("train", "heldout"):
            raise ManifestError(
                f"{path}: `{task_id}` has split {split!r}; expected 'train' or 'heldout'."
            )
        entries.append(
            TaskEntry(
                id=task_id,
                domain=str(raw.get("domain", "")).strip(),
                split=split,
                type=task_type,  # type: ignore[arg-type]
                origin="user",
                prompt=str(raw.get("prompt", "") or "").strip(),
                bench_ref=str(raw.get("bench_ref", "") or "").strip(),
                verify=str(raw.get("verify", "") or "").strip(),
                reset=str(raw.get("reset", "") or "").strip(),
                retired=bool(raw.get("retired", False)),
                draft=bool(raw.get("draft", False)),
                source=str(raw.get("source", "") or "").strip(),
                added_at=str(raw.get("added_at", "") or ""),
            )
        )
    return entries


# ------------------------------------------------------------------ validation


def validate_entries(
    entries: Iterable[TaskEntry],
    *,
    root: Path,
    check_bench: bool = True,
) -> list[Issue]:
    """Requirement 1.4. Returns findings; the caller decides how loud to be.

    Bench-ref resolution is a *warning* when AutomationBench is not importable —
    a laptop without the bench installed must still be able to manage tasks —
    but a hard failure when it IS installed and the id does not exist, because
    then we know for certain the task would score 0.0 for "task not found".
    """
    issues: list[Issue] = []
    bench_ids = bench_task_domains() if check_bench else None

    for e in entries:
        if not TASK_ID_RE.match(e.id):
            issues.append(
                Issue(
                    e.id,
                    f"id {e.id!r} must be `domain.snake_case_action` "
                    f"(lowercase, digits and underscores, exactly one dot).",
                )
            )
        if not e.domain:
            issues.append(Issue(e.id, "`domain` is required."))

        if e.type == "real":
            if not e.prompt:
                issues.append(Issue(e.id, "a `real` task needs a non-empty `prompt`."))
            for label, raw in (("verify", e.verify), ("reset", e.reset)):
                # A draft task's scripts are EXPECTED to be missing — that is
                # what `draft: true` means, and `runnable_in_epoch` already
                # guarantees it cannot run. Reporting it fatally would make an
                # approved proposal abort every subsequent load (strict=True),
                # so the finding is loud but non-fatal until a human finishes
                # the job and drops the flag (spec 05, Req 2.4).
                if not raw:
                    issues.append(
                        Issue(
                            e.id,
                            f"a `real` task needs a `{label}` script path.",
                            fatal=not e.draft,
                        )
                    )
                    continue
                path = _resolve(raw, root)
                if not path.exists():
                    issues.append(
                        Issue(
                            e.id,
                            (
                                f"DRAFT — `{label}` script not written yet: {path}. "
                                f"An LLM drafted {path.name}.draft beside it; review "
                                f"it, rename it, then remove `draft: true`. This task "
                                f"cannot run until you do."
                            )
                            if e.draft
                            else f"`{label}` script not found: {path}",
                            fatal=not e.draft,
                        )
                    )
            if e.bench_ref:
                issues.append(
                    Issue(e.id, "`bench_ref` is meaningless on a `real` task.", fatal=False)
                )
            if e.draft and all(
                raw and _resolve(raw, root).exists() for raw in (e.verify, e.reset)
            ):
                # Both scripts are on disk but the flag is still set. Say so
                # explicitly: the task is silently sitting out of every run, and
                # "I wrote the verifier and nothing happened" is the exact
                # confusion this gate would otherwise cause.
                issues.append(
                    Issue(
                        e.id,
                        "both scripts now exist, but `draft: true` is still set so "
                        "this task is excluded from every run. Remove the flag to "
                        "activate it.",
                        fatal=False,
                    )
                )
        else:
            if e.prompt:
                issues.append(
                    Issue(
                        e.id,
                        "`prompt` is ignored on a `bench` task — the prompt comes "
                        "from AutomationBench.",
                        fatal=False,
                    )
                )
            if e.verify or e.reset:
                issues.append(
                    Issue(
                        e.id,
                        "`verify`/`reset` are ignored on a `bench` task — grading "
                        "comes from the bench assertions.",
                        fatal=False,
                    )
                )
            if not check_bench:
                # Not asked to resolve refs — stay silent rather than reporting
                # a check we deliberately skipped as an unverified one.
                pass
            elif bench_ids is None:
                issues.append(
                    Issue(
                        e.id,
                        "AutomationBench could not be consulted (not installed, "
                        "or its dataset failed to load), so `bench_ref` "
                        f"{e.bench_task_id!r} is UNVERIFIED. If the id is wrong "
                        "the task will score 0.0 at run time.",
                        fatal=False,
                    )
                )
            elif e.bench_task_id not in bench_ids:
                issues.append(
                    Issue(
                        e.id,
                        f"`{e.bench_task_id}` does not exist in AutomationBench. "
                        f"Check the id (and set --bench-ref if this task is meant "
                        f"to grade against a differently-named bench task).",
                    )
                )
            elif bench_ids[e.bench_task_id] != e.domain:
                # The backend looks the task up via get_domain_dataset(domain),
                # so a right id under a wrong domain raises `Unknown domain` at
                # run time and the task scores 0.0 having never been attempted.
                issues.append(
                    Issue(
                        e.id,
                        f"`{e.bench_task_id}` lives in AutomationBench domain "
                        f"{bench_ids[e.bench_task_id]!r}, but this task declares "
                        f"domain {e.domain!r}. Set `--domain "
                        f"{bench_ids[e.bench_task_id]}`.",
                    )
                )
    return issues


def _resolve(raw: str, root: Path) -> Path:
    p = Path(raw).expanduser()
    return p if p.is_absolute() else (root / p)


_BENCH_IDS_CACHE: tuple[dict[str, str] | None] | None = None


def bench_task_domains() -> dict[str, str] | None:
    """Every AutomationBench task id mapped to its domain, or None if unavailable.

    The domain matters as much as the id: the backend looks the task up with
    `get_domain_dataset(spec.domain)`, so a correct id filed under the wrong
    domain raises `Unknown domain` at run time and burns a task slot on a 0.0.
    Returning the mapping rather than a flat set is what lets validation catch
    that before an epoch pays for it.

    Memoized per process: this loads all seven domain datasets and costs ~2s,
    which is fine once and wasteful per-entry.

    None means "we could not check", which is reported as a warning — never as
    a passing check. Silently treating an unavailable bench as agreement is how
    a typo'd `bench_ref` reaches an epoch and scores 0.0 for "task not found".

    `get_available_domains()` is the supported entry point; `DOMAINS` is the
    dict behind it and is read only as a fallback. Both are tried because
    getting this wrong fails OPEN — the lookup returns None, every bench_ref
    downgrades to a warning, and validation quietly stops validating.
    """
    global _BENCH_IDS_CACHE
    if _BENCH_IDS_CACHE is not None:
        return _BENCH_IDS_CACHE[0]

    result = _load_bench_task_domains()
    _BENCH_IDS_CACHE = (result,)
    return result


def _load_bench_task_domains() -> dict[str, str] | None:
    try:
        from automationbench.domains import get_available_domains, get_domain_dataset
    except ImportError:
        return None  # bench not installed — the honest, common case

    try:
        domains = list(get_available_domains())
    except Exception:
        try:
            from automationbench.domains import DOMAINS

            domains = list(DOMAINS)
        except Exception:  # pragma: no cover - a broken bench install
            return None

    ids: dict[str, str] = {}
    for domain in domains:
        try:
            for row in get_domain_dataset(domain):
                ids[str(row["task"])] = domain
        except Exception:
            continue
    return ids or None


# --------------------------------------------------------------------- loading


@dataclass
class Manifest:
    """The merged, validated curriculum."""

    entries: list[TaskEntry] = field(default_factory=list)
    warnings: list[Issue] = field(default_factory=list)
    bench_path: Path | None = None
    user_path: Path | None = None

    # -------------------------------------------------------------- construction

    @classmethod
    def load(
        cls,
        path: Path,
        user_path: Path | None = None,
        *,
        root: Path | None = None,
        strict: bool = True,
        check_bench: bool = True,
    ) -> Manifest:
        """Merge the frozen and user manifests.

        A duplicate id aborts (Requirement 1.1): silently letting a user entry
        shadow a bench task would change what the frozen curve measures without
        changing a single number's appearance.

        `check_bench=False` skips bench-ref resolution, which costs ~2s because
        it loads every domain dataset. Correct for pure reads (listing tasks for
        the dashboard, which polls); never for `add` or `validate`, where an
        unresolvable ref is the whole point of asking.
        """
        user_path = user_path if user_path is not None else default_user_path(path)
        root = root or path.resolve().parents[2]

        bench = _parse_bench(path)
        user = _parse_user(user_path)

        seen: dict[str, TaskEntry] = {}
        for e in bench:
            if e.id in seen:
                raise ManifestError(
                    f"{path}: duplicate task id `{e.id}` in the frozen manifest."
                )
            seen[e.id] = e
        for e in user:
            if e.id in seen:
                raise ManifestError(
                    f"{user_path}: task id `{e.id}` is already defined in {path.name}. "
                    f"Ids must be unique across both manifests — rename the user "
                    f"task, or drop it if you meant to reuse the bench task "
                    f"(a `bench` task with `bench_ref: {e.id}` does that)."
                )
            seen[e.id] = e

        issues = validate_entries(user, root=root, check_bench=check_bench)
        fatal = [i for i in issues if i.fatal]
        if fatal and strict:
            detail = "\n".join(f"  - {i.task_id}: {i.message}" for i in fatal)
            raise ManifestError(
                f"{user_path} has {len(fatal)} invalid entr"
                f"{'y' if len(fatal) == 1 else 'ies'}:\n{detail}\n"
                f"Fix them (or run `helixis task validate`) — a bad user manifest "
                f"must never silently shrink the task set."
            )

        return cls(
            entries=list(seen.values()),
            warnings=[i for i in issues if not i.fatal] + (fatal if not strict else []),
            bench_path=path,
            user_path=user_path,
        )

    # ------------------------------------------------------------------ queries

    def for_split(self, split: str, *, include_real: bool = False) -> list[TaskSpec]:
        """Specs a mocked epoch should execute.

        Retired tasks are dropped here — one place, so every caller (runner,
        experiment, dashboard) sees the same active set. `real` tasks are
        dropped too unless asked for; `skipped_in_split` reports what and why so
        the epoch log can say it out loud rather than shrinking in silence.

        Drafts are dropped unconditionally, `include_real` notwithstanding: a
        task whose verifier is still an unreviewed LLM draft must not grade real
        work (spec 05, Req 2.4), and that is not a preference the caller gets to
        override.
        """
        return [
            e.to_spec()
            for e in self.entries
            if e.split == split
            and not e.retired
            and not e.draft
            and (include_real or e.type == "bench")
        ]

    def skipped_in_split(self, split: str) -> list[TaskEntry]:
        return [
            e
            for e in self.entries
            if e.split == split
            and not e.retired
            and (e.type != "bench" or e.draft)
        ]

    def get(self, task_id: str) -> TaskEntry | None:
        return next((e for e in self.entries if e.id == task_id), None)

    @property
    def train(self) -> list[TaskSpec]:
        return self.for_split("train")

    @property
    def heldout(self) -> list[TaskSpec]:
        return self.for_split("heldout")

    @property
    def user_entries(self) -> list[TaskEntry]:
        return [e for e in self.entries if e.origin == "user"]


def default_user_path(bench_path: Path) -> Path:
    return bench_path.with_name("tasks.user.yaml")


# --------------------------------------------------------------------- writing


def write_user_manifest(path: Path, entries: list[TaskEntry]) -> None:
    """Atomic write: temp file in the same directory, then rename.

    Same directory so the rename stays on one filesystem and is therefore
    actually atomic — a half-written manifest must never be loadable.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = HEADER + "\n" + yaml.safe_dump(
        {"tasks": [e.to_yaml_entry() for e in entries]},
        sort_keys=False,
        allow_unicode=True,
        default_flow_style=False,
    )
    fd, tmp_name = tempfile.mkstemp(dir=str(path.parent), prefix=".tasks.user.", suffix=".tmp")
    tmp = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(payload)
            fh.flush()
            os.fsync(fh.fileno())
        tmp.replace(path)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise


def load_user_entries(path: Path) -> list[TaskEntry]:
    """Read the user manifest alone, for mutation. No merge, no strict gate."""
    return _parse_user(path)
