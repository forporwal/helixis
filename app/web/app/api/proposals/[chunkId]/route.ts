import { NextResponse } from "next/server";
import { run } from "@/lib/cli";

export const dynamic = "force-dynamic";

/**
 * Approve or reject a pending policy proposal.
 *
 * This is the one route that mutates the containment boundary, so it is the
 * one route that gets paranoid. The threat is command injection via `chunkId`
 * or `action` reaching a shell.
 *
 * Defenses, in order:
 *   1. `action` must be the literal string "approve" or "reject" — an allow-list,
 *      not a sanitizer. Anything else is 400 before we touch the filesystem.
 *   2. `chunkId` must match /^[A-Za-z0-9_-]{1,64}$/ — no dots, slashes, spaces,
 *      quotes, semicolons, backticks or `$`. Validated BEFORE it is ever passed on.
 *   3. Execution goes through execFile with an argument array and `shell: false`,
 *      so even a hypothetical validation slip cannot become shell metacharacters.
 *   4. `reason` is passed as its own argv element and length-capped; it is never
 *      concatenated into a command string.
 */

const CHUNK_ID_RE = /^[A-Za-z0-9_-]{1,64}$/;
const ACTIONS = new Set(["approve", "reject"]);

// The sandbox name is operator config, not user input, and is validated the same way.
const RAW_SANDBOX = process.env.HELIXIS_SANDBOX ?? "helixis";
const SANDBOX = CHUNK_ID_RE.test(RAW_SANDBOX) ? RAW_SANDBOX : "helixis";

export async function POST(
  request: Request,
  context: { params: Promise<{ chunkId: string }> },
) {
  const { chunkId } = await context.params;

  if (!CHUNK_ID_RE.test(chunkId)) {
    return NextResponse.json(
      { ok: false, error: "Invalid chunkId. Expected 1-64 chars matching [A-Za-z0-9_-]." },
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

  // Free text, but it never meets a shell — argv element only.
  const reason =
    typeof payload.reason === "string" && payload.reason.trim()
      ? payload.reason.trim().slice(0, 500)
      : "rejected from the Helixis dashboard";

  const args =
    action === "approve"
      ? ["rule", "approve", SANDBOX, "--chunk-id", chunkId]
      : ["rule", "reject", SANDBOX, "--chunk-id", chunkId, "--reason", reason];

  const result = await run("openshell", args, { timeoutMs: 120_000 });

  if (!result.ok && result.kind === "missing") {
    return NextResponse.json(
      {
        ok: false,
        error:
          "The `openshell` CLI is not installed or not on PATH, so policy decisions cannot be applied from this dashboard.",
        hint: "Install OpenShell (pinned 0.0.85) on the host running the dashboard, or approve via the CLI directly.",
      },
      { status: 503 },
    );
  }

  if (!result.ok) {
    return NextResponse.json(
      { ok: false, error: result.message, stderr: result.stderr?.slice(0, 2000) ?? "" },
      { status: 502 },
    );
  }

  return NextResponse.json({
    ok: true,
    action,
    chunkId,
    stdout: result.stdout.slice(0, 4000),
  });
}
