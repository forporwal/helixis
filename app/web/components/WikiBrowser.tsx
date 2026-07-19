"use client";

import { useMemo, useState } from "react";
import Link from "next/link";
import { BookOpen, FileText, Search } from "lucide-react";
import { Card, Disclosure, EmptyState, FilterChip, NoMatches, Toolbar } from "./ui";
import { StartRunAction } from "./StartRunAction";
import { Input } from "./ui/input";
import { usePoll } from "./usePoll";
import type { SkillItem, SkillsResponse, WikiPagesResponse } from "@/lib/types";

/**
 * Wiki growth + skill browser.
 *
 * The growth chart is ONE series, so it takes categorical slot 1 and needs no
 * legend -- the heading already says what is plotted. Bars are capped well under
 * the band width so the leftover reads as air, with a 4px rounded cap and a
 * square foot on the baseline.
 */

function GrowthChart({ growth }: { growth: SkillsResponse["growth"] }) {
  if (!growth.length) return null;
  const max = Math.max(...growth.map((g) => g.cumulative), 1);
  const H = 64;

  return (
    <div>
      <h3 className="mb-2 text-xs font-semibold uppercase tracking-wide text-ink-secondary">
        Cumulative skills by epoch
      </h3>
      {/* Slots are flex-1 but the bars cap at 24px, so an unconstrained
          container spreads six bars across the full card and reads as scattered
          marks rather than a series. Cap the track to what the bars can
          actually fill -- no 160px floor, or a single bar strands itself in the
          middle of empty space.

          Height is left to content: a column is value label + bar + epoch
          label, so pinning the track to the bar height alone made the tallest
          column overflow upward into the heading. `items-end` still lines the
          epoch labels up along one baseline. */}
      <div className="flex items-end gap-1.5" style={{ maxWidth: growth.length * 40 }}>
        {growth.map((g) => {
          const h = Math.max((g.cumulative / max) * H, 2);
          return (
            <div key={g.epoch} className="flex flex-1 flex-col items-center gap-1">
              <span
                className="text-[10px] font-medium text-ink"
                style={{ fontVariantNumeric: "tabular-nums" }}
              >
                {g.cumulative}
              </span>
              <div
                className="w-full max-w-6"
                style={{
                  height: h,
                  background: g.gatedOut ? "var(--status-warning)" : "var(--series-train)",
                  borderRadius: "4px 4px 0 0",
                }}
                title={`epoch ${g.epoch}: +${g.nSkills} from ${g.nFailures} failures${g.gatedOut ? " (gated out)" : ""}`}
              />
              <span
                className="text-[10px] text-ink-muted"
                style={{ fontVariantNumeric: "tabular-nums" }}
              >
                {g.epoch}
              </span>
            </div>
          );
        })}
      </div>
      <p className="mt-1 text-[10px] text-ink-muted">epoch</p>
    </div>
  );
}

