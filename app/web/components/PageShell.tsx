"use client";

import type { ReactNode } from "react";
import { ProvenanceBanner } from "./ProvenanceBanner";
import { StartRunAction } from "./StartRunAction";
import type { ProvenanceInfo } from "@/lib/types";

/**
 * Common frame for every page: title, one-line purpose, and the two pieces of
 * context that must never be page-local — data provenance and a missing run DB.
 *
 * Provenance rides the shell rather than the Overview page because a judge can
 * land on any URL. A simulated-data warning that only appears on the home page
 * would let /tasks or /wiki present simulator output as if it were graded.
 */
export function PageShell({
  title,
  intent,
  provenance,
  dbMissing = false,
  refreshSeconds,
  actions,
  children,
}: {
  title: string;
  intent: string;
  provenance?: ProvenanceInfo;
  dbMissing?: boolean;
  refreshSeconds?: number;
  actions?: ReactNode;
  children: ReactNode;
}) {
  return (
    <main className="mx-auto w-full max-w-[1500px] flex-1 px-5 py-7 sm:px-8">
      {/*
        Title block sits directly against the top of the viewport now that the
        global header is gone, so it carries the weight the header used to: a
        larger title, the purpose line beneath it, and the page's primary action
        anchored right. The rule underneath separates chrome from content.
      */}
      <header className="mb-6 flex flex-wrap items-start justify-between gap-4 border-b border-hairline pb-5">
        <div className="min-w-0">
          <h1 className="text-2xl font-semibold tracking-tight text-ink">{title}</h1>
          <p className="mt-1 max-w-3xl text-xs leading-relaxed text-ink-secondary">
            {intent}
          </p>
        </div>
        <div className="flex shrink-0 items-center gap-3">
          {refreshSeconds ? (
            <span className="text-[11px] text-ink-muted">
              refreshes every {refreshSeconds}s
            </span>
          ) : null}
          {actions}
        </div>
      </header>

      <div className="flex flex-col gap-4">
        <ProvenanceBanner provenance={provenance} />
        {dbMissing ? <NoDatabaseNotice /> : null}
        {children}
      </div>
    </main>
  );
}

function NoDatabaseNotice() {
  return (
    <div
      className="rounded-xl border border-hairline px-5 py-4"
      style={{ background: "var(--surface-sunken)" }}
    >
      <h2 className="text-sm font-semibold text-ink">No run database yet</h2>
      <p className="mt-1 text-xs leading-relaxed text-ink-secondary">
        Nothing has been written to <code className="font-mono">runs/helixis.db</code> yet
        (override with <code className="font-mono">HELIXIS_DB</code>). Panels are showing
        honest empty states, not errors — start a run to fill them, or use{" "}
        <code className="font-mono">helixis run</code> from a terminal.
      </p>
      <div className="mt-3">
        <StartRunAction />
      </div>
    </div>
  );
}
