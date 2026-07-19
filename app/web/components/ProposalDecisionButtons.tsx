"use client";

import { useProposalActions } from "./useProposalActions";

/**
 * The approve/reject pair, wherever a pending proposal is shown.
 *
 * Approve is the affirmative fill and reject is the quiet outline, in that
 * order, on both Containment and Home — a review control that changes shape
 * between surfaces makes an operator re-read it every time. Errors land under
 * the buttons on the row that failed, so a rejected CLI call never looks like a
 * successful one.
 */
export function ProposalDecisionButtons({
  chunkId,
  onDecided,
}: {
  chunkId: string;
  onDecided: () => void;
}) {
  const { busy, error, decide } = useProposalActions(chunkId, onDecided);

  return (
    <>
      <div className="mt-2 flex items-center gap-2">
        <button
          type="button"
          disabled={busy !== null}
          onClick={() => decide("approve")}
          className="rounded-md px-2.5 py-1 text-xs font-semibold text-white transition-opacity disabled:opacity-50"
          style={{ background: "var(--status-good)" }}
        >
          {busy === "approve" ? "Approving…" : "Approve"}
        </button>
        <button
          type="button"
          disabled={busy !== null}
          onClick={() => decide("reject")}
          className="rounded-md border border-hairline-strong px-2.5 py-1 text-xs font-semibold text-ink transition-colors hover:bg-sunken disabled:opacity-50"
        >
          {busy === "reject" ? "Rejecting…" : "Reject"}
        </button>
      </div>

      {error ? (
        <p className="mt-1.5 text-[11px]" style={{ color: "var(--status-critical)" }}>
          {error}
        </p>
      ) : null}
    </>
  );
}
