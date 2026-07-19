"use client";

import type { ReactNode } from "react";
import { ChevronRight, type LucideIcon } from "lucide-react";
import { cn } from "@/lib/utils";

export function Card({
  title,
  subtitle,
  action,
  children,
  className = "",
  refreshing = false,
}: {
  title: string;
  subtitle?: ReactNode;
  action?: ReactNode;
  children: ReactNode;
  className?: string;
  refreshing?: boolean;
}) {
  return (
    <section
      className={`flex flex-col rounded-2xl border border-hairline bg-surface ${className}`}
      style={{ boxShadow: "var(--shadow-card)" }}
    >
      <header className="flex items-start justify-between gap-4 px-5 pt-4 pb-3">
        <div className="min-w-0">
          <h2 className="text-sm font-semibold tracking-tight text-ink">{title}</h2>
          {subtitle ? (
            <p className="mt-0.5 text-xs leading-relaxed text-ink-muted">{subtitle}</p>
          ) : null}
        </div>
        {action ? <div className="shrink-0">{action}</div> : null}
      </header>
      <div className={`min-h-0 flex-1 px-5 pb-5 ${refreshing ? "is-refetching" : ""}`}>
        {children}
      </div>
    </section>
  );
}

/**
 * Honest empty state: says what is missing, what would fill it, and — where a
 * next step exists — offers it directly.
 *
 * `action` matters more than it looks. Without it every dead end in the app
 * ends in prose, and the operator has to already know that runs start in Lab.
 * `icon` gives the block a focal point so an empty panel reads as a designed
 * state rather than a failed fetch.
 */
export function EmptyState({
  title,
  hint,
  icon: Icon,
  action,
}: {
  title: string;
  hint?: string;
  icon?: LucideIcon;
  action?: ReactNode;
}) {
  return (
    <div className="flex h-full min-h-32 flex-col items-center justify-center rounded-lg border border-dashed border-hairline px-6 py-8 text-center">
      {Icon ? (
        <span
          aria-hidden
          className="mb-3 flex size-10 items-center justify-center rounded-xl text-ink-muted"
          style={{ background: "var(--surface-sunken)" }}
        >
          <Icon className="size-5" />
        </span>
      ) : null}
      <p className="text-sm font-medium text-ink-secondary">{title}</p>
      {hint ? <p className="mt-1 max-w-md text-xs text-ink-muted">{hint}</p> : null}
      {action ? <div className="mt-4">{action}</div> : null}
    </div>
  );
}

/**
 * Filter/search row above a collection. Search grows, controls sit right —
 * matching the shape of every list surface in the app so the eye learns one
 * position for "narrow this down".
 */
export function Toolbar({
  search,
  chips,
  children,
}: {
  search?: ReactNode;
  chips?: ReactNode;
  children?: ReactNode;
}) {
  return (
    <div className="mb-3 flex flex-col gap-2.5">
      <div className="flex flex-wrap items-center gap-2">
        {search ? <div className="min-w-0 flex-1">{search}</div> : <div className="flex-1" />}
        {children}
      </div>
      {chips ? <div className="flex flex-wrap items-center gap-1.5">{chips}</div> : null}
    </div>
  );
}

/** Toggleable filter chip. Selection is carried by fill AND weight, not hue alone. */
export function FilterChip({
  active,
  onClick,
  children,
  count,
}: {
  active: boolean;
  onClick: () => void;
  children: ReactNode;
  count?: number;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      aria-pressed={active}
      className={`inline-flex items-center gap-1.5 rounded-md border px-2.5 py-1 text-[11px] transition-colors ${
        active
          ? "border-transparent bg-primary font-semibold text-on-primary"
          : "border-hairline font-medium text-ink-secondary hover:bg-sunken hover:text-ink"
      }`}
    >
      {children}
      {count != null ? (
        <span
          className={active ? "text-on-primary/70" : "text-ink-muted"}
          style={{ fontVariantNumeric: "tabular-nums" }}
        >
          {count}
        </span>
      ) : null}
    </button>
  );
}

