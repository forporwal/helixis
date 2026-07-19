"use client";

import { useState } from "react";
import { Terminal } from "lucide-react";
import { Card, Disclosure, EmptyState, Pill } from "./ui";
import { usePoll } from "./usePoll";
import type { Job, JobsResponse } from "@/lib/types";

/**
 * Live console for engine processes started from this dashboard.
 *
 * The job registry only knows about processes the dashboard itself spawned —
 * runs launched from a terminal do not appear here, and the panel says so
 * rather than implying full coverage.
 */

function JobRow({ job }: { job: Job }) {
  const [open, setOpen] = useState(job.status === "running");
  const tone = job.status === "running" ? "train" : job.status === "exited" ? "good" : "critical";

  return (
    <li className="border-b border-hairline last:border-0">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        aria-expanded={open}
        className="group flex w-full items-center gap-3 py-2 text-left transition-colors hover:bg-sunken"
      >
        <Disclosure open={open} />
        <span className="min-w-0 flex-1 truncate font-mono text-[11px] text-ink" title={job.command.join(" ")}>
          {job.command.join(" ")}
        </span>
        <span className="shrink-0 text-[10px] text-ink-muted" style={{ fontVariantNumeric: "tabular-nums" }}>
          {new Date(job.startedAt).toLocaleTimeString()}
        </span>
        <Pill tone={tone}>
          {job.status === "exited" ? "done" : job.status}
          {job.exitCode !== null && job.exitCode !== 0 ? ` (${job.exitCode})` : ""}
        </Pill>
      </button>

      {open ? (
        <div className="mb-2.5 ml-6 rounded-lg border border-hairline bg-sunken p-3">
          {job.log.length ? (
            <pre className="max-h-64 overflow-auto whitespace-pre-wrap break-words font-mono text-[10px] leading-relaxed text-ink-secondary">
              {job.log.join("\n")}
            </pre>
          ) : (
            <p className="text-[11px] text-ink-muted">No output yet.</p>
          )}
        </div>
      ) : null}
    </li>
  );
}

export function JobsPanel() {
  const { data, refreshing } = usePoll<JobsResponse>("/api/control", 3000);
  const jobs = data?.jobs ?? [];
  const running = jobs.filter((j) => j.status === "running").length;

  return (
    <Card
      title="Engine jobs"
      subtitle="Processes started from this dashboard, with their live output. Runs launched from a terminal are not tracked here."
      refreshing={refreshing}
      action={
        running ? (
          <Pill tone="train">
            {running} running
          </Pill>
        ) : undefined
      }
    >
      {jobs.length ? (
        <ul>
          {jobs.map((j) => (
            <JobRow key={j.id} job={j} />
          ))}
        </ul>
      ) : (
        <EmptyState
          icon={Terminal}
          title="No jobs yet"
          hint="Start an epoch, distillation or rehearsal with the controls above and its output streams here."
        />
      )}
    </Card>
  );
}
