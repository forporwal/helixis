"use client";

import { useState } from "react";
import { Card, EmptyState, fmtUsd } from "./ui";
import type { StatusResponse } from "@/lib/types";

/**
 * Operator controls + cost meter.
 *
 * The meter's fill carries severity (accent -> warning -> danger) against an
 * unfilled track that is a lighter step of the same ramp, so state reads across
 * the whole bar rather than only where it is filled.
 */

function Meter({
  label,
  value,
  cap,
}: {
  label: string;
  value: number;
  cap: number;
}) {
  const frac = cap > 0 ? Math.min(value / cap, 1) : 0;
  const fill =
    frac >= 0.9
      ? "var(--status-critical)"
      : frac >= 0.7
        ? "var(--status-warning)"
        : "var(--series-train)";
  return (
    <div>
      <div className="flex items-baseline justify-between gap-2">
        <span className="text-xs text-ink-secondary">{label}</span>
        <span
          className="text-xs font-semibold text-ink"
          style={{ fontVariantNumeric: "tabular-nums" }}
        >
          {fmtUsd(value)}{" "}
          <span className="font-normal text-ink-muted">/ {fmtUsd(cap)}</span>
        </span>
      </div>
      <div
        className="mt-1.5 h-2 w-full overflow-hidden rounded-full"
        style={{ background: "var(--seq-100)" }}
        role="meter"
        aria-valuenow={value}
        aria-valuemin={0}
        aria-valuemax={cap}
        aria-label={label}
      >
        <div
          className="h-full rounded-full transition-[width] duration-500"
          style={{ width: `${Math.max(frac * 100, value > 0 ? 1.5 : 0)}%`, background: fill }}
        />
      </div>
      <p className="mt-1 text-[10px] text-ink-muted">
        {(frac * 100).toFixed(1)}% of cap
      </p>
    </div>
  );
}

