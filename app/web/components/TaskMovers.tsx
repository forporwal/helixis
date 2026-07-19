"use client";

import { useMemo } from "react";
import Link from "next/link";
import { Card, EmptyState } from "./ui";
import type { TasksResponse } from "@/lib/types";

/**
 * Which tasks actually moved, first epoch to last.
 *
 * The grid shows everything at once and is therefore good at texture and bad at
 * ranking — a reader cannot tell a +0.4 row from a -0.4 row by shade alone.
 * This ranks the same data so the curve's aggregate gain can be attributed to
 * specific tasks, and so regressions stay visible instead of averaging away.
 *
 * Computed from the cells already on the page; no extra request.
 */
type Move = { taskId: string; split: string; first: number; last: number; delta: number };

export function TaskMovers({
  data,
  refreshing,
}: {
  data: TasksResponse | null;
  refreshing: boolean;
}) {
  const moves = useMemo<Move[]>(() => {
    if (!data) return [];
    const byTask = new Map<string, { epoch: number; pc: number }[]>();
    for (const c of data.cells) {
      const key = `${c.split}/${c.taskId}`;
      const list = byTask.get(key) ?? [];
      list.push({ epoch: c.epoch, pc: c.partialCredit });
      byTask.set(key, list);
    }
    const out: Move[] = [];
    for (const [key, points] of byTask) {
      // A single attempt has no trajectory to report.
      if (points.length < 2) continue;
      points.sort((a, b) => a.epoch - b.epoch);
      const [split, ...rest] = key.split("/");
      const first = points[0].pc;
      const last = points[points.length - 1].pc;
      out.push({ taskId: rest.join("/"), split, first, last, delta: last - first });
    }
    return out.sort((a, b) => b.delta - a.delta);
  }, [data]);

  if (!data || data.empty || moves.length === 0) {
    return (
      <Card title="Movers" subtitle="Change from first epoch to last.">
        <EmptyState
          title="Not enough epochs yet"
          hint="A task needs at least two recorded attempts before its change can be measured."
        />
      </Card>
    );
  }

  const gained = moves.filter((m) => m.delta > 0.001).slice(0, 5);
  const lost = moves.filter((m) => m.delta < -0.001).slice(-5).reverse();
  const flat = moves.length - gained.length - lost.length;

  return (
    <Card
      title="Movers"
      subtitle="Change in partial credit, first epoch to last."
      refreshing={refreshing}
    >
      <div className="flex flex-col gap-4">
        <MoveList label="Improved" items={gained} tone="var(--delta-good)" />
        <MoveList label="Regressed" items={lost} tone="var(--status-critical)" />
        <p className="text-[11px] leading-relaxed text-ink-muted">
          {flat} task{flat === 1 ? "" : "s"} unchanged. A skill that helps on average can
          still cost credit on individual tasks; regressions are listed so that trade is
          visible rather than absorbed into the mean.
        </p>
      </div>
    </Card>
  );
}

function MoveList({
  label,
  items,
  tone,
}: {
  label: string;
  items: Move[];
  tone: string;
}) {
  return (
    <div>
      <p className="mb-1.5 text-[11px] font-semibold uppercase tracking-wide text-ink-secondary">
        {label}
      </p>
      {items.length === 0 ? (
        <p className="text-[11px] text-ink-muted">None.</p>
      ) : (
        <ul className="flex flex-col gap-1">
          {items.map((m) => (
            <li key={`${m.split}/${m.taskId}`} className="flex items-baseline gap-2 text-xs">
              <span
                className="w-16 shrink-0 whitespace-nowrap text-right font-medium tabular-nums"
                style={{ color: tone }}
              >
                {m.delta > 0 ? "+" : ""}
                {(m.delta * 100).toFixed(0)} pts
              </span>
              <Link
                href={`/runs/${0}/${m.split}/${encodeURIComponent(m.taskId)}`}
                className="min-w-0 flex-1 truncate text-ink-secondary underline-offset-2 hover:text-ink hover:underline"
                title={m.taskId}
              >
                {m.taskId}
              </Link>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
