import path from "node:path";

/**
 * The dashboard is read-only infrastructure sitting beside the engine.
 * Everything it reads is resolvable from the repo root, which is two levels
 * up from `app/web` (the Next.js cwd).
 */
const REPO_ROOT = path.resolve(process.cwd(), "..", "..");

export const DB_PATH = process.env.HELIXIS_DB
  ? path.resolve(process.env.HELIXIS_DB)
  : path.join(REPO_ROOT, "runs", "helixis.db");

export const WIKI_DIR = process.env.HELIXIS_WIKI
  ? path.resolve(process.env.HELIXIS_WIKI)
  : path.join(REPO_ROOT, "wiki");

/**
 * Runs directory. Defaults to the DATABASE'S OWN directory rather than a fixed
 * path, so pointing HELIXIS_DB at another run also moves the trajectory and
 * experiment-log lookups. Otherwise the provenance check could describe one
 * dataset while the charts describe another.
 */
export const RUNS_DIR = process.env.HELIXIS_RUNS
  ? path.resolve(process.env.HELIXIS_RUNS)
  : path.dirname(DB_PATH);

export const REPO_ROOT_DIR = REPO_ROOT;

/**
 * Training cadence, mirroring `helixis/config.py` (spec 03, Req 4.1). The
 * dashboard only ever *reports* these — the engine is what acts on them — but
 * the nudge would lie if the number it shows and the number that triggers a run
 * came from different places, so both read the same variables.
 */
export const REAL_TRAIN_THRESHOLD = Number(
  process.env.HELIXIS_REAL_TRAIN_THRESHOLD ?? 10,
);
export const AUTO_TRAIN =
  (process.env.HELIXIS_AUTO_TRAIN ?? "").toLowerCase() !== "" &&
  !["0", "false", "no"].includes((process.env.HELIXIS_AUTO_TRAIN ?? "").toLowerCase());

/** Budget caps mirror `helixis/config.py` defaults so the meter agrees with the runner. */
export const EPOCH_COST_CAP_USD = Number(
  process.env.HELIXIS_EPOCH_COST_CAP_USD ?? 8,
);
export const TOTAL_COST_CAP_USD = Number(
  process.env.HELIXIS_TOTAL_COST_CAP_USD ?? 150,
);
