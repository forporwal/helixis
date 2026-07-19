import fs from "node:fs";
import path from "node:path";
import { dbExists, query } from "./db";
import { RUNS_DIR } from "./paths";

/**
 * Honesty layer.
 *
 * The engine can run against the offline simulator instead of a graded model.
 * When it does, every trajectory's metadata line carries `"simulated": true`
 * and each experiment-log entry carries the same flag. Numbers produced that
 * way are NOT experimental results and the dashboard must never present them
 * as such.
 *
 * We look at two independent sources so a partially-written run still reports
 * honestly, and we take the pessimistic reading: if anything says simulated,
 * the dashboard says simulated.
 */

export type Provenance = {
  /** True if any inspected episode came from the offline simulator. */
  simulated: boolean;
  /** True if every inspected episode was simulated. */
  allSimulated: boolean;
  /** How many episodes we actually inspected (0 = we could not tell). */
  inspected: number;
  simulatedCount: number;
  /** Where the verdict came from, for the UI to explain itself. */
  sources: string[];
};

const EMPTY: Provenance = {
  simulated: false,
  allSimulated: false,
  inspected: 0,
  simulatedCount: 0,
  sources: [],
};

let cache: { key: string; value: Provenance } | null = null;

function mtimeKey(...files: string[]): string {
  return files
    .map((f) => {
      try {
        return `${f}:${fs.statSync(f).mtimeMs}`;
      } catch {
        return `${f}:none`;
      }
    })
    .join("|");
}

/** Read line 0 (the metadata record) of a trajectory JSONL without loading the file. */
function readTrajectoryMeta(file: string): Record<string, unknown> | null {
  try {
    const fd = fs.openSync(file, "r");
    try {
      const buf = Buffer.alloc(8192);
      const n = fs.readSync(fd, buf, 0, buf.length, 0);
      const firstLine = buf.subarray(0, n).toString("utf8").split("\n")[0];
      if (!firstLine.trim()) return null;
      return JSON.parse(firstLine) as Record<string, unknown>;
    } finally {
      fs.closeSync(fd);
    }
  } catch {
    return null;
  }
}

export function getProvenance(): Provenance {
  // Provenance describes the episodes being displayed. With no database there
  // are none, so there is nothing to characterize -- and a stale experiment log
  // left on disk must not make an empty dashboard claim it is showing
  // simulated results.
  if (!dbExists()) return EMPTY;

  const episodes = query<{ c: number }>("SELECT COUNT(*) AS c FROM episodes")[0]?.c ?? 0;
  if (episodes === 0) return EMPTY;

  const logPath = path.join(RUNS_DIR, "experiment-log.json");
  const key = mtimeKey(logPath, RUNS_DIR);
  if (cache && cache.key === key) return cache.value;

  const sources: string[] = [];
  let inspected = 0;
  let simulatedCount = 0;

  // Source 1: the experiment log, one entry per epoch phase.
  try {
    const raw = fs.readFileSync(logPath, "utf8");
    const entries = JSON.parse(raw) as Array<Record<string, unknown>>;
    const graded = entries.filter((e) => "simulated" in e);
    if (graded.length) {
      sources.push("runs/experiment-log.json");
      for (const e of graded) {
        inspected += 1;
        if (e.simulated === true) simulatedCount += 1;
      }
    }
  } catch {
    /* no log yet — fall through to trajectories */
  }

  // Source 2: sample the most recent trajectories on disk.
  const rows = query<{ trajectory_path: string }>(
    "SELECT trajectory_path FROM episodes WHERE trajectory_path <> '' ORDER BY finished_at DESC LIMIT 25",
  );
  let sampled = 0;
  for (const r of rows) {
    const file = path.isAbsolute(r.trajectory_path)
      ? r.trajectory_path
      : path.resolve(RUNS_DIR, "..", r.trajectory_path);
    const meta = readTrajectoryMeta(file);
    if (!meta) continue;
    sampled += 1;
    inspected += 1;
    if (meta.simulated === true) simulatedCount += 1;
  }
  if (sampled) sources.push(`${sampled} trajectory metadata records`);

  const value: Provenance =
    inspected === 0
      ? EMPTY
      : {
          simulated: simulatedCount > 0,
          allSimulated: simulatedCount === inspected,
          inspected,
          simulatedCount,
          sources,
        };

  cache = { key, value };
  return value;
}
