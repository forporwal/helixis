"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { Card, Disclosure, EmptyState, Pill, fmtPct, fmtUsd } from "./ui";
import type { TrajectoryAssertion, TrajectoryMessage, TrajectoryResponse } from "@/lib/types";

/**
 * Full episode transcript: what the agent was told, what it thought, which
 * tools it called, and which assertions graded the outcome.
 *
 * Reading order matters when debugging a failure, so the page leads with the
 * verdict (status + assertions), then the transcript. Long payloads (system
 * prompt, tool results, reasoning) start collapsed — the spine of the
 * conversation stays scannable.
 */

function prettyJson(raw: string): string {
  try {
    return JSON.stringify(JSON.parse(raw), null, 2);
  } catch {
    return raw;
  }
}

const ROLE_LABEL: Record<TrajectoryMessage["role"], string> = {
  system: "System",
  user: "User",
  assistant: "Assistant",
  tool: "Tool result",
};

function Collapsible({
  summary,
  children,
  defaultOpen = false,
}: {
  summary: string;
  children: React.ReactNode;
  defaultOpen?: boolean;
}) {
  const [open, setOpen] = useState(defaultOpen);
  return (
    <div className="rounded-lg border border-hairline">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        aria-expanded={open}
        className="group flex w-full items-center gap-2 px-3 py-2 text-left text-[11px] font-medium text-ink-secondary transition-colors hover:bg-sunken"
      >
        <Disclosure open={open} />
        {summary}
      </button>
      {open ? <div className="border-t border-hairline px-3 py-2">{children}</div> : null}
    </div>
  );
}

function Mono({ text }: { text: string }) {
  return (
    <pre className="max-h-96 overflow-auto whitespace-pre-wrap break-words font-mono text-[11px] leading-relaxed text-ink-secondary">
      {text}
    </pre>
  );
}

function Message({ msg }: { msg: TrajectoryMessage }) {
  const longAside = msg.role === "system" || msg.role === "tool";
  return (
    <li className="flex gap-3">
      <div className="w-20 shrink-0 pt-0.5 text-right">
        <span
          className="text-[10px] font-semibold uppercase tracking-wide"
          style={{
            color:
              msg.role === "assistant"
                ? "var(--series-train)"
                : msg.role === "user"
                  ? "var(--series-heldout)"
                  : "var(--text-muted)",
          }}
        >
          {ROLE_LABEL[msg.role]}
        </span>
        <div className="text-[10px] text-ink-muted" style={{ fontVariantNumeric: "tabular-nums" }}>
          #{msg.index}
        </div>
      </div>

      <div className="min-w-0 flex-1 space-y-2 border-l border-hairline pb-4 pl-3">
        {msg.reasoning ? (
          <Collapsible summary="Reasoning">
            <Mono text={msg.reasoning} />
          </Collapsible>
        ) : null}

        {msg.content ? (
          longAside ? (
            <Collapsible summary={`${ROLE_LABEL[msg.role]} content (${msg.content.length.toLocaleString()} chars)`}>
              <Mono text={msg.role === "tool" ? prettyJson(msg.content) : msg.content} />
            </Collapsible>
          ) : (
            <p className="whitespace-pre-wrap text-xs leading-relaxed text-ink">{msg.content}</p>
          )
        ) : null}

        {msg.toolCalls.map((tc) => (
          <Collapsible key={tc.id || tc.name} summary={`Tool call · ${tc.name}`}>
            <Mono text={prettyJson(tc.arguments)} />
          </Collapsible>
        ))}

        {msg.truncated ? (
          <p className="text-[10px] text-ink-muted">
            Content truncated for display — the full record is in the trajectory file on disk.
          </p>
        ) : null}
      </div>
    </li>
  );
}

