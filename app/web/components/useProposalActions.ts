"use client";

import { useState } from "react";

export type ProposalDecision = "approve" | "reject";

/**
 * Approve/reject a policy proposal through the one hardened path.
 *
 * Extracted from ContainmentFeed because home's action feed now offers the same
 * decision inline: two copies of a mutation call is exactly the kind of
 * duplication that lets one of them drift out of step with the API's error
 * shape. The hook owns busy/error state per row, so a failed decision reports
 * on the row that failed rather than as a page-level toast.
 */
export function useProposalActions(chunkId: string, onDecided: () => void) {
  const [busy, setBusy] = useState<ProposalDecision | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function decide(action: ProposalDecision) {
    setBusy(action);
    setError(null);
    try {
      const res = await fetch(`/api/proposals/${encodeURIComponent(chunkId)}`, {
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

  return { busy, error, decide };
}
