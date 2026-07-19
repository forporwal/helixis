"use client";

import { ContainmentFeed } from "../ContainmentFeed";
import { PageShell } from "../PageShell";
import { usePoll } from "../usePoll";
import type { PolicyResponse, StatusResponse } from "@/lib/types";

// Poll cadence. Raised from 4s: policy events are the most live thing here (denials arrive as the
// agent runs), so this stays the fastest of the page-level polls.
// Panels hold their previous payload across refetches, so a longer
// interval costs staleness, never a blank panel.
const INTERVAL = 10_000;

/**
 * The boundary. Denials, proposals, prover verdicts, and the human approval
 * control — the live demo beat where the agent is blocked, asks, and is let
 * through only by a person.
 */
export function ContainmentPage() {
  const status = usePoll<StatusResponse>("/api/status", INTERVAL);
  const policy = usePoll<PolicyResponse>("/api/policy", INTERVAL);

  const openshellMissing = status.data
    ? !status.data.controls.openshellAvailable
    : false;

  return (
    <PageShell
      title="Containment"
      intent="Egress denials, policy proposals, and pending human approvals. The agent holds real capability; the boundary is what stops it, not its goodwill."
      provenance={status.data?.provenance}
      refreshSeconds={INTERVAL / 1000}
    >
      {openshellMissing ? (
        <div
          className="rounded-xl border border-hairline px-5 py-4"
          style={{ background: "var(--surface-sunken)" }}
        >
          <h2 className="text-sm font-semibold text-ink">OpenShell CLI not reachable</h2>
          <p className="mt-1 text-xs leading-relaxed text-ink-secondary">
            Approve and reject are disabled because the{" "}
            <code className="font-mono">openshell</code> binary is not on this
            process&rsquo;s PATH. Events below still render from whatever{" "}
            <code className="font-mono">helixis tail-policy</code> has already ingested.
          </p>
        </div>
      ) : null}
      <ContainmentFeed
        data={policy.data}
        refreshing={policy.refreshing}
        onRefetch={() => void policy.refetch()}
      />
    </PageShell>
  );
}
