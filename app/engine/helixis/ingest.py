"""Real Helixis Claw sessions -> first-class episodes in the shared store.

Until this module existed the improvement loop learned only from AutomationBench
runs: every real conversation the user had with the agent lived and died inside
the container. This is the path that makes core Requirement 5.4 ("real-tier
trajectories feed the same distillation loop") true for the interactive agent.

Four stages, in this order, and the order is the safety property:

    discover -> parse -> REDACT -> judge -> store

Redaction happens before anything leaves the capture volume. `runs/claw-sessions`
holds whatever OpenClaw wrote, verbatim — that is the agent's own state
directory, not something this module authors — but nothing reaches
`runs/real/`, the database, or a distillation prompt without going through
`Redactor` first. A redactor exception is fail-closed: the session is
quarantined rather than written (design.md, Error handling).

The parser targets OpenClaw's session JSONL as it exists in the pinned image
(`session` version 3). It is deliberately tolerant — an unrecognized record type
is skipped, not fatal — because the format is undocumented and a surprise in one
session must never block the others.
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

from .config import Settings
from .store import EpisodeResult, EpisodeStore

# --------------------------------------------------------------------- parsing

# OpenClaw injects this turn itself when a session starts or is reset. It is not
# something the human asked for, so it must not be handed to the judge as "what
# the user wanted" — a session whose only user turn is this one has no request
# in it at all, and judging it would manufacture a label out of nothing.
BOOTSTRAP_MARKERS = (
    "A new session was started via /new or /reset",
    "Execute your Session Startup sequence",
)


@dataclass
class ParsedSession:
    """One OpenClaw session, mapped onto the trajectory JSONL vocabulary."""

    session_id: str
    path: Path
    messages: list[dict[str, Any]] = field(default_factory=list)
    user_turns: list[str] = field(default_factory=list)
    model: str = ""
    started_at: str = ""
    finished_at: str = ""
    tokens_in: int = 0
    tokens_out: int = 0
    cost_usd: float = 0.0
    cwd: str = ""

    @property
    def steps(self) -> int:
        """Assistant turns — the closest analogue to a benchmark episode's steps."""
        return sum(1 for m in self.messages if m.get("role") == "assistant")

    @property
    def instruction(self) -> str:
        """What the human actually asked, across the whole conversation.

        The judge sees every human turn rather than just the first: a real chat
        refines the goal as it goes, and judging turn one against a transcript
        that fulfilled turn four would score honest work as a failure.
        """
        return "\n\n".join(f"[user] {t}" for t in self.user_turns)

    @property
    def has_request(self) -> bool:
        return bool(self.user_turns)


def _parts_text(parts: Any, kind: str) -> str:
    """Concatenate content parts of one type. Content is always a part list."""
    if not isinstance(parts, list):
        return str(parts or "")
    out = []
    for p in parts:
        if not isinstance(p, dict) or p.get("type") != kind:
            continue
        # The text payload is under the part's own type key for thinking parts
        # ({"type":"thinking","thinking":...}) and under "text" for text parts.
        value = p.get("text") if kind == "text" else p.get(kind)
        if isinstance(value, str) and value:
            out.append(value)
    return "\n".join(out)


def _tool_calls(parts: Any) -> list[dict[str, Any]]:
    if not isinstance(parts, list):
        return []
    calls = []
    for p in parts:
        if isinstance(p, dict) and p.get("type") == "toolCall":
            calls.append({
                "id": str(p.get("id", "")),
                "name": str(p.get("name", "unknown")),
                # The viewer and the distiller both want a string here; OpenClaw
                # stores the already-parsed object.
                "arguments": json.dumps(p.get("arguments", {}), default=str),
            })
    return calls


