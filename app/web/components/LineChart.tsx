"use client";

import { useMemo, useState } from "react";
import { useMeasure } from "./useMeasure";

/**
 * Rate-over-epochs line chart.
 *
 * Mark specs are fixed by the design system: 2px lines with round joins, >=8px
 * markers each carrying a 2px ring in the surface color so overlapping points
 * stay legible, solid hairline gridlines one step off the surface, and direct
 * end-labels rather than a value on every point.
 *
 * The y scale is always 0..1 because every measure plotted here is a rate.
 * That is also why two measures never share one plot with two scales -- they
 * are drawn as separate charts against the same axis instead.
 */

export type Series = {
  key: string;
  label: string;
  color: string;
  points: { x: number; y: number; n?: number }[];
};

type Props = {
  series: Series[];
  height?: number;
  xTicks: number[];
  yFormat: (v: number) => string;
  /** Draw a labelled reference line at the first value, so the rise is a visible quantity. */
  baseline?: { value: number; label: string } | null;
  /** Label the first and last point of each series; off for the compact chart. */
  endLabels?: boolean;
  ariaLabel: string;
};

const PAD = { top: 18, right: 62, bottom: 28, left: 40 };

export function LineChart({
  series,
  height = 300,
  xTicks,
  yFormat,
  baseline = null,
  endLabels = true,
  ariaLabel,
}: Props) {
  const { ref, width } = useMeasure<HTMLDivElement>();
  const [hoverX, setHoverX] = useState<number | null>(null);

  const w = Math.max(width, 280);
  const plotW = Math.max(w - PAD.left - PAD.right, 10);
  const plotH = Math.max(height - PAD.top - PAD.bottom, 10);

  const xMin = Math.min(...xTicks);
  const xMax = Math.max(...xTicks);
  const sx = (x: number) => (xMax === xMin ? PAD.left + plotW / 2 : PAD.left + ((x - xMin) / (xMax - xMin)) * plotW);
  const sy = (y: number) => PAD.top + (1 - Math.max(0, Math.min(1, y))) * plotH;

  const yTicks = [0, 0.25, 0.5, 0.75, 1];

  // Nearest-epoch hover: the hit target is the whole column, never the 8px dot.
  const hovered = useMemo(() => {
    if (hoverX === null) return null;
    let best = xTicks[0];
    let bestD = Infinity;
    for (const t of xTicks) {
      const d = Math.abs(sx(t) - hoverX);
      if (d < bestD) {
        bestD = d;
        best = t;
      }
    }
    return best;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [hoverX, xTicks, w]);

  const hoverRows =
    hovered === null
      ? []
      : series
          .map((s) => ({ s, p: s.points.find((p) => p.x === hovered) }))
          .filter((r): r is { s: Series; p: { x: number; y: number; n?: number } } => !!r.p);

  return (
    <div ref={ref} className="relative w-full">
      <svg
        width={w}
        height={height}
        role="img"
        aria-label={ariaLabel}
        className="block overflow-visible"
        onMouseMove={(e) => setHoverX(e.nativeEvent.offsetX)}
        onMouseLeave={() => setHoverX(null)}
      >
        {/* gridlines: solid hairlines, recessive */}
        {yTicks.map((t) => (
          <g key={t}>
            <line
              x1={PAD.left}
              x2={PAD.left + plotW}
              y1={sy(t)}
              y2={sy(t)}
              stroke="var(--gridline)"
              strokeWidth={1}
            />
            <text
              x={PAD.left - 8}
              y={sy(t)}
              textAnchor="end"
              dominantBaseline="middle"
              fill="var(--text-muted)"
              fontSize={11}
              style={{ fontVariantNumeric: "tabular-nums" }}
            >
              {yFormat(t)}
            </text>
          </g>
        ))}

        {/* epoch-0 reference line: makes the delta a distance you can see */}
        {baseline ? (
          <g>
            <line
              x1={PAD.left}
              x2={PAD.left + plotW}
              y1={sy(baseline.value)}
              y2={sy(baseline.value)}
              stroke="var(--axis)"
              strokeWidth={1}
              strokeDasharray="4 4"
            />
            {/* Anchored to the RIGHT end of the rule: the left edge is where the
                y-axis ticks and the series' first points already compete. */}
            <text
              x={PAD.left + plotW - 4}
              y={sy(baseline.value) - 6}
              textAnchor="end"
              fill="var(--text-muted)"
              fontSize={10}
            >
              {baseline.label}
            </text>
          </g>
        ) : null}

        {/* x axis */}
        <line
          x1={PAD.left}
          x2={PAD.left + plotW}
          y1={sy(0)}
          y2={sy(0)}
          stroke="var(--axis)"
          strokeWidth={1}
        />
        {xTicks.map((t) => (
          <text
            key={t}
            x={sx(t)}
            y={height - PAD.bottom + 16}
            textAnchor="middle"
            fill="var(--text-muted)"
            fontSize={11}
            style={{ fontVariantNumeric: "tabular-nums" }}
          >
            {t}
          </text>
        ))}
        <text
          x={PAD.left + plotW / 2}
          y={height - 1}
          textAnchor="middle"
          fill="var(--text-muted)"
          fontSize={10}
        >
          epoch
        </text>

        {/* crosshair */}
        {hovered !== null ? (
          <line
            x1={sx(hovered)}
            x2={sx(hovered)}
            y1={PAD.top}
            y2={sy(0)}
            stroke="var(--axis)"
            strokeWidth={1}
          />
        ) : null}

        {series.map((s) => {
          const pts = [...s.points].sort((a, b) => a.x - b.x);
          if (!pts.length) return null;
          const d = pts.map((p, i) => `${i === 0 ? "M" : "L"}${sx(p.x)},${sy(p.y)}`).join(" ");
          const last = pts[pts.length - 1];
          return (
            <g key={s.key}>
              <path
                d={d}
                fill="none"
                stroke={s.color}
                strokeWidth={2}
                strokeLinejoin="round"
                strokeLinecap="round"
              />
              {pts.map((p) => (
                <circle
                  key={p.x}
                  cx={sx(p.x)}
                  cy={sy(p.y)}
                  r={4}
                  fill={s.color}
                  stroke="var(--surface-1)"
                  strokeWidth={2}
                />
              ))}
              {/* Label the endpoint only. Start values are already stated
                  numerically in the delta tiles and marked by the baseline rule,
                  so repeating them here only collides with the y-axis ticks. */}
              {endLabels ? (
                <text
                  x={sx(last.x) + 10}
                  y={sy(last.y)}
                  dominantBaseline="middle"
                  fill="var(--text-primary)"
                  fontSize={12}
                  fontWeight={600}
                  style={{ fontVariantNumeric: "tabular-nums" }}
                >
                  {yFormat(last.y)}
                </text>
              ) : null}
            </g>
          );
        })}
      </svg>

      {/* Tooltip enhances; every value is also in the table view. */}
      {hovered !== null && hoverRows.length ? (
        <div
          className="pointer-events-none absolute z-10 rounded-lg border border-hairline bg-surface px-3 py-2 text-xs shadow-lg"
          style={{
            left: Math.min(sx(hovered) + 12, w - 150),
            top: PAD.top,
            boxShadow: "var(--shadow-card)",
          }}
        >
          <div className="mb-1 font-semibold text-ink">epoch {hovered}</div>
          {hoverRows.map(({ s, p }) => (
            <div key={s.key} className="flex items-center gap-2 whitespace-nowrap">
              <span
                aria-hidden
                className="size-2 shrink-0 rounded-full"
                style={{ background: s.color }}
              />
              <span className="text-ink-secondary">{s.label}</span>
              <span
                className="ml-auto font-medium text-ink"
                style={{ fontVariantNumeric: "tabular-nums" }}
              >
                {yFormat(p.y)}
              </span>
              {p.n ? <span className="text-ink-muted">n={p.n}</span> : null}
            </div>
          ))}
        </div>
      ) : null}
    </div>
  );
}
