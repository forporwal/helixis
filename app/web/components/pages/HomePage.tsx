"use client";

import { ActionFeed } from "../ActionFeed";
import { ClawLaunchCards } from "../ClawLaunchCards";
import { LearningDelta } from "../LearningDelta";
import { PageShell } from "../PageShell";
import { usePoll } from "../usePoll";
import type { ActionsResponse, CurveResponse, StatusResponse } from "@/lib/types";

// Poll cadence. Raised from 4s: home shows status + action nudges; both change on epoch
// and ingest boundaries (minutes at the fastest), not per second.
// Panels hold their previous payload across refetches, so a longer
// interval costs staleness, never a blank panel.
const INTERVAL = 15_000;

/**
 * The front door.
 *
 * Home used to be the researcher's artifact — the learning curve — while the
 * thing the user actually opens was a link in a collapsed rail. That order is
 * inverted here: launch the agent, see what it needs from you, and only then a
 * one-line glance at whether it is getting better, which links to the Lab where
 * the whole training story now lives.
 *
 * The provenance banner still rides PageShell exactly as it did on Overview: a
 * judge can land on this URL first, and a simulated-data warning that only
 * appeared deeper in the app would be a lie by omission.
 */
export function HomePage() {
  const status = usePoll<StatusResponse>("/api/status", INTERVAL);
  const actions = usePoll<ActionsResponse>("/api/actions", INTERVAL);
  const curve = usePoll<CurveResponse>("/api/curve", INTERVAL);

  return (
    <PageShell
      title="Helixis Claw"
      intent="Your agent, and everything it needs from you. It runs with skills distilled from its own past runs; the training that produces them lives in the Lab."
      provenance={status.data?.provenance ?? curve.data?.provenance}
      dbMissing={status.data ? !status.data.dbPresent : false}
      refreshSeconds={INTERVAL / 1000}
    >
      <ClawLaunchCards status={status.data} />
      <ActionFeed
        data={actions.data}
        refreshing={actions.refreshing}
        onRefetch={() => void actions.refetch()}
      />
      <LearningDelta data={curve.data} />
    </PageShell>
  );
}
