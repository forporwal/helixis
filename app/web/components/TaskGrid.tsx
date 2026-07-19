"use client";

import { useMemo, useState } from "react";
import Link from "next/link";
import { LayoutGrid, ListChecks, Rows3, Search } from "lucide-react";
import { Card, EmptyState, FilterChip, NoMatches, Toolbar, fmtPct, fmtUsd } from "./ui";
import { StartRunAction } from "./StartRunAction";
import { Input } from "./ui/input";
import { ToggleGroup, ToggleGroupItem } from "./ui/toggle-group";
import type { Split, TaskCell, TasksResponse } from "@/lib/types";

/**
 * Tasks x epochs.
 *
 * Partial credit is a continuous magnitude, so it gets the SEQUENTIAL ramp --
 * one hue, light to dark. Pass/fail/error is a separate, categorical fact, so it
 * rides a second channel (a status dot / a status fill) rather than competing
 * for the same hue. Cells are separated by a 2px surface gap, never a border.
 */

const RAMP = ["var(--seq-100)", "var(--seq-250)", "var(--seq-400)", "var(--seq-550)", "var(--seq-700)"];

/** A recorded cell links to its full transcript. */
function episodeHref(cell: TaskCell): string {
  return `/runs/${cell.epoch}/${cell.split}/${encodeURIComponent(cell.taskId)}`;
}

function rampStep(v: number): string {
  if (v >= 0.8) return RAMP[4];
  if (v >= 0.6) return RAMP[3];
  if (v >= 0.4) return RAMP[2];
  if (v >= 0.2) return RAMP[1];
  return RAMP[0];
}

function cellFill(cell: TaskCell | undefined): string {
  if (!cell) return "var(--surface-sunken)";
  if (cell.status === "error") return "var(--status-critical)";
  return rampStep(cell.partialCredit);
}

