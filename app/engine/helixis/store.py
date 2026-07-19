"""Two-layer episode storage.

Raw layer: one JSONL file per attempt under `runs/epoch-<N>/<split>/<task_id>.jsonl`,
holding every message, tool call and tool result verbatim. Never summarized in
place — the distiller reads these raw (Meta-Harness Table 3: raw traces beat
summaries, 50% vs ~35%).

Index layer: SQLite over the same data, powering dashboard queries, distiller
selection, and resume-after-crash.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator, Literal

Split = Literal["train", "heldout", "real"]

SCHEMA = """
CREATE TABLE IF NOT EXISTS episodes (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    epoch            INTEGER NOT NULL,
    task_id          TEXT    NOT NULL,
    split            TEXT    NOT NULL,
    domain           TEXT    NOT NULL,
    tier             TEXT    NOT NULL DEFAULT 'mocked',
    origin           TEXT    NOT NULL DEFAULT 'bench',
    passed           INTEGER NOT NULL DEFAULT 0,
    partial_credit   REAL    NOT NULL DEFAULT 0.0,
    steps            INTEGER NOT NULL DEFAULT 0,
    tokens_in        INTEGER NOT NULL DEFAULT 0,
    tokens_out       INTEGER NOT NULL DEFAULT 0,
    cost_usd         REAL    NOT NULL DEFAULT 0.0,
    wiki_generation  INTEGER NOT NULL DEFAULT 0,
    injected_skills  TEXT    NOT NULL DEFAULT '[]',
    model            TEXT    NOT NULL DEFAULT '',
    -- Real-tier outcome, from the LLM judge (spec 03, Req 2.2). NULL means
    -- "never judged" and is the honest state for every mocked episode as well
    -- as for a real one ingested while the judge endpoint was down — it is not
    -- the same as a judged failure, and the distiller treats it differently.
    judge_passed     INTEGER,
    judge_confidence REAL,
    error            TEXT,
    started_at       TEXT    NOT NULL,
    finished_at      TEXT    NOT NULL,
    trajectory_path  TEXT    NOT NULL,
    UNIQUE(epoch, task_id, split)
);
CREATE INDEX IF NOT EXISTS idx_ep_epoch  ON episodes(epoch, split);
CREATE INDEX IF NOT EXISTS idx_ep_task   ON episodes(task_id);
CREATE INDEX IF NOT EXISTS idx_ep_passed ON episodes(passed);
CREATE INDEX IF NOT EXISTS idx_ep_domain ON episodes(domain);
CREATE INDEX IF NOT EXISTS idx_ep_gen    ON episodes(wiki_generation);
CREATE INDEX IF NOT EXISTS idx_ep_origin ON episodes(origin);

