export type Split = "train" | "heldout" | "real";

export type CurvePoint = {
  epoch: number;
  n: number;
  meanPartialCredit: number;
  passRate: number;
  costUsd: number;
  tokens: number;
};

export type CurveDelta = {
  split: Split;
  firstEpoch: number;
  lastEpoch: number;
  partialCreditFrom: number;
  partialCreditTo: number;
  partialCreditDelta: number;
  passRateFrom: number;
  passRateTo: number;
  passRateDelta: number;
};

/** A change to the active task set, so a curve can never silently compare two. */
export type CurriculumEvent = {
  /** Last epoch recorded when the change was made; null if it predates any run. */
  epoch: number | null;
  ts: string;
  action: string;
  taskId: string;
  split: string;
  taskType: string;
};

export type CurveResponse = {
  /** Headline: the FROZEN bench set only. Comparable across every epoch. */
  series: { split: Split; points: CurvePoint[] }[];
  deltas: CurveDelta[];
  /**
   * Secondary series including user `bench`-type tasks. Only meaningful when
   * read next to `curriculumEvents` — the task set behind it can change
   * between epochs, which is exactly what makes it not the headline.
   */
  fullSeries: { split: Split; points: CurvePoint[] }[];
  curriculumEvents: CurriculumEvent[];
  /** Episodes excluded from the headline curve because tier != 'mocked'. */
  excludedRealEpisodes: number;
  /** Episodes excluded from the headline curve because origin = 'user'. */
  excludedUserEpisodes: number;
  epochs: number[];
  provenance: ProvenanceInfo;
  empty: boolean;
};

export type ManifestTask = {
  id: string;
  domain: string;
  split: "train" | "heldout";
  type: "bench" | "real";
  origin: "bench" | "user";
  prompt: string;
  benchRef: string;
  verify: string;
  reset: string;
  retired: boolean;
  /**
   * An approved mined task whose verify.py/reset.py are still LLM drafts
   * (spec 05, Req 2.4). Excluded from every run until a human completes them —
   * badged rather than hidden, because a task silently sitting out is worse
   * than one visibly waiting on you.
   */
  draft: boolean;
  /** "miner" when the task miner drafted it; "" when hand-added (Req 3.3). */
  source: string;
  addedAt: string;
};

/** One mined task proposal awaiting, or past, an operator decision. */
export type TaskProposal = {
  id: string;
  fingerprint: string;
  status: "pending" | "approved" | "rejected" | "invalid";
  title: string;
  domain: string;
  taskType: string;
  draftYaml: string;
  verifyDraft: string;
  resetDraft: string;
  sourceEpisodeIds: number[];
  occurrences: number;
  modelId: string;
  createdAt: string;
  resolvedAt: string | null;
  reason: string | null;
};

/** Provenance: the real sessions a proposal was mined from (Req 2.1). */
export type ProposalEpisode = {
  id: number;
  epoch: number;
  split: Split;
  taskId: string;
  finishedAt: string;
  href: string;
};

export type TaskProposalsResponse = {
  proposals: TaskProposal[];
  counts: { pending: number; total: number };
  empty: boolean;
};

export type TaskProposalDetailResponse = {
  proposal: TaskProposal | null;
  episodes: ProposalEpisode[];
  found: boolean;
};

export type ManifestResponse = {
  tasks: ManifestTask[];
  warnings: { taskId: string; message: string; fatal: boolean }[];
  /** False when the engine CLI is unreachable — an empty state, not an error. */
  available: boolean;
  error: string | null;
  empty: boolean;
};

export type ProvenanceInfo = {
  simulated: boolean;
  allSimulated: boolean;
  inspected: number;
  simulatedCount: number;
  sources: string[];
};

export type TaskCell = {
  epoch: number;
  taskId: string;
  split: Split;
  domain: string;
  tier: string;
  status: "pass" | "fail" | "error" | "missing";
  partialCredit: number;
  steps: number;
  costUsd: number;
  tokensIn: number;
  tokensOut: number;
  model: string;
  wikiGeneration: number;
  injectedSkills: string[];
  error: string | null;
  /** 'user' tasks are badged in the grid and kept out of the headline curve. */
  origin: string;
};

export type TasksResponse = {
  epochs: number[];
  tasks: { taskId: string; split: Split; domain: string; origin: string }[];
  cells: TaskCell[];
  empty: boolean;
};

