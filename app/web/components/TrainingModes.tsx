"use client";

import { useState } from "react";
import { AlertTriangle, FlaskConical, Info, Target, Waypoints } from "lucide-react";
import { Card, EmptyState, fmtUsd } from "./ui";
import type { PreflightResponse, TrainingMode } from "@/lib/types";

/**
 * How you actually train, as three choices instead of eleven CLI buttons.
 *
 * The old panel exposed the plumbing: one button per `helixis` subcommand, with
 * no indication that `distill`/`pages`/`mine-tasks` are steps *inside* a cycle
 * rather than peers of "Start epoch", and no way to tell whether Start would
 * reach a real model or the offline stub. Both questions are answered here
 * before the click: pick what you want to happen, read what it will do, start.
 *
 * Every fact in the detail panel comes from `helixis preflight`, so this cannot
 * drift from what the engine would really select — see /api/preflight.
 */

type ModeDef = {
  id: TrainingMode;
  label: string;
  tagline: string;
  icon: typeof FlaskConical;
  /** What lands when it finishes — the reason to pick this one. */
  produces: string;
  cta: string;
};

const MODES: ModeDef[] = [
  {
    id: "simulated",
    label: "Simulated",
    tagline: "Offline stub · free · seconds",
    icon: FlaskConical,
    produces:
      "A demo curve that exercises the whole loop without a model. Episodes are marked simulated and are never reported as results.",
    cta: "Run simulated epoch",
  },
  {
    id: "benchmark",
    label: "Benchmark",
    tagline: "AutomationBench · real model · costs money",
    icon: Target,
    produces:
      "Real graded episodes on the frozen bench set. This is what moves the headline learning curve.",
    cta: "Run benchmark epoch",
  },
  {
    id: "real",
    label: "Real trajectories",
    tagline: "Your Claw sessions · vLLM judge",
    icon: Waypoints,
    produces:
      "Ingests and judges captured sessions, distills failures into new wiki skills, then proposes tasks. Skills reach the agent within ~30s. Does not move the headline curve.",
    cta: "Run training cycle",
  },
];

