"use client";

import Link from "next/link";
import { fmtUsd } from "./ui";
import type { PolicyResponse, StatusResponse } from "@/lib/types";

/**
 * The four numbers that situate the curve: how far the experiment has run, how
 * much memory it has accumulated, and what it has cost. Deliberately read-only
 * and deliberately small — it captions the curve above it on Lab rather than
 * competing with the run controls below. Every tile links to the page that
 * explains it.
 */
export function StatusStrip({
  data,
  policy,
  refreshing,
}: {
  data: StatusResponse | null;
  policy: PolicyResponse | null;
  refreshing?: boolean;
}) {
  const epochs = data?.epochs ?? [];
  const latest = epochs.length ? Math.max(...epochs.map((e) => e.epoch)) : null;
  const denials = policy?.counts.denials;
  const pending = policy?.counts.pending ?? 0;

  const tiles = [
    {
      label: "Epochs run",
      value: latest === null ? "—" : String(latest + 1),
      note: data?.running ? "run in progress" : `${data?.episodeCount ?? 0} episodes`,
      href: "/tasks",
    },
    {
      label: "Skills distilled",
      value: data?.skillCount != null ? String(data.skillCount) : "—",
      note: data?.wikiGeneration != null ? `wiki gen ${data.wikiGeneration}` : "no wiki yet",
      href: "/wiki",
    },
    {
      label: "Policy denials",
      value: denials != null ? String(denials) : "—",
      // A pending approval is the one thing here that wants a human, so it
      // displaces the neutral caption rather than hiding below the fold.
      note: pending > 0 ? `${pending} awaiting approval` : "boundary held",
      href: "/containment",
    },
    {
      label: "Spend",
      value: data ? fmtUsd(data.cost.total) : "—",
      note: data?.cost.totalCap ? `of ${fmtUsd(data.cost.totalCap)} cap` : "no cap set",
      href: "/lab",
    },
  ];

  return (
    <div className={`grid gap-3 sm:grid-cols-2 lg:grid-cols-4 ${refreshing ? "is-refetching" : ""}`}>
      {tiles.map((t) => (
        <Link
          key={t.label}
          href={t.href}
          className="rounded-2xl border border-hairline bg-surface px-4 py-3 transition-colors hover:border-hairline-strong"
          style={{ boxShadow: "var(--shadow-card)" }}
        >
          <p className="text-[11px] font-medium uppercase tracking-wider text-ink-muted">
            {t.label}
          </p>
          <p className="mt-1 text-2xl font-semibold tabular-nums tracking-tight text-ink">
            {t.value}
          </p>
          <p className="mt-0.5 text-[11px] text-ink-muted">{t.note}</p>
        </Link>
      ))}
    </div>
  );
}
