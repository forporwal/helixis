"use client";

import { ActionFeed } from "../ActionFeed";
import { Controls } from "../Controls";
import { JobsPanel } from "../JobsPanel";
import { RealTrainingResults } from "../RealTrainingResults";
import { LearningCurve } from "../LearningCurve";
import { PageShell } from "../PageShell";
import { StatusStrip } from "../StatusStrip";
import { TaskManager } from "../TaskManager";
import { TrainingModes } from "../TrainingModes";
import { usePoll } from "../usePoll";
import type {
  ActionsResponse,
  CurveResponse,
  PolicyResponse,
  PreflightResponse,
  RealTrainingResponse,
  StatusResponse,
} from "@/lib/types";

// Poll cadence. Raised from 4s: the learning curve and policy move only when an epoch or a train
// cycle completes. JobsPanel polls /api/control separately and faster,
// which is what keeps a RUNNING job feeling live.
// Panels hold their previous payload across refetches, so a longer
// interval costs staleness, never a blank panel.
const INTERVAL = 15_000;

/**
 * The training story, end to end: how to produce a run, and what the runs
 * produced.
 *
 * The curve sat on top for a while, on the reasoning that the result is the
 * reason to press Start. That holds once there is a curve worth reading — but
 * a lab with two epochs of flat zeroes spends its most valuable screen space
 * on a chart that says nothing, and pushes the only thing worth doing below the
 * fold. So the mode selector leads: decide what to run, then read what running
 * it produced. The chart is one scroll away either way.
 */
export function LabPage() {
  const status = usePoll<StatusResponse>("/api/status", INTERVAL);
  const curve = usePoll<CurveResponse>("/api/curve", INTERVAL);
  const policy = usePoll<PolicyResponse>("/api/policy", INTERVAL);
  // Slower than the rest: preflight shells out to the engine, and which
  // backends are reachable changes on the scale of config edits, not seconds.
  const preflight = usePoll<PreflightResponse>("/api/preflight", INTERVAL * 4);
  const realTraining = usePoll<RealTrainingResponse>("/api/real-training", INTERVAL);
  const actions = usePoll<ActionsResponse>("/api/actions", INTERVAL);

  return (
    <PageShell
      title="Lab"
      intent="Does the agent get better as it runs? The curve per epoch, plus the three ways to produce the next one — a free simulated epoch, a real benchmark epoch, or a training cycle over the agent's own captured sessions."
      provenance={status.data?.provenance ?? curve.data?.provenance}
      dbMissing={status.data ? !status.data.dbPresent : false}
      refreshSeconds={INTERVAL / 1000}
    >
      <StatusStrip
        data={status.data}
        policy={policy.data}
        refreshing={status.refreshing}
      />
      <TrainingModes
        data={preflight.data}
        refreshing={preflight.refreshing}
        cliMissing={status.data ? !status.data.controls.helixisAvailable : false}
        onAction={() => {
          void status.refetch();
          void curve.refetch();
          void preflight.refetch();
          void realTraining.refetch();
          void actions.refetch();
        }}
      />

      <RealTrainingResults
        data={realTraining.data}
        refreshing={realTraining.refreshing}
      />

      <ActionFeed
        data={actions.data}
        refreshing={actions.refreshing}
        onRefetch={() => {
          void actions.refetch();
          void realTraining.refetch();
        }}
      />

      <LearningCurve data={curve.data} refreshing={curve.refreshing} />

      <div className="grid items-start gap-4 lg:grid-cols-2">
        <Controls
          data={status.data}
          refreshing={status.refreshing}
          onAction={() => {
            void status.refetch();
            void curve.refetch();
          }}
        />
        <JobsPanel />
      </div>

      <TaskManager />

      <footer className="pb-4 pt-1 text-[11px] leading-relaxed text-ink-muted">
        Metrics are computed from the SQLite index at query time; nothing on this page is
        hand-entered. Held-out tasks are never shown to the distiller, so the held-out
        series measures transfer rather than memorization. Real-tier episodes are excluded
        from the headline curve by design, and so are your own tasks — the headline is
        computed over the frozen bench set alone, so adding tasks can never move it. That
        is why a training cycle over real trajectories reports its result as skills and
        proposals rather than as a move in the curve above.
      </footer>
    </PageShell>
  );
}
