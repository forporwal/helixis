"use client";

import Link from "next/link";
import { AlertTriangle, FileSearch, Sparkles } from "lucide-react";
import { TaskProposalDecisionButtons } from "./TaskProposalDecisionButtons";
import { Card, EmptyState, Pill } from "./ui";
import { usePoll } from "./usePoll";
import type { TaskProposalDetailResponse } from "@/lib/types";

const INTERVAL = 15_000;

/**
 * The full case for one proposed task (spec 05, Req 2.1).
 *
 * Three things have to be on this page for approval to be a judgment rather
 * than a reflex: what the miner wants to add, what evidence it has, and what
 * the drafted verifier would check. The last one matters most — an LLM wrote
 * both the task and its grader, and the operator is the only ground truth in
 * that loop, so the verifier is shown in full rather than summarized.
 */

function tone(status: string): "good" | "warning" | "neutral" | "critical" {
  if (status === "approved") return "good";
  if (status === "rejected") return "neutral";
  if (status === "invalid") return "critical";
  return "warning";
}

export function TaskProposalReview({ id }: { id: string }) {
  const detail = usePoll<TaskProposalDetailResponse>(
    `/api/task-proposals/${encodeURIComponent(id)}`,
    INTERVAL,
  );
  const proposal = detail.data?.proposal ?? null;
  const episodes = detail.data?.episodes ?? [];

  if (detail.data && !proposal) {
    return (
      <Card title="Proposed task">
        <EmptyState
          icon={FileSearch}
          title="No such proposal"
          hint={`Nothing is stored under \`${id}\`. It may have been decided and cleaned up, or the link is stale.`}
        />
      </Card>
    );
  }

  return (
    <div className="flex flex-col gap-4">
      <Card
        title={proposal?.title || id}
        subtitle={
          proposal
            ? `Mined from ${proposal.occurrences} real session${
                proposal.occurrences === 1 ? "" : "s"
              } · drafted by ${proposal.modelId || "the distiller tier"}`
            : "Loading…"
        }
        refreshing={detail.refreshing}
      >
        {proposal ? (
          <div className="flex flex-col gap-3">
            <div className="flex flex-wrap items-center gap-1.5">
              <Pill tone={tone(proposal.status)}>{proposal.status}</Pill>
              <Pill tone="neutral">{proposal.domain}</Pill>
              <Pill tone="warning">{proposal.taskType}</Pill>
              <span className="font-mono text-[11px] text-ink-muted">{proposal.id}</span>
            </div>

            {proposal.reason ? (
              <div
                className="flex items-start gap-2 rounded-md border border-hairline p-2 text-[11px] leading-relaxed"
                style={{ color: "var(--status-warning)" }}
              >
                <AlertTriangle aria-hidden className="mt-0.5 size-3.5 shrink-0" />
                <span>{proposal.reason}</span>
              </div>
            ) : null}

            {proposal.status === "pending" ? (
              <TaskProposalDecisionButtons id={proposal.id} onDecided={detail.refetch} />
            ) : null}
          </div>
        ) : null}
      </Card>

      {proposal ? (
        <>
          <Card
            title="Drafted manifest entry"
            subtitle="Exactly what `helixis task add` would receive on approval."
          >
            <Block>{proposal.draftYaml}</Block>
          </Card>

          <Card
            title="Drafted verify.py"
            subtitle="Written by a model, reviewed by nobody. This is the part to read."
          >
            <p className="mb-2 text-[11px] leading-relaxed text-ink-secondary">
              On approval this is written as{" "}
              <code className="font-mono">verify.py.draft</code> and the task is marked{" "}
              <code className="font-mono">draft: true</code>, so it is excluded from every
              run until you finish it and rename it. A drafted verifier never grades real
              work unreviewed.
            </p>
            <Block>{proposal.verifyDraft || "(no verifier drafted)"}</Block>
          </Card>

          <Card
            title="Drafted reset.py"
            subtitle="Must be idempotent — running it twice is the same as running it once."
          >
            <Block>{proposal.resetDraft || "(no reset drafted)"}</Block>
          </Card>

          <Card
            title="Evidence"
            subtitle={`The ${episodes.length} real session${
              episodes.length === 1 ? "" : "s"
            } this workflow was mined from.`}
          >
            {episodes.length === 0 ? (
              <EmptyState
                icon={Sparkles}
                title="Source sessions unavailable"
                hint="The proposal cites episode ids that are no longer in the database."
              />
            ) : (
              <ul className="divide-y divide-hairline">
                {episodes.map((ep) => (
                  <li key={ep.id} className="flex items-center gap-2 py-2">
                    <div className="min-w-0 flex-1">
                      <span className="truncate font-mono text-[11px] text-ink">
                        {ep.taskId}
                      </span>
                      <div className="mt-0.5 text-[10px] text-ink-muted">
                        episode {ep.id} · {ep.finishedAt}
                      </div>
                    </div>
                    <Link
                      href={ep.href}
                      className="shrink-0 text-[11px] font-medium text-primary hover:underline"
                    >
                      Open transcript
                    </Link>
                  </li>
                ))}
              </ul>
            )}
          </Card>
        </>
      ) : null}
    </div>
  );
}

function Block({ children }: { children: React.ReactNode }) {
  return (
    <pre className="max-h-96 overflow-auto whitespace-pre-wrap rounded-md border border-hairline bg-sunken p-2 font-mono text-[11px] leading-relaxed text-ink-secondary">
      {children}
    </pre>
  );
}
