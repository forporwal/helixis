"use client";

import Link from "next/link";
import { ArrowRight, TrendingUp } from "lucide-react";
import { fmtPct } from "./ui";
import type { CurveDelta, CurveResponse } from "@/lib/types";

/**
 * Home's one glance at the training result — and only a glance.
 *
 * The full curve lives on Lab now. What belongs here is the claim in one line
 * ("held-out partial credit went up N points") plus enough shape to see that it
 * is a trend rather than a single lucky epoch. Deliberately *not* a second
 * chart: home is for acting, and two charts would make it a dashboard again.
 */

const SPLIT_LABEL: Record<string, string> = {
  train: "Train",
  heldout: "Held-out",
  real: "Real",
};

const W = 132;
const H = 34;

/**
 * One series, so no legend — the card's own text names it. No per-point labels
 * either; the endpoint value is stated numerically right beside the sparkline.
 */
function Sparkline({ values, color }: { values: number[]; color: string }) {
  if (values.length < 2) return null;

  // A rate sparkline is scaled to its own range, not 0..1: over five epochs a
  // 12-point rise on a 0..1 axis is a flat line, which would understate the
  // very thing the card exists to show. The exact numbers are stated as text
  // next to it, and the full-axis chart is one click away on Lab.
  const min = Math.min(...values);
  const max = Math.max(...values);
  const flat = max === min;
  const pad = 3;
  const x = (i: number) => (i / (values.length - 1)) * (W - pad * 2) + pad;
  // A series that never moved is drawn through the middle, not pinned to the
  // floor — a flat line along the bottom edge reads as an axis, i.e. as no data
  // at all, when what it actually means is "measured, and unchanged".
  const y = (v: number) => (flat ? H / 2 : H - pad - ((v - min) / (max - min)) * (H - pad * 2));

  const d = values.map((v, i) => `${i === 0 ? "M" : "L"}${x(i)},${y(v)}`).join(" ");
  const lastX = x(values.length - 1);
  const lastY = y(values[values.length - 1]);

  return (
    <svg
      width={W}
      height={H}
      viewBox={`0 0 ${W} ${H}`}
      aria-hidden
      className="block shrink-0 overflow-visible"
    >
      <path d={d} fill="none" stroke={color} strokeWidth={2} strokeLinejoin="round" strokeLinecap="round" />
      {/* Only the endpoint is marked, with a 2px surface ring so it stays
          legible where it sits against the line. */}
      <circle cx={lastX} cy={lastY} r={3} fill={color} stroke="var(--surface-1)" strokeWidth={2} />
    </svg>
  );
}

export function LearningDelta({ data }: { data: CurveResponse | null }) {
  // Held-out is the transfer claim, so it leads when present — same precedence
  // as the full curve on Lab, so the two never disagree about the headline.
  const delta: CurveDelta | undefined =
    data?.deltas.find((d) => d.split === "heldout") ??
    data?.deltas.find((d) => d.split === "train");

  const series = delta ? data?.series.find((s) => s.split === delta.split) : undefined;
  const values = series?.points.map((p) => p.meanPartialCredit) ?? [];
  const color =
    delta?.split === "heldout" ? "var(--series-heldout)" : "var(--series-train)";

  const empty = !data || data.empty || !delta;
  const d = delta?.partialCreditDelta ?? 0;
  const sign = d >= 0 ? "+" : "−";

  return (
    <Link
      href="/lab"
      className="group flex items-center gap-4 rounded-2xl border border-hairline bg-surface px-5 py-4 transition-colors hover:border-hairline-strong"
      style={{ boxShadow: "var(--shadow-card)" }}
    >
      <span
        aria-hidden
        className="flex size-9 shrink-0 items-center justify-center rounded-xl text-ink-muted"
        style={{ background: "var(--surface-sunken)" }}
      >
        <TrendingUp className="size-4" />
      </span>

      <div className="min-w-0 flex-1">
        <p className="text-[11px] font-medium uppercase tracking-wider text-ink-muted">
          Learning
        </p>
        {empty ? (
          <p className="mt-0.5 text-sm text-ink-secondary">
            No graded episodes yet — run an epoch in the Lab.
          </p>
        ) : (
          <>
            <p className="mt-0.5 text-sm text-ink">
              <span className="text-lg font-semibold tracking-tight tabular-nums">
                {sign}
                {Math.abs(d * 100).toFixed(1)} pts
              </span>{" "}
              <span className="text-ink-secondary">
                {SPLIT_LABEL[delta.split] ?? delta.split} mean partial credit
              </span>
            </p>
            <p className="mt-0.5 text-[11px] tabular-nums text-ink-muted">
              {fmtPct(delta.partialCreditFrom)} → {fmtPct(delta.partialCreditTo)} · epoch{" "}
              {delta.firstEpoch}→{delta.lastEpoch}
            </p>
          </>
        )}
      </div>

      {empty ? null : <Sparkline values={values} color={color} />}

      <span className="inline-flex shrink-0 items-center gap-1 text-[11px] font-medium text-primary">
        Full curve
        <ArrowRight aria-hidden className="size-3 transition-transform group-hover:translate-x-0.5" />
      </span>
    </Link>
  );
}
