import { NextResponse } from "next/server";
import { isAvailable, run } from "@/lib/cli";
import { listJobs, startJob, stopAll } from "@/lib/runner";
import { REPO_ROOT_DIR } from "@/lib/paths";

export const dynamic = "force-dynamic";

/**
 * Operator controls (Requirement 7.6): start/stop an epoch, trigger a held-out eval.
 *
 * Same discipline as the proposals route:
 *   1. `action` is checked against a literal allow-list.
 *   2. Numeric inputs are coerced with Number.isInteger + range checks, then
 *      re-serialized by US — the string that reaches argv is one we generated,
 *      never the client's.
 *   3. `split` is an allow-listed literal.
 *   4. Everything is spawned with an argument array and `shell: false`.
 * No user-supplied string is ever concatenated into a command.
 */

const ACTIONS = new Set([
  "start-epoch",
  "heldout",
  "run",
  "stop",
  "status",
  "distill",
  "triage",
  "pages",
  "tail-policy",
  "rehearse",
  "task-add",
  "task-remove",
  "task-validate",
  "ingest-real",
  "train-cycle",
  "mine-tasks",
]);
const SPLITS = new Set(["train", "heldout"]);

/**
 * Task ids reach argv, so they are pattern-checked here as well as engine-side.
 * Same shape the engine enforces (`domain.snake_case_action`) — a value that
 * cannot match this can never become an argument.
 */
const TASK_ID_RE = /^[a-z][a-z0-9_]*\.[a-z][a-z0-9_]*$/;

/** Accept only a real integer in range, and return OUR string form of it. */
function intArg(value: unknown, min: number, max: number): string | null {
  const n = typeof value === "number" ? value : Number(value);
  if (!Number.isInteger(n) || n < min || n > max) return null;
  return String(n);
}

/**
 * Re-serialize a task payload into a JSON string WE construct, field by field,
 * from an explicit key list. The client's object never reaches argv — only a
 * document we built out of values we type-checked. The engine then validates it
 * properly (id shape, bench-ref resolution, script paths); this layer stays
 * deliberately dumb, per design.md §3.
 */
function taskJsonArg(payload: Record<string, unknown>): { json: string } | { error: string } {
  const id = payload.id;
  if (typeof id !== "string" || !TASK_ID_RE.test(id)) {
    return { error: "`id` must look like `domain.snake_case_action`." };
  }
  const type = payload.type === "real" ? "real" : "bench";
  const split = typeof payload.split === "string" && SPLITS.has(payload.split) ? payload.split : "train";

  const out: Record<string, unknown> = { id, type, split };
  // Held-out is opt-in twice over: the engine refuses the split without this.
  if (split === "heldout") out.heldout = true;

  for (const key of ["domain", "prompt", "bench_ref", "verify", "reset"] as const) {
    const value = payload[key];
    if (value === undefined || value === null || value === "") continue;
    if (typeof value !== "string") return { error: `\`${key}\` must be a string.` };
    if (value.length > 20_000) return { error: `\`${key}\` is too long.` };
    out[key] = value;
  }
  return { json: JSON.stringify(out) };
}

export async function GET() {
  return NextResponse.json({ jobs: listJobs() });
}

