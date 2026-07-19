import { NextResponse } from "next/server";
import { hasColumn, parseJson, query } from "@/lib/db";
import type { Split, TaskCell, TasksResponse } from "@/lib/types";

export const dynamic = "force-dynamic";

type Row = {
  epoch: number;
  task_id: string;
  split: Split;
  domain: string;
  tier: string;
  passed: number;
  partial_credit: number;
  steps: number;
  cost_usd: number;
  tokens_in: number;
  tokens_out: number;
  model: string;
  wiki_generation: number;
  injected_skills: string;
  error: string | null;
  origin: string | null;
};

export function GET() {
  // Selecting a column the engine has not added yet fails the WHOLE query, and
  // `query()` would then hand back an empty grid for a database full of
  // episodes. Ask for it only once it exists; every row without it is bench
  // work by definition.
  const originCol = hasColumn("episodes", "origin") ? "origin" : "'bench' AS origin";
  const rows = query<Row>(
    `SELECT epoch, task_id, split, domain, tier, passed, partial_credit, steps,
            cost_usd, tokens_in, tokens_out, model, wiki_generation, injected_skills,
            error, ${originCol}
     FROM episodes
     ORDER BY epoch, split, task_id`,
  );

  const cells: TaskCell[] = rows.map((r) => ({
    epoch: r.epoch,
    taskId: r.task_id,
    split: r.split,
    domain: r.domain,
    tier: r.tier,
    // `error` is the engine's signal that the attempt blew up rather than
    // merely scoring badly — worth distinguishing from an honest failure.
    status: r.error ? "error" : r.passed ? "pass" : "fail",
    partialCredit: r.partial_credit,
    steps: r.steps,
    costUsd: r.cost_usd,
    tokensIn: r.tokens_in,
    tokensOut: r.tokens_out,
    model: r.model,
    wikiGeneration: r.wiki_generation,
    injectedSkills: parseJson<string[]>(r.injected_skills, []),
    error: r.error,
    // Episodes written before user tasks existed have no origin; they are
    // bench episodes by definition.
    origin: r.origin ?? "bench",
  }));

  const epochs = [...new Set(rows.map((r) => r.epoch))].sort((a, b) => a - b);

  // A task is a row in the grid; keep split+domain so the grid can group.
  const seen = new Map<string, { taskId: string; split: Split; domain: string; origin: string }>();
  for (const r of rows) {
    const key = `${r.split}/${r.task_id}`;
    if (!seen.has(key)) {
      seen.set(key, {
        taskId: r.task_id,
        split: r.split,
        domain: r.domain,
        origin: r.origin ?? "bench",
      });
    }
  }
  const tasks = [...seen.values()].sort(
    (a, b) => a.split.localeCompare(b.split) || a.taskId.localeCompare(b.taskId),
  );

  const body: TasksResponse = { epochs, tasks, cells, empty: rows.length === 0 };
  return NextResponse.json(body);
}
