import fs from "node:fs";
import path from "node:path";
import { query, queryOne } from "./db";
import { AUTO_TRAIN, REAL_TRAIN_THRESHOLD, WIKI_DIR } from "./paths";

/**
 * The training cadence signal (spec 03, Req 4.1 and 4.4).
 *
 * Shared by `/api/status` (which reports it) and `/api/actions` (which turns it
 * into a nudge), because two routes computing "is it worth training" from two
 * queries is two chances to disagree — and a nudge that disagrees with the
 * status strip is worse than no nudge.
 *
 * Read-only, like everything else the dashboard does: this decides what to
 * *show*. The engine re-derives the same numbers before it acts on them.
 */

export type TrainReadiness = {
  newRealEpisodes: number;
  threshold: number;
  autoTrain: boolean;
  ready: boolean;
  totalRealEpisodes: number;
  lastDistillAt: string | null;
};

export function getTrainReadiness(): TrainReadiness {
  const lastDistillAt =
    queryOne<{ created_at: string }>(
      "SELECT created_at FROM distill_runs ORDER BY id DESC LIMIT 1",
    )?.created_at ?? null;

  // Mirrors EpisodeStore.new_real_episodes_since_distill() exactly, including
  // counting unjudged episodes.
  const newRealEpisodes =
    (lastDistillAt === null
      ? queryOne<{ n: number }>("SELECT COUNT(*) AS n FROM episodes WHERE tier='real'")
      : queryOne<{ n: number }>(
          "SELECT COUNT(*) AS n FROM episodes WHERE tier='real' AND finished_at > ?",
          [lastDistillAt],
        ))?.n ?? 0;

  const totalRealEpisodes =
    queryOne<{ n: number }>("SELECT COUNT(*) AS n FROM episodes WHERE tier='real'")?.n ?? 0;

  return {
    newRealEpisodes,
    threshold: REAL_TRAIN_THRESHOLD,
    autoTrain: AUTO_TRAIN,
    ready: newRealEpisodes >= REAL_TRAIN_THRESHOLD,
    totalRealEpisodes,
    lastDistillAt,
  };
}

export type TrainCycleEvent = {
  ts: string;
  generation: number;
  skills: string[];
};

/** How long a completed cycle stays newsworthy on the feed (Req 4.4). */
const RECENT_WINDOW_MS = 24 * 60 * 60 * 1000;

/**
 * The most recent completed train-cycle, if it landed within the last 24h.
 *
 * Read from `wiki/history.jsonl` rather than the database because the wiki is
 * where the skills actually live — if someone restores an older wiki, the claim
 * "N new skills live" should travel with it rather than being asserted by a
 * database row about skills that are no longer there.
 */
export function getRecentTrainCycle(now: number = Date.now()): TrainCycleEvent | null {
  let raw: string;
  try {
    raw = fs.readFileSync(path.join(WIKI_DIR, "history.jsonl"), "utf8");
  } catch {
    return null;
  }

  let latest: TrainCycleEvent | null = null;
  for (const line of raw.split("\n")) {
    if (!line.trim()) continue;
    let rec: Record<string, unknown>;
    try {
      rec = JSON.parse(line);
    } catch {
      continue;
    }
    if (rec.event !== "real_train_cycle") continue;
    const skills = Array.isArray(rec.skills) ? rec.skills.map(String) : [];
    if (!skills.length) continue;
    const ts = typeof rec.ts === "string" ? rec.ts : "";
    const at = Date.parse(ts);
    if (!Number.isFinite(at) || now - at > RECENT_WINDOW_MS) continue;
    // Last matching line wins: history.jsonl is append-only and ordered.
    latest = {
      ts,
      generation: typeof rec.generation === "number" ? rec.generation : 0,
      skills,
    };
  }
  return latest;
}