export function TaskGrid({
  data,
  refreshing,
  variant = "panel",
}: {
  data: TasksResponse | null;
  refreshing: boolean;
  /**
   * "panel" clamps the grid to 420px and scrolls internally — correct when it
   * shares a screen with other cards. "page" lets it run full height, because
   * an internal scrollbar on a dedicated page hides the held-out group
   * entirely while the viewport below sits empty. Task labels also get more
   * room in "page", where nothing competes for the width.
   */
  variant?: "panel" | "page";
}) {
  const page = variant === "page";
  const labelPx = page ? 260 : 184;
  const [hover, setHover] = useState<{ cell: TaskCell; x: number; y: number } | null>(null);
  const [showTable, setShowTable] = useState(false);
  const [query, setQuery] = useState("");
  // Empty set means "no filter", which reads better than pre-selecting all
  // three — deselecting the last chip would otherwise show nothing.
  const [splits, setSplits] = useState<Set<Split>>(new Set());

  const index = useMemo(() => {
    const m = new Map<string, TaskCell>();
    for (const c of data?.cells ?? []) m.set(`${c.split}/${c.taskId}/${c.epoch}`, c);
    return m;
  }, [data]);

  const needle = query.trim().toLowerCase();
  const tasks = useMemo(
    () =>
      (data?.tasks ?? []).filter(
        (t) =>
          (splits.size === 0 || splits.has(t.split)) &&
          (!needle ||
            t.taskId.toLowerCase().includes(needle) ||
            t.domain.toLowerCase().includes(needle)),
      ),
    [data, splits, needle],
  );

  const visible = useMemo(
    () => new Set(tasks.map((t) => `${t.split}/${t.taskId}`)),
    [tasks],
  );

  if (!data || data.empty) {
    return (
      <Card title="Task grid" subtitle="Per-task status across epochs.">
        <EmptyState
          icon={ListChecks}
          title="No episodes recorded"
          hint="Each cell is one (epoch, task) attempt. Cells appear here as the runner writes episodes."
          action={<StartRunAction />}
        />
      </Card>
    );
  }

  const groups = ["train", "heldout", "real"].filter((s) =>
    tasks.some((t) => t.split === s),
  );

  const allSplits = (["train", "heldout", "real"] as const).filter((s) =>
    data.tasks.some((t) => t.split === s),
  );

  function toggleSplit(s: Split) {
    setSplits((prev) => {
      const next = new Set(prev);
      if (next.has(s)) next.delete(s);
      else next.add(s);
      return next;
    });
  }

  function clearFilters() {
    setQuery("");
    setSplits(new Set());
  }

  const filtered = needle !== "" || splits.size > 0;

  const toolbar = (
    <Toolbar
      search={
        <div className="relative">
          <Search
            aria-hidden
            className="pointer-events-none absolute left-3 top-1/2 size-3.5 -translate-y-1/2 text-ink-muted"
          />
          <Input
            type="search"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="Filter tasks by id or domain…"
            aria-label="Filter tasks"
            className="h-8 rounded-lg pl-8 text-xs"
          />
        </div>
      }
      chips={allSplits.map((s) => (
        <FilterChip
          key={s}
          active={splits.has(s)}
          onClick={() => toggleSplit(s)}
          count={data.tasks.filter((t) => t.split === s).length}
        >
          {s === "heldout" ? "Held-out" : s === "train" ? "Train" : "Real"}
        </FilterChip>
      ))}
    >
      <ToggleGroup
        type="single"
        value={showTable ? "table" : "grid"}
        onValueChange={(v) => {
          if (v) setShowTable(v === "table");
        }}
        aria-label="View mode"
      >
        <ToggleGroupItem value="grid" aria-label="Grid view">
          <LayoutGrid />
          Grid
        </ToggleGroupItem>
        <ToggleGroupItem value="table" aria-label="Table view">
          <Rows3 />
          Table
        </ToggleGroupItem>
      </ToggleGroup>
    </Toolbar>
  );

  return (
    <Card
      title="Task grid"
      subtitle={
        filtered
          ? `${tasks.length} of ${data.tasks.length} tasks × ${data.epochs.length} epochs · shade = partial credit, dot = passed`
          : `${data.tasks.length} tasks × ${data.epochs.length} epochs · shade = partial credit, dot = passed`
      }
      refreshing={refreshing}
    >
      {toolbar}
      {tasks.length === 0 ? (
        <NoMatches onClear={clearFilters} />
      ) : showTable ? (
        <GridTable data={data} visible={visible} />
      ) : (
        <div className="relative">
          <div className={page ? "pr-1" : "max-h-[420px] overflow-auto pr-1"}>
            {groups.map((split) => (
              <div key={split} className="mb-4 last:mb-0">
                <div className="sticky top-0 z-10 mb-1.5 bg-surface pb-1 text-[11px] font-semibold uppercase tracking-wide text-ink-secondary">
                  {split === "heldout" ? "Held-out" : split}
                </div>

                {/* epoch header */}
                {/* label column + the 2px row gap -- must match the rows exactly */}
                <div
                  className="mb-1 flex items-center gap-0.5"
                  style={{ paddingLeft: labelPx + 2 }}
                >
                  {data.epochs.map((e) => (
                    <div
                      key={e}
                      className="w-6 text-center text-[10px] text-ink-muted"
                      style={{ fontVariantNumeric: "tabular-nums" }}
                    >
                      {e}
                    </div>
                  ))}
                </div>

                <div className="flex flex-col gap-0.5">
                  {tasks
                    .filter((t) => t.split === split)
                    .map((t) => (
                      <div key={`${t.split}/${t.taskId}`} className="flex items-center gap-0.5">
                        <div
                          className="flex shrink-0 items-center gap-1 pr-2 text-[11px] text-ink-secondary"
                          style={{ width: labelPx }}
                          title={
                            t.origin === "user"
                              ? `${t.taskId} — your own task; excluded from the headline curve`
                              : t.taskId
                          }
                        >
                          <span className="truncate">{t.taskId}</span>
                          {/* Origin is carried by a glyph, not a hue: user tasks
                              are excluded from the headline curve, and that is
                              too important to encode in color alone. */}
                          {t.origin === "user" ? (
                            <span
                              className="shrink-0 rounded border border-hairline px-1 text-[9px] font-semibold uppercase tracking-wide text-ink-muted"
                              aria-label="user-defined task"
                            >
                              you
                            </span>
                          ) : null}
                        </div>
                        {data.epochs.map((e) => {
                          const cell = index.get(`${t.split}/${t.taskId}/${e}`);
                          const dot =
                            cell?.status === "pass" ? (
                              <span
                                aria-hidden
                                className="absolute right-0.5 top-0.5 size-1.5 rounded-full"
                                style={{
                                  background: "var(--status-good)",
                                  boxShadow: "0 0 0 1.5px var(--surface-1)",
                                }}
                              />
                            ) : null;
                          if (!cell) {
                            return (
                              <div
                                key={e}
                                className="relative h-5 w-6 shrink-0 rounded-[3px]"
                                style={{ background: cellFill(cell) }}
                                aria-label={`${t.taskId} epoch ${e}: not run`}
                              />
                            );
                          }
                          // A cell is a link: click opens the full transcript.
                          return (
                            <Link
                              key={e}
                              href={episodeHref(cell)}
                              className="relative h-5 w-6 shrink-0 rounded-[3px] outline-offset-1"
                              style={{ background: cellFill(cell) }}
                              onMouseEnter={(ev) =>
                                setHover({
                                  cell,
                                  x: ev.currentTarget.offsetLeft,
                                  y: ev.currentTarget.offsetTop,
                                })
                              }
                              onMouseLeave={() => setHover(null)}
                              onFocus={(ev) =>
                                setHover({
                                  cell,
                                  x: ev.currentTarget.offsetLeft,
                                  y: ev.currentTarget.offsetTop,
                                })
                              }
                              onBlur={() => setHover(null)}
                              aria-label={`${t.taskId} epoch ${e}: ${cell.status}, partial credit ${fmtPct(cell.partialCredit)} — open transcript`}
                            >
                              {dot}
                            </Link>
                          );
                        })}
                      </div>
                    ))}
                </div>
              </div>
            ))}
          </div>

          {hover ? (
            <div
              className="pointer-events-none absolute z-20 w-60 rounded-lg border border-hairline bg-surface p-3 text-xs"
              style={{
                left: Math.min(hover.x + 30, 240),
                top: hover.y + 26,
                boxShadow: "var(--shadow-card)",
              }}
            >
              <div className="font-semibold text-ink">{hover.cell.taskId}</div>
              <div className="mt-0.5 text-ink-muted">
                epoch {hover.cell.epoch} · {hover.cell.domain} · {hover.cell.tier}
              </div>
              <dl className="mt-2 space-y-0.5" style={{ fontVariantNumeric: "tabular-nums" }}>
                <Row k="Status" v={hover.cell.status} />
                <Row k="Partial credit" v={fmtPct(hover.cell.partialCredit)} />
                <Row k="Steps" v={String(hover.cell.steps)} />
                <Row k="Cost" v={fmtUsd(hover.cell.costUsd)} />
                <Row
                  k="Tokens in/out"
                  v={`${hover.cell.tokensIn.toLocaleString()} / ${hover.cell.tokensOut.toLocaleString()}`}
                />
                <Row k="Model" v={hover.cell.model || "—"} />
                <Row k="Wiki gen" v={String(hover.cell.wikiGeneration)} />
                <Row
                  k="Skills injected"
                  v={hover.cell.injectedSkills.length ? hover.cell.injectedSkills.join(", ") : "none"}
                />
              </dl>
              {hover.cell.error ? (
                <p className="mt-1.5 line-clamp-3 text-[11px]" style={{ color: "var(--status-critical)" }}>
                  {hover.cell.error}
                </p>
              ) : null}
              <p className="mt-1.5 text-[10px] text-ink-muted">click to open the transcript</p>
            </div>
          ) : null}

          <GridLegend />
        </div>
      )}
    </Card>
  );
}