function AssertionsTable({ assertions }: { assertions: TrajectoryAssertion[] }) {
  if (!assertions.length) {
    return <EmptyState title="No assertions recorded" hint="The grader writes assertions at the end of the episode." />;
  }
  return (
    <div className="overflow-x-auto">
      <table className="w-full min-w-[480px] text-left text-xs">
        <thead>
          <tr className="border-b border-hairline text-ink-muted">
            <th className="py-2 pr-3 font-medium">Result</th>
            <th className="py-2 pr-3 font-medium">Check</th>
            <th className="py-2 pr-3 font-medium">Parameters</th>
          </tr>
        </thead>
        <tbody>
          {assertions.map((a, i) => (
            <tr key={i} className={`border-b border-hairline last:border-0 ${a.excluded ? "opacity-50" : ""}`}>
              <td className="py-1.5 pr-3">
                <Pill tone={a.excluded ? "neutral" : a.passed ? "good" : "critical"}>
                  {a.excluded ? "excluded" : a.passed ? "pass" : "fail"}
                </Pill>
              </td>
              <td className="py-1.5 pr-3 font-mono text-[11px] text-ink">{a.type}</td>
              <td className="py-1.5 pr-3">
                <pre className="max-w-md overflow-x-auto whitespace-pre-wrap break-words font-mono text-[10px] text-ink-muted">
                  {JSON.stringify(a.params)}
                </pre>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function HeaderStat({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-lg border border-hairline px-2.5 py-2">
      <div className="text-[10px] text-ink-muted">{label}</div>
      <div className="text-sm font-semibold text-ink" style={{ fontVariantNumeric: "tabular-nums" }}>
        {value}
      </div>
    </div>
  );
}

export function TrajectoryViewer({
  epoch,
  split,
  taskId,
}: {
  epoch: number;
  split: string;
  taskId: string;
}) {
  const [data, setData] = useState<TrajectoryResponse | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let alive = true;
    (async () => {
      try {
        const res = await fetch(
          `/api/trajectory?epoch=${epoch}&split=${encodeURIComponent(split)}&task=${encodeURIComponent(taskId)}`,
          { cache: "no-store" },
        );
        const json = await res.json();
        if (!alive) return;
        if (!res.ok) throw new Error(json.error ?? `${res.status} ${res.statusText}`);
        setData(json as TrajectoryResponse);
      } catch (err) {
        if (alive) setError((err as Error).message);
      }
    })();
    return () => {
      alive = false;
    };
  }, [epoch, split, taskId]);

  return (
    <main className="mx-auto w-full max-w-4xl flex-1 px-5 py-6 sm:px-8">
      <nav className="mb-4 text-xs text-ink-muted">
        {/* Trajectories are reached by selecting a cell in the task grid, so
            the crumb returns there rather than to the home page. */}
        <Link href="/tasks" className="underline underline-offset-2 hover:text-ink">
          Tasks
        </Link>{" "}
        / epoch {epoch} / {split} / <span className="font-mono text-ink-secondary">{taskId}</span>
      </nav>

      {error ? (
        <Card title="Episode unavailable">
          <EmptyState title={error} hint="The episode may not have been recorded yet, or the runs directory moved." />
        </Card>
      ) : !data ? (
        <Card title={taskId}>
          <EmptyState title="Loading transcript…" />
        </Card>
      ) : (
        <div className="flex flex-col gap-4">
          <Card
            title={data.episode.taskId}
            subtitle={`epoch ${data.episode.epoch} · ${data.episode.split} · ${data.episode.domain} · ${data.episode.tier}${data.simulated ? " · simulated" : ""}`}
            action={
              <Pill tone={data.episode.status === "pass" ? "good" : data.episode.status === "error" ? "critical" : "warning"}>
                {data.episode.status}
              </Pill>
            }
          >
            <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
              <HeaderStat label="Partial credit" value={fmtPct(data.episode.partialCredit)} />
              <HeaderStat label="Steps" value={String(data.episode.steps)} />
              <HeaderStat
                label="Tokens in / out"
                value={`${data.episode.tokensIn.toLocaleString()} / ${data.episode.tokensOut.toLocaleString()}`}
              />
              <HeaderStat label="Cost" value={fmtUsd(data.episode.costUsd)} />
            </div>
            <dl className="mt-3 space-y-1 text-[11px] text-ink-secondary">
              <div className="flex gap-2">
                <dt className="text-ink-muted">Model</dt>
                <dd className="ml-auto truncate font-mono">{data.episode.model || "—"}</dd>
              </div>
              <div className="flex gap-2">
                <dt className="text-ink-muted">Skills injected</dt>
                <dd className="ml-auto truncate">
                  {data.episode.injectedSkills.length ? data.episode.injectedSkills.join(", ") : "none"}
                </dd>
              </div>
              <div className="flex gap-2">
                <dt className="text-ink-muted">Ran</dt>
                <dd className="ml-auto" style={{ fontVariantNumeric: "tabular-nums" }}>
                  {data.episode.startedAt} → {data.episode.finishedAt}
                </dd>
              </div>
            </dl>
            {data.episode.error ? (
              <p className="mt-3 rounded-lg border border-hairline p-3 text-xs leading-relaxed" style={{ color: "var(--status-critical)" }}>
                {data.episode.error}
              </p>
            ) : null}
          </Card>

          <Card
            title="Grading assertions"
            subtitle="How the episode was scored. Excluded checks do not count toward partial credit."
          >
            <AssertionsTable assertions={data.assertions} />
          </Card>

          <Card
            title="Transcript"
            subtitle={`${data.messages.length} records · system prompt, tool results and reasoning start collapsed`}
          >
            {data.messages.length ? (
              <ol className="mt-1 flex flex-col">
                {data.messages.map((m) => (
                  <Message key={m.index} msg={m} />
                ))}
              </ol>
            ) : (
              <EmptyState
                title="Transcript not readable"
                hint="The trajectory file referenced by the database is missing or outside the runs directory."
              />
            )}
          </Card>
        </div>
      )}
    </main>
  );
}