def parse_session(path: Path) -> ParsedSession:
    """Read one OpenClaw session file into the trajectory vocabulary.

    Raises ValueError when the file has no session header or no messages —
    those are the two cases where "we parsed it" would be a lie, and the caller
    quarantines rather than storing an empty episode.
    """
    session_id = ""
    parsed = ParsedSession(session_id="", path=path)

    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        if not line.strip():
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue  # a torn last line is normal for a file being appended to
        if not isinstance(rec, dict):
            continue

        rtype = rec.get("type")
        if rtype == "session":
            session_id = str(rec.get("id") or "")
            parsed.started_at = str(rec.get("timestamp") or "")
            parsed.cwd = str(rec.get("cwd") or "")
            continue
        if rtype == "model_change":
            parsed.model = str(rec.get("modelId") or parsed.model)
            continue
        if rtype != "message":
            continue

        msg = rec.get("message")
        if not isinstance(msg, dict):
            continue
        role = msg.get("role")
        content = msg.get("content")
        ts = rec.get("timestamp")
        if isinstance(ts, str) and ts:
            parsed.finished_at = ts

        if role == "user":
            text = _parts_text(content, "text")
            record: dict[str, Any] = {"role": "user", "content": text}
            if text and not any(m in text for m in BOOTSTRAP_MARKERS):
                parsed.user_turns.append(text)
        elif role == "assistant":
            record = {
                "role": "assistant",
                "content": _parts_text(content, "text"),
                "reasoning_content": _parts_text(content, "thinking"),
                "tool_calls": _tool_calls(content),
            }
            parsed.model = str(msg.get("model") or parsed.model)
            usage = msg.get("usage")
            if isinstance(usage, dict):
                parsed.tokens_in += int(usage.get("input") or 0)
                parsed.tokens_out += int(usage.get("output") or 0)
                cost = usage.get("cost")
                if isinstance(cost, dict):
                    parsed.cost_usd += float(cost.get("total") or 0.0)
        elif role == "toolResult":
            record = {
                "role": "tool",
                "content": _parts_text(content, "text"),
                "tool_call_id": str(msg.get("toolCallId", "")),
                "name": str(msg.get("toolName", "")),
            }
        else:
            continue
        parsed.messages.append(record)

    parsed.session_id = session_id or path.name.split(".")[0]
    if not session_id:
        raise ValueError("no session header record")
    if not parsed.messages:
        raise ValueError("session contains no messages")
    if not parsed.finished_at:
        parsed.finished_at = parsed.started_at
    return parsed


# ------------------------------------------------------------------- redaction


@dataclass
class RedactionReport:
    """Counts only. A report that quoted what it found would be the leak."""

    counts: dict[str, int] = field(default_factory=dict)

    @property
    def total(self) -> int:
        return sum(self.counts.values())

    def hit(self, label: str, n: int = 1) -> None:
        if n:
            self.counts[label] = self.counts.get(label, 0) + n


