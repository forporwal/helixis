"use client";

import Link from "next/link";
import { ArrowRight, BookOpenCheck, ListChecks, Play, ShieldCheck, Sparkles } from "lucide-react";
import { ProposalDecisionButtons } from "./ProposalDecisionButtons";
import { TaskProposalDecisionButtons } from "./TaskProposalDecisionButtons";
import { TrainCycleButton } from "./TrainCycleButton";
import { Card, EmptyState } from "./ui";
import type { ActionItem, ActionsResponse } from "@/lib/types";

/**
 * What needs you, in the order it needs you.
 *
 * Home's job is to close the loop: a training run that produces a proposal
 * nobody sees produced nothing. Every row therefore carries its decision inline
 * *and* a link to the page that shows it in full — the feed is the fast path,
 * not a replacement for the detail surfaces.
 *
 * `Row` switches exhaustively on `item.kind`; adding a member to the union
 * without a case here is a compile error, which is what keeps later specs from
 * silently rendering blank rows.
 */

function RowShell({
  icon: Icon,
  tag,
  title,
  href,
  hrefLabel,
  needsHuman,
  children,
}: {
  icon: typeof ShieldCheck;
  tag: string;
  title: React.ReactNode;
  href: string;
  hrefLabel: string;
  needsHuman: boolean;
  children?: React.ReactNode;
}) {
  return (
    <li
      className="border-b border-hairline py-3 last:border-0"
      style={
        // Needs-human rows get a marked left rail so urgency survives a
        // greyscale print or a colorblind reader — the "needs you" tag says it
        // in words as well.
        needsHuman
          ? { borderLeft: "3px solid var(--status-warning)", paddingLeft: 10 }
          : { borderLeft: "3px solid transparent", paddingLeft: 10 }
      }
    >
      <div className="flex items-start gap-3">
        <span
          aria-hidden
          className="mt-0.5 flex size-7 shrink-0 items-center justify-center rounded-lg text-ink-muted"
          style={{ background: "var(--surface-sunken)" }}
        >
          <Icon className="size-4" />
        </span>
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
            <span className="text-[10px] font-semibold uppercase tracking-wider text-ink-muted">
              {tag}
            </span>
            {needsHuman ? (
              <span className="rounded-full border border-hairline bg-sunken px-1.5 py-0.5 text-[10px] font-medium text-ink-secondary">
                needs you
              </span>
            ) : null}
            <Link
              href={href}
              className="ml-auto inline-flex items-center gap-1 text-[11px] font-medium text-primary hover:underline"
            >
              {hrefLabel}
              <ArrowRight aria-hidden className="size-3" />
            </Link>
          </div>
          <p className="mt-1 text-sm font-medium text-ink">{title}</p>
          {children}
        </div>
      </div>
    </li>
  );
}

function Row({ item, onDecided }: { item: ActionItem; onDecided: () => void }) {
  switch (item.kind) {
    case "policy-proposal": {
      const p = item.proposal;
      const findings = p.proverFindings.length;
      return (
        <RowShell
          icon={ShieldCheck}
          tag="Policy proposal"
          title={p.ruleName || p.chunkId}
          href={item.href}
          hrefLabel="Containment"
          needsHuman={item.needsHuman}
        >
          <p className="mt-1 text-[11px] leading-relaxed text-ink-secondary">
            {p.intentSummary || "No intent summary recorded."}
          </p>
          <p className="mt-1 text-[10px] text-ink-muted">
            prover: {findings} finding{findings === 1 ? "" : "s"}
            {item.needsHuman ? " · human approval required" : ""}
          </p>
          <ProposalDecisionButtons chunkId={p.chunkId} onDecided={onDecided} />
        </RowShell>
      );
    }
    case "train-nudge":
      return (
        <RowShell
          icon={Play}
          tag="Ready to train"
          title={`${item.newRealEpisodes} new real session${
            item.newRealEpisodes === 1 ? "" : "s"
          } since the last distillation`}
          href={item.href}
          hrefLabel="Lab"
          needsHuman={false}
        >
          <p className="mt-1 text-[11px] leading-relaxed text-ink-secondary">
            Past the threshold of {item.threshold}.{" "}
            {item.autoTrain
              ? "Auto-training is on, so this starts on its own — the button runs it now."
              : "Training folds what went wrong in real use into the wiki, and the new skills reach Helixis Claw within 30s."}
          </p>
          {/* The nudge carries its own trigger: sending the operator to Lab to
              find the right button is how a loop stops closing. */}
          <TrainCycleButton onStarted={onDecided} />
        </RowShell>
      );
    case "skills-live":
      return (
        <RowShell
          icon={BookOpenCheck}
          tag="New skills live"
          title={`${item.skills.length} new skill${
            item.skills.length === 1 ? "" : "s"
          } now in Helixis Claw`}
          href={item.href}
          hrefLabel="Wiki"
          needsHuman={false}
        >
          <p className="mt-1 text-[11px] leading-relaxed text-ink-secondary">
            {item.skills.slice(0, 3).join(", ")}
            {item.skills.length > 3 ? `, +${item.skills.length - 3} more` : ""} — distilled
            from real sessions and delivered at wiki generation {item.generation}.
          </p>
        </RowShell>
      );
    case "task-proposal":
      return (
        <RowShell
          icon={Sparkles}
          tag="Proposed task"
          title={`Helixis noticed you ${item.title}`}
          href={item.href}
          hrefLabel="Review"
          needsHuman={item.needsHuman}
        >
          <p className="mt-1 text-[11px] leading-relaxed text-ink-secondary">
            Seen in {item.occurrences} real session
            {item.occurrences === 1 ? "" : "s"}. Approving adds it to your
            curriculum as a draft — it cannot run until you finish its verifier.
          </p>
          <p className="mt-1 text-[10px] text-ink-muted">
            {item.domain} · {item.taskId}
          </p>
          <TaskProposalDecisionButtons id={item.taskId} onDecided={onDecided} />
        </RowShell>
      );
  }
}

export function ActionFeed({
  data,
  refreshing,
  onRefetch,
}: {
  data: ActionsResponse | null;
  refreshing: boolean;
  onRefetch: () => void;
}) {
  const items = data?.items ?? [];
  const needsHuman = data?.counts.needsHuman ?? 0;

  return (
    <Card
      title="Needs you"
      subtitle={
        items.length
          ? `${items.length} open item${items.length === 1 ? "" : "s"}${
              needsHuman ? ` · ${needsHuman} awaiting your approval` : ""
            }`
          : "Approvals, training nudges, and proposed updates land here."
      }
      refreshing={refreshing}
    >
      {items.length === 0 ? (
        <EmptyState
          icon={ListChecks}
          title="Nothing needs you"
          hint="Helixis Claw is learning on its own. Policy proposals, training nudges, and proposed updates appear here the moment a run produces one."
        />
      ) : (
        <ul>
          {items.map((item) => (
            <Row key={item.id} item={item} onDecided={onRefetch} />
          ))}
        </ul>
      )}
    </Card>
  );
}