export type SkillItem = {
  name: string;
  description: string;
  category: string;
  generation: number;
  createdEpoch: number;
  /**
   * What kind of evidence taught this skill: 'mocked', 'real', or 'mocked+real'
   * (spec 03, Req 3.2). Read from SKILL.md frontmatter, so a skill written
   * before real ingestion existed reports 'mocked' rather than guessing.
   */
  sourceTier: string;
  sourceEpisodes: string[];
  sourceLinks: { label: string; epoch: number | null; taskId: string }[];
  path: string;
  createdAt: string;
  body: string;
  bodyAvailable: boolean;
};

export type SkillsResponse = {
  skills: SkillItem[];
  growth: { epoch: number; generation: number; nSkills: number; cumulative: number; nFailures: number; gatedOut: boolean }[];
  generation: number;
  empty: boolean;
};

export type PolicyEvent = {
  id: number;
  ts: string;
  kind: string;
  severity: string;
  action: string;
  actor: string;
  dstHost: string;
  dstPort: number | null;
  reason: string;
  isHoneypot: boolean;
};

export type Proposal = {
  chunkId: string;
  ruleName: string;
  intentSummary: string;
  status: string;
  proverFindings: unknown[];
  requiresHuman: boolean;
  rejectionReason: string | null;
  createdAt: string;
  decidedAt: string | null;
};

export type PolicyResponse = {
  events: PolicyEvent[];
  proposals: Proposal[];
  counts: { denials: number; honeypot: number; pending: number };
  empty: boolean;
};

/** The three things a user can actually ask the Lab to do. */
export type TrainingMode = "simulated" | "benchmark" | "real";

export type ModeReadiness = {
  available: boolean;
  /** Why this mode cannot run. Non-empty means the button is disabled. */
  blockers: string[];
  /** Runnable, but the operator should know this first. */
  warnings: string[];
  estimatedCostUsd: number | null;
};

/**
 * The engine's own answer to "what would Start do right now" (`helixis
 * preflight --json`). Mirrors `_preflight()` in cli.py field for field.
 */
export type Preflight = {
  /** Which backend `helixis epoch` would actually select this instant. */
  activeMode: Extract<TrainingMode, "simulated" | "benchmark">;
  modes: Record<TrainingMode, ModeReadiness>;
  agent: {
    model: string;
    baseUrl: string;
    configured: boolean;
    automationbench: boolean;
  };
  distiller: { model: string; baseUrl: string; configured: boolean };
  real: {
    sessionsDir: string;
    pendingSessions: number;
    totalSessions: number;
    ingestedSessions: number;
    newRealEpisodes: number;
    threshold: number;
    autoTrain: boolean;
  };
  budget: {
    epochCapUsd: number;
    totalCapUsd: number;
    totalSpentUsd: number;
    epochSpentUsd: number;
  };
  tasks: { train: number; heldout: number; draftExcluded: number };
  lastEpoch: number | null;
  nextEpoch: number;
};

/**
 * What the real-trajectory loop produced. Reported separately from the curve
 * because real-tier episodes are excluded from the headline by design — this is
 * where a training cycle's result actually shows up.
 */
export type RealTrainingResponse = {
  dbPresent: boolean;
  sessions: { ingested: number; quarantined: number; failed: number };
  episodes: {
    total: number;
    helpful: number;
    unhelpful: number;
    /** Judge endpoint was unreachable at ingest time — evidence, not a label. */
    unlabeled: number;
    meanConfidence: number | null;
  };
  skills: {
    fromReal: number;
    total: number;
    /** SKILL.md files we could not open — kept distinct from "not from real". */
    unreadable: number;
    names: string[];
  };
  proposals: { pending: number; approved: number; rejected: number };
  readiness: StatusResponse["trainReadiness"];
  lastCycle: { ts: string; generation: number; skills: string[] } | null;
  empty: boolean;
};

export type PreflightResponse = {
  available: boolean;
  error: string | null;
  preflight: Preflight | null;
};

