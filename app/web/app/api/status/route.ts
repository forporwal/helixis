import fs from "node:fs";
import path from "node:path";
import { NextResponse } from "next/server";
import { dbExists, query } from "@/lib/db";
import { isAvailable } from "@/lib/cli";
import { clawTuiUrl, clawUiUrl, probeGateway } from "@/lib/claw";
import { getProvenance } from "@/lib/provenance";
import { getTrainReadiness } from "@/lib/readiness";
import { EPOCH_COST_CAP_USD, TOTAL_COST_CAP_USD, WIKI_DIR } from "@/lib/paths";
import type { Split, StatusResponse } from "@/lib/types";

export const dynamic = "force-dynamic";

type EpochRow = {
  epoch: number;
  split: Split;
  status: string;
  n_tasks: number;
  n_done: number;
  cost_usd: number;
  wiki_generation: number;
  started_at: string | null;
  finished_at: string | null;
};

export async function GET() {
  const present = dbExists();

  const epochRows = query<EpochRow>(
    `SELECT epoch, split, status, n_tasks, n_done, cost_usd, wiki_generation,
            started_at, finished_at
     FROM epochs ORDER BY epoch, split`,
  );

  // `n_done` is only backfilled by finish_epoch, so a running epoch's progress
  // has to come from the episodes actually on record.
  const liveCounts = query<{ epoch: number; split: Split; n: number }>(
    "SELECT epoch, split, COUNT(*) AS n FROM episodes GROUP BY epoch, split",
  );
  const liveMap = new Map(liveCounts.map((r) => [`${r.epoch}/${r.split}`, r.n]));

  const epochs = epochRows.map((r) => ({
    epoch: r.epoch,
    split: r.split,
    status: r.status,
    nTasks: r.n_tasks,
    nDone: Math.max(r.n_done, liveMap.get(`${r.epoch}/${r.split}`) ?? 0),
    costUsd: r.cost_usd,
    wikiGeneration: r.wiki_generation,
    startedAt: r.started_at,
    finishedAt: r.finished_at,
  }));

  const running = epochs.filter((e) => e.status === "running");
  const currentEpoch = running.length
    ? Math.max(...running.map((e) => e.epoch))
    : epochs.length
      ? Math.max(...epochs.map((e) => e.epoch))
      : null;

  const totalCost = query<{ c: number }>(
    "SELECT COALESCE(SUM(cost_usd),0) AS c FROM episodes",
  )[0]?.c ?? 0;
  const tokenTotals = query<{ tin: number; tout: number }>(
    "SELECT COALESCE(SUM(tokens_in),0) AS tin, COALESCE(SUM(tokens_out),0) AS tout FROM episodes",
  )[0] ?? { tin: 0, tout: 0 };
  const epochCost =
    currentEpoch === null
      ? 0
      : (query<{ c: number }>(
          "SELECT COALESCE(SUM(cost_usd),0) AS c FROM episodes WHERE epoch=?",
          [currentEpoch],
        )[0]?.c ?? 0);

  const skillCount = query<{ c: number }>("SELECT COUNT(*) AS c FROM skills")[0]?.c ?? 0;
  const episodeCount = query<{ c: number }>("SELECT COUNT(*) AS c FROM episodes")[0]?.c ?? 0;

  let wikiGeneration = 0;
  try {
    const state = JSON.parse(fs.readFileSync(path.join(WIKI_DIR, "state.json"), "utf8"));
    if (typeof state.generation === "number") wikiGeneration = state.generation;
  } catch {
    wikiGeneration = epochs.reduce((m, e) => Math.max(m, e.wikiGeneration), 0);
  }

  const [helixisAvailable, openshellAvailable, gatewayUp] = await Promise.all([
    isAvailable("helixis"),
    isAvailable("openshell"),
    probeGateway(),
  ]);

  const body: StatusResponse = {
    dbPresent: present,
    running: running.length > 0,
    currentEpoch,
    epochs,
    cost: {
      total: totalCost,
      totalCap: TOTAL_COST_CAP_USD,
      epochCost,
      epochCap: EPOCH_COST_CAP_USD,
    },
    tokens: { totalIn: tokenTotals.tin, totalOut: tokenTotals.tout },
    wikiGeneration,
    skillCount,
    episodeCount,
    provenance: getProvenance(),
    trainReadiness: getTrainReadiness(),
    controls: { helixisAvailable, openshellAvailable },
    claw: {
      gatewayUp,
      wikiGeneration,
      skillCount,
      uiUrl: clawUiUrl(),
      tuiUrl: clawTuiUrl(),
    },
    empty: !present || episodeCount === 0,
  };
  return NextResponse.json(body);
}
