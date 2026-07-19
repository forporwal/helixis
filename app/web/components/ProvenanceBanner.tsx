"use client";

import type { ProvenanceInfo } from "@/lib/types";

/**
 * The honesty banner.
 *
 * If any episode on screen came from the offline simulator rather than a graded
 * model run, the numbers below are NOT experimental results and this says so
 * before the reader gets to the curve. It is deliberately loud and deliberately
 * not dismissible -- a judge must not be able to mistake simulated output for
 * a measured result.
 */
export function ProvenanceBanner({ provenance }: { provenance: ProvenanceInfo | undefined }) {
  if (!provenance || !provenance.simulated) return null;

  const partial = !provenance.allSimulated;

  return (
    <div
      role="alert"
      className="rounded-xl border px-5 py-4"
      style={{
        borderColor: "var(--status-critical)",
        borderLeftWidth: 4,
        background: "var(--surface-sunken)",
      }}
    >
      <div className="flex items-start gap-3">
        <span aria-hidden className="text-lg leading-none">
          ⚠
        </span>
        <div className="min-w-0">
          <h2 className="text-sm font-bold tracking-tight text-ink">
            {partial
              ? "Partly simulated data — not an experimental result"
              : "Simulated data — these are not experimental results"}
          </h2>
          <p className="mt-1 text-xs leading-relaxed text-ink-secondary">
            {partial ? (
              <>
                {provenance.simulatedCount} of {provenance.inspected} inspected episodes were
                produced by the offline simulator, not by a graded model run. Every metric
                on this page mixes simulated and real episodes and must not be reported as
                a measured outcome.
              </>
            ) : (
              <>
                Every inspected episode ({provenance.inspected}) carries{" "}
                <code className="font-mono">simulated: true</code> in its trajectory
                metadata. The engine generated these scores offline without calling a
                graded model, so the learning curve below demonstrates that the pipeline
                runs end to end — it does <strong className="font-semibold text-ink">not</strong>{" "}
                show that the agent learned anything. Re-run with live inference before
                reporting any number here.
              </>
            )}
          </p>
          {provenance.sources.length ? (
            <p className="mt-1 text-[11px] text-ink-muted">
              Determined from: {provenance.sources.join("; ")}.
            </p>
          ) : null}
        </div>
      </div>
    </div>
  );
}
