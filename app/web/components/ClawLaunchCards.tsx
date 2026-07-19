"use client";

import { ExternalLink, SquareTerminal, Waypoints, type LucideIcon } from "lucide-react";
import { CLAW_COMPOSE_HINT } from "@/lib/claw";
import type { StatusResponse } from "@/lib/types";

/**
 * The front door: open your agent.
 *
 * These are the largest, highest things on home because the product is the
 * agent, not the training dashboard that produced it. Both open in a new tab —
 * the gateway is a separate application, and replacing the dashboard with it
 * would strand an operator mid-review.
 *
 * The subtitle is the whole point of the restructure in one line: this agent is
 * running with skills the training loop distilled, and the generation number
 * says which ones.
 */

function skillLine(status: StatusResponse | null): string {
  if (!status) return "checking the gateway…";
  const { wikiGeneration, skillCount } = status.claw;
  if (skillCount === 0) {
    return "no distilled skills yet — running on the base agent";
  }
  return `running with generation-${wikiGeneration} skills · ${skillCount} skill${
    skillCount === 1 ? "" : "s"
  }`;
}

function LaunchCard({
  href,
  label,
  blurb,
  icon: Icon,
  subtitle,
  down,
}: {
  // Null until the first status lands: the Control UI href carries the gateway
  // token and is built server-side, so the client has nothing to link to yet.
  href: string | null;
  label: string;
  blurb: string;
  icon: LucideIcon;
  subtitle: string;
  down: boolean;
}) {
  const body = (
    <>
      <div className="flex items-start gap-3">
        <span
          aria-hidden
          className="flex size-10 shrink-0 items-center justify-center rounded-xl text-primary"
          style={{ background: "var(--surface-sunken)" }}
        >
          <Icon className="size-5" />
        </span>
        <div className="min-w-0">
          <h2 className="flex items-center gap-1.5 text-sm font-semibold tracking-tight text-ink">
            Helixis Claw — {label}
            {down || !href ? null : (
              <ExternalLink aria-hidden className="size-3.5 text-ink-muted" />
            )}
          </h2>
          <p className="mt-0.5 text-xs leading-relaxed text-ink-secondary">{blurb}</p>
        </div>
      </div>

      {down ? (
        <div className="mt-4 rounded-lg border border-hairline px-3 py-2.5" style={{ background: "var(--surface-sunken)" }}>
          <p className="flex items-center gap-1.5 text-[11px] font-medium text-ink">
            {/* State is named, never carried by the dot's color alone. */}
            <span
              aria-hidden
              className="size-1.5 shrink-0 rounded-full"
              style={{ background: "var(--status-serious)" }}
            />
            Gateway not answering
          </p>
          <p className="mt-1 text-[11px] leading-relaxed text-ink-muted">
            Start it with{" "}
            <code className="font-mono text-ink-secondary">{CLAW_COMPOSE_HINT}</code>, then this
            card turns back into a link.
          </p>
        </div>
      ) : (
        <p className="mt-4 flex items-center gap-1.5 text-[11px] text-ink-muted">
          <span
            aria-hidden
            className="size-1.5 shrink-0 rounded-full"
            style={{ background: "var(--status-good)" }}
          />
          {subtitle}
        </p>
      )}
    </>
  );

  const shell =
    "flex flex-col rounded-2xl border border-hairline bg-surface px-5 py-4 text-left";

  // A down gateway renders as a card, not a dead link: clicking through to a
  // connection-refused page tells the operator less than the compose hint does.
  // A missing href is the same call for a different reason — linking before the
  // token has arrived would open the Control UI unauthenticated.
  return down || !href ? (
    <div className={shell} style={{ boxShadow: "var(--shadow-card)" }} aria-disabled>
      {body}
    </div>
  ) : (
    <a
      href={href}
      target="_blank"
      rel="noopener noreferrer"
      className={`${shell} transition-colors hover:border-hairline-strong hover:bg-sunken/40`}
      style={{ boxShadow: "var(--shadow-card)" }}
    >
      {body}
    </a>
  );
}

export function ClawLaunchCards({ status }: { status: StatusResponse | null }) {
  // Unknown is treated as up until the first status lands, so the cards do not
  // flash a scary down state on every page load.
  const down = status ? !status.claw.gatewayUp : false;
  const subtitle = skillLine(status);

  return (
    <div className="grid gap-4 sm:grid-cols-2">
      <LaunchCard
        href={status?.claw.uiUrl ?? null}
        label="Control UI"
        blurb="Chat with the agent, watch its tools run, and steer a session."
        icon={Waypoints}
        subtitle={subtitle}
        down={down}
      />
      <LaunchCard
        href={status?.claw.tuiUrl ?? null}
        label="Terminal"
        blurb="The same agent in a browser terminal, for when you want the shell."
        icon={SquareTerminal}
        // The TUI is a separate ttyd sidecar, so the gateway probe says nothing
        // about it; only the Control UI card claims a reachability state.
        subtitle={subtitle}
        down={false}
      />
    </div>
  );
}
