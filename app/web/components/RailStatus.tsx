"use client";

import Link from "next/link";
import { usePoll } from "./usePoll";
import type { StatusResponse } from "@/lib/types";

/**
 * Pinned to the foot of the navigation rail: is the engine running right now?
 *
 * This was previously knowable only from the Controls card on Lab, which meant
 * that from Tasks or Wiki you could not tell a stalled run from a finished one
 * without navigating away. Run state is global, so it belongs on the shell.
 */
export function RailStatus({ collapsed = false }: { collapsed?: boolean }) {
  const { data } = usePoll<StatusResponse>("/api/status", 5000);
  const running = data?.running ?? false;

  const dot = (
    <span
      aria-hidden
      className={`size-2 shrink-0 rounded-full ${running ? "pulse-live" : ""}`}
      style={{ background: running ? "var(--status-good)" : "var(--text-muted)" }}
    />
  );

  if (collapsed) {
    return (
      <Link
        href="/lab"
        title={running ? `Epoch ${data?.currentEpoch} running` : "Engine idle"}
        aria-label={running ? `Epoch ${data?.currentEpoch} running` : "Engine idle"}
        className="flex items-center justify-center rounded-xl py-2.5 transition-colors hover:bg-sunken"
      >
        {dot}
      </Link>
    );
  }

  return (
    <Link
      href="/lab"
      className="flex items-center gap-2.5 rounded-xl px-2.5 py-2 transition-colors hover:bg-sunken"
    >
      {dot}
      <span className="min-w-0">
        <span className="block text-[11px] font-semibold text-ink">
          {running ? "Run in progress" : "Engine idle"}
        </span>
        <span className="mt-0.5 block truncate text-[10px] text-ink-muted">
          {data
            ? running
              ? `epoch ${data.currentEpoch} · ${data.episodeCount} episodes`
              : data.currentEpoch !== null
                ? `last epoch ${data.currentEpoch}`
                : "no runs yet"
            : "checking…"}
        </span>
      </span>
    </Link>
  );
}