# Credential shapes worth catching generically. Deliberately biased toward
# false positives: a redacted string costs the distiller a little context, an
# un-redacted one costs a credential.
CREDENTIAL_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("aws_access_key", re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{8,20}\b")),
    ("private_key", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----", re.S)),
    ("jwt", re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\b")),
    ("bearer_token", re.compile(r"(?i)\b(?:bearer|token)\s+([A-Za-z0-9_\-\.]{16,})")),
    ("openai_style_key", re.compile(r"\b(?:sk|hx|rpa|gsk)[-_](?:live[-_]|test[-_])?[A-Za-z0-9]{16,}\b")),
    ("github_token", re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}\b")),
    ("slack_token", re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}\b")),
    # `KEY=value` / `"secret": "value"` shapes, which catch provider-specific
    # formats the list above does not know about.
    ("assigned_secret", re.compile(
        r"(?i)\b([a-z0-9_]*(?:secret|password|passwd|api[_-]?key|access[_-]?token|credential)[a-z0-9_]*)"
        r"\s*[:=]\s*[\"']?([^\s\"',;]{8,})"
    )),
)

REDACTED = "[REDACTED]"

# Honeypot lines like `AWS_DEFAULT_REGION=us-east-1` carry no secret, and
# redacting a value this short and this common would scrub ordinary prose.
MIN_HONEYPOT_VALUE_LEN = 12


def load_honeypot_values(path: Path) -> list[str]:
    """Exact planted values from the honeypot env file (Req 1.3).

    These are the ones we can assert on: if a honeypot string ever reaches
    `runs/real/` or the database, redaction has a hole, and the end-to-end check
    in tasks.md item 8 is exactly that assertion.
    """
    if not path.exists():
        return []
    values = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        _, _, value = line.partition("=")
        value = value.strip().strip("\"'")
        if len(value) >= MIN_HONEYPOT_VALUE_LEN:
            values.append(value)
    return values


class Redactor:
    """Scrubs credentials from text. Fail-closed by contract: the caller treats
    any exception from `scrub` as a reason to quarantine the session."""

    def __init__(self, honeypot_values: Iterable[str] = ()):
        # Longest first, so a value that contains another is replaced whole.
        self.honeypot_values = sorted(set(honeypot_values), key=len, reverse=True)

    def scrub(self, text: str, report: RedactionReport) -> str:
        if not text:
            return text

        for value in self.honeypot_values:
            if value in text:
                report.hit("honeypot", text.count(value))
                text = text.replace(value, REDACTED)

        for label, pattern in CREDENTIAL_PATTERNS:
            if label == "assigned_secret":
                # Keep the key name, drop the value: "the agent pasted an API
                # key here" is the lesson worth distilling, the key is not.
                text, n = pattern.subn(lambda m: f"{m.group(1)}={REDACTED}", text)
            elif label == "bearer_token":
                text, n = pattern.subn(lambda m: m.group(0).replace(m.group(1), REDACTED), text)
            else:
                text, n = pattern.subn(REDACTED, text)
            report.hit(label, n)
        return text

    def scrub_messages(
        self, messages: list[dict[str, Any]], report: RedactionReport
    ) -> list[dict[str, Any]]:
        out = []
        for msg in messages:
            clean = dict(msg)
            for key in ("content", "reasoning_content"):
                if isinstance(clean.get(key), str):
                    clean[key] = self.scrub(clean[key], report)
            if calls := clean.get("tool_calls"):
                clean["tool_calls"] = [
                    dict(c, arguments=self.scrub(str(c.get("arguments", "")), report))
                    for c in calls
                ]
            out.append(clean)
        return out


# ------------------------------------------------------------------- discovery


@dataclass
class SessionCandidate:
    session_id: str
    path: Path
    mtime: float
    active: bool
    agent: str = "main"


def session_dirs(root: Path) -> list[Path]:
    """Every directory under `root` that holds OpenClaw session files.

    The capture mount is OpenClaw's `agents/` tree (see docker-compose.yml for
    why it is mounted there and not at the sessions directory itself), so the
    real layout is `<agent>/sessions/`. Pointing HELIXIS_CLAW_SESSIONS_DIR
    straight at a single sessions directory also works — that is the shape a
    hand-copied session archive has, and refusing it would make the CLI harder
    to test than it needs to be.
    """
    if not root.is_dir():
        return []
    if (root / "sessions.json").exists():
        return [root]
    dirs = [d / "sessions" for d in sorted(root.iterdir()) if (d / "sessions").is_dir()]
    # Fall back to the root itself so a directory holding only *.jsonl files
    # (no sessions.json yet, e.g. a session that never completed) still works.
    return dirs or ([root] if any(root.glob("*.jsonl*")) else [])


def active_session_ids(sessions_dir: Path) -> set[str]:
    """Session ids OpenClaw currently has open, per its own sessions.json.

    This is what makes "is it finished?" a fact rather than a guess: a session
    the agent is still writing to is named here, so ingestion can leave it alone
    without inferring liveness from mtime alone.
    """
    index = sessions_dir / "sessions.json"
    if not index.exists():
        return set()
    try:
        data = json.loads(index.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return set()
    if not isinstance(data, dict):
        return set()
    ids = set()
    for entry in data.values():
        if isinstance(entry, dict) and entry.get("sessionId"):
            ids.add(str(entry["sessionId"]))
    return ids


def discover_sessions(
    root: Path, quiescent_after_s: float = 300.0
) -> list[SessionCandidate]:
    """Completed session files across every captured agent, oldest first.

    A session counts as complete when it is either archived (OpenClaw renames a
    file to `<id>.jsonl.reset.<ts>` when the user runs /new or /reset — that one
    is definitively over) or not the agent's active session, or simply has not
    been touched for `quiescent_after_s`. The mtime rule is the backstop for a
    crashed gateway that never updated sessions.json.
    """
    now = time.time()
    out: list[SessionCandidate] = []

    for sessions_dir in session_dirs(root):
        active = active_session_ids(sessions_dir)
        # `<agents>/<name>/sessions` -> `<name>`; a bare sessions directory has
        # no agent name to report, so it is attributed to main.
        agent = (
            sessions_dir.parent.name if sessions_dir.name == "sessions" else "main"
        )
        for path in sorted(sessions_dir.iterdir()):
            name = path.name
            if not path.is_file() or ".jsonl" not in name or name == "sessions.json":
                continue
            if name.endswith(".tmp"):
                continue
            session_id = name.split(".")[0]
            try:
                mtime = path.stat().st_mtime
            except OSError:
                continue
            is_archived = ".jsonl.reset." in name
            is_active = session_id in active and not is_archived
            if is_active and (now - mtime) < quiescent_after_s:
                continue
            out.append(SessionCandidate(session_id, path, mtime, is_active, agent))

    return sorted(out, key=lambda c: c.mtime)


# ------------------------------------------------------------------- ingestion

REAL_JUDGE_SYSTEM = """You are reviewing a real conversation between a user and an autonomous assistant.
Judge whether the assistant accomplished what the user actually asked for: helpful (+1), unhelpful (-1), or unclear (0).
Use +1 when the user's request was carried out, including when the assistant correctly answered a question or correctly reported that something could not be done.
Use -1 when the assistant misunderstood the request, took wrong or harmful actions, gave up on something achievable, claimed success it did not achieve, or left the request unresolved.
Use 0 when the conversation is chit-chat with no request in it, or the evidence is too thin to tell.
Judge the outcome for the user, not the assistant's tone or how many tools it used.
Think briefly, then end your reply with exactly one of: Score: 1 / Score: -1 / Score: 0"""


@dataclass
class IngestReport:
    ingested: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    quarantined: list[tuple[str, str]] = field(default_factory=list)
    judged: int = 0
    unjudged: int = 0
    redactions: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "ingested": self.ingested,
            "n_ingested": len(self.ingested),
            "n_skipped": len(self.skipped),
            "quarantined": [{"session": s, "error": e} for s, e in self.quarantined],
            "judged": self.judged,
            "unjudged": self.unjudged,
            "redactions": self.redactions,
        }


class RealSessionIngestor:
    """Turns captured OpenClaw sessions into `tier='real'` episodes."""

    def __init__(
        self,
        settings: Settings,
        store: EpisodeStore,
        wiki_generation: int,
        judge: Any = None,
    ):
        self.settings = settings
        self.store = store
        self.wiki_generation = wiki_generation
        # A Distiller, or None to store episodes unlabeled. Injected rather than
        # constructed here so ingestion has no hard dependency on a reachable
        # vLLM endpoint (design.md, Error handling).
        self.judge = judge
        self.redactor = Redactor(
            load_honeypot_values(settings.paths.policy / "honeypot" / "aws_keys.env")
        )

    @property
    def real_dir(self) -> Path:
        return self.settings.paths.runs / "real"

    def trajectory_path(self, session_id: str) -> Path:
        return self.real_dir / f"{session_id}.jsonl"

    @staticmethod
    def task_id(session_id: str) -> str:
        """`claw.s<8 hex>` — matches the `domain.name` shape used everywhere else.

        Prefixed with `s` because the id's first character is often a digit and
        every other task id in the system starts with a letter.
        """
        short = re.sub(r"[^a-z0-9]", "", session_id.lower())[:8] or "unknown"
        return f"claw.s{short}"

    async def ingest(
        self, *, force: bool = False, quiescent_after_s: float | None = None
    ) -> IngestReport:
        report = IngestReport()
        sessions_dir = self.settings.paths.claw_sessions
        candidates = discover_sessions(
            sessions_dir,
            quiescent_after_s=(
                self.settings.claw_quiescent_after_s
                if quiescent_after_s is None
                else quiescent_after_s
            ),
        )
        ledger = self.store.real_session_ledger()

        for candidate in candidates:
            seen = ledger.get(candidate.session_id)
            if seen and not force:
                report.skipped.append(candidate.session_id)
                continue
            try:
                await self._ingest_one(candidate, report)
            except Exception as exc:  # noqa: BLE001 — one bad session, not a stop
                message = f"{type(exc).__name__}: {exc}"
                report.quarantined.append((candidate.session_id, message))
                self.store.record_real_session(
                    session_id=candidate.session_id,
                    path=str(candidate.path),
                    status="quarantined",
                    error=message[:500],
                    source_mtime=candidate.mtime,
                )
        return report

    async def _ingest_one(self, candidate: SessionCandidate, report: IngestReport) -> None:
        parsed = parse_session(candidate.path)

        # Redaction first: everything below this line writes somewhere durable.
        redaction = RedactionReport()
        messages = self.redactor.scrub_messages(parsed.messages, redaction)
        instruction = self.redactor.scrub(parsed.instruction, redaction)
        report.redactions += redaction.total

        judge_passed: bool | None = None
        judge_confidence: float | None = None
        if self.judge is not None and parsed.has_request:
            transcript = "\n".join(
                f"[{m.get('role')}] {str(m.get('content') or '')[:1500]}" for m in messages
            )
            try:
                verdict = await self.judge.judge(
                    instruction, transcript, system=REAL_JUDGE_SYSTEM
                )
                score = verdict.get("score", 0.0)
                votes = verdict.get("votes") or []
                if votes and score != 0.0:
                    judge_passed = score > 0
                    # Confidence is the share of votes that agreed with the
                    # majority — an unpicked apart 2-1 is exactly the "don't
                    # teach from this" case the threshold exists to catch.
                    agree = sum(1 for v in votes if (v > 0) == judge_passed)
                    judge_confidence = agree / len(votes)
            except Exception:  # noqa: BLE001 — an outage stores the episode unlabeled
                judge_passed = None
        if judge_passed is None:
            report.unjudged += 1
        else:
            report.judged += 1

        path = self.trajectory_path(parsed.session_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        self.store.write_trajectory(
            epoch=0,
            split="real",
            task_id=self.task_id(parsed.session_id),
            path=path,
            metadata={
                "source": "openclaw-session",
                "session_id": parsed.session_id,
                "session_file": candidate.path.name,
                "agent": candidate.agent,
                "model": parsed.model,
                "cwd": parsed.cwd,
                "simulated": False,
                "ingested_from": str(self.settings.paths.claw_sessions),
                "redactions": redaction.counts,
                "judge_passed": judge_passed,
                "judge_confidence": judge_confidence,
            },
            messages=messages,
            # Real sessions have no assertions — there is no ground truth to
            # assert against, which is precisely why they are judge-labeled.
            assertions=[],
        )

        epoch = self.store.last_epoch() or 0
        episode_id = self.store.record_episode(EpisodeResult(
            epoch=epoch,
            task_id=self.task_id(parsed.session_id),
            split="real",
            domain="claw",
            tier="real",
            origin="claw",
            # No assertion grading exists for a real session, so partial credit
            # mirrors the judge rather than inventing a score. `passed` follows
            # the judge too, but the tier filter keeps both out of the curve.
            passed=bool(judge_passed),
            partial_credit=1.0 if judge_passed else 0.0,
            steps=parsed.steps,
            tokens_in=parsed.tokens_in,
            tokens_out=parsed.tokens_out,
            cost_usd=parsed.cost_usd,
            wiki_generation=self.wiki_generation,
            model=parsed.model,
            judge_passed=judge_passed,
            judge_confidence=judge_confidence,
            started_at=parsed.started_at,
            finished_at=parsed.finished_at,
            trajectory_path=str(path),
        ))

        self.store.record_real_session(
            session_id=parsed.session_id,
            path=str(candidate.path),
            status="ingested",
            n_redactions=redaction.total,
            episode_id=episode_id,
            source_mtime=candidate.mtime,
        )
        report.ingested.append(parsed.session_id)
