"use client";

import { useState } from "react";

/**
 * Approve or reject a mined task proposal, inline.
 *
 * Mirrors ProposalDecisionButtons rather than sharing with it: the two act on
 * different tables through different CLIs, and the copy differs in the way that
 * matters — approving a policy rule changes the containment boundary now,
 * whereas approving a task adds something that still cannot run until a human
 * finishes its verifier. Collapsing them would cost that distinction.
 */

type Decision = "approve" | "reject";

export function TaskProposalDecisionButtons({
  id,
  onDecided,
}: {
  id: string;
  onDecided: () => void;
}) {
  const [busy, setBusy] = useState<Decision | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function decide(action: Decision) {
    setBusy(action);
    setError(null);
    try {
      const res = await fetch(`/api/task-proposals/${encodeURIComponent(id)}`, {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ action }),
      });
      const json = await res.json();
      if (!res.ok) throw new Error(json.error ?? `${res.status}`);
      onDecided();
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(null);
    }
  }

  return (
    <div className="mt-2 flex flex-col gap-1.5">
      <div className="flex items-center gap-1.5">
        <button
          type="button"
          onClick={() => decide("approve")}
          disabled={busy !== null}
          className="btn-primary rounded-md px-2.5 py-1 text-[11px] font-medium disabled:opacity-40"
          title="Adds the task to your manifest as a draft. It cannot run until you finish its verifier."
        >
          {busy === "approve" ? "…" : "Add to curriculum"}
        </button>
        <button
          type="button"
          onClick={() => decide("reject")}
          disabled={busy !== null}
          className="rounded-md border border-hairline-strong px-2.5 py-1 text-[11px] font-semibold text-ink transition-colors hover:bg-sunken disabled:opacity-40"
          title="Rejects it and suppresses this workflow so it is not proposed again."
        >
          {busy === "reject" ? "…" : "Not useful"}
        </button>
      </div>
      {error ? (
        <pre
          className="max-h-28 overflow-auto whitespace-pre-wrap text-[10px] leading-relaxed"
          style={{ color: "var(--status-critical)" }}
        >
          {error}
        </pre>
      ) : null}
    </div>
  );
}