function Row({ k, v }: { k: string; v: string }) {
  return (
    <div className="flex gap-2">
      <dt className="text-ink-muted">{k}</dt>
      <dd className="ml-auto truncate text-ink">{v}</dd>
    </div>
  );
}

function GridLegend() {
  return (
    <div className="mt-3 flex flex-wrap items-center gap-x-5 gap-y-2 border-t border-hairline pt-3 text-[11px] text-ink-secondary">
      <div className="flex items-center gap-1.5">
        <span className="text-ink-muted">partial credit</span>
        <span className="text-ink-muted">0</span>
        {RAMP.map((c) => (
          <span key={c} className="h-3 w-5 rounded-[2px]" style={{ background: c }} />
        ))}
        <span className="text-ink-muted">1</span>
      </div>
      <div className="flex items-center gap-1.5">
        <span
          aria-hidden
          className="size-1.5 rounded-full"
          style={{ background: "var(--status-good)" }}
        />
        passed
      </div>
      <div className="flex items-center gap-1.5">
        <span
          aria-hidden
          className="h-3 w-5 rounded-[2px]"
          style={{ background: "var(--status-critical)" }}
        />
        errored
      </div>
      <div className="flex items-center gap-1.5">
        <span
          aria-hidden
          className="h-3 w-5 rounded-[2px]"
          style={{ background: "var(--surface-sunken)" }}
        />
        not run
      </div>
      <div className="flex items-center gap-1.5">
        <span
          aria-hidden
          className="rounded border border-hairline px-1 text-[9px] font-semibold uppercase tracking-wide text-ink-muted"
        >
          you
        </span>
        your task · not in the headline curve
      </div>
    </div>
  );
}

function GridTable({ data, visible }: { data: TasksResponse; visible: Set<string> }) {
  const rows = data.cells.filter((c) => visible.has(`${c.split}/${c.taskId}`));
  return (
    <div className="max-h-[420px] overflow-auto">
      <table className="w-full min-w-[560px] text-left text-xs">
        <thead className="sticky top-0 bg-surface">
          <tr className="border-b border-hairline text-ink-muted">
            <th className="py-2 pr-3 font-medium">Task</th>
            <th className="py-2 pr-3 font-medium">Split</th>
            <th className="py-2 pr-3 font-medium">Epoch</th>
            <th className="py-2 pr-3 font-medium">Status</th>
            <th className="py-2 pr-3 font-medium">Partial credit</th>
            <th className="py-2 pr-3 font-medium">Steps</th>
          </tr>
        </thead>
        <tbody style={{ fontVariantNumeric: "tabular-nums" }}>
          {rows.map((c) => (
            <tr key={`${c.split}/${c.taskId}/${c.epoch}`} className="border-b border-hairline last:border-0">
              <td className="py-1.5 pr-3 text-ink">
                <Link href={episodeHref(c)} className="underline underline-offset-2 hover:text-ink-secondary">
                  {c.taskId}
                </Link>
              </td>
              <td className="py-1.5 pr-3 text-ink-secondary">{c.split}</td>
              <td className="py-1.5 pr-3 text-ink-secondary">{c.epoch}</td>
              <td className="py-1.5 pr-3 text-ink">{c.status}</td>
              <td className="py-1.5 pr-3 text-ink">{fmtPct(c.partialCredit)}</td>
              <td className="py-1.5 pr-3 text-ink-secondary">{c.steps}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