/** "No rows match the filter" — distinct from "there is no data at all". */
export function NoMatches({ onClear }: { onClear: () => void }) {
  return (
    <div className="flex flex-col items-center justify-center rounded-lg border border-dashed border-hairline px-6 py-8 text-center">
      <p className="text-sm font-medium text-ink-secondary">No matches</p>
      <p className="mt-1 text-xs text-ink-muted">
        Nothing here matches the current search and filters.
      </p>
      <button
        type="button"
        onClick={onClear}
        className="mt-3 text-xs font-medium text-primary underline underline-offset-2"
      >
        Clear filters
      </button>
    </div>
  );
}

export function Pill({
  children,
  tone = "neutral",
}: {
  children: ReactNode;
  tone?: "neutral" | "good" | "warning" | "critical" | "train" | "heldout";
}) {
  const tones: Record<string, string> = {
    neutral: "border-hairline text-ink-secondary",
    good: "border-transparent text-ink",
    warning: "border-transparent text-ink",
    critical: "border-transparent text-ink",
    train: "border-hairline text-ink-secondary",
    heldout: "border-hairline text-ink-secondary",
  };
  const dot: Record<string, string | undefined> = {
    good: "var(--status-good)",
    warning: "var(--status-warning)",
    critical: "var(--status-critical)",
    train: "var(--series-train)",
    heldout: "var(--series-heldout)",
    neutral: undefined,
  };
  return (
    <span
      className={`inline-flex items-center gap-1.5 rounded-md border px-2 py-0.5 text-[11px] font-medium ${tones[tone]}`}
      style={tone === "good" || tone === "warning" || tone === "critical" ? { background: "var(--surface-sunken)" } : undefined}
    >
      {dot[tone] ? (
        <span
          aria-hidden
          className="size-1.5 shrink-0 rounded-full"
          style={{ background: dot[tone] }}
        />
      ) : null}
      {children}
    </span>
  );
}

/**
 * Expand/collapse affordance for any disclosure row.
 *
 * These were ▸/▾ text glyphs at 10px in the muted ink — the weakest colour in
 * the palette at the smallest size in the app, which made the single most
 * important interaction on the wiki, jobs, and trajectory rows nearly
 * invisible. A real icon at 14px in secondary ink reads at a glance, brightens
 * with the row on hover (give the row `group`), and rotates instead of swapping
 * character, so the state change is continuous rather than a flicker.
 */
export function Disclosure({ open, className }: { open: boolean; className?: string }) {
  return (
    <span
      aria-hidden
      // The rows are `items-start`, so this box sits at the top of a multi-line
      // cell -- it centers the chevron against the *first* line, not the row.
      // Pass a height matching that line (badges make it taller than the text).
      className={cn(
        "flex h-4 w-4 shrink-0 items-center justify-center text-ink-secondary transition-colors group-hover:text-ink",
        className,
      )}
    >
      {/*
        Tailwind v4's `rotate-90` sets the standalone CSS `rotate` property, not
        `transform` — so `transition-transform` would compile fine and animate
        nothing. Transition `rotate` explicitly.
      */}
      <ChevronRight
        className={`size-3.5 transition-[rotate] duration-150 ${open ? "rotate-90" : ""}`}
      />
    </span>
  );
}

/** Legend: identity is never carried by color alone. */
export function Legend({ items }: { items: { label: string; color: string; note?: string }[] }) {
  return (
    <ul className="flex flex-wrap items-center gap-x-4 gap-y-1.5">
      {items.map((it) => (
        <li key={it.label} className="flex items-center gap-1.5 text-xs text-ink-secondary">
          <span
            aria-hidden
            className="h-0.5 w-4 shrink-0 rounded-full"
            style={{ background: it.color }}
          />
          <span className="font-medium text-ink">{it.label}</span>
          {it.note ? <span className="text-ink-muted">{it.note}</span> : null}
        </li>
      ))}
    </ul>
  );
}

export function fmtPct(v: number, digits = 1) {
  return `${(v * 100).toFixed(digits)}%`;
}

export function fmtUsd(v: number) {
  if (v >= 1000) return `$${(v / 1000).toFixed(1)}k`;
  if (v >= 1) return `$${v.toFixed(2)}`;
  return `$${v.toFixed(3)}`;
}
