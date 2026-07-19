import fs from "node:fs";
import path from "node:path";
import { NextResponse } from "next/server";
import { parseJson, queryOne } from "@/lib/db";
import { RUNS_DIR } from "@/lib/paths";
import type {
  Split,
  TrajectoryAssertion,
  TrajectoryMessage,
  TrajectoryResponse,
} from "@/lib/types";

export const dynamic = "force-dynamic";

/**
 * Full transcript for one (epoch, split, task) episode.
 *
 * The DB row points at the raw JSONL the runner wrote. Two rules keep this
 * from becoming an arbitrary-file reader:
 *   1. Identifiers are validated at the boundary (int range / allow-list /
 *      strict pattern) and the file path is NEVER taken from the client — it
 *      comes from the episode row.
 *   2. Even the engine-written path must resolve under RUNS_DIR.
 *
 * The `end_state` record (a full world snapshot, often megabytes) is skipped;
 * the viewer shows the assertions instead, which is what a human debugging a
 * failure actually reads.
 */

const SPLITS = new Set<Split>(["train", "heldout", "real"]);
const TASK_ID = /^[A-Za-z0-9._-]{1,200}$/;
const MAX_CONTENT_CHARS = 20_000;

type EpisodeRow = {
  epoch: number;
  task_id: string;
  split: Split;
  domain: string;
  tier: string;
  passed: number;
  partial_credit: number;
  steps: number;
  tokens_in: number;
  tokens_out: number;
  cost_usd: number;
  model: string;
  wiki_generation: number;
  injected_skills: string;
  error: string | null;
  started_at: string;
  finished_at: string;
  trajectory_path: string;
};

function clip(text: string): { content: string; truncated: boolean } {
  if (text.length <= MAX_CONTENT_CHARS) return { content: text, truncated: false };
  return { content: text.slice(0, MAX_CONTENT_CHARS), truncated: true };
}

export function GET(request: Request) {
  const url = new URL(request.url);
  const epoch = Number(url.searchParams.get("epoch"));
  const split = url.searchParams.get("split") ?? "";
  const taskId = url.searchParams.get("task") ?? "";

  if (!Number.isInteger(epoch) || epoch < 0 || epoch > 999) {
    return NextResponse.json({ error: "`epoch` must be an integer between 0 and 999." }, { status: 400 });
  }
  if (!SPLITS.has(split as Split)) {
    return NextResponse.json({ error: "`split` must be train, heldout or real." }, { status: 400 });
  }
  if (!TASK_ID.test(taskId)) {
    return NextResponse.json({ error: "`task` has an invalid format." }, { status: 400 });
  }

  const row = queryOne<EpisodeRow>(
    `SELECT epoch, task_id, split, domain, tier, passed, partial_credit, steps,
            tokens_in, tokens_out, cost_usd, model, wiki_generation,
            injected_skills, error, started_at, finished_at, trajectory_path
     FROM episodes WHERE epoch = ? AND split = ? AND task_id = ?`,
    [epoch, split, taskId],
  );
  if (!row) {
    return NextResponse.json({ error: "No episode recorded for this epoch/split/task." }, { status: 404 });
  }

  const abs = path.resolve(row.trajectory_path);
  const root = path.resolve(RUNS_DIR);
  const inRoot = abs === root || abs.startsWith(root + path.sep);
  let raw: string | null = null;
  if (inRoot) {
    try {
      raw = fs.readFileSync(abs, "utf8");
    } catch {
      raw = null;
    }
  }

  const messages: TrajectoryMessage[] = [];
  const assertions: TrajectoryAssertion[] = [];
  let simulated: boolean | null = null;

  for (const line of raw ? raw.split("\n") : []) {
    if (!line.trim()) continue;
    let rec: Record<string, unknown>;
    try {
      rec = JSON.parse(line);
    } catch {
      continue;
    }
    if (rec.type === "metadata") {
      if (typeof rec.simulated === "boolean") simulated = rec.simulated;
    } else if (rec.type === "message") {
      const { content, truncated } = clip(typeof rec.content === "string" ? rec.content : "");
      const rawCalls = Array.isArray(rec.tool_calls) ? rec.tool_calls : [];
      const toolCalls = rawCalls.flatMap((c) => {
        try {
          const parsed = typeof c === "string" ? JSON.parse(c) : c;
          return [
            {
              id: String(parsed.id ?? ""),
              name: String(parsed.name ?? "unknown"),
              arguments: typeof parsed.arguments === "string" ? parsed.arguments : JSON.stringify(parsed.arguments ?? {}),
            },
          ];
        } catch {
          return [];
        }
      });
      messages.push({
        index: typeof rec.index === "number" ? rec.index : messages.length,
        role: (rec.role as TrajectoryMessage["role"]) ?? "assistant",
        content,
        truncated,
        reasoning: typeof rec.reasoning_content === "string" && rec.reasoning_content ? rec.reasoning_content : null,
        toolCallId: typeof rec.tool_call_id === "string" ? rec.tool_call_id : null,
        toolCalls,
      });
    } else if (rec.type === "assertions" && Array.isArray(rec.results)) {
      for (const a of rec.results) {
        if (a && typeof a === "object") {
          assertions.push({
            type: String((a as Record<string, unknown>).type ?? "unknown"),
            passed: Boolean((a as Record<string, unknown>).passed),
            excluded: Boolean((a as Record<string, unknown>).excluded),
            params: ((a as Record<string, unknown>).params as Record<string, unknown>) ?? {},
          });
        }
      }
    }
    // `end_state` is deliberately skipped — it is a full world snapshot.
  }

  const body: TrajectoryResponse = {
    episode: {
      epoch: row.epoch,
      taskId: row.task_id,
      split: row.split,
      domain: row.domain,
      tier: row.tier,
      status: row.error ? "error" : row.passed ? "pass" : "fail",
      partialCredit: row.partial_credit,
      steps: row.steps,
      tokensIn: row.tokens_in,
      tokensOut: row.tokens_out,
      costUsd: row.cost_usd,
      model: row.model,
      wikiGeneration: row.wiki_generation,
      injectedSkills: parseJson<string[]>(row.injected_skills, []),
      error: row.error,
      startedAt: row.started_at,
      finishedAt: row.finished_at,
    },
    messages,
    assertions,
    simulated,
  };
  return NextResponse.json(body);
}
