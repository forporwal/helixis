"use client";

import { useState } from "react";
import { Play } from "lucide-react";

/**
 * The nudge's one-click trigger: ingest real sessions, distill, regenerate pages.
 *
 * This POSTs directly rather than routing to Lab for confirmation the way
 * `StartRunAction` does, and the difference is deliberate. A benchmark run costs
 * agent-tier money and makes the operator choose an epoch; a train-cycle spends
 * only the local vLLM tier on evidence that has already been collected, and the
 * spec asks for one click precisely because the loop closes or it doesn't
 * (Req 4.1). The job then streams in Lab's jobs panel like every other run, so
 * "one click" never means "and now you can't see what it's doing".
 */
export function TrainCycleButton({ onStarted }: { onStarted?: () => void }) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [started, setStarted] = useState(false);

  async function start() {
    setBusy(true);
    setError(null);
    try {
      const res = await fetch("/api/control", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ action: "train-cycle" }),
      });
      const body = await res.json().catch(() => ({}));
      if (!res.ok || !body.ok) {
        setError(body.error ?? `Could not start training (HTTP ${res.status}).`);
        return;
      }
      setStarted(true);
      onStarted?.();
    } catch {
      setError("Could not reach the dashboard API.");
    } finally {
      setBusy(false);
    }
  }

  if (started) {
    return (
      <p className="mt-2 text-[11px] text-ink-secondary">
        Training started — follow it in the Lab jobs panel.
      </p>
    );
  }

  return (
    <>
      <div className="mt-2">
        <button
          type="button"
          disabled={busy}
          onClick={start}
          className="inline-flex items-center gap-1.5 rounded-md px-2.5 py-1 text-xs font-semibold text-white transition-opacity disabled:opacity-50"
          style={{ background: "var(--status-good)" }}
        >
          <Play aria-hidden className="size-3" />
          {busy ? "Starting…" : "Train now"}
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
