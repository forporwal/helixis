"""Central configuration, loaded from environment with sane offline defaults.

Everything provider-specific lives here. The agent tier and the distiller tier are
configured independently so they can point at different OpenAI-compatible
endpoints (e.g. Featherless/Fireworks for the agent, vLLM-on-RunPod for the
distiller) without either being hardcoded.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from dotenv import load_dotenv

load_dotenv()

# Repo layout: <root>/app/engine/helixis/config.py -> <root>
ROOT = Path(__file__).resolve().parents[3]


def _env(key: str, default: str = "") -> str:
    return os.environ.get(key, default).strip()


def _env_int(key: str, default: int) -> int:
    raw = _env(key)
    return int(raw) if raw else default


def _env_float(key: str, default: float) -> float:
    raw = _env(key)
    return float(raw) if raw else default


def _env_bool(key: str, default: bool) -> bool:
    raw = _env(key).lower()
    if not raw:
        return default
    return raw not in ("0", "false", "no")


@dataclass(frozen=True)
class ModelTier:
    """One OpenAI-compatible endpoint."""

    model: str
    base_url: str
    api_key_var: str
    # Blended pricing per 1M tokens; used for the cost meter.
    input_cost_per_m: float = 0.0
    output_cost_per_m: float = 0.0
    max_concurrency: int = 8

    @property
    def api_key(self) -> str:
        return os.environ.get(self.api_key_var, "")

    @property
    def is_fake(self) -> bool:
        """Offline mode: no endpoint configured, use the deterministic stub."""
        return self.base_url == FAKE_BASE_URL

    def cost(self, tokens_in: int, tokens_out: int) -> float:
        return (
            tokens_in * self.input_cost_per_m + tokens_out * self.output_cost_per_m
        ) / 1_000_000


FAKE_BASE_URL = "fake://offline"


def _env_path(key: str, default: Path) -> Path:
    raw = _env(key)
    return Path(raw).expanduser().resolve() if raw else default


@dataclass(frozen=True)
class Paths:
    """Where experiment state lives.

    Overridable so a demo dataset can sit beside a real one rather than
    overwriting it — the episode store keys on (epoch, task_id, split), so two
    experiments sharing a directory would silently merge into one curve.
    `HELIXIS_DB` matches the variable the dashboard already reads.
    """

    root: Path = ROOT
    runs: Path = field(default_factory=lambda: _env_path("HELIXIS_RUNS_DIR", ROOT / "runs"))
    wiki: Path = field(default_factory=lambda: _env_path("HELIXIS_WIKI_DIR", ROOT / "wiki"))
    db: Path = field(
        default_factory=lambda: _env_path(
            "HELIXIS_DB", _env_path("HELIXIS_RUNS_DIR", ROOT / "runs") / "helixis.db"
        )
    )
    manifest: Path = field(default_factory=lambda: ROOT / "app" / "engine" / "tasks.yaml")
    # The operator's own tasks. Git-ignored and the ONLY manifest tooling
    # writes — `manifest` above stays frozen so the headline curve keeps a
    # stable measuring stick (spec 04, Requirement 1.1).
    user_manifest: Path = field(
        default_factory=lambda: _env_path(
            "HELIXIS_USER_MANIFEST", ROOT / "app" / "engine" / "tasks.user.yaml"
        )
    )
    policy: Path = field(default_factory=lambda: ROOT / "policy")
    real_tier: Path = field(default_factory=lambda: ROOT / "app" / "real_tier")
    # Where the nemoclaw service's OpenClaw session files land on the host
    # (spec 03, Req 1.1). Under `runs/` because it is captured experiment
    # state, but it is written by the agent and only ever READ by ingestion.
    claw_sessions: Path = field(
        default_factory=lambda: _env_path(
            "HELIXIS_CLAW_SESSIONS_DIR",
            _env_path("HELIXIS_RUNS_DIR", ROOT / "runs") / "claw-sessions",
        )
    )

    def ensure(self) -> None:
        for p in (self.runs, self.wiki, self.wiki / "skills", self.wiki / "pages"):
            p.mkdir(parents=True, exist_ok=True)


@dataclass(frozen=True)
class Settings:
    agent: ModelTier
    distiller: ModelTier
    paths: Paths

    # Runner
    max_steps: int = 50
    task_timeout_s: int = 900
    max_concurrent_tasks: int = 4
    toolset: str = "api"

    # Wiki / retrieval
    top_k_skills: int = 4
    retrieval_mode: Literal["embedding", "keyword"] = "keyword"
    embedding_model: str = "sentence-transformers/all-MiniLM-L6-v2"

    # Distillation gating (Requirement 3.1)
    distill_success_threshold: float = 0.6
    distill_min_failures: int = 5
    max_new_skills: int = 3
    max_failures_per_distill: int = 6

    # Budget (Requirement 8.1)
    epoch_cost_cap_usd: float = 8.0
    total_cost_cap_usd: float = 150.0

    # Real-transcript ingestion and training cadence (spec 03, Req 4).
    #
    # Training is not an everyday activity: it fires when enough new real
    # trajectories have piled up to be worth learning from. Below the threshold
    # Helixis says nothing. At it, the home feed shows a nudge — and only runs
    # by itself if `auto_train` was deliberately turned on, which is why the
    # default is False (Req 4.3: nothing runs without the operator's click).
    real_train_threshold: int = 10
    auto_train: bool = False
    # Judge labels below this confidence are evidence of usage but not evidence
    # worth teaching from, so distillation skips them (Req 3.3).
    judge_min_confidence: float = 0.6
    # How long a session file must sit untouched before ingestion treats it as
    # finished, when sessions.json still lists it as active (crashed gateway).
    claw_quiescent_after_s: float = 300.0

    # Task mining (spec 05). The miner PROPOSES; nothing here can enact.
    #
    # The caps are the anti-spam story (Req 3.3): at most `max_proposals_per_run`
    # drafts reach the feed per run, and a workflow must have been seen
    # `mine_min_occurrences` times before it counts as recurring at all. One-off
    # work is not a curriculum, it is a Tuesday.
    max_proposals_per_run: int = 3
    mine_min_occurrences: int = 2
    # Cosine similarity over stemmed token sets, above which two workflows are
    # "the same thing". Used for clustering, for dedup against the merged
    # manifest, and for dedup against prior proposals.
    mine_similarity_threshold: float = 0.45
    # How many real episodes one run will summarize. Bounds the stage-1 burst.
    mine_max_episodes: int = 60
    mine_on_train_cycle: bool = True

    # Containment
    openshell_bin: str = "openshell"
    sandbox_name: str = "helixis"
    ocsf_log_dir: Path = field(default_factory=lambda: ROOT / "runs" / "ocsf")

    @property
    def demo_mode(self) -> bool:
        return _env_bool("HELIXIS_DEMO_MODE", True)


def load_settings() -> Settings:
    agent = ModelTier(
        model=_env("HELIXIS_AGENT_MODEL", "nvidia/nemotron-4-340b-instruct"),
        base_url=_env("HELIXIS_AGENT_BASE_URL", FAKE_BASE_URL),
        api_key_var=_env("HELIXIS_AGENT_API_KEY_VAR", "HELIXIS_AGENT_API_KEY"),
        input_cost_per_m=_env_float("HELIXIS_AGENT_INPUT_COST_PER_M", 1.0),
        output_cost_per_m=_env_float("HELIXIS_AGENT_OUTPUT_COST_PER_M", 3.0),
        max_concurrency=_env_int("HELIXIS_AGENT_CONCURRENCY", 4),
    )
    distiller = ModelTier(
        model=_env("HELIXIS_DISTILLER_MODEL", "nvidia/nemotron-nano-9b-v2"),
        base_url=_env("HELIXIS_DISTILLER_BASE_URL", FAKE_BASE_URL),
        api_key_var=_env("HELIXIS_DISTILLER_API_KEY_VAR", "HELIXIS_DISTILLER_API_KEY"),
        # vLLM on our own GPU: token cost is sunk into the RunPod hourly rate.
        input_cost_per_m=_env_float("HELIXIS_DISTILLER_INPUT_COST_PER_M", 0.0),
        output_cost_per_m=_env_float("HELIXIS_DISTILLER_OUTPUT_COST_PER_M", 0.0),
        max_concurrency=_env_int("HELIXIS_DISTILLER_CONCURRENCY", 16),
    )
    paths = Paths()
    return Settings(
        agent=agent,
        distiller=distiller,
        paths=paths,
        max_steps=_env_int("HELIXIS_MAX_STEPS", 50),
        task_timeout_s=_env_int("HELIXIS_TASK_TIMEOUT_S", 900),
        max_concurrent_tasks=_env_int("HELIXIS_MAX_CONCURRENT_TASKS", 4),
        toolset=_env("HELIXIS_TOOLSET", "api"),
        top_k_skills=_env_int("HELIXIS_TOP_K_SKILLS", 4),
        retrieval_mode=_env("HELIXIS_RETRIEVAL_MODE", "keyword"),  # type: ignore[arg-type]
        epoch_cost_cap_usd=_env_float("HELIXIS_EPOCH_COST_CAP_USD", 8.0),
        total_cost_cap_usd=_env_float("HELIXIS_TOTAL_COST_CAP_USD", 150.0),
        openshell_bin=_env("HELIXIS_OPENSHELL_BIN", "openshell"),
        sandbox_name=_env("HELIXIS_SANDBOX_NAME", "helixis"),
        real_train_threshold=_env_int("HELIXIS_REAL_TRAIN_THRESHOLD", 10),
        auto_train=_env_bool("HELIXIS_AUTO_TRAIN", False),
        judge_min_confidence=_env_float("HELIXIS_JUDGE_MIN_CONFIDENCE", 0.6),
        claw_quiescent_after_s=_env_float("HELIXIS_CLAW_QUIESCENT_AFTER_S", 300.0),
        max_proposals_per_run=_env_int("HELIXIS_MAX_PROPOSALS_PER_RUN", 3),
        mine_min_occurrences=_env_int("HELIXIS_MINE_MIN_OCCURRENCES", 2),
        mine_similarity_threshold=_env_float("HELIXIS_MINE_SIMILARITY", 0.45),
        mine_max_episodes=_env_int("HELIXIS_MINE_MAX_EPISODES", 60),
        mine_on_train_cycle=_env_bool("HELIXIS_MINE_ON_TRAIN_CYCLE", True),
    )


SETTINGS = load_settings()
