"use client";

/**
 * A row of headline numbers directly under a page title.
 *
 * Tasks and Wiki both used to open straight into a dense control — a 30×N grid
 * or a collapsed skill list — so the first question they provoked was "is there
 * anything in here at all?". These tiles answer that before the eye reaches the
 * detail, and they stay legible while the underlying data is still loading.
 *
 * Deliberately not links, unlike StatusStrip on Overview: there is nowhere more
 * specific to send someone who is already on the page the tiles describe.
 */
export function StatTiles({
  tiles,
  refreshing,
}: {
  tiles: { label: string; value: string; note?: string }[];
  refreshing?: boolean;
}) {
  return (
    <div
      className={`grid gap-px overflow-hidden rounded-lg border border-hairline bg-hairline sm:grid-cols-2 lg:grid-cols-4 ${
        refreshing ? "is-refetching" : ""
      }`}
    >
      {tiles.map((t) => (
        <div key={t.label} className="bg-surface px-4 py-3">
          <p className="text-[11px] font-medium uppercase tracking-wider text-ink-muted">
            {t.label}
          </p>
          <p className="mt-1 text-2xl font-semibold tabular-nums tracking-tight text-ink">
            {t.value}
          </p>
          {t.note ? <p className="mt-0.5 text-[11px] text-ink-muted">{t.note}</p> : null}
        </div>
      ))}
    </div>
  );
}
