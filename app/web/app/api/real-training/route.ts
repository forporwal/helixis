import fs from "node:fs";
import path from "node:path";
import { NextResponse } from "next/server";
import { parse as parseYaml } from "yaml";
import { dbExists, query, queryOne } from "@/lib/db";
import { WIKI_DIR } from "@/lib/paths";
import { getRecentTrainCycle, getTrainReadiness } from "@/lib/readiness";
import type { RealTrainingResponse } from "@/lib/types";

export const dynamic = "force-dynamic";

/**
 * What the real-trajectory loop has actually produced.
 *
 * This exists because a training cycle over real sessions is invisible on the
 * Lab's headline curve BY DESIGN — real-tier episodes are excluded from it, so
 * running the vLLM+Nemotron pipeline moves nothing on the chart and reads as a
 * no-op. The loop's real output is episodes judged, skills distilled, and tasks
 * proposed, so that is what gets reported here.
 *
 * Read-only, like the rest of the dashboard: every number is a query, and a
 * missing database is an empty state rather than an error.
 */

/**
 * Skills record their evidence tier in SKILL.md frontmatter, not in SQLite.
 *
 * The path column holds an ABSOLUTE path, so it is resolved rather than joined
 * onto the wiki root — `path.join(root, "/abs/path")` silently produces a
 * garbage path that fails to read, which would report every skill as `mocked`
 * and quietly contradict the cycle history. Same containment check the skills
 * route uses: engine-written or not, this never reads outside the wiki.
 *
 * Returns null when the file cannot be read, so "unknown" stays distinct from
 * "mocked" — a skill we failed to open must not be counted as evidence either
 * way.
 */
function skillSourceTier(skillPath: string): string | null {
  if (!skillPath) return null;
  const abs = path.resolve(skillPath);
  const root = path.resolve(WIKI_DIR);
  if (abs !== root && !abs.startsWith(root + path.sep)) return null;
  let raw: string;
  try {
    raw = fs.readFileSync(abs, "utf8");
  } catch {
    return null;
  }
  if (!raw.startsWith("---")) return "mocked";
  const end = raw.indexOf("\n---", 3);
  if (end === -1) return "mocked";
  try {
    const data = parseYaml(raw.slice(4, end));
    return String(
      (data && typeof data === "object" ? (data as Record<string, unknown>).source_tier : null) ??
        "mocked",
    );
  } catch {
    return "mocked";
  }
}

export async function GET() {
  if (!dbExists()) {
    return NextResponse.json({
      dbPresent: false,
      sessions: { ingested: 0, quarantined: 0, failed: 0 },
      episodes: { total: 0, helpful: 0, unhelpful: 0, unlabeled: 0, meanConfidence: null },
      skills: { fromReal: 0, total: 0, unreadable: 0, names: [] },
      proposals: { pending: 0, approved: 0, rejected: 0 },
      readiness: getTrainReadiness(),
      lastCycle: null,
      empty: true,
    } satisfies RealTrainingResponse);
  }

  const sessionRows = query<{ status: string; n: number }>(
    "SELECT status, COUNT(*) AS n FROM real_sessions GROUP BY status",
  );
  const byStatus = new Map(sessionRows.map((r) => [r.status, r.n]));

  // judge_passed is NULL when no distiller endpoint was reachable at ingest
  // time. That is an honest "unlabeled", not a failure, and it is counted
  // separately so a judge outage never looks like a batch of bad sessions.
  const episodes =
    queryOne<{ total: number; helpful: number; unhelpful: number; unlabeled: number }>(
      `SELECT COUNT(*) AS total,
              COALESCE(SUM(CASE WHEN judge_passed = 1 THEN 1 ELSE 0 END), 0) AS helpful,
              COALESCE(SUM(CASE WHEN judge_passed = 0 THEN 1 ELSE 0 END), 0) AS unhelpful,
              COALESCE(SUM(CASE WHEN judge_passed IS NULL THEN 1 ELSE 0 END), 0) AS unlabeled
       FROM episodes WHERE tier = 'real'`,
    ) ?? { total: 0, helpful: 0, unhelpful: 0, unlabeled: 0 };

  const meanConfidence =
    queryOne<{ c: number | null }>(
      "SELECT AVG(judge_confidence) AS c FROM episodes WHERE tier='real' AND judge_passed IS NOT NULL",
    )?.c ?? null;

  const skillRows = query<{ name: string; path: string }>("SELECT name, path FROM skills");
  const tiers = skillRows.map((s) => ({ ...s, tier: skillSourceTier(s.path) }));
  const fromReal = tiers.filter((s) => s.tier?.includes("real"));
  const unreadable = tiers.filter((s) => s.tier === null).length;

  const proposalRows = query<{ status: string; n: number }>(
    "SELECT status, COUNT(*) AS n FROM task_proposals GROUP BY status",
  );
  const byProposal = new Map(proposalRows.map((r) => [r.status, r.n]));

  const body: RealTrainingResponse = {
    dbPresent: true,
    sessions: {
      ingested: byStatus.get("ingested") ?? 0,
      quarantined: byStatus.get("quarantined") ?? 0,
      failed: byStatus.get("failed") ?? 0,
    },
    episodes: {
      total: episodes.total,
      helpful: episodes.helpful,
      unhelpful: episodes.unhelpful,
      unlabeled: episodes.unlabeled,
      meanConfidence,
    },
    skills: {
      fromReal: fromReal.length,
      total: skillRows.length,
      unreadable,
      names: fromReal.map((s) => s.name).slice(0, 8),
    },
    proposals: {
      pending: byProposal.get("pending") ?? 0,
      approved: byProposal.get("approved") ?? 0,
      rejected: byProposal.get("rejected") ?? 0,
    },
    readiness: getTrainReadiness(),
    lastCycle: getRecentTrainCycle(),
    empty: episodes.total === 0,
  };
  return NextResponse.json(body);
}