/** Minimal markdown rendering for SKILL.md bodies -- headings, lists, bold, code. */
function Markdown({ text }: { text: string }) {
  const inline = (s: string, key: string) => {
    // Relative wiki links (../skills/...) don't resolve in the browser; keep
    // the text, drop the target.
    const unlinked = s.replace(/\[([^\]]+)\]\(([^)]+)\)/g, "$1");
    const parts = unlinked.split(/(\*\*[^*]+\*\*|`[^`]+`)/g);
    return parts.map((p, i) => {
      if (p.startsWith("**") && p.endsWith("**")) {
        return (
          <strong key={`${key}-${i}`} className="font-semibold text-ink">
            {p.slice(2, -2)}
          </strong>
        );
      }
      if (p.startsWith("`") && p.endsWith("`")) {
        return (
          <code key={`${key}-${i}`} className="rounded bg-sunken px-1 py-0.5 font-mono text-[11px]">
            {p.slice(1, -1)}
          </code>
        );
      }
      return <span key={`${key}-${i}`}>{p}</span>;
    });
  };

  const lines = text.split("\n");
  const out: React.ReactNode[] = [];
  let list: React.ReactNode[] = [];
  const flush = (k: string) => {
    if (list.length) {
      out.push(
        <ol key={`l-${k}`} className="ml-4 list-decimal space-y-1 text-ink-secondary">
          {list}
        </ol>,
      );
      list = [];
    }
  };

  lines.forEach((raw, i) => {
    const line = raw.trimEnd();
    const key = String(i);
    if (/^#{1,6}\s/.test(line)) {
      flush(key);
      out.push(
        <h4 key={`h-${key}`} className="mt-2 text-xs font-semibold text-ink">
          {line.replace(/^#{1,6}\s/, "")}
        </h4>,
      );
    } else if (/^\s*[-*]\s+/.test(line) || /^\s*\d+\.\s+/.test(line)) {
      list.push(
        <li key={`i-${key}`}>{inline(line.replace(/^\s*(?:[-*]|\d+\.)\s+/, ""), key)}</li>,
      );
    } else if (!line.trim()) {
      flush(key);
    } else {
      flush(key);
      out.push(
        <p key={`p-${key}`} className="text-ink-secondary">
          {inline(line, key)}
        </p>,
      );
    }
  });
  flush("end");
  return <div className="space-y-1.5 text-[12px] leading-relaxed">{out}</div>;
}

function SkillRow({ skill }: { skill: SkillItem }) {
  const [open, setOpen] = useState(false);
  return (
    <li className="border-b border-hairline last:border-0">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        aria-expanded={open}
        className="group flex w-full items-start gap-3 py-2.5 text-left transition-colors hover:bg-sunken"
      >
        {/* The name line is taller than its text -- the "gen n" badge sets the
            height -- so match it here or the chevron rides high. */}
        <Disclosure open={open} className="h-5" />
        <span className="min-w-0 flex-1">
          <span className="flex flex-wrap items-center gap-2">
            <span className="font-mono text-xs font-semibold text-ink">{skill.name}</span>
            <span className="rounded-md border border-hairline px-1.5 py-0.5 text-[10px] text-ink-muted">
              gen {skill.generation}
            </span>
            {/* Learned from real usage, not the benchmark (spec 03, Req 3.2).
                Badged rather than buried in the detail panel because it changes
                how much a reader should trust the skill: real-tier provenance
                means a judge labeled the failure, not an assertion. */}
            {skill.sourceTier.includes("real") ? (
              <span
                className="rounded-md border px-1.5 py-0.5 text-[10px] font-medium"
                style={{
                  borderColor: "var(--status-good)",
                  color: "var(--status-good)",
                }}
                title="Distilled from real Helixis Claw sessions, judge-labeled"
              >
                {skill.sourceTier === "real" ? "from real use" : "real + bench"}
              </span>
            ) : null}
            <span className="text-[10px] text-ink-muted">
              from epoch {skill.createdEpoch} · {skill.category}
            </span>
          </span>
          <span className="mt-0.5 block text-[11px] leading-relaxed text-ink-secondary">
            {skill.description}
          </span>
        </span>
      </button>

      {open ? (
        <div className="pb-3 pl-6">
          {skill.bodyAvailable ? (
            <div className="rounded-lg border border-hairline bg-sunken p-3">
              <Markdown text={skill.body} />
            </div>
          ) : (
            <p className="rounded-lg border border-dashed border-hairline p-3 text-[11px] text-ink-muted">
              SKILL.md is not readable at {skill.path} — the wiki directory may have moved.
            </p>
          )}

          {/* The point of the panel: a skill links back to the failures that produced it. */}
          <div className="mt-2.5">
            <h5 className="text-[10px] font-semibold uppercase tracking-wide text-ink-muted">
              Distilled from {skill.sourceLinks.length} failing episode
              {skill.sourceLinks.length === 1 ? "" : "s"}
            </h5>
            <ul className="mt-1.5 flex flex-wrap gap-1.5">
              {skill.sourceLinks.map((s) =>
                s.epoch !== null ? (
                  <li key={s.label}>
                    {/* Distillation only reads train failures, so the transcript lives under the train split. */}
                    <Link
                      href={`/runs/${s.epoch}/train/${encodeURIComponent(s.taskId)}`}
                      className="inline-flex items-center gap-1.5 rounded-md border border-hairline px-1.5 py-0.5 font-mono text-[10px] text-ink-secondary transition-colors hover:bg-sunken hover:text-ink"
                      title={`${s.label} — open transcript`}
                    >
                      <span
                        className="text-ink-muted"
                        style={{ fontVariantNumeric: "tabular-nums" }}
                      >
                        e{s.epoch}
                      </span>
                      {s.taskId}
                    </Link>
                  </li>
                ) : (
                  <li key={s.label}>
                    <span
                      className="inline-flex items-center gap-1.5 rounded-md border border-hairline px-1.5 py-0.5 font-mono text-[10px] text-ink-secondary"
                      title={s.label}
                    >
                      {s.taskId}
                    </span>
                  </li>
                ),
              )}
            </ul>
          </div>
        </div>
      ) : null}
    </li>
  );
}

function PageRow({ page }: { page: WikiPagesResponse["pages"][number] }) {
  const [open, setOpen] = useState(false);
  return (
    <li className="border-b border-hairline last:border-0">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        aria-expanded={open}
        className="group flex w-full items-start gap-3 py-2.5 text-left transition-colors hover:bg-sunken"
      >
        {/* Title line is text-xs (12px/16px), so the default 16px box centers
            on it exactly -- no height override needed here. */}
        <Disclosure open={open} />
        <span className="min-w-0 flex-1">
          <span className="text-xs font-semibold text-ink">{page.title}</span>
          <span className="mt-0.5 block font-mono text-[10px] text-ink-muted">wiki/pages/{page.name}</span>
        </span>
      </button>
      {open ? (
        <div className="mb-3 ml-6 rounded-lg border border-hairline bg-sunken p-3">
          <Markdown text={page.body} />
        </div>
      ) : null}
    </li>
  );
}

function PagesTab() {
  const { data } = usePoll<WikiPagesResponse>("/api/wiki/pages", 15_000);
  if (!data || data.empty) {
    return (
      <EmptyState
        icon={FileText}
        title="No overview pages yet"
        hint="The engine writes wiki/pages/*.md when pages are regenerated — run 'helixis pages', or use Regenerate wiki pages under engine operations in Lab."
      />
    );
  }
  return (
    <div className="flex flex-col gap-4">
      <ul className="max-h-[300px] overflow-y-auto">
        {data.pages.map((p) => (
          <PageRow key={p.name} page={p} />
        ))}
      </ul>
      {data.history.length ? (
        <div>
          <h3 className="mb-1 text-xs font-semibold uppercase tracking-wide text-ink-secondary">
            Wiki history
          </h3>
          <ul className="max-h-[160px] space-y-1.5 overflow-y-auto">
            {data.history.map((h, i) => (
              <li key={i} className="text-[11px] leading-relaxed text-ink-secondary">
                {h.ts ? (
                  <span className="mr-1.5 font-mono text-[10px] text-ink-muted" style={{ fontVariantNumeric: "tabular-nums" }}>
                    {h.ts.slice(0, 16).replace("T", " ")}
                  </span>
                ) : null}
                {h.summary}
              </li>
            ))}
          </ul>
        </div>
      ) : null}
    </div>
  );
}

export function WikiBrowser({
  data,
  refreshing,
}: {
  data: SkillsResponse | null;
  refreshing: boolean;
}) {
  const [tab, setTab] = useState<"skills" | "pages">("skills");
  const [query, setQuery] = useState("");
  const [cats, setCats] = useState<Set<string>>(new Set());

  const needle = query.trim().toLowerCase();

  // Categories are engine-assigned and open-ended, so the chip row is derived
  // from the data rather than hardcoded — a new category shows up on its own.
  const categories = useMemo(() => {
    const counts = new Map<string, number>();
    for (const s of data?.skills ?? []) {
      counts.set(s.category, (counts.get(s.category) ?? 0) + 1);
    }
    return [...counts.entries()].sort((a, b) => b[1] - a[1]);
  }, [data]);

  const skills = useMemo(
    () =>
      (data?.skills ?? []).filter(
        (s) =>
          (cats.size === 0 || cats.has(s.category)) &&
          (!needle ||
            s.name.toLowerCase().includes(needle) ||
            s.description.toLowerCase().includes(needle) ||
            s.category.toLowerCase().includes(needle)),
      ),
    [data, cats, needle],
  );

  function clearFilters() {
    setQuery("");
    setCats(new Set());
  }

  const filtered = needle !== "" || cats.size > 0;

  const tabs = (
    <div role="tablist" className="flex gap-1 rounded-lg border border-hairline p-0.5">
      {(["skills", "pages"] as const).map((t) => (
        <button
          key={t}
          type="button"
          role="tab"
          aria-selected={tab === t}
          onClick={() => setTab(t)}
          className={`rounded-md px-2.5 py-1 text-xs font-medium transition-colors ${
            tab === t ? "bg-sunken text-ink" : "text-ink-secondary hover:text-ink"
          }`}
        >
          {t === "skills" ? "Skills" : "Pages"}
        </button>
      ))}
    </div>
  );

  if (!data || data.empty) {
    return (
      <Card title="Wiki" subtitle="Skills distilled from failing episodes." action={tabs}>
        {tab === "pages" ? (
          <PagesTab />
        ) : (
          <EmptyState
            icon={BookOpen}
            title="The wiki is empty"
            hint="Skills appear after the distiller runs on an epoch's failures and the gate admits them. Run an epoch to produce the failures it learns from."
            action={<StartRunAction />}
          />
        )}
      </Card>
    );
  }

  return (
    <Card
      title="Wiki"
      subtitle={
        filtered
          ? `${skills.length} of ${data.skills.length} skills · generation ${data.generation}`
          : `${data.skills.length} skill${data.skills.length === 1 ? "" : "s"} · generation ${data.generation}`
      }
      refreshing={refreshing}
      action={tabs}
    >
      {tab === "pages" ? (
        <PagesTab />
      ) : (
        <div className="flex flex-col gap-4">
          <GrowthChart growth={data.growth} />
          <div>
            <h3 className="mb-2 text-xs font-semibold uppercase tracking-wide text-ink-secondary">
              Skills
            </h3>
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
                    placeholder="Filter skills by name, description, or category…"
                    aria-label="Filter skills"
                    className="h-8 rounded-lg pl-8 text-xs"
                  />
                </div>
              }
              chips={
                categories.length > 1
                  ? categories.map(([c, n]) => (
                      <FilterChip
                        key={c}
                        active={cats.has(c)}
                        count={n}
                        onClick={() =>
                          setCats((prev) => {
                            const next = new Set(prev);
                            if (next.has(c)) next.delete(c);
                            else next.add(c);
                            return next;
                          })
                        }
                      >
                        {c}
                      </FilterChip>
                    ))
                  : null
              }
            />
            {skills.length === 0 ? (
              <NoMatches onClear={clearFilters} />
            ) : (
              <ul className="max-h-[460px] overflow-y-auto">
                {skills.map((s) => (
                  <SkillRow key={s.name} skill={s} />
                ))}
              </ul>
            )}
          </div>
        </div>
      )}
    </Card>
  );
}
