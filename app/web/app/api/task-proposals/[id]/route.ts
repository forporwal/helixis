import { NextResponse } from "next/server";
import { run } from "@/lib/cli";
import { REPO_ROOT_DIR } from "@/lib/paths";
import { getTaskProposal, proposalEpisodes } from "@/lib/taskProposals";
import type { TaskProposalDetailResponse } from "@/lib/types";

export const dynamic = "force-dynamic";

/**
 * Read one mined task proposal; approve or reject it.
 *
 * Same discipline as `/api/proposals/[chunkId]`, because this route has the
 * same shape of risk: an id reaches argv.
 *
 *   1. The id must match the engine's own task-id pattern — an allow-list on
 *      shape, checked BEFORE the value is used for anything.
 *   2. `action` must be the literal "approve" or "reject".
 *   3. `reason` is free text but is passed as its own argv element, capped, and
 *      never concatenated into a command string.
 *   4. Execution goes through execFile with an argument array and shell: false.
 *
 * Crucially the mutation is delegated, not performed. Approval runs
 * `helixis proposal approve`, which internally runs the same `task add` a human
 * types — so tasks.user.yaml keeps exactly one writer and a mined task clears
 * exactly the validation a hand-written one does (spec 05, design §3).
 */

const TASK_ID_RE = /^[a-z][a-z0-9_]*\.[a-z][a-z0-9_]*$/;
const ACTIONS = new Set(["approve", "reject"]);

export async function GET(
  _request: Request,
  context: { params: Promise<{ id: string }> },
) {
  const { id } = await context.params;
  const proposal = TASK_ID_RE.test(id) ? getTaskProposal(id) : null;
  const body: TaskProposalDetailResponse = {
    proposal,
    episodes: proposal ? proposalEpisodes(proposal.sourceEpisodeIds) : [],
    found: proposal !== null,
  };
  // 200 even when nothing was found. `found` carries that, and the client
  // renders an empty state from it — a 404 would make usePoll throw, which
  // holds the page on a loading skeleton forever instead of saying plainly
  // that the proposal is gone.
  return NextResponse.json(body);
}

export async function POST(
  request: Request,
  context: { params: Promise<{ id: string }> },
) {
  const { id } = await context.params;

  if (!TASK_ID_RE.test(id)) {
    return NextResponse.json(
      { ok: false, error: "Invalid proposal id. Expected `domain.snake_case_action`." },
      { status: 400 },
    );
  }

  let payload: { action?: unknown; reason?: unknown };
  try {
    payload = await request.json();
  } catch {
    return NextResponse.json({ ok: false, error: "Body must be JSON." }, { status: 400 });
  }

  const action = payload.action;
  if (typeof action !== "string" || !ACTIONS.has(action)) {
    return NextResponse.json(
      { ok: false, error: 'Invalid action. Expected exactly "approve" or "reject".' },
      { status: 400 },
    );
  }

  const reason =
    typeof payload.reason === "string" && payload.reason.trim()
      ? payload.reason.trim().slice(0, 500)
      : "rejected from the Helixis dashboard";

  const args =
    action === "approve"
      ? ["proposal", "approve", "--id", id]
      : ["proposal", "reject", "--id", id, "--reason", reason];

  const res = await run("helixis", args, { cwd: REPO_ROOT_DIR, timeoutMs: 120_000 });

  if (!res.ok && res.kind === "missing") {
    return NextResponse.json(
      {
        ok: false,
        error:
          "The `helixis` CLI is not installed or not on PATH, so task proposals cannot be decided from this dashboard.",
        hint: "Install the engine (`pip install -e app/engine`) on the host running the dashboard, or use `helixis proposal` in a terminal.",
      },
      { status: 503 },
    );
  }

  if (!res.ok) {
    // A non-zero exit on approval means the engine REJECTED the draft, and it
    // has already re-pended the proposal with the validator's own words
    // attached (Req 2.2). Surface that wording rather than a generic failure —
    // it is the only thing that tells the operator what to fix.
    const detail = [res.stdout, res.stderr].filter(Boolean).join("\n").trim();
    return NextResponse.json(
      { ok: false, error: detail || res.message, output: detail },
      { status: 400 },
    );
  }

  return NextResponse.json({
    ok: true,
    action,
    id,
    output: res.stdout.trim().slice(0, 4000),
  });
}