-- Every mutation of the active task set, so the curve can say "curriculum
-- changed at epoch N" instead of quietly comparing two different task sets
-- (spec 04, Requirement 3.2). `epoch` is the last epoch recorded when the
-- change was made — i.e. the last point the old curriculum is valid through.
CREATE TABLE IF NOT EXISTS curriculum_events (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    ts         TEXT    NOT NULL,
    action     TEXT    NOT NULL,
    task_id    TEXT    NOT NULL,
    split      TEXT    NOT NULL DEFAULT 'train',
    task_type  TEXT    NOT NULL DEFAULT 'bench',
    epoch      INTEGER,
    detail     TEXT    NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_ce_epoch ON curriculum_events(epoch);

CREATE TABLE IF NOT EXISTS epochs (
    epoch           INTEGER NOT NULL,
    split           TEXT    NOT NULL,
    status          TEXT    NOT NULL,
    n_tasks         INTEGER NOT NULL DEFAULT 0,
    n_done          INTEGER NOT NULL DEFAULT 0,
    cost_usd        REAL    NOT NULL DEFAULT 0.0,
    wiki_generation INTEGER NOT NULL DEFAULT 0,
    started_at      TEXT,
    finished_at     TEXT,
    PRIMARY KEY (epoch, split)
);

CREATE TABLE IF NOT EXISTS skills (
    name            TEXT PRIMARY KEY,
    description     TEXT NOT NULL,
    category        TEXT NOT NULL DEFAULT 'general',
    generation      INTEGER NOT NULL DEFAULT 0,
    created_epoch   INTEGER NOT NULL DEFAULT 0,
    source_episodes TEXT NOT NULL DEFAULT '[]',
    path            TEXT NOT NULL,
    created_at      TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS distill_runs (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    epoch        INTEGER NOT NULL,
    generation   INTEGER NOT NULL,
    n_failures   INTEGER NOT NULL,
    n_skills     INTEGER NOT NULL,
    gated_out    INTEGER NOT NULL DEFAULT 0,
    stats        TEXT NOT NULL DEFAULT '{}',
    created_at   TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS policy_events (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    ts          TEXT NOT NULL,
    kind        TEXT NOT NULL,
    severity    TEXT NOT NULL DEFAULT 'MED',
    action      TEXT NOT NULL DEFAULT '',
    actor       TEXT NOT NULL DEFAULT '',
    dst_host    TEXT NOT NULL DEFAULT '',
    dst_port    INTEGER,
    reason      TEXT NOT NULL DEFAULT '',
    is_honeypot INTEGER NOT NULL DEFAULT 0,
    raw         TEXT NOT NULL DEFAULT '{}',
    fingerprint TEXT UNIQUE
);
CREATE INDEX IF NOT EXISTS idx_pe_ts ON policy_events(ts DESC);

CREATE TABLE IF NOT EXISTS proposals (
    chunk_id        TEXT PRIMARY KEY,
    rule_name       TEXT NOT NULL DEFAULT '',
    intent_summary  TEXT NOT NULL DEFAULT '',
    status          TEXT NOT NULL DEFAULT 'pending',
    prover_findings TEXT NOT NULL DEFAULT '[]',
    requires_human  INTEGER NOT NULL DEFAULT 1,
    rejection_reason TEXT,
    created_at      TEXT NOT NULL,
    decided_at      TEXT
);

-- Ingestion ledger for real Helixis Claw sessions (spec 03, Req 2.1).
--
-- Idempotency keys on `session_id`, not on the file path: OpenClaw renames a
-- session file when it is reset (`<id>.jsonl` -> `<id>.jsonl.reset.<ts>`), so a
-- path-keyed ledger would re-ingest the same conversation under a second
-- episode the moment the user typed /new. `status` is 'ingested' or
-- 'quarantined'; a quarantined row records why and is skipped forever unless
-- ingestion is re-run with --force, so one unparseable session can never wedge
-- the pipeline for the others.
CREATE TABLE IF NOT EXISTS real_sessions (
    session_id   TEXT PRIMARY KEY,
    path         TEXT NOT NULL,
    status       TEXT NOT NULL DEFAULT 'ingested',
    error        TEXT,
    n_redactions INTEGER NOT NULL DEFAULT 0,
    episode_id   INTEGER,
    source_mtime REAL,
    ingested_at  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_rs_status ON real_sessions(status);

-- Task proposals mined from real usage (spec 05, Req 1.2). Mirrors the
-- `proposals` conventions above so the web layer treats both alike.
--
-- `fingerprint` is UNIQUE and that is the suppression mechanism (Req 2.3): a
-- cluster that was already proposed — approved, rejected, or still pending —
-- can never be proposed a second time, because the insert would collide. A
-- rejection that could be re-proposed next cycle is not a rejection, it is a
-- delay, and the operator would learn to ignore the feed.
CREATE TABLE IF NOT EXISTS task_proposals (
    id                 TEXT PRIMARY KEY,
    fingerprint        TEXT NOT NULL,
    status             TEXT NOT NULL DEFAULT 'pending',
    title              TEXT NOT NULL DEFAULT '',
    domain             TEXT NOT NULL DEFAULT '',
    task_type          TEXT NOT NULL DEFAULT 'real',
    draft_yaml         TEXT NOT NULL DEFAULT '',
    verify_draft       TEXT NOT NULL DEFAULT '',
    reset_draft        TEXT NOT NULL DEFAULT '',
    source_episode_ids TEXT NOT NULL DEFAULT '[]',
    occurrences        INTEGER NOT NULL DEFAULT 0,
    model_id           TEXT NOT NULL DEFAULT '',
    created_at         TEXT NOT NULL,
    resolved_at        TEXT,
    reason             TEXT
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_tp_fingerprint ON task_proposals(fingerprint);
CREATE INDEX IF NOT EXISTS idx_tp_status ON task_proposals(status);

-- Mining ledger (spec 05, design §1). `watermark` is the newest episode
-- `finished_at` the run actually considered, and a row is written ONLY on a
-- successful run — a vLLM outage mid-mining must leave the watermark where it
-- was so the next cycle re-reads the same episodes rather than skipping them.
CREATE TABLE IF NOT EXISTS mining_runs (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    watermark   TEXT    NOT NULL DEFAULT '',
    n_episodes  INTEGER NOT NULL DEFAULT 0,
    n_clusters  INTEGER NOT NULL DEFAULT 0,
    n_proposals INTEGER NOT NULL DEFAULT 0,
    model_id    TEXT    NOT NULL DEFAULT '',
    stats       TEXT    NOT NULL DEFAULT '{}',
    created_at  TEXT    NOT NULL
);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class EpisodeResult:
    """One task attempt. Mirrors the `episodes` table."""

    epoch: int
    task_id: str
    split: Split
    domain: str
    partial_credit: float = 0.0
    passed: bool = False
    tier: str = "mocked"
    # 'bench' for the frozen demo curriculum, 'user' for operator-defined tasks.
    # The headline curve filters on this, so it must be recorded per episode
    # rather than re-derived from a manifest that may have changed since.
    origin: str = "bench"
    steps: int = 0
    tokens_in: int = 0
    tokens_out: int = 0
    cost_usd: float = 0.0
    wiki_generation: int = 0
    injected_skills: list[str] = field(default_factory=list)
    model: str = ""
    # LLM-judge outcome. Only real-tier episodes carry one; None means unjudged
    # (mocked episodes always, real episodes when the judge was unreachable).
    judge_passed: bool | None = None
    judge_confidence: float | None = None
    error: str | None = None
    started_at: str = field(default_factory=_now)
    finished_at: str = field(default_factory=_now)
    trajectory_path: str = ""


class EpisodeStore:
    def __init__(self, db_path: Path, runs_dir: Path):
        self.db_path = db_path
        self.runs_dir = runs_dir
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.runs_dir.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        with self.connect() as con:
            # Columns BEFORE the script, not after. SCHEMA declares indexes over
            # columns that `_migrate` adds (`idx_ep_origin` over `origin`), and
            # on a database created before those columns existed the whole
            # script aborts at that index with "no such column" — taking every
            # later statement with it and leaving the CLI unable to open an
            # otherwise healthy store. Running the migration first means the
            # columns exist by the time the indexes are declared.
            self._migrate(con)
            con.executescript(SCHEMA)

    @staticmethod
    def _migrate(con: sqlite3.Connection) -> None:
        """Add columns that post-date the original schema.

        `CREATE TABLE IF NOT EXISTS` is a no-op on a database created before a
        column existed, so a store from an earlier run would keep working right
        up until the first INSERT naming the new column. Adding it here is the
        difference between a seamless upgrade and a crashed epoch.
        """
        exists = con.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='episodes'"
        ).fetchone()
        if not exists:
            return  # fresh database: SCHEMA below creates it with every column

        added = {r["name"] for r in con.execute("PRAGMA table_info(episodes)")}
        for column, ddl in (
            ("origin", "TEXT NOT NULL DEFAULT 'bench'"),
            ("judge_passed", "INTEGER"),
            ("judge_confidence", "REAL"),
        ):
            if column not in added:
                con.execute(f"ALTER TABLE episodes ADD COLUMN {column} {ddl}")

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        con = sqlite3.connect(self.db_path, timeout=30.0)
        con.row_factory = sqlite3.Row
        con.execute("PRAGMA journal_mode=WAL")
        con.execute("PRAGMA foreign_keys=ON")
        try:
            yield con
            con.commit()
        finally:
            con.close()

    # ---------------------------------------------------------------- raw layer

    def trajectory_path(self, epoch: int, split: str, task_id: str) -> Path:
        return self.runs_dir / f"epoch-{epoch}" / split / f"{task_id}.jsonl"

    def write_trajectory(
        self,
        *,
        epoch: int,
        split: str,
        task_id: str,
        metadata: dict[str, Any],
        messages: list[dict[str, Any]],
        assertions: list[dict[str, Any]],
        end_state: dict[str, Any] | None = None,
        path: Path | None = None,
    ) -> Path:
        """Persist the full uncompressed trace as one JSONL episode file.

        Line 0 is metadata, then one line per message, then assertion outcomes
        and (optionally) the final world state.

        `path` overrides the epoch/split layout. Real sessions use it because
        they are not attempts at a manifest task in an epoch directory — they
        live flat under `runs/real/` keyed by session id (spec 03) — but they
        must stay byte-compatible with this format so the distiller's slicing
        and the dashboard's trajectory viewer read them without a special case.
        """
        path = path or self.trajectory_path(epoch, split, task_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".jsonl.tmp")
        with tmp.open("w", encoding="utf-8") as fh:
            fh.write(json.dumps({"type": "metadata", **metadata}, default=str) + "\n")
            for i, msg in enumerate(messages):
                fh.write(
                    json.dumps({"type": "message", "index": i, **_plain(msg)}, default=str)
                    + "\n"
                )
            fh.write(
                json.dumps({"type": "assertions", "results": assertions}, default=str)
                + "\n"
            )
            if end_state is not None:
                fh.write(
                    json.dumps({"type": "end_state", "world": end_state}, default=str)
                    + "\n"
                )
        tmp.replace(path)  # atomic: a half-written file must never look complete
        return path

    def read_trajectory(self, path: Path) -> list[dict[str, Any]]:
        if not path.exists():
            return []
        with path.open(encoding="utf-8") as fh:
            return [json.loads(line) for line in fh if line.strip()]

    def read_messages(self, path: Path) -> list[dict[str, Any]]:
        return [r for r in self.read_trajectory(path) if r.get("type") == "message"]

    # -------------------------------------------------------------- index layer

    def record_episode(self, result: EpisodeResult) -> int:
        """Insert or update one episode. Returns its row id."""
        with self._lock, self.connect() as con:
            con.execute(
                """
                INSERT INTO episodes (epoch, task_id, split, domain, tier, origin,
                    passed, partial_credit, steps, tokens_in, tokens_out, cost_usd,
                    wiki_generation, injected_skills, model, judge_passed,
                    judge_confidence, error, started_at,
                    finished_at, trajectory_path)
                VALUES (:epoch, :task_id, :split, :domain, :tier, :origin,
                    :passed, :partial_credit, :steps, :tokens_in, :tokens_out, :cost_usd,
                    :wiki_generation, :injected_skills, :model, :judge_passed,
                    :judge_confidence, :error, :started_at,
                    :finished_at, :trajectory_path)
                ON CONFLICT(epoch, task_id, split) DO UPDATE SET
                    passed=excluded.passed, partial_credit=excluded.partial_credit,
                    origin=excluded.origin,
                    steps=excluded.steps, tokens_in=excluded.tokens_in,
                    tokens_out=excluded.tokens_out, cost_usd=excluded.cost_usd,
                    wiki_generation=excluded.wiki_generation,
                    injected_skills=excluded.injected_skills, error=excluded.error,
                    judge_passed=excluded.judge_passed,
                    judge_confidence=excluded.judge_confidence,
                    finished_at=excluded.finished_at,
                    trajectory_path=excluded.trajectory_path
                """,
                {
                    **asdict(result),
                    "passed": int(result.passed),
                    "judge_passed": (
                        None if result.judge_passed is None else int(result.judge_passed)
                    ),
                    "injected_skills": json.dumps(result.injected_skills),
                },
            )
            row = con.execute(
                "SELECT id FROM episodes WHERE epoch=? AND task_id=? AND split=?",
                (result.epoch, result.task_id, result.split),
            ).fetchone()
            return int(row["id"]) if row else 0

    def completed_task_ids(self, epoch: int, split: str) -> set[str]:
        """Resume support (Requirement 8.3): tasks already done this epoch.

        A row only counts as complete if its trajectory file is still on disk —
        otherwise the distiller would later select an episode it cannot read.
        """
        with self.connect() as con:
            rows = con.execute(
                "SELECT task_id, trajectory_path FROM episodes "
                "WHERE epoch=? AND split=? AND error IS NULL",
                (epoch, split),
            ).fetchall()
        return {r["task_id"] for r in rows if Path(r["trajectory_path"]).exists()}

    def recorded_generations(self, epoch: int, split: str) -> set[int]:
        """Wiki generations the existing episodes for this epoch were run under."""
        with self.connect() as con:
            rows = con.execute(
                "SELECT DISTINCT wiki_generation FROM episodes WHERE epoch=? AND split=?",
                (epoch, split),
            ).fetchall()
        return {r["wiki_generation"] for r in rows}

    def query_episodes(
        self,
        *,
        epoch: int | None = None,
        split: str | None = None,
        domain: str | None = None,
        passed: bool | None = None,
        wiki_generation: int | None = None,
        limit: int = 500,
    ) -> list[dict[str, Any]]:
        """Filterable episode query (Requirement 2.4)."""
        clauses: list[str] = []
        params: list[Any] = []
        for col, val in (
            ("epoch", epoch),
            ("split", split),
            ("domain", domain),
            ("wiki_generation", wiki_generation),
        ):
            if val is not None:
                clauses.append(f"{col}=?")
                params.append(val)
        if passed is not None:
            clauses.append("passed=?")
            params.append(int(passed))
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        params.append(limit)
        with self.connect() as con:
            rows = con.execute(
                f"SELECT * FROM episodes {where} ORDER BY epoch, task_id LIMIT ?",
                params,
            ).fetchall()
        return [dict(r) | {"injected_skills": json.loads(r["injected_skills"])} for r in rows]

    def epoch_curve(self, *, origin: str | None = "bench") -> list[dict[str, Any]]:
        """The headline metric: mean partial credit + pass rate per epoch/split.

        Defaults to the FROZEN BENCH SET (Requirement 3.2). The curriculum is
        mutable now, and a mean taken over a task set that grew between epochs
        is not a measurement of anything — adding three easy tasks at epoch 4
        would read exactly like the agent getting better. Pass `origin=None`
        for the full-curriculum series, which callers must annotate with
        `curriculum_events` before showing it beside the headline.
        """
        clause = "WHERE tier='mocked'"
        params: list[Any] = []
        if origin is not None:
            clause += " AND origin=?"
            params.append(origin)
        with self.connect() as con:
            rows = con.execute(
                f"""
                SELECT epoch, split,
                       COUNT(*)               AS n,
                       AVG(partial_credit)    AS mean_partial_credit,
                       AVG(CAST(passed AS REAL)) AS pass_rate,
                       SUM(cost_usd)          AS cost_usd,
                       SUM(tokens_in+tokens_out) AS tokens
                FROM episodes
                {clause}
                GROUP BY epoch, split
                ORDER BY epoch, split
                """,
                params,
            ).fetchall()
        return [dict(r) for r in rows]

    # ------------------------------------------------------- curriculum changes

    def count_episodes_for_task(self, task_id: str) -> int:
        """Does this task own history? Decides retire-vs-delete (Req 2.3)."""
        with self.connect() as con:
            row = con.execute(
                "SELECT COUNT(*) AS n FROM episodes WHERE task_id=?", (task_id,)
            ).fetchone()
        return int(row["n"])

    def last_epoch(self) -> int | None:
        with self.connect() as con:
            row = con.execute("SELECT MAX(epoch) AS e FROM episodes").fetchone()
        return None if row["e"] is None else int(row["e"])

    def record_curriculum_event(
        self,
        *,
        action: str,
        task_id: str,
        split: str = "train",
        task_type: str = "bench",
        detail: str = "",
    ) -> None:
        """Stamp a task-set change against the epoch it happened after."""
        with self.connect() as con:
            con.execute(
                "INSERT INTO curriculum_events (ts, action, task_id, split,"
                " task_type, epoch, detail) VALUES (?,?,?,?,?,?,?)",
                (_now(), action, task_id, split, task_type, self.last_epoch(), detail),
            )

    def curriculum_events(self) -> list[dict[str, Any]]:
        with self.connect() as con:
            rows = con.execute(
                "SELECT * FROM curriculum_events ORDER BY id"
            ).fetchall()
        return [dict(r) for r in rows]

    # ------------------------------------------------- real sessions (spec 03)

    def real_session_ledger(self) -> dict[str, dict[str, Any]]:
        """Every session ingestion has already seen, keyed by session id."""
        with self.connect() as con:
            rows = con.execute("SELECT * FROM real_sessions").fetchall()
        return {r["session_id"]: dict(r) for r in rows}

    def record_real_session(
        self,
        *,
        session_id: str,
        path: str,
        status: str = "ingested",
        error: str | None = None,
        n_redactions: int = 0,
        episode_id: int | None = None,
        source_mtime: float | None = None,
    ) -> None:
        with self._lock, self.connect() as con:
            con.execute(
                """
                INSERT INTO real_sessions (session_id, path, status, error,
                    n_redactions, episode_id, source_mtime, ingested_at)
                VALUES (?,?,?,?,?,?,?,?)
                ON CONFLICT(session_id) DO UPDATE SET
                    path=excluded.path, status=excluded.status,
                    error=excluded.error, n_redactions=excluded.n_redactions,
                    episode_id=excluded.episode_id,
                    source_mtime=excluded.source_mtime,
                    ingested_at=excluded.ingested_at
                """,
                (session_id, path, status, error, n_redactions, episode_id,
                 source_mtime, _now()),
            )

    def last_distill_started_at(self) -> str | None:
        """When distillation last ran — the clock the train nudge counts from."""
        with self.connect() as con:
            row = con.execute(
                "SELECT created_at FROM distill_runs ORDER BY id DESC LIMIT 1"
            ).fetchone()
        return row["created_at"] if row else None

    def new_real_episodes_since_distill(self) -> int:
        """Real episodes recorded since the last distill run (Req 4.1).

        Counts unjudged episodes too: a session the judge could not label is
        still evidence the agent was used, and suppressing the nudge because the
        vLLM endpoint was down would hide exactly the backlog worth training on.
        """
        since = self.last_distill_started_at()
        with self.connect() as con:
            if since is None:
                row = con.execute(
                    "SELECT COUNT(*) AS n FROM episodes WHERE tier='real'"
                ).fetchone()
            else:
                row = con.execute(
                    "SELECT COUNT(*) AS n FROM episodes"
                    " WHERE tier='real' AND finished_at > ?",
                    (since,),
                ).fetchone()
        return int(row["n"])

    # Both cost meters below measure THE EXPERIMENT, so they filter to the
    # mocked tier. They gate the runner's budget caps, and a real Helixis Claw
    # session is stamped with the current epoch — so without this filter a long
    # afternoon of chatting with the agent would abort the next training epoch
    # for "budget exceeded" against spend the experiment never made. The
    # dashboard's spend tile sums episodes unfiltered and remains the true
    # all-in figure.

    def total_cost(self) -> float:
        with self.connect() as con:
            row = con.execute(
                "SELECT COALESCE(SUM(cost_usd),0) AS c FROM episodes WHERE tier='mocked'"
            ).fetchone()
        return float(row["c"])

    def epoch_cost(self, epoch: int) -> float:
        with self.connect() as con:
            row = con.execute(
                "SELECT COALESCE(SUM(cost_usd),0) AS c FROM episodes"
                " WHERE epoch=? AND tier='mocked'",
                (epoch,),
            ).fetchone()
        return float(row["c"])

    def real_cost(self) -> float:
        """Agent-tier spend from real sessions, reported separately."""
        with self.connect() as con:
            row = con.execute(
                "SELECT COALESCE(SUM(cost_usd),0) AS c FROM episodes WHERE tier='real'"
            ).fetchone()
        return float(row["c"])

    # ----------------------------------------------------------------- lifecycle

    def start_epoch(self, epoch: int, split: str, n_tasks: int, generation: int) -> None:
        with self.connect() as con:
            con.execute(
                """
                INSERT INTO epochs (epoch, split, status, n_tasks, wiki_generation, started_at)
                VALUES (?,?,'running',?,?,?)
                ON CONFLICT(epoch, split) DO UPDATE SET
                    status='running', n_tasks=excluded.n_tasks,
                    wiki_generation=excluded.wiki_generation, started_at=excluded.started_at
                """,
                (epoch, split, n_tasks, generation, _now()),
            )

    def finish_epoch(self, epoch: int, split: str, status: str = "done") -> None:
        with self.connect() as con:
            con.execute(
                """
                UPDATE epochs SET status=?, finished_at=?,
                    n_done=(SELECT COUNT(*) FROM episodes e WHERE e.epoch=epochs.epoch AND e.split=epochs.split),
                    cost_usd=(SELECT COALESCE(SUM(cost_usd),0) FROM episodes e WHERE e.epoch=epochs.epoch AND e.split=epochs.split)
                WHERE epoch=? AND split=?
                """,
                (status, _now(), epoch, split),
            )

    def record_distill_run(
        self,
        *,
        epoch: int,
        generation: int,
        n_failures: int,
        n_skills: int,
        gated_out: bool,
        stats: dict[str, Any],
    ) -> None:
        with self.connect() as con:
            con.execute(
                "INSERT INTO distill_runs (epoch, generation, n_failures, n_skills,"
                " gated_out, stats, created_at) VALUES (?,?,?,?,?,?,?)",
                (epoch, generation, n_failures, n_skills, int(gated_out),
                 json.dumps(stats), _now()),
            )

    def register_skill(
        self,
        *,
        name: str,
        description: str,
        category: str,
        generation: int,
        created_epoch: int,
        source_episodes: list[str],
        path: str,
    ) -> None:
        with self.connect() as con:
            con.execute(
                "INSERT OR REPLACE INTO skills (name, description, category, generation,"
                " created_epoch, source_episodes, path, created_at) VALUES (?,?,?,?,?,?,?,?)",
                (name, description, category, generation, created_epoch,
                 json.dumps(source_episodes), path, _now()),
            )

    # ------------------------------------------------------------ policy events

    def record_policy_event(self, event: dict[str, Any]) -> bool:
        """Insert a denial/config event. Returns False if already seen."""
        with self.connect() as con:
            cur = con.execute(
                "INSERT OR IGNORE INTO policy_events (ts, kind, severity, action, actor,"
                " dst_host, dst_port, reason, is_honeypot, raw, fingerprint)"
                " VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (
                    event.get("ts", _now()),
                    event.get("kind", "NET:OPEN"),
                    event.get("severity", "MED"),
                    event.get("action", "DENIED"),
                    event.get("actor", ""),
                    event.get("dst_host", ""),
                    event.get("dst_port"),
                    event.get("reason", ""),
                    int(event.get("is_honeypot", False)),
                    json.dumps(event.get("raw", {})),
                    event.get("fingerprint"),
                ),
            )
            return cur.rowcount > 0

    # ------------------------------------------------- task proposals (spec 05)

    def mining_watermark(self) -> str | None:
        """The newest episode timestamp the last successful mining run covered.

        None means mining has never run, in which case every real episode is
        fair game. Read from the last ledger row rather than tracked in memory
        so a crashed run simply re-reads its input next time.
        """
        with self.connect() as con:
            row = con.execute(
                "SELECT watermark FROM mining_runs ORDER BY id DESC LIMIT 1"
            ).fetchone()
        return (row["watermark"] or None) if row else None

    def record_mining_run(
        self,
        *,
        watermark: str,
        n_episodes: int,
        n_clusters: int,
        n_proposals: int,
        model_id: str = "",
        stats: dict[str, Any] | None = None,
    ) -> None:
        with self.connect() as con:
            con.execute(
                "INSERT INTO mining_runs (watermark, n_episodes, n_clusters,"
                " n_proposals, model_id, stats, created_at) VALUES (?,?,?,?,?,?,?)",
                (watermark, n_episodes, n_clusters, n_proposals, model_id,
                 json.dumps(stats or {}), _now()),
            )

    def real_episodes_since(
        self, since: str | None = None, *, limit: int = 500
    ) -> list[dict[str, Any]]:
        """Real episodes newer than the watermark, oldest first (Req 1.1).

        Ordered oldest-first so a `limit` truncation drops the NEWEST episodes,
        which the next run will pick up — truncating the oldest would strand
        them behind a watermark that has already moved past.
        """
        clauses = ["tier='real'"]
        params: list[Any] = []
        if since:
            clauses.append("finished_at > ?")
            params.append(since)
        params.append(limit)
        with self.connect() as con:
            rows = con.execute(
                f"SELECT * FROM episodes WHERE {' AND '.join(clauses)}"
                f" ORDER BY finished_at, id LIMIT ?",
                params,
            ).fetchall()
        return [
            dict(r) | {"injected_skills": json.loads(r["injected_skills"])} for r in rows
        ]

    def task_proposals(self, status: str | None = None) -> list[dict[str, Any]]:
        clause = "WHERE status=?" if status else ""
        params = [status] if status else []
        with self.connect() as con:
            rows = con.execute(
                f"SELECT * FROM task_proposals {clause} ORDER BY created_at DESC",
                params,
            ).fetchall()
        return [
            dict(r) | {"source_episode_ids": json.loads(r["source_episode_ids"])}
            for r in rows
        ]

    def get_task_proposal(self, proposal_id: str) -> dict[str, Any] | None:
        with self.connect() as con:
            row = con.execute(
                "SELECT * FROM task_proposals WHERE id=?", (proposal_id,)
            ).fetchone()
        if row is None:
            return None
        return dict(row) | {"source_episode_ids": json.loads(row["source_episode_ids"])}

    def proposal_fingerprints(self) -> set[str]:
        """Every fingerprint ever proposed, in ANY status (Req 1.3)."""
        with self.connect() as con:
            rows = con.execute("SELECT fingerprint FROM task_proposals").fetchall()
        return {r["fingerprint"] for r in rows}

    def insert_task_proposal(self, proposal: dict[str, Any]) -> bool:
        """Store one pending proposal. False if its fingerprint is already known.

        INSERT OR IGNORE rather than upsert: a proposal the operator has already
        decided on must never be silently reopened by a later mining run.
        """
        with self._lock, self.connect() as con:
            cur = con.execute(
                """
                INSERT OR IGNORE INTO task_proposals (id, fingerprint, status, title,
                    domain, task_type, draft_yaml, verify_draft, reset_draft,
                    source_episode_ids, occurrences, model_id, created_at)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    proposal["id"],
                    proposal["fingerprint"],
                    proposal.get("status", "pending"),
                    proposal.get("title", ""),
                    proposal.get("domain", ""),
                    proposal.get("task_type", "real"),
                    proposal.get("draft_yaml", ""),
                    proposal.get("verify_draft", ""),
                    proposal.get("reset_draft", ""),
                    json.dumps(proposal.get("source_episode_ids", [])),
                    int(proposal.get("occurrences", 0)),
                    proposal.get("model_id", ""),
                    proposal.get("created_at", _now()),
                ),
            )
            return cur.rowcount > 0

    def resolve_task_proposal(
        self, proposal_id: str, status: str, reason: str | None = None
    ) -> None:
        """Move a proposal out of `pending`.

        `resolved_at` is cleared when a proposal returns to `pending` (Req 2.2:
        a failed approval re-pends with the validator error attached), so the
        timestamp always means "when this was decided" and never lingers from a
        decision that did not stick.
        """
        with self._lock, self.connect() as con:
            con.execute(
                "UPDATE task_proposals SET status=?, reason=?, resolved_at=?"
                " WHERE id=?",
                (status, reason, None if status == "pending" else _now(), proposal_id),
            )

    def upsert_proposal(self, proposal: dict[str, Any]) -> None:
        with self.connect() as con:
            con.execute(
                """
                INSERT INTO proposals (chunk_id, rule_name, intent_summary, status,
                    prover_findings, requires_human, rejection_reason, created_at, decided_at)
                VALUES (?,?,?,?,?,?,?,?,?)
                ON CONFLICT(chunk_id) DO UPDATE SET
                    status=excluded.status,
                    prover_findings=excluded.prover_findings,
                    requires_human=excluded.requires_human,
                    rejection_reason=excluded.rejection_reason,
                    decided_at=excluded.decided_at
                """,
                (
                    proposal["chunk_id"],
                    proposal.get("rule_name", ""),
                    proposal.get("intent_summary", ""),
                    proposal.get("status", "pending"),
                    json.dumps(proposal.get("prover_findings", [])),
                    int(proposal.get("requires_human", True)),
                    proposal.get("rejection_reason"),
                    proposal.get("created_at", _now()),
                    proposal.get("decided_at"),
                ),
            )


def _plain(msg: Any) -> dict[str, Any]:
    """Messages may arrive as pydantic objects from verifiers; normalize to dict."""
    if hasattr(msg, "model_dump"):
        return msg.model_dump()
    if isinstance(msg, dict):
        return msg
    return {"role": "unknown", "content": str(msg)}