export type StatusResponse = {
  dbPresent: boolean;
  running: boolean;
  currentEpoch: number | null;
  epochs: {
    epoch: number;
    split: Split;
    status: string;
    nTasks: number;
    nDone: number;
    costUsd: number;
    wikiGeneration: number;
    startedAt: string | null;
    finishedAt: string | null;
  }[];
  cost: {
    total: number;
    totalCap: number;
    epochCost: number;
    epochCap: number;
  };
  tokens: { totalIn: number; totalOut: number };
  wikiGeneration: number;
  skillCount: number;
  episodeCount: number;
  provenance: ProvenanceInfo;
  controls: { helixisAvailable: boolean; openshellAvailable: boolean };
  /**
   * The agent the product ships, as home needs to describe it: is its gateway
   * answering, and which generation of distilled wiki is it running with.
   * Generation and skill count are duplicated from the top-level fields on
   * purpose — the launch card states them as a property of *the agent*, and
   * spec 01 extends this block rather than the flat status body.
   */
  /**
   * `uiUrl` carries the gateway token in its fragment and is built server-side
   * per request (see lib/claw.ts) — the client cannot construct it, because the
   * token is deliberately not a `NEXT_PUBLIC_` build-time value.
   */
  claw: {
    gatewayUp: boolean;
    wikiGeneration: number;
    skillCount: number;
    uiUrl: string;
    tuiUrl: string;
  };
  /**
   * Is it worth training yet (spec 03, Req 4.1)? Training is not a daily
   * activity — it earns its cost once enough real trajectories have piled up.
   * `newRealEpisodes` counts real episodes recorded since the last distillation,
   * including unjudged ones: a session the judge could not label is still
   * evidence the agent was used.
   */
  trainReadiness: {
    newRealEpisodes: number;
    threshold: number;
    autoTrain: boolean;
    ready: boolean;
    totalRealEpisodes: number;
    lastDistillAt: string | null;
  };
  empty: boolean;
};

/**
 * One thing that wants a human, in the feed on home.
 *
 * A discriminated union rather than a bag of optional fields: later specs add
 * `train-nudge` (03) and `task-proposal` (05) members without any existing
 * renderer changing, and an unhandled kind is a type error rather than a blank
 * row. `href` is on every member so a row always has somewhere fuller to go.
 */
export type ActionItem =
  | {
      kind: "policy-proposal";
      id: string;
      href: string;
      needsHuman: boolean;
      createdAt: string;
      proposal: Proposal;
    }
  | {
      kind: "train-nudge";
      id: string;
      href: string;
      needsHuman: false;
      createdAt: string;
      newRealEpisodes: number;
      threshold: number;
      autoTrain: boolean;
    }
  | {
      kind: "task-proposal";
      id: string;
      href: string;
      needsHuman: boolean;
      createdAt: string;
      title: string;
      domain: string;
      /** The proposed task id — also the key the approve/reject route takes. */
      taskId: string;
      /** How many real sessions this workflow was seen in (spec 05, Req 1.1). */
      occurrences: number;
      taskType: string;
    }
  | {
      /**
       * Purely informational, and the only member that reports something that
       * already happened rather than something to do. It exists because a loop
       * the user cannot see close does not read as a loop: this is the row that
       * says the failures they hit yesterday became skills the agent has today
       * (spec 03, Req 4.4).
       */
      kind: "skills-live";
      id: string;
      href: string;
      needsHuman: false;
      createdAt: string;
      skills: string[];
      generation: number;
    };

export type ActionsResponse = {
  items: ActionItem[];
  counts: { needsHuman: number; total: number };
  dbPresent: boolean;
  empty: boolean;
};

/** One agent turn (or system/user/tool record) from a trajectory JSONL. */
export type TrajectoryMessage = {
  index: number;
  role: "system" | "user" | "assistant" | "tool";
  content: string;
  truncated: boolean;
  reasoning: string | null;
  toolCallId: string | null;
  toolCalls: { id: string; name: string; arguments: string }[];
};

export type TrajectoryAssertion = {
  type: string;
  passed: boolean;
  excluded: boolean;
  params: Record<string, unknown>;
};

export type TrajectoryResponse = {
  episode: {
    epoch: number;
    taskId: string;
    split: Split;
    domain: string;
    tier: string;
    status: "pass" | "fail" | "error";
    partialCredit: number;
    steps: number;
    tokensIn: number;
    tokensOut: number;
    costUsd: number;
    model: string;
    wikiGeneration: number;
    injectedSkills: string[];
    error: string | null;
    startedAt: string;
    finishedAt: string;
  };
  messages: TrajectoryMessage[];
  assertions: TrajectoryAssertion[];
  simulated: boolean | null;
};

export type Job = {
  id: string;
  command: string[];
  startedAt: string;
  status: "running" | "exited" | "failed";
  exitCode: number | null;
  log: string[];
};

export type JobsResponse = { jobs: Job[] };

export type WikiPage = { name: string; title: string; body: string };

export type WikiHistoryEntry = {
  ts: string | null;
  summary: string;
};

export type WikiPagesResponse = {
  pages: WikiPage[];
  history: WikiHistoryEntry[];
  empty: boolean;
};