export function Controls({
  data,
  refreshing,
  onAction,
}: {
  data: StatusResponse | null;
  refreshing: boolean;
  onAction: () => void;
}) {
  const [busy, setBusy] = useState<string | null>(null);
  const [message, setMessage] = useState<{ tone: "ok" | "err"; text: string } | null>(null);
  const [epoch, setEpoch] = useState(0);
  const [epochs, setEpochs] = useState(6);
  const [showMore, setShowMore] = useState(false);

  async function send(action: string, extra: Record<string, unknown> = {}) {
    setBusy(action);
    setMessage(null);
    try {
      const res = await fetch("/api/control", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ action, ...extra }),
      });
      const json = await res.json();
      if (!res.ok) throw new Error(json.hint ? `${json.error} ${json.hint}` : json.error);
      setMessage({ tone: "ok", text: json.note ?? `Started: ${action}` });
      onAction();
    } catch (err) {
      setMessage({ tone: "err", text: (err as Error).message });
    } finally {
      setBusy(null);
    }
  }

  if (!data) {
    return (
      <Card title="Controls" subtitle="Run control and budget.">
        <EmptyState title="Status unavailable" />
      </Card>
    );
  }

  const cliMissing = !data.controls.helixisAvailable;
  const nextEpoch = data.currentEpoch === null ? 0 : data.currentEpoch + 1;

  return (
    <Card
      title="Budget & operations"
      subtitle={
        data.running
          ? `Epoch ${data.currentEpoch} running`
          : data.currentEpoch !== null
            ? `Idle · last epoch ${data.currentEpoch}`
            : "Idle · no runs yet"
      }
      refreshing={refreshing}
      action={
        <span className="flex items-center gap-1.5 text-[11px] text-ink-secondary">
          <span
            aria-hidden
            className={`size-2 rounded-full ${data.running ? "pulse-live" : ""}`}
            style={{
              background: data.running ? "var(--status-good)" : "var(--text-muted)",
            }}
          />
          {data.running ? "live" : "idle"}
        </span>
      }
    >
      <div className="flex flex-col gap-4">
        <div className="grid grid-cols-2 gap-3">
          <Stat label="Episodes" value={String(data.episodeCount)} />
          <Stat label="Skills" value={String(data.skillCount)} />
          <Stat label="Wiki gen" value={String(data.wikiGeneration)} />
          <Stat
            label="Tokens in / out"
            value={`${fmtTokens(data.tokens.totalIn)} / ${fmtTokens(data.tokens.totalOut)}`}
          />
        </div>

        <div className="flex flex-col gap-3">
          <Meter label="Total spend" value={data.cost.total} cap={data.cost.totalCap} />
          <Meter
            label={`Epoch ${data.currentEpoch ?? 0} spend`}
            value={data.cost.epochCost}
            cap={data.cost.epochCap}
          />
        </div>

        <div className="border-t border-hairline pt-3">
          <div className="mb-2 flex items-center gap-2">
            <label htmlFor="epoch-input" className="text-xs text-ink-secondary">
              Epoch
            </label>
            <input
              id="epoch-input"
              type="number"
              min={0}
              max={999}
              value={epoch}
              onChange={(e) => setEpoch(Number(e.target.value))}
              className="w-16 rounded-md border border-hairline bg-surface px-2 py-1 text-xs text-ink"
              style={{ fontVariantNumeric: "tabular-nums" }}
            />
            <button
              type="button"
              onClick={() => setEpoch(nextEpoch)}
              className="text-[10px] text-ink-muted underline underline-offset-2"
            >
              next ({nextEpoch})
            </button>
          </div>

          <div className="flex flex-wrap gap-2">
            <Btn
              disabled={cliMissing || busy !== null}
              busy={busy === "heldout"}
              onClick={() => send("heldout", { epoch })}
            >
              Held-out eval
            </Btn>
            <Btn
              disabled={busy !== null}
              busy={busy === "stop"}
              onClick={() => send("stop")}
            >
              Stop
            </Btn>
          </div>

          <button
            type="button"
            onClick={() => setShowMore((v) => !v)}
            aria-expanded={showMore}
            className="mt-3 text-[11px] font-medium text-ink-secondary underline underline-offset-2"
          >
            {showMore ? "Hide advanced operations" : "Advanced engine operations…"}
          </button>

          {showMore ? (
            <div className="mt-2 flex flex-col gap-2 rounded-lg border border-hairline p-3">
              <div className="flex flex-wrap items-center gap-2">
                <Btn
                  disabled={cliMissing || busy !== null}
                  busy={busy === "distill"}
                  onClick={() => send("distill", { epoch })}
                >
                  Distill epoch {epoch}
                </Btn>
                <Btn
                  disabled={cliMissing || busy !== null}
                  busy={busy === "triage"}
                  onClick={() => send("triage", { epoch })}
                >
                  Triage epoch {epoch}
                </Btn>
              </div>
              <div className="flex flex-wrap items-center gap-2">
                <label htmlFor="epochs-input" className="text-xs text-ink-secondary">
                  Full run
                </label>
                <input
                  id="epochs-input"
                  type="number"
                  min={1}
                  max={100}
                  value={epochs}
                  onChange={(e) => setEpochs(Number(e.target.value))}
                  className="w-14 rounded-md border border-hairline bg-surface px-2 py-1 text-xs text-ink"
                  style={{ fontVariantNumeric: "tabular-nums" }}
                />
                <Btn
                  disabled={cliMissing || busy !== null}
                  busy={busy === "run"}
                  onClick={() => send("run", { epochs })}
                >
                  Run {epochs} epochs
                </Btn>
              </div>
              <div className="flex flex-wrap items-center gap-2">
                <Btn
                  disabled={cliMissing || busy !== null}
                  busy={busy === "pages"}
                  onClick={() => send("pages")}
                >
                  Regenerate wiki pages
                </Btn>
                <Btn
                  disabled={cliMissing || busy !== null}
                  busy={busy === "tail-policy"}
                  onClick={() => send("tail-policy")}
                >
                  Ingest policy logs
                </Btn>
                {/* Spec 03: the same two commands the home nudge fires, so an
                    operator who lives in Lab never has to go to home to run
                    them — and cron never has to go through the dashboard. */}
                <Btn
                  disabled={cliMissing || busy !== null}
                  busy={busy === "ingest-real"}
                  onClick={() => send("ingest-real")}
                >
                  Ingest real sessions
                </Btn>
                <Btn
                  disabled={cliMissing || busy !== null}
                  busy={busy === "train-cycle"}
                  onClick={() => send("train-cycle")}
                >
                  Train cycle
                </Btn>
                {/* Spec 05, Req 3.1: mining normally rides along on a
                    train-cycle, but it is runnable on its own so a demo can
                    show the proposal step without paying for distillation. */}
                <Btn
                  disabled={cliMissing || busy !== null}
                  busy={busy === "mine-tasks"}
                  onClick={() => send("mine-tasks")}
                >
                  Mine task proposals
                </Btn>
                <Btn
                  disabled={cliMissing || busy !== null}
                  busy={busy === "rehearse"}
                  onClick={() => send("rehearse")}
                >
                  Containment rehearsal
                </Btn>
              </div>
              <p className="text-[10px] leading-relaxed text-ink-muted">
                Individual steps of the cycles above, for when you need to re-run one
                on its own. Each launches the matching{" "}
                <code className="font-mono">helixis</code> command and streams into the
                Engine jobs panel.
              </p>
            </div>
          ) : null}

          {cliMissing ? (
            <p className="mt-2 text-[11px] text-ink-muted">
              The <code className="font-mono">helixis</code> CLI is not on PATH for the
              dashboard process, so runs must be started from a terminal.
            </p>
          ) : null}

          {message ? (
            <p
              className="mt-2 text-[11px] leading-relaxed"
              style={{
                color: message.tone === "err" ? "var(--status-critical)" : "var(--text-secondary)",
              }}
            >
              {message.text}
            </p>
          ) : null}
        </div>
      </div>
    </Card>
  );
}

function fmtTokens(n: number): string {
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`;
  if (n >= 1_000) return `${(n / 1_000).toFixed(1)}k`;
  return String(n);
}

function Stat({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-lg border border-hairline px-2.5 py-2">
      <div className="text-[10px] text-ink-muted">{label}</div>
      <div className="text-lg font-semibold text-ink">{value}</div>
    </div>
  );
}

function Btn({
  children,
  onClick,
  disabled,
  busy,
  primary,
}: {
  children: React.ReactNode;
  onClick: () => void;
  disabled?: boolean;
  busy?: boolean;
  primary?: boolean;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={disabled}
      className={`rounded-md px-2.5 py-1.5 text-xs disabled:opacity-40 ${
        primary
          ? "btn-primary font-medium"
          : "border border-hairline-strong font-semibold text-ink transition-colors hover:bg-sunken"
      }`}
    >
      {busy ? "…" : children}
    </button>
  );
}
