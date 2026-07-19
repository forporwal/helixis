import { NextResponse } from "next/server";
import { run } from "@/lib/cli";
import { REPO_ROOT_DIR } from "@/lib/paths";
import type { PreflightResponse } from "@/lib/types";

export const dynamic = "force-dynamic";

/**
 * What each training mode would actually do, straight from the engine.
 *
 * This shells out to `helixis preflight --json` rather than re-deriving mode
 * selection from env vars here. `EpochRunner._default_backend` is the only
 * thing that decides simulated-vs-real; a second implementation in TypeScript
 * would eventually disagree with it, and the whole point of this endpoint is to
 * stop the UI from lying about which backend a click will reach.
 *
 * A missing CLI is an empty state, not an error: the page still renders, it
 * just cannot promise what Start would do.
 */
export async function GET() {
  const res = await run("helixis", ["preflight", "--json"], {
    cwd: REPO_ROOT_DIR,
    timeoutMs: 20_000,
  });

  // Always 200, even when the engine is unreachable. "I cannot tell you what a
  // run would do, and here is why" is a legitimate answer to this question, and
  // the poller on the client treats any non-2xx as a transport error — it holds
  // `data` at null, so an error status would leave the card spinning on
  // "Checking engine…" forever instead of showing the reason.
  if (!res.ok) {
    return NextResponse.json({
      available: false,
      error:
        res.kind === "missing"
          ? "The `helixis` CLI is not on PATH for the dashboard process, so it cannot report what a run would do — or start one."
          : res.message,
      preflight: null,
    } satisfies PreflightResponse);
  }

  try {
    return NextResponse.json({
      available: true,
      error: null,
      preflight: JSON.parse(res.stdout),
    } satisfies PreflightResponse);
  } catch {
    return NextResponse.json({
      available: false,
      error: "The engine returned output that is not valid preflight JSON.",
      preflight: null,
    } satisfies PreflightResponse);
  }
}
