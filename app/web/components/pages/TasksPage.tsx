"use client";

import { useMemo } from "react";
import { PageShell } from "../PageShell";
import { StartRunAction } from "../StartRunAction";
import { StatTiles } from "../StatTiles";
import { TaskGrid } from "../TaskGrid";
import { TaskMovers } from "../TaskMovers";
import { usePoll } from "../usePoll";
import { fmtPct } from "../ui";
import type { StatusResponse, TasksResponse } from "@/lib/types";

// Poll cadence. Raised from 4s: the task manifest changes when the operator edits it or a mined
// proposal is approved — both human-paced.
// Panels hold their previous payload across refetches, so a longer
// interval costs staleness, never a blank panel.
const INTERVAL = 20_000;

/**
 * The evidence behind the curve, task by task. Full width — the grid is 30
 * tasks by N epochs and was previously squeezed into two thirds, which forced
 * task labels to truncate exactly where they differ from one another.
 */
export function TasksPage() {
  const status = usePoll<StatusResponse>("/api/status", INTERVAL);
  const tasks = usePoll<TasksResponse>("/api/tasks", INTERVAL);

  const data = tasks.data;

  const tiles = useMemo(() => {
    const cells = data?.cells ?? [];
    const graded = cells.filter((c) => c.status === "pass" || c.status === "fail");
    const passed = cells.filter((c) => c.status === "pass").length;
    const errored = cells.filter((c) => c.status === "error").length;
    const splits = new Set((data?.tasks ?? []).map((t) => t.split));

    return [
      {
        label: "Tasks",
        value: data ? String(data.tasks.length) : "—",
        note: `${splits.size} split${splits.size === 1 ? "" : "s"}`,
      },
      {
        label: "Epochs",
        value: data?.epochs.length ? String(data.epochs.length) : "—",
        note: data?.epochs.length
          ? `${data.epochs[0]}–${data.epochs[data.epochs.length - 1]}`
          : "none recorded",
      },
      {
        label: "Attempts",
        value: data ? String(cells.length) : "—",
        note: errored ? `${errored} errored` : "no errors",
      },
      {
        label: "Pass rate",
        value: graded.length ? fmtPct(passed / graded.length, 0) : "—",
        note: graded.length ? `${passed} of ${graded.length} graded` : "nothing graded yet",
      },
    ];
  }, [data]);

  return (
    <PageShell
      title="Tasks"
      intent="Per-task results across epochs. Shade is partial credit, dot is a full pass. Select a cell to read that attempt's full trajectory."
      provenance={status.data?.provenance}
      dbMissing={status.data ? !status.data.dbPresent : false}
      refreshSeconds={INTERVAL / 1000}
      actions={<StartRunAction />}
    >
      <StatTiles tiles={tiles} refreshing={tasks.refreshing} />
      <div className="grid items-start gap-4 xl:grid-cols-[minmax(0,1fr)_320px]">
        <TaskGrid data={tasks.data} refreshing={tasks.refreshing} variant="page" />
        <TaskMovers data={tasks.data} refreshing={tasks.refreshing} />
      </div>
    </PageShell>
  );
}
