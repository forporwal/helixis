"use client";

import { ShieldCheck } from "lucide-react";
import { ProposalDecisionButtons } from "./ProposalDecisionButtons";
import { Card, EmptyState } from "./ui";
import type { PolicyEvent, PolicyResponse, Proposal } from "@/lib/types";

/**
 * Containment feed: denials, prover verdicts, and pending human approvals.
 *
 * Status color never carries meaning alone -- every severity ships with a label,
 * and honeypot hits additionally get a marked left rail and an explicit
 * "HONEYPOT" tag so they are distinguishable without relying on hue.
 */

const SEVERITY: Record<string, { color: string; label: string }> = {
  LOW: { color: "var(--status-good)", label: "low" },
  MED: { color: "var(--status-warning)", label: "medium" },
  HIGH: { color: "var(--status-serious)", label: "high" },
  CRIT: { color: "var(--status-critical)", label: "critical" },
};

function severityOf(s: string) {
  return SEVERITY[s?.toUpperCase()] ?? { color: "var(--text-muted)", label: s || "unknown" };
}

function timeOf(ts: string) {
  const d = new Date(ts);
  return Number.isNaN(d.getTime()) ? ts : d.toLocaleTimeString();
}

function EventRow({ e }: { e: PolicyEvent }) {
  const sev = severityOf(e.severity);
  return (
    <li
      className="flex gap-2.5 border-b border-hairline py-2 last:border-0"
      style={
        e.isHoneypot
          ? {
              borderLeft: "3px solid var(--status-critical)",
              paddingLeft: 8,
              background: "var(--surface-sunken)",
            }
          : { borderLeft: "3px solid transparent", paddingLeft: 8 }
      }
    >
      <span
        aria-hidden
        className="mt-1.5 size-2 shrink-0 rounded-full"
        style={{ background: sev.color }}
      />
      <div className="min-w-0 flex-1">
        <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
          <span className="font-mono text-[11px] font-semibold text-ink">{e.kind}</span>
          <span className="rounded border border-hairline px-1 py-0.5 text-[10px] font-medium text-ink-secondary">
            {e.action || "—"}
          </span>
          <span className="text-[10px] text-ink-muted">{sev.label}</span>
          {e.isHoneypot ? (
            <span
              className="rounded px-1 py-0.5 text-[10px] font-bold tracking-wide"
              style={{ background: "var(--status-critical)", color: "#fff" }}
            >
              ⚠ HONEYPOT
            </span>
          ) : null}
          <span
            className="ml-auto text-[10px] text-ink-muted"
            style={{ fontVariantNumeric: "tabular-nums" }}
          >
            {timeOf(e.ts)}
          </span>
        </div>
        <p className="mt-0.5 truncate text-[11px] text-ink-secondary">
          {e.actor ? <span className="text-ink-muted">{e.actor} → </span> : null}
          {e.dstHost}
          {e.dstPort ? `:${e.dstPort}` : ""}
          {e.reason ? <span className="text-ink-muted"> · {e.reason}</span> : null}
        </p>
      </div>
    </li>
  );
}

function ProposalRow({
  p,
  onDecided,
}: {
  p: Proposal;
  onDecided: () => void;
}) {
  const pending = p.status === "pending";
  const findings = p.proverFindings.length;

  return (
    <li className="border-b border-hairline py-2.5 last:border-0">
      <div className="flex flex-wrap items-center gap-2">
        <span className="font-mono text-[11px] font-semibold text-ink">
          {p.ruleName || p.chunkId}
        </span>
        <span
          className="rounded-full px-1.5 py-0.5 text-[10px] font-medium"
          style={{
            background: pending ? "var(--surface-sunken)" : "transparent",
            border: "1px solid var(--border)",
            color: "var(--text-secondary)",
          }}
        >
          {p.status}
        </span>
        {p.requiresHuman && pending ? (
          <span className="text-[10px] text-ink-muted">needs human approval</span>
        ) : null}
      </div>

      <p className="mt-1 text-[11px] leading-relaxed text-ink-secondary">
        {p.intentSummary || "No intent summary recorded."}
      </p>

      <p className="mt-1 text-[10px] text-ink-muted">
        prover: {findings} finding{findings === 1 ? "" : "s"}
        {p.rejectionReason ? ` · rejected: ${p.rejectionReason}` : ""}
      </p>

      {pending ? (
        <ProposalDecisionButtons chunkId={p.chunkId} onDecided={onDecided} />
      ) : null}
    </li>
  );
}

export function ContainmentFeed({
  data,
  refreshing,
  onRefetch,
}: {
  data: PolicyResponse | null;
  refreshing: boolean;
  onRefetch: () => void;
}) {
  const hasAny = data && !data.empty;

  return (
    <Card
      title="Containment"
      subtitle={
        hasAny
          ? `${data.counts.denials} denials · ${data.counts.honeypot} honeypot · ${data.counts.pending} awaiting approval`
          : "Policy denials, prover verdicts and pending approvals."
      }
      refreshing={refreshing}
    >
      {!hasAny ? (
        <EmptyState
          icon={ShieldCheck}
          title="No policy events yet"
          hint="The boundary has not been tested. Events appear when the gateway denies a call or the agent files a policy proposal — run `helixis tail-policy` to ingest OpenShell logs."
        />
      ) : (
        <div className="flex flex-col gap-4">
          {data.proposals.length ? (
            <div>
              <h3 className="mb-1 text-xs font-semibold uppercase tracking-wide text-ink-secondary">
                Proposals
              </h3>
              <ul className="max-h-[220px] overflow-y-auto">
                {data.proposals.map((p) => (
                  <ProposalRow key={p.chunkId} p={p} onDecided={onRefetch} />
                ))}
              </ul>
            </div>
          ) : null}

          {data.events.length ? (
            <div>
              <h3 className="mb-1 text-xs font-semibold uppercase tracking-wide text-ink-secondary">
                Recent events
              </h3>
              <ul className="max-h-[260px] overflow-y-auto">
                {data.events.map((e) => (
                  <EventRow key={e.id} e={e} />
                ))}
              </ul>
            </div>
          ) : null}
        </div>
      )}
    </Card>
  );
}
