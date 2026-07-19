"use client";

import Link from "next/link";
import { Waypoints } from "lucide-react";
import { Card, EmptyState } from "./ui";
import type { RealTrainingResponse } from "@/lib/types";

/**
 * The result panel for the real-trajectory mode.
 *
 * Every other mode reports itself on the learning curve. This one cannot: real
 * -tier episodes are excluded from the headline by design, so without this
 * panel a training cycle produces no visible change anywhere on Lab and reads
 * as a no-op. The four columns follow the pipeline in order — sessions
 * ingested, episodes judged, skills distilled, tasks proposed — so a cycle that
 * stalls shows you which stage it stalled at.
 */
export function RealTrainingResults({
  data,
  refreshing,
}: {
  data: RealTrainingResponse | null;
  refreshing: boolean;
}) {
  if (!data || data.empty) {
    return (
      <Card
        title="Real-trajectory results"
        subtitle="What the training cycle produced from the agent's own sessions."
        refreshing={refreshing}
      >
        <EmptyState
          icon={Waypoints}
          title="No real episodes yet"
          hint="Use the agent, then run a training cycle. Its sessions are judged, distilled into skills, and mined for task proposals — none of which move the curve above."
        />
      </Card>
    );
  }

  const { sessions, episodes, skills, proposals, readiness, lastCycle } = data;
  const judged = episodes.helpful + episodes.unhelpful;

  return (
    <Card
      title="Real-trajectory results"
      subtitle="What the training cycle produced from the agent's own sessions. These episodes are excluded from the curve above by design, so this is where the loop reports itself."
      refreshing={refreshing}
      action={
        readiness.ready ? (
          <span
            className="rounded-full px-2 py-0.5 text-[10px] font-semibold"
            style={{ background: "var(--seq-100)", color: "var(--series-train)" }}
          >
            ready to train
          </span>
        ) : null
      }
    >
      <div className="flex flex-col gap-4">
        <div className="grid gap-3 sm:grid-cols-4">
          <Stage
            step="1"
            label="Sessions ingested"
            value={String(sessions.ingested)}
            note={
              sessions.quarantined > 0
                ? `${sessions.quarantined} quarantined`
                : "redacted before storage"
            }
            tone={sessions.quarantined > 0 ? "warn" : "plain"}
          />
          <Stage
            step="2"
            label="Episodes judged"
            value={`${judged} / ${episodes.total}`}
            note={
              episodes.unlabeled > 0
                ? `${episodes.unlabeled} unlabeled — judge offline`
                : `${episodes.helpful} helpful · ${episodes.unhelpful} not`
            }
            tone={episodes.unlabeled > 0 ? "warn" : "plain"}
          />
          <Stage
            step="3"
            label="Skills from real use"
            value={String(skills.fromReal)}
            note={
              skills.unreadable > 0
                ? `of ${skills.total} · ${skills.unreadable} unreadable`
                : `of ${skills.total} in the wiki`
            }
            tone={skills.unreadable > 0 ? "warn" : "plain"}
          />
          <Stage
            step="4"
            label="Tasks proposed"
            value={String(proposals.pending)}
            note={proposals.pending > 0 ? "awaiting your review" : "none pending"}
            href={proposals.pending > 0 ? "/tasks" : undefined}
          />
        </div>

        {skills.names.length > 0 ? (
          <div>
            <p className="mb-1.5 text-[10px] font-semibold uppercase tracking-wide text-ink-muted">
              Taught by real sessions
            </p>
            <div className="flex flex-wrap gap-1.5">
              {skills.names.map((n) => (
                <span
                  key={n}
                  className="rounded-md border border-hairline px-2 py-0.5 font-mono text-[10px] text-ink-secondary"
                >
                  {n}
                </span>
              ))}
            </div>
          </div>
        ) : null}

        <p className="text-[10px] leading-relaxed text-ink-muted">
          {lastCycle
            ? `Last cycle landed ${lastCycle.skills.length} skill${lastCycle.skills.length === 1 ? "" : "s"} at wiki generation ${lastCycle.generation}; the sync loop delivers them to the agent within ~30s. `
            : ""}
          {readiness.newRealEpisodes} new real episode
          {readiness.newRealEpisodes === 1 ? "" : "s"} since the last distillation
          {readiness.ready
            ? ` — past the threshold of ${readiness.threshold}, so a cycle is worth running.`
            : `; a cycle earns its cost at ${readiness.threshold}.`}
          {readiness.autoTrain ? "" : " Nothing runs without your click."}
        </p>
      </div>
    </Card>
  );
}

function Stage({
  step,
  label,
  value,
  note,
  tone = "plain",
  href,
}: {
  step: string;
  label: string;
  value: string;
  note: string;
  tone?: "plain" | "warn";
  href?: string;
}) {
  const body = (
    <>
      <div className="flex items-center gap-1.5">
        <span
          aria-hidden
          className="flex size-4 items-center justify-center rounded-full text-[9px] font-semibold"
          style={{ background: "var(--seq-100)", color: "var(--text-secondary)" }}
        >
          {step}
        </span>
        <span className="text-[10px] text-ink-muted">{label}</span>
      </div>
      <div className="mt-1 text-lg font-semibold text-ink">{value}</div>
      <div
        className="text-[10px]"
        style={{ color: tone === "warn" ? "var(--status-warning)" : "var(--text-muted)" }}
      >
        {note}
      </div>
    </>
  );

  const className = "rounded-lg border border-hairline px-2.5 py-2";
  return href ? (
    <Link href={href} className={`${className} transition-colors hover:bg-sunken`}>
      {body}
    </Link>
  ) : (
    <div className={className}>{body}</div>
  );
}
