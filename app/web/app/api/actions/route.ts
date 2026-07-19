import { NextResponse } from "next/server";
import { dbExists, parseJson, query } from "@/lib/db";
import { getRecentTrainCycle, getTrainReadiness } from "@/lib/readiness";
import { listTaskProposals } from "@/lib/taskProposals";
import type { ActionItem, ActionsResponse, Proposal } from "@/lib/types";

export const dynamic = "force-dynamic";

/**
 * Everything that wants a human, in one sorted list.
 *
 * Strictly read-only: the rows act through the two hardened mutation paths that
 * already exist (`/api/proposals/[chunkId]` → openshell, `/api/control` →
 * helixis). Aggregating here rather than in the client means home makes one
 * request instead of one per source, and the sort order — the thing that
 * decides what an operator sees first — is defined once, server-side.
 *
 * v1 emits `policy-proposal` only. Spec 03's train nudge and spec 05's task
 * proposals append their own members to the union and their own blocks here;
 * the feed already renders whatever arrives, so neither needs UI work in this
 * spec beyond its own row.
 */

type ProposalRow = {
  chunk_id: string;
  rule_name: string;
  intent_summary: string;
  status: string;
  prover_findings: string;
  requires_human: number;
  rejection_reason: string | null;
  created_at: string;
  decided_at: string | null;
};

/** Needs-human first, then nudges, then informational (Req 2.5). */
const KIND_RANK: Record<ActionItem["kind"], number> = {
  "policy-proposal": 0,
  "task-proposal": 1,
  "train-nudge": 2,
  "skills-live": 3,
};

export function GET() {
  const rows = query<ProposalRow>(
    `SELECT chunk_id, rule_name, intent_summary, status, prover_findings,
            requires_human, rejection_reason, created_at, decided_at
     FROM proposals
     WHERE status = 'pending'
     ORDER BY created_at DESC
     LIMIT 50`,
  );

  const items: ActionItem[] = rows.map((r) => {
    const proposal: Proposal = {
      chunkId: r.chunk_id,
      ruleName: r.rule_name,
      intentSummary: r.intent_summary,
      status: r.status,
      proverFindings: parseJson<unknown[]>(r.prover_findings, []),
      requiresHuman: r.requires_human === 1,
      rejectionReason: r.rejection_reason,
      createdAt: r.created_at,
      decidedAt: r.decided_at,
    };
    return {
      kind: "policy-proposal",
      id: `policy-proposal:${r.chunk_id}`,
      href: "/containment",
      needsHuman: proposal.requiresHuman,
      createdAt: r.created_at,
      proposal,
    };
  });

  // Tasks the miner drafted from real usage (spec 05, Req 2.1). Every one wants
  // a human by construction — the miner proposes and never enacts, so a pending
  // proposal is a decision nobody has made yet.
  for (const p of listTaskProposals("pending")) {
    items.push({
      kind: "task-proposal",
      id: `task-proposal:${p.id}`,
      href: `/proposals/tasks/${encodeURIComponent(p.id)}`,
      needsHuman: true,
      createdAt: p.createdAt,
      title: p.title || p.id,
      domain: p.domain,
      taskId: p.id,
      occurrences: p.occurrences,
      taskType: p.taskType,
    });
  }

  // Ready to train (spec 03, Req 4.1). Shown only at the threshold: below it
  // there is nothing to decide, and a feed that nags every time one session
  // lands is a feed the operator learns to ignore.
  const readiness = getTrainReadiness();
  if (readiness.ready) {
    items.push({
      kind: "train-nudge",
      id: `train-nudge:${readiness.lastDistillAt ?? "initial"}`,
      href: "/lab",
      needsHuman: false,
      // The nudge is as old as the backlog it describes, so it sorts stably
      // against other items instead of jumping to "now" on every poll.
      createdAt: readiness.lastDistillAt ?? new Date(0).toISOString(),
      newRealEpisodes: readiness.newRealEpisodes,
      threshold: readiness.threshold,
      autoTrain: readiness.autoTrain,
    });
  }

  // "N new skills live" (Req 4.4) — the other end of the same loop.
  const cycle = getRecentTrainCycle();
  if (cycle) {
    items.push({
      kind: "skills-live",
      id: `skills-live:${cycle.ts}`,
      href: "/wiki",
      needsHuman: false,
      createdAt: cycle.ts,
      skills: cycle.skills,
      generation: cycle.generation,
    });
  }

  // Within a rank, an item that explicitly wants a human outranks one that does
  // not, and newer outranks older — a queue that reorders under the cursor is
  // worse than a stale one, so the order is fully determined by stored fields.
  items.sort(
    (a, b) =>
      KIND_RANK[a.kind] - KIND_RANK[b.kind] ||
      Number(b.needsHuman) - Number(a.needsHuman) ||
      b.createdAt.localeCompare(a.createdAt),
  );

  const body: ActionsResponse = {
    items,
    counts: {
      needsHuman: items.filter((i) => i.needsHuman).length,
      total: items.length,
    },
    dbPresent: dbExists(),
    empty: items.length === 0,
  };
  return NextResponse.json(body);
}
