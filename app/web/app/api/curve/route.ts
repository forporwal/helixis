import { NextResponse } from "next/server";
import { hasColumn, query } from "@/lib/db";
import { getProvenance } from "@/lib/provenance";
import type {
  CurriculumEvent,
  CurveDelta,
  CurvePoint,
  CurveResponse,
  Split,
} from "@/lib/types";

export const dynamic = "force-dynamic";

type Row = {
  epoch: number;
  split: Split;
  n: number;
  mean_partial_credit: number | null;
  pass_rate: number | null;
  cost_usd: number | null;
  tokens: number | null;
};

/**
 * Mirrors EpisodeStore.epoch_curve() exactly, including both filters:
 *
 *  - tier='mocked'  — real-tier episodes stay out of headline metrics so
 *    real-world flakiness cannot corrupt the curve (design.md, Error Handling).
 *  - origin='bench' — the headline is computed over the FROZEN bench set. The
 *    curriculum is mutable now, and a mean taken over a task set that grew
 *    between epochs measures nothing: adding three easy tasks at epoch 4 would
 *    read exactly like the agent improving (spec 04, Requirement 3.2).
 *
 * The full-curriculum series is returned alongside, but it is only ever shown
 * with the curriculum-change annotations below it.
 */
function curveRows(origin: string | null): Row[] {
  const clause = origin ? "WHERE tier='mocked' AND origin=?" : "WHERE tier='mocked'";
  return query<Row>(
    `SELECT epoch, split,
            COUNT(*)                  AS n,
            AVG(partial_credit)       AS mean_partial_credit,
            AVG(CAST(passed AS REAL)) AS pass_rate,
            SUM(cost_usd)             AS cost_usd,
            SUM(tokens_in+tokens_out) AS tokens
     FROM episodes
     ${clause}
     GROUP BY epoch, split
     ORDER BY epoch, split`,
    origin ? [origin] : [],
  );
}

/** Fixed series order so a split's color never depends on which ran first. */
const ORDER: Split[] = ["train", "heldout", "real"];

function toSeries(rows: Row[]): { split: Split; points: CurvePoint[] }[] {
  const bySplit = new Map<Split, CurvePoint[]>();
  for (const r of rows) {
    const pts = bySplit.get(r.split) ?? [];
    pts.push({
      epoch: r.epoch,
      n: r.n,
      meanPartialCredit: r.mean_partial_credit ?? 0,
      passRate: r.pass_rate ?? 0,
      costUsd: r.cost_usd ?? 0,
      tokens: r.tokens ?? 0,
    });
    bySplit.set(r.split, pts);
  }
  return ORDER.filter((s) => bySplit.has(s)).map((split) => ({
    split,
    points: (bySplit.get(split) ?? []).sort((a, b) => a.epoch - b.epoch),
  }));
}

export function GET() {
  // The engine adds `origin` when it next opens the store. Until then every row
  // predates user tasks and IS bench work, so the unfiltered query is the right
  // answer — filtering on a column that does not exist would fail, fall back to
  // the empty set, and render "no graded episodes yet" over a full database.
  const originTracked = hasColumn("episodes", "origin");
  const rows = curveRows(originTracked ? "bench" : null);

  const excluded = query<{ n: number }>(
    "SELECT COUNT(*) AS n FROM episodes WHERE tier <> 'mocked'",
  );
  const excludedUser = originTracked
    ? query<{ n: number }>(
        "SELECT COUNT(*) AS n FROM episodes WHERE tier = 'mocked' AND origin = 'user'",
      )
    : [];

  const events = query<{
    epoch: number | null;
    ts: string;
    action: string;
    task_id: string;
    split: string;
    task_type: string;
  }>("SELECT epoch, ts, action, task_id, split, task_type FROM curriculum_events ORDER BY id");

  const curriculumEvents: CurriculumEvent[] = events.map((e) => ({
    epoch: e.epoch,
    ts: e.ts,
    action: e.action,
    taskId: e.task_id,
    split: e.split,
    taskType: e.task_type,
  }));

  const series = toSeries(rows);
  const fullSeries = toSeries(curveRows(null));

  // The claim IS the delta, so compute it server-side from the same rows the
  // chart draws — no hand-computed numbers anywhere in the demo.
  const deltas: CurveDelta[] = series
    .filter((s) => s.points.length >= 1)
    .map((s) => {
      const first = s.points[0];
      const last = s.points[s.points.length - 1];
      return {
        split: s.split,
        firstEpoch: first.epoch,
        lastEpoch: last.epoch,
        partialCreditFrom: first.meanPartialCredit,
        partialCreditTo: last.meanPartialCredit,
        partialCreditDelta: last.meanPartialCredit - first.meanPartialCredit,
        passRateFrom: first.passRate,
        passRateTo: last.passRate,
        passRateDelta: last.passRate - first.passRate,
      };
    });

  const epochs = [...new Set(rows.map((r) => r.epoch))].sort((a, b) => a - b);

  const body: CurveResponse = {
    series,
    deltas,
    fullSeries,
    curriculumEvents,
    epochs,
    excludedRealEpisodes: excluded[0]?.n ?? 0,
    excludedUserEpisodes: excludedUser[0]?.n ?? 0,
    provenance: getProvenance(),
    empty: rows.length === 0,
  };
  return NextResponse.json(body);
}
