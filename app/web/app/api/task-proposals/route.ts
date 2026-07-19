import { NextResponse } from "next/server";
import { listTaskProposals } from "@/lib/taskProposals";
import type { TaskProposalsResponse } from "@/lib/types";

export const dynamic = "force-dynamic";

/**
 * Every mined task proposal, newest first.
 *
 * All statuses, not just pending: a rejected proposal is the record of a
 * decision, and its fingerprint suppresses re-proposal (spec 05, Req 2.3).
 * Hiding it would make the miner look like it silently stopped noticing a
 * workflow the operator explicitly turned down.
 */

export function GET(request: Request) {
  const raw = new URL(request.url).searchParams.get("status");
  const status =
    raw && ["pending", "approved", "rejected", "invalid"].includes(raw) ? raw : undefined;

  const proposals = listTaskProposals(status);
  const body: TaskProposalsResponse = {
    proposals,
    counts: {
      // Counted over the FULL table, not the filtered view: the pending count
      // drives a badge, and a badge that changes when you click a filter is
      // reporting the filter, not the work.
      pending: listTaskProposals("pending").length,
      total: proposals.length,
    },
    empty: proposals.length === 0,
  };
  return NextResponse.json(body);
}
