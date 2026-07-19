"use client";

import { useMemo } from "react";
import { PageShell } from "../PageShell";
import { StartRunAction } from "../StartRunAction";
import { StatTiles } from "../StatTiles";
import { WikiBrowser } from "../WikiBrowser";
import { usePoll } from "../usePoll";
import type { SkillsResponse, StatusResponse } from "@/lib/types";

// Poll cadence. Raised from 4s: skills and pages are rewritten only by a distill/train cycle, so this
// was polling ~450x more often than the data could possibly change.
// Panels hold their previous payload across refetches, so a longer
// interval costs staleness, never a blank panel.
const INTERVAL = 30_000;

/**
 * The agent's memory. This is the page that makes the project legible as
 * distillation rather than prompt engineering: every skill links back to the
 * failed episodes it was mined from.
 */
export function WikiPage() {
  const status = usePoll<StatusResponse>("/api/status", INTERVAL);
  const skills = usePoll<SkillsResponse>("/api/skills", INTERVAL);

  const data = skills.data;

  const tiles = useMemo(() => {
    const list = data?.skills ?? [];
    const categories = new Set(list.map((s) => s.category));
    // Distinct failing episodes across every skill — the raw material the
    // wiki was mined from, and the number that makes "distillation" concrete.
    const sources = new Set(list.flatMap((s) => s.sourceLinks.map((l) => l.label)));
    const lastEpoch = list.length ? Math.max(...list.map((s) => s.createdEpoch)) : null;

    return [
      {
        label: "Skills",
        value: data ? String(list.length) : "—",
        note: lastEpoch === null ? "none distilled yet" : `newest from epoch ${lastEpoch}`,
      },
      {
        label: "Generation",
        value: data ? String(data.generation) : "—",
        note: "current wiki version",
      },
      {
        label: "Categories",
        value: data ? String(categories.size) : "—",
        note: categories.size ? [...categories].slice(0, 2).join(", ") : "—",
      },
      {
        label: "Source failures",
        value: data ? String(sources.size) : "—",
        note: "episodes mined for skills",
      },
    ];
  }, [data]);

  return (
    <PageShell
      title="Wiki"
      intent="Skills the agent distilled from its own failures, and the failures each one came from. This directory is the memory that carries across epochs."
      provenance={status.data?.provenance}
      dbMissing={status.data ? !status.data.dbPresent : false}
      refreshSeconds={INTERVAL / 1000}
      actions={<StartRunAction />}
    >
      <StatTiles tiles={tiles} refreshing={skills.refreshing} />
      <WikiBrowser data={skills.data} refreshing={skills.refreshing} />
    </PageShell>
  );
}
