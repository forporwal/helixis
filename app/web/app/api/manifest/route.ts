import { NextResponse } from "next/server";
import { run } from "@/lib/cli";
import { REPO_ROOT_DIR } from "@/lib/paths";
import type { ManifestResponse, ManifestTask } from "@/lib/types";

export const dynamic = "force-dynamic";

/**
 * The merged task manifest, read through `helixis task list --json`.
 *
 * Deliberately NOT a YAML parse in this process. The engine owns the merge
 * (frozen bench + user file), the dup-id rule and the validation; a second
 * implementation here would be a second source of truth that drifts silently.
 * Reading through the CLI costs a subprocess and buys exactly one answer to
 * "what is the task set?" — the same one the runner will use.
 *
 * A missing CLI is an honest empty state, never a 500: a judge who opens the
 * dashboard without the engine installed should see "unavailable", not a crash.
 */

type CliTask = {
  id: string;
  domain: string;
  split: string;
  type: string;
  origin: string;
  prompt: string;
  bench_ref: string;
  verify: string;
  reset: string;
  retired: boolean;
  draft: boolean;
  source: string;
  added_at: string;
};

export async function GET() {
  const res = await run("helixis", ["task", "list", "--json", "--include-retired"], {
    cwd: REPO_ROOT_DIR,
    timeoutMs: 30_000,
  });

  if (!res.ok) {
    const body: ManifestResponse = {
      tasks: [],
      warnings: [],
      available: false,
      error:
        res.kind === "missing"
          ? "The `helixis` CLI is not on PATH for the dashboard process, so the task manifest cannot be read here."
          : [res.stdout, res.stderr].filter(Boolean).join("\n").trim() || res.message,
      empty: true,
    };
    return NextResponse.json(body);
  }

  let parsed: { tasks?: CliTask[]; warnings?: { task_id: string; message: string; fatal: boolean }[] };
  try {
    parsed = JSON.parse(res.stdout);
  } catch {
    return NextResponse.json({
      tasks: [],
      warnings: [],
      available: false,
      error: "The engine returned output that was not valid JSON.",
      empty: true,
    } satisfies ManifestResponse);
  }

  const tasks: ManifestTask[] = (parsed.tasks ?? []).map((t) => ({
    id: t.id,
    domain: t.domain,
    split: t.split as ManifestTask["split"],
    type: t.type as ManifestTask["type"],
    origin: t.origin as ManifestTask["origin"],
    prompt: t.prompt,
    benchRef: t.bench_ref,
    verify: t.verify,
    reset: t.reset,
    retired: t.retired,
    // Engines from before spec 05 do not emit these. Defaulting rather than
    // trusting the field keeps an older CLI from rendering every task as an
    // unfinished draft.
    draft: t.draft ?? false,
    source: t.source ?? "",
    addedAt: t.added_at,
  }));

  const body: ManifestResponse = {
    tasks,
    warnings: (parsed.warnings ?? []).map((w) => ({
      taskId: w.task_id,
      message: w.message,
      fatal: w.fatal,
    })),
    available: true,
    error: null,
    empty: tasks.length === 0,
  };
  return NextResponse.json(body);
}
