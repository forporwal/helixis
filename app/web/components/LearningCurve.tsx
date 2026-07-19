"use client";

import { useState } from "react";
import { LineChart, type Series } from "./LineChart";
import { TrendingUp } from "lucide-react";
import { Card, EmptyState, Legend, fmtPct } from "./ui";
import { StartRunAction } from "./StartRunAction";
import type { CurriculumEvent, CurveDelta, CurveResponse, Split } from "@/lib/types";

const SPLIT_COLOR: Record<string, string> = {
  train: "var(--series-train)",
  heldout: "var(--series-heldout)",
  real: "var(--text-muted)",
};

const SPLIT_LABEL: Record<string, string> = {
  train: "Train",
  heldout: "Held-out",
  real: "Real",
};

function toSeries(
  series: CurveResponse["series"],
  measure: "meanPartialCredit" | "passRate",
): Series[] {
  return series.map((s) => ({
    key: `${s.split}-${measure}`,
    label: SPLIT_LABEL[s.split] ?? s.split,
    color: SPLIT_COLOR[s.split] ?? "var(--text-muted)",
    points: s.points.map((p) => ({ x: p.epoch, y: p[measure], n: p.n })),
  }));
}

/**
 * The honesty guard for a mutable curriculum (Requirement 3.2).
 *
 * The default series is the frozen bench set, which is comparable across every
 * epoch. The full-curriculum series is available, but it can only ever be shown
 * with this banner: it is a mean over a task set that CHANGED between the
 * epochs being compared, and without saying where it changed, adding three easy
 * tasks at epoch 4 looks identical to the agent getting better.
 */
function CurriculumNotice({ events }: { events: CurriculumEvent[] }) {
  if (events.length === 0) {
    return (
      <p className="rounded-md border border-hairline px-3 py-2 text-[11px] leading-relaxed text-ink-muted">
        The task set has not changed, so this series matches the frozen-bench headline.
      </p>
    );
  }
  const byEpoch = new Map<string, CurriculumEvent[]>();
  for (const e of events) {
    const key = e.epoch === null ? "before the first epoch" : `after epoch ${e.epoch}`;
    byEpoch.set(key, [...(byEpoch.get(key) ?? []), e]);
  }
  return (
    <div
      className="rounded-md border px-3 py-2 text-[11px] leading-relaxed"
      style={{ borderColor: "var(--status-warning)", color: "var(--text-secondary)" }}
    >
      <p className="font-semibold text-ink">
        Curriculum changed — points on this series are not directly comparable.
      </p>
      <ul className="mt-1 space-y-0.5">
        {[...byEpoch.entries()].map(([when, list]) => (
          <li key={when}>
            <span className="text-ink-muted">{when}:</span>{" "}
            {list.map((e) => `${e.action} ${e.taskId} (${e.split})`).join(", ")}
          </li>
        ))}
      </ul>
      <p className="mt-1 text-ink-muted">
        The headline numbers above stay on the frozen bench set precisely so this cannot
        move them.
      </p>
    </div>
  );
}

/** The delta IS the claim, so it gets the hero treatment and states its own basis. */
function DeltaTile({ delta, primary }: { delta: CurveDelta; primary: boolean }) {
  const d = delta.partialCreditDelta;
  const sign = d >= 0 ? "+" : "−";
  const color = SPLIT_COLOR[delta.split] ?? "var(--text-muted)";
  return (
    <div
      className={`rounded-lg border border-hairline px-4 py-3 ${primary ? "bg-sunken" : ""}`}
    >
      <div className="flex items-center gap-1.5">
        <span aria-hidden className="h-0.5 w-3 rounded-full" style={{ background: color }} />
        <span className="text-xs font-medium text-ink-secondary">
          {SPLIT_LABEL[delta.split] ?? delta.split} · mean partial credit
        </span>
      </div>
      <div
        className={`mt-1 font-semibold tracking-tight text-ink ${primary ? "text-5xl" : "text-3xl"}`}
      >
        {sign}
        {Math.abs(d * 100).toFixed(1)}
        <span className={primary ? "text-2xl" : "text-lg"}> pts</span>
      </div>
      <div className="mt-1 text-xs text-ink-muted" style={{ fontVariantNumeric: "tabular-nums" }}>
        {fmtPct(delta.partialCreditFrom)} → {fmtPct(delta.partialCreditTo)}
        <span className="text-ink-muted"> · epoch {delta.firstEpoch}→{delta.lastEpoch}</span>
      </div>
      <div className="mt-0.5 text-xs text-ink-muted" style={{ fontVariantNumeric: "tabular-nums" }}>
        pass rate {fmtPct(delta.passRateFrom, 0)} → {fmtPct(delta.passRateTo, 0)}
      </div>
    </div>
  );
}

