import { NextResponse } from "next/server";
import { parseJson, query } from "@/lib/db";
import type { PolicyEvent, PolicyResponse, Proposal } from "@/lib/types";

export const dynamic = "force-dynamic";

type EventRow = {
  id: number;
  ts: string;
  kind: string;
  severity: string;
  action: string;
  actor: string;
  dst_host: string;
  dst_port: number | null;
  reason: string;
  is_honeypot: number;
};

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

export function GET() {
  const eventRows = query<EventRow>(
    `SELECT id, ts, kind, severity, action, actor, dst_host, dst_port, reason, is_honeypot
     FROM policy_events ORDER BY ts DESC LIMIT 100`,
  );
  const events: PolicyEvent[] = eventRows.map((r) => ({
    id: r.id,
    ts: r.ts,
    kind: r.kind,
    severity: r.severity,
    action: r.action,
    actor: r.actor,
    dstHost: r.dst_host,
    dstPort: r.dst_port,
    reason: r.reason,
    isHoneypot: r.is_honeypot === 1,
  }));

  const proposalRows = query<ProposalRow>(
    `SELECT chunk_id, rule_name, intent_summary, status, prover_findings,
            requires_human, rejection_reason, created_at, decided_at
     FROM proposals
     ORDER BY (status='pending') DESC, created_at DESC
     LIMIT 100`,
  );
  const proposals: Proposal[] = proposalRows.map((r) => ({
    chunkId: r.chunk_id,
    ruleName: r.rule_name,
    intentSummary: r.intent_summary,
    status: r.status,
    proverFindings: parseJson<unknown[]>(r.prover_findings, []),
    requiresHuman: r.requires_human === 1,
    rejectionReason: r.rejection_reason,
    createdAt: r.created_at,
    decidedAt: r.decided_at,
  }));

  const body: PolicyResponse = {
    events,
    proposals,
    counts: {
      denials: events.filter((e) => e.action.toUpperCase().includes("DEN")).length,
      honeypot: events.filter((e) => e.isHoneypot).length,
      pending: proposals.filter((p) => p.status === "pending").length,
    },
    empty: events.length === 0 && proposals.length === 0,
  };
  return NextResponse.json(body);
}
