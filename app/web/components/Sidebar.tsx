"use client";

import { useSyncExternalStore } from "react";
import {
  ExternalLink,
  PanelLeftClose,
  PanelLeftOpen,
  SquareTerminal,
  Waypoints,
} from "lucide-react";
import { HelixisLogo, HelixisWordmark } from "./Logo";
import { NavLinks } from "./NavLinks";
import { QuickSearch } from "./QuickSearch";
import { RailStatus } from "./RailStatus";
import { ThemeToggle } from "./ThemeToggle";
import { UserMenu } from "./UserMenu";
import { cn } from "@/lib/utils";

// Secondary access to the agent. Home carries the primary launch cards; these
// stay so the agent is one click away from any page, and both resolve their
// URLs through `lib/claw.ts` rather than keeping a second copy.
//
// The URLs arrive as props instead of being imported: the Control UI href
// carries the gateway token, which is read from the environment server-side at
// request time, and this is a client component.
function tools(uiUrl: string, tuiUrl: string) {
  return [
    {
      href: uiUrl,
      label: "Helixis Claw — Control UI",
      note: "sandboxed agent gateway",
      icon: Waypoints,
    },
    {
      href: tuiUrl,
      label: "Helixis Claw — Terminal",
      note: "terminal in the browser",
      icon: SquareTerminal,
    },
  ] as const;
}

const STORAGE_KEY = "helixis:sidebar-collapsed";

/**
 * localStorage as an external store, which is what it actually is.
 *
 * The collapse flag can't be read during render (server and client would
 * disagree and hydration would blow up) and shouldn't be read in an effect
 * either — a setState in an effect body is a second render pass on every mount.
 * `useSyncExternalStore` is the sanctioned third option: React takes the server
 * snapshot for SSR, then swaps in the real value during hydration in one pass.
 *
 * The subscriber list is what makes a toggle in this tab repaint; the `storage`
 * event covers the same dashboard open in a second tab.
 */
const listeners = new Set<() => void>();

function subscribeToCollapse(onChange: () => void) {
  listeners.add(onChange);
  window.addEventListener("storage", onChange);
  return () => {
    listeners.delete(onChange);
    window.removeEventListener("storage", onChange);
  };
}

// Only an explicit "0" expands it; unset means the collapsed default. Returns a
// primitive, so React's snapshot comparison is stable across polls.
function getCollapsed() {
  return window.localStorage.getItem(STORAGE_KEY) !== "0";
}

/** Collapsed is the default, and the only honest guess before localStorage exists. */
function getCollapsedOnServer() {
  return true;
}

function setCollapsed(next: boolean) {
  window.localStorage.setItem(STORAGE_KEY, next ? "1" : "0");
  for (const listener of listeners) listener();
}

/**
 * App-level navigation rail, and now the app's only chrome — the global header
 * was removed, so search, theme, run state, and account all live here, stacked
 * at the foot in the order you reach for them. External tools open in a new tab
 * and are marked with ↗ so leaving the dashboard is never a surprise.
 *
 * Collapsed is the default: the rail is a way back, not the main event, and the
 * content area should own the width.
 */
export function Sidebar({
  email,
  clawUiUrl,
  clawTuiUrl,
}: {
  email?: string | null;
  clawUiUrl: string;
  clawTuiUrl: string;
}) {
  const TOOLS = tools(clawUiUrl, clawTuiUrl);
  const collapsed = useSyncExternalStore(
    subscribeToCollapse,
    getCollapsed,
    getCollapsedOnServer,
  );

  function toggle() {
    setCollapsed(!collapsed);
  }

  return (
    <aside
      className={cn(
        // Always present, never hidden at a breakpoint: with the global header
        // removed, this rail is the only navigation, so a mobile viewport that
        // hid it would strand the user on whatever page they landed on.
        "sticky top-0 flex h-screen shrink-0 flex-col border-r border-hairline bg-sidebar py-5 transition-[width] duration-200",
        collapsed ? "w-[68px] px-2.5" : "w-60 px-4",
      )}
    >
      <div className={cn("flex items-center", collapsed ? "justify-center" : "px-2")}>
        {collapsed ? (
          <HelixisLogo />
        ) : (
          <>
            <HelixisWordmark />
            <button
              type="button"
              onClick={toggle}
              aria-label="Collapse sidebar"
              className="ml-auto rounded-md p-1 text-ink-muted transition-colors hover:bg-sunken hover:text-ink"
            >
              <PanelLeftClose className="size-4" />
            </button>
          </>
        )}
      </div>

      {collapsed ? (
        <button
          type="button"
          onClick={toggle}
          aria-label="Expand sidebar"
          className="mt-3 flex items-center justify-center rounded-md py-1.5 text-ink-muted transition-colors hover:bg-sunken hover:text-ink"
        >
          <PanelLeftOpen className="size-4" />
        </button>
      ) : null}

      <NavLinks collapsed={collapsed} />

      {collapsed ? (
        <div className="mx-2.5 mt-4 mb-2 border-t border-hairline" />
      ) : (
        <p className="mt-6 px-2 text-[11px] font-semibold uppercase tracking-wider text-ink-muted">
          Tools
        </p>
      )}
      <nav className={cn("flex flex-col gap-1 text-sm", collapsed ? "mt-0" : "mt-3")}>
        {TOOLS.map((tool) => {
          const Icon = tool.icon;
          return (
            <a
              key={tool.label}
              href={tool.href}
              target="_blank"
              rel="noopener noreferrer"
              title={collapsed ? `${tool.label} — ${tool.note}` : undefined}
              className={cn(
                "group flex items-start gap-3 rounded-xl py-2 font-medium text-ink-secondary transition-colors hover:bg-surface/70 hover:text-ink dark:hover:bg-sunken",
                collapsed ? "justify-center px-0" : "px-2.5",
              )}
            >
              <span
                aria-hidden
                className="flex size-7 shrink-0 items-center justify-center rounded-lg text-ink-muted"
              >
                <Icon className="size-4" />
              </span>
              {collapsed ? (
                <span className="sr-only">{tool.label}</span>
              ) : (
                <span className="min-w-0">
                  <span className="flex items-center gap-1.5">
                    {tool.label}
                    <ExternalLink aria-hidden className="size-3 text-ink-muted" />
                  </span>
                  <span className="mt-0.5 block text-[11px] font-normal text-ink-muted">
                    {tool.note}
                  </span>
                </span>
              )}
            </a>
          );
        })}
      </nav>

      {/*
        Pinned to the foot, in reach order: search, then the two switches, then
        run state, then account last — the same bottom-anchored utility stack
        the header used to hold, minus the header.
      */}
      <div className="mt-auto flex flex-col gap-0.5 border-t border-hairline pt-3">
        <QuickSearch collapsed={collapsed} />
        <ThemeToggle collapsed={collapsed} />
        <RailStatus collapsed={collapsed} />
        <div className="mt-1 border-t border-hairline pt-1">
          <UserMenu email={email} collapsed={collapsed} />
        </div>
      </div>
    </aside>
  );
}