export function LearningCurve({
  data,
  refreshing,
}: {
  data: CurveResponse | null;
  refreshing: boolean;
}) {
  const [showTable, setShowTable] = useState(false);
  // Frozen bench is the DEFAULT and stays the headline. The full-curriculum
  // view is opt-in and always annotated — never the number a judge lands on.
  const [showFull, setShowFull] = useState(false);

  if (!data || data.empty) {
    return (
      <Card
        title="Learning curve"
        subtitle="Mean partial credit and pass rate per epoch, train vs held-out."
        className="lg:col-span-2"
      >
        <EmptyState
          icon={TrendingUp}
          title="No graded episodes yet"
          hint="The curve appears once the first epoch writes episodes to runs/helixis.db."
          action={<StartRunAction />}
        />
      </Card>
    );
  }

  // Held-out is the transfer claim, so it leads when present.
  const heldout = data.deltas.find((d) => d.split === "heldout");
  const train = data.deltas.find((d) => d.split === "train");
  const hero = heldout ?? train;
  const secondary = data.deltas.filter((d) => d !== hero);

  // The toggle only exists once there is something for it to reveal.
  const hasUserWork = data.excludedUserEpisodes > 0 || data.curriculumEvents.length > 0;
  const active = showFull && hasUserWork ? data.fullSeries : data.series;
  const pcSeries = toSeries(active, "meanPartialCredit");
  const prSeries = toSeries(active, "passRate");
  const baselineSplit = hero?.split as Split | undefined;

  // A flat line at zero and a chart that has no data look identical, and the
  // second reading is the wrong one — say which this is.
  const allZero =
    active.some((s) => s.points.length > 0) &&
    active.every((s) =>
      s.points.every((p) => p.meanPartialCredit === 0 && p.passRate === 0),
    );

  return (
    <Card
      title="Learning curve"
      subtitle={
        <>
          Mean partial credit and pass rate per epoch, train vs held-out, over the{" "}
          <strong className="font-semibold text-ink-secondary">frozen bench set</strong>.
          Real-tier episodes are excluded from these headline metrics by design
          {data.excludedRealEpisodes > 0 ? ` (${data.excludedRealEpisodes} excluded)` : ""}
          {hasUserWork
            ? `, and so are your own tasks (${data.excludedUserEpisodes} episode${data.excludedUserEpisodes === 1 ? "" : "s"}) — a curriculum you can change must never move the headline`
            : ""}
          .
        </>
      }
      className="lg:col-span-2"
      refreshing={refreshing}
      action={
        <div className="flex items-center gap-1.5">
          {hasUserWork ? (
            <button
              type="button"
              onClick={() => setShowFull((v) => !v)}
              aria-pressed={showFull}
              className={`rounded-md border px-2.5 py-1 text-xs transition-colors ${
                showFull
                  ? "border-transparent bg-primary font-semibold text-on-primary"
                  : "border-hairline font-medium text-ink-secondary hover:bg-sunken"
              }`}
              title="Include your own bench-type tasks. Annotated wherever the task set changed."
            >
              Full curriculum
            </button>
          ) : null}
          <button
            type="button"
            onClick={() => setShowTable((v) => !v)}
            className="rounded-md border border-hairline px-2.5 py-1 text-xs font-medium text-ink-secondary transition-colors hover:bg-sunken"
          >
            {showTable ? "Chart" : "Table"}
          </button>
        </div>
      }
    >
      <div className="flex flex-col gap-5">
        {allZero ? (
          <p
            className="rounded-lg border border-hairline px-3 py-2 text-[11px] leading-relaxed"
            style={{ color: "var(--text-secondary)" }}
          >
            Every graded episode so far scored zero, so the lines below sit flat on
            the axis. That is a real result, not a missing one — the agent has not
            yet completed a bench task. Open a task in the grid below to see where
            its run stopped.
          </p>
        ) : null}

        <div className="grid gap-3 sm:grid-cols-3">
          {hero ? <DeltaTile delta={hero} primary /> : null}
          <div className="flex flex-col gap-3 sm:col-span-2">
            <div className="grid gap-3 sm:grid-cols-2">
              {secondary.map((d) => (
                <DeltaTile key={d.split} delta={d} primary={false} />
              ))}
            </div>
            <Legend
              items={active.map((s) => ({
                label: SPLIT_LABEL[s.split] ?? s.split,
                color: SPLIT_COLOR[s.split] ?? "var(--text-muted)",
                note: `${s.points.length} epoch${s.points.length === 1 ? "" : "s"}`,
              }))}
            />
            {/* The tiles always state the frozen-bench claim, even while the
                charts below show the full curriculum — the headline number and
                its basis must never drift apart. */}
            <p className="text-[10px] text-ink-muted">
              Deltas above are computed over the frozen bench set
              {showFull ? "; the charts below show the full curriculum" : ""}.
            </p>
          </div>
        </div>

        {showFull ? <CurriculumNotice events={data.curriculumEvents} /> : null}

        {showTable ? (
          <CurveTable series={active} />
        ) : (
          <div className="flex flex-col gap-4">
            <div>
              <h3 className="mb-1 text-xs font-semibold uppercase tracking-wide text-ink-secondary">
                Mean partial credit
              </h3>
              <LineChart
                series={pcSeries}
                xTicks={data.epochs}
                height={280}
                yFormat={(v) => fmtPct(v, 0)}
                ariaLabel="Mean partial credit per epoch, train and held-out"
                baseline={
                  hero
                    ? { value: hero.partialCreditFrom, label: `epoch ${hero.firstEpoch} baseline (${SPLIT_LABEL[baselineSplit ?? "train"]})` }
                    : null
                }
              />
            </div>
            <div>
              <h3 className="mb-1 text-xs font-semibold uppercase tracking-wide text-ink-secondary">
                Pass rate
              </h3>
              {/* Same y scale (0-100%), drawn as its own chart -- never a second axis. */}
              <LineChart
                series={prSeries}
                xTicks={data.epochs}
                height={160}
                yFormat={(v) => fmtPct(v, 0)}
                ariaLabel="Pass rate per epoch, train and held-out"
                endLabels
              />
            </div>
          </div>
        )}
      </div>
    </Card>
  );
}