export function TrainingModes({
  data,
  refreshing,
  cliMissing,
  onAction,
}: {
  data: PreflightResponse | null;
  refreshing: boolean;
  cliMissing: boolean;
  onAction: () => void;
}) {
  const [selected, setSelected] = useState<TrainingMode | null>(null);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<{ tone: "ok" | "err"; text: string } | null>(null);
  const [epoch, setEpoch] = useState<number | null>(null);

  const pf = data?.preflight ?? null;
  // Default to whatever the engine would really pick, so the page opens on the
  // truth rather than on a guess the operator then has to correct.
  const mode: TrainingMode = selected ?? pf?.activeMode ?? "simulated";
  const def = MODES.find((m) => m.id === mode)!;
  const readiness = pf?.modes[mode] ?? null;
  const effectiveEpoch = epoch ?? pf?.nextEpoch ?? 0;

  async function start() {
    if (!pf) return;
    setBusy(true);
    setMessage(null);
    const body =
      mode === "real"
        ? { action: "train-cycle" }
        : { action: "start-epoch", epoch: effectiveEpoch, split: "train", mode };
    try {
      const res = await fetch("/api/control", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify(body),
      });
      const json = await res.json();
      if (!res.ok) throw new Error(json.hint ? `${json.error} ${json.hint}` : json.error);
      setMessage({
        tone: "ok",
        text:
          json.note ??
          (mode === "real"
            ? "Training cycle started — watch the Engine jobs panel."
            : `Epoch ${effectiveEpoch} started in ${mode} mode — watch the Engine jobs panel.`),
      });
      onAction();
    } catch (err) {
      setMessage({ tone: "err", text: (err as Error).message });
    } finally {
      setBusy(false);
    }
  }

  if (!pf) {
    // Three genuinely different states, and conflating them is what made this
    // card read as broken: still loading, engine unreachable, engine answered
    // badly. Only the first one is allowed to say "checking".
    const failed = data !== null && !data.available;
    return (
      <Card title="Training mode" subtitle="Pick what you want the next run to do.">
        <EmptyState
          title={failed ? "Cannot reach the engine" : "Checking engine…"}
          hint={
            failed
              ? `${data.error} Install it where the dashboard runs (\`pip install -e app/engine\`), or put its virtualenv's bin on PATH before starting the dashboard.`
              : "Asking the engine which backends are reachable and what a run would cost."
          }
          icon={failed ? AlertTriangle : Info}
        />
      </Card>
    );
  }

  const blocked = !readiness?.available;

  return (
    <Card
      title="Training mode"
      subtitle="Pick what you want the next run to do. Every number below comes from the engine, not from this page."
      refreshing={refreshing}
    >
      <div className="flex flex-col gap-4">
        <div
          className="grid gap-2 sm:grid-cols-3"
          role="radiogroup"
          aria-label="Training mode"
        >
          {MODES.map((m) => {
            const r = pf.modes[m.id];
            const active = m.id === mode;
            const Icon = m.icon;
            return (
              <button
                key={m.id}
                type="button"
                role="radio"
                aria-checked={active}
                onClick={() => {
                  setSelected(m.id);
                  setMessage(null);
                }}
                className={`flex flex-col gap-1.5 rounded-xl border p-3 text-left transition-colors ${
                  active
                    ? "border-hairline-strong bg-sunken"
                    : "border-hairline hover:bg-sunken"
                }`}
              >
                <span className="flex items-center gap-2">
                  <Icon
                    className="size-4 shrink-0"
                    style={{
                      color: active ? "var(--series-train)" : "var(--text-muted)",
                    }}
                    aria-hidden
                  />
                  <span className="text-xs font-semibold text-ink">{m.label}</span>
                  {!r.available ? (
                    <span
                      className="ml-auto text-[9px] font-semibold uppercase tracking-wide"
                      style={{ color: "var(--text-muted)" }}
                    >
                      blocked
                    </span>
                  ) : m.id === pf.activeMode ? (
                    <span
                      className="ml-auto text-[9px] font-semibold uppercase tracking-wide"
                      style={{ color: "var(--status-good)" }}
                    >
                      default
                    </span>
                  ) : null}
                </span>
                <span className="text-[10px] leading-snug text-ink-muted">
                  {m.tagline}
                </span>
              </button>
            );
          })}
        </div>

        <div className="rounded-xl border border-hairline p-3">
          <p className="text-[11px] leading-relaxed text-ink-secondary">
            {def.produces}
          </p>

          <dl className="mt-3 grid grid-cols-2 gap-x-4 gap-y-2 text-[10px]">
            {mode === "real" ? (
              <>
                <Fact label="Sessions pending" value={String(pf.real.pendingSessions)} />
                <Fact
                  label="Real episodes on record"
                  value={String(pf.real.ingestedSessions)}
                />
                <Fact
                  label="Judge"
                  value={pf.distiller.configured ? pf.distiller.model : "not configured"}
                />
                <Fact
                  label="New since distill"
                  value={`${pf.real.newRealEpisodes} / ${pf.real.threshold}`}
                />
              </>
            ) : (
              <>
                <Fact
                  label="Agent"
                  value={mode === "simulated" ? "deterministic stub" : pf.agent.model}
                />
                <Fact
                  label="Tasks"
                  value={`${pf.tasks.train} train · ${pf.tasks.heldout} held-out`}
                />
                <Fact
                  label="Est. cost"
                  value={mode === "simulated" ? "free" : "metered, capped"}
                />
                <Fact
                  label="Epoch budget"
                  value={`${fmtUsd(pf.budget.epochSpentUsd)} / ${fmtUsd(pf.budget.epochCapUsd)}`}
                />
              </>
            )}
          </dl>

          {readiness?.blockers.map((b) => (
            <Note key={b} tone="block" text={b} />
          ))}
          {readiness?.warnings.map((w) => (
            <Note key={w} tone="warn" text={w} />
          ))}
        </div>

        <div className="flex flex-wrap items-center gap-2">
          {mode !== "real" ? (
            <>
              <label htmlFor="mode-epoch" className="text-xs text-ink-secondary">
                Epoch
              </label>
              <input
                id="mode-epoch"
                type="number"
                min={0}
                max={999}
                value={effectiveEpoch}
                onChange={(e) => setEpoch(Number(e.target.value))}
                className="w-16 rounded-md border border-hairline bg-surface px-2 py-1 text-xs text-ink"
                style={{ fontVariantNumeric: "tabular-nums" }}
              />
            </>
          ) : null}
          <button
            type="button"
            onClick={() => void start()}
            disabled={blocked || busy || cliMissing}
            className="btn-primary rounded-md px-3 py-1.5 text-xs font-medium disabled:opacity-40"
          >
            {busy ? "…" : def.cta}
          </button>
        </div>

        {message ? (
          <p
            className="text-[11px] leading-relaxed"
            style={{
              color:
                message.tone === "err" ? "var(--status-critical)" : "var(--text-secondary)",
            }}
          >
            {message.text}
          </p>
        ) : null}
      </div>
    </Card>
  );
}

function Fact({ label, value }: { label: string; value: string }) {
  return (
    <div className="min-w-0">
      <dt className="text-ink-muted">{label}</dt>
      <dd className="truncate font-medium text-ink" title={value}>
        {value}
      </dd>
    </div>
  );
}

function Note({ tone, text }: { tone: "block" | "warn"; text: string }) {
  const color = tone === "block" ? "var(--status-critical)" : "var(--status-warning)";
  return (
    <p className="mt-2 flex items-start gap-1.5 text-[10px] leading-relaxed" style={{ color }}>
      <AlertTriangle className="mt-px size-3 shrink-0" aria-hidden />
      <span>{text}</span>
    </p>
  );
}