export async function POST(request: Request) {
  let payload: Record<string, unknown>;
  try {
    payload = await request.json();
  } catch {
    return NextResponse.json({ ok: false, error: "Body must be JSON." }, { status: 400 });
  }

  const action = payload.action;
  if (typeof action !== "string" || !ACTIONS.has(action)) {
    return NextResponse.json(
      { ok: false, error: `Invalid action. Expected one of: ${[...ACTIONS].join(", ")}.` },
      { status: 400 },
    );
  }

  if (action === "status") {
    return NextResponse.json({ ok: true, jobs: listJobs() });
  }

  if (action === "stop") {
    const stopped = stopAll();
    return NextResponse.json({
      ok: true,
      stopped,
      // Say plainly what did and did not happen.
      note:
        stopped === 0
          ? "No engine process started from this dashboard is running. A run launched from a terminal must be stopped there — the dashboard only signals processes it owns."
          : `Sent SIGTERM to ${stopped} engine process(es). The runner finishes the in-flight task, then stops.`,
    });
  }

  // Task management is fast and its errors belong in the form, not in a job
  // log — so these run to completion and return the engine's own message.
  // Note this is the ONLY write path to tasks.user.yaml: the web process never
  // touches the file itself (design.md §3).
  if (action === "task-add" || action === "task-remove" || action === "task-validate") {
    let taskArgs: string[];
    if (action === "task-add") {
      const built = taskJsonArg(payload);
      if ("error" in built) {
        return NextResponse.json({ ok: false, error: built.error }, { status: 400 });
      }
      taskArgs = ["task", "add", "--json", built.json];
    } else if (action === "task-remove") {
      const id = payload.id;
      if (typeof id !== "string" || !TASK_ID_RE.test(id)) {
        return NextResponse.json(
          { ok: false, error: "`id` must look like `domain.snake_case_action`." },
          { status: 400 },
        );
      }
      taskArgs = ["task", "remove", "--id", id];
    } else {
      taskArgs = ["task", "validate", "--json"];
    }

    const res = await run("helixis", taskArgs, { cwd: REPO_ROOT_DIR, timeoutMs: 60_000 });
    if (!res.ok && res.kind === "missing") {
      return NextResponse.json(
        {
          ok: false,
          error: "The `helixis` CLI is not installed or not on PATH, so tasks cannot be managed from this dashboard.",
          hint: "Install the engine (`pip install -e app/engine`) on the host running the dashboard, or use `helixis task` in a terminal.",
        },
        { status: 503 },
      );
    }
    if (!res.ok) {
      // A non-zero exit here is a REJECTED task, not an infrastructure fault —
      // surface the engine's own wording so the operator sees the real reason.
      const detail = [res.stdout, res.stderr].filter(Boolean).join("\n").trim();
      return NextResponse.json(
        { ok: false, error: detail || res.message, output: detail },
        { status: 400 },
      );
    }
    return NextResponse.json({
      ok: true,
      output: res.stdout.trim(),
      note: res.stdout.trim().split("\n")[0] || `Completed: ${action}`,
    });
  }

  // Validate and build argv BEFORE probing for the binary, so malformed input
  // gets an accurate 400 rather than being masked by a missing-CLI 503.
  let args: string[];

  if (action === "start-epoch") {
    const epoch = intArg(payload.epoch, 0, 999);
    if (epoch === null) {
      return NextResponse.json(
        { ok: false, error: "`epoch` must be an integer between 0 and 999." },
        { status: 400 },
      );
    }
    const split = typeof payload.split === "string" && SPLITS.has(payload.split) ? payload.split : "train";
    args = ["epoch", "--epoch", epoch, "--split", split];
    // The Lab's mode selector. `--offline` only ever downgrades a run to the
    // deterministic stub, so it needs no authority check — a client cannot use
    // it to spend money or reach a real endpoint, only to avoid both.
    if (payload.mode === "simulated") args.push("--offline");
  } else if (action === "heldout") {
    const epoch = intArg(payload.epoch, 0, 999);
    if (epoch === null) {
      return NextResponse.json(
        { ok: false, error: "`epoch` must be an integer between 0 and 999." },
        { status: 400 },
      );
    }
    args = ["heldout", "--epoch", epoch];
    if (payload.mode === "simulated") args.push("--offline");
  } else if (action === "distill" || action === "triage") {
    const epoch = intArg(payload.epoch, 0, 999);
    if (epoch === null) {
      return NextResponse.json(
        { ok: false, error: "`epoch` must be an integer between 0 and 999." },
        { status: 400 },
      );
    }
    args = [action, "--epoch", epoch];
  } else if (
    action === "pages" ||
    action === "tail-policy" ||
    action === "rehearse" ||
    // Real-session ingestion and the training cycle (spec 03, Req 2.4 / 4.1).
    // Deliberately argument-free: the engine reads the session directory,
    // thresholds and cost caps from its own config, so the dashboard cannot
    // widen what gets ingested or override a cap by sending a flag. `--watch`
    // is intentionally NOT reachable from here — a long-lived poll loop is a
    // process the operator should own from a terminal or cron, not something a
    // browser click leaves running.
    action === "ingest-real" ||
    action === "train-cycle" ||
    // Standalone mining (spec 05, Req 3.1). Argument-free for the same reason:
    // `--allow-single` and `--max-proposals` relax the anti-spam filters, and
    // those are not knobs a browser click should be able to reach. A demo that
    // needs them uses the terminal.
    action === "mine-tasks"
  ) {
    args = [action];
  } else {
    // action === "run"
    const epochs = intArg(payload.epochs ?? 6, 1, 100);
    if (epochs === null) {
      return NextResponse.json(
        { ok: false, error: "`epochs` must be an integer between 1 and 100." },
        { status: 400 },
      );
    }
    args = ["run", "--epochs", epochs];
  }

  // Arguments are valid; now confirm the binary exists so a missing CLI is a
  // clear 503 rather than an opaque spawn failure.
  if (!(await isAvailable("helixis"))) {
    return NextResponse.json(
      {
        ok: false,
        error: "The `helixis` CLI is not installed or not on PATH, so runs cannot be started from this dashboard.",
        hint: "Install the engine (`pip install -e app/engine`) on the host running the dashboard, or start the run from a terminal.",
      },
      { status: 503 },
    );
  }

  const result = startJob("helixis", args);
  if (!result.ok) {
    return NextResponse.json({ ok: false, error: result.message }, { status: 502 });
  }
  return NextResponse.json({ ok: true, job: result.job });
}