function CurveTable({ series }: { series: CurveResponse["series"] }) {
  const rows = series.flatMap((s) => s.points.map((p) => ({ split: s.split, ...p })));
  return (
    <div className="overflow-x-auto">
      <table className="w-full min-w-[520px] text-left text-xs">
        <thead>
          <tr className="border-b border-hairline text-ink-muted">
            <th className="py-2 pr-3 font-medium">Split</th>
            <th className="py-2 pr-3 font-medium">Epoch</th>
            <th className="py-2 pr-3 font-medium">n</th>
            <th className="py-2 pr-3 font-medium">Mean partial credit</th>
            <th className="py-2 pr-3 font-medium">Pass rate</th>
            <th className="py-2 pr-3 font-medium">Cost</th>
          </tr>
        </thead>
        <tbody style={{ fontVariantNumeric: "tabular-nums" }}>
          {rows.map((r) => (
            <tr key={`${r.split}-${r.epoch}`} className="border-b border-hairline last:border-0">
              <td className="py-1.5 pr-3">
                <span className="inline-flex items-center gap-1.5 text-ink">
                  <span
                    aria-hidden
                    className="h-0.5 w-3 rounded-full"
                    style={{ background: SPLIT_COLOR[r.split] }}
                  />
                  {SPLIT_LABEL[r.split] ?? r.split}
                </span>
              </td>
              <td className="py-1.5 pr-3 text-ink-secondary">{r.epoch}</td>
              <td className="py-1.5 pr-3 text-ink-secondary">{r.n}</td>
              <td className="py-1.5 pr-3 text-ink">{fmtPct(r.meanPartialCredit)}</td>
              <td className="py-1.5 pr-3 text-ink">{fmtPct(r.passRate)}</td>
              <td className="py-1.5 pr-3 text-ink-secondary">${r.costUsd.toFixed(3)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
