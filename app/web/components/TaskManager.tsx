"use client";

import { useMemo, useState } from "react";
import { ListPlus } from "lucide-react";
import { Card, EmptyState, FilterChip, Pill } from "./ui";
import { Input } from "./ui/input";
import { usePoll } from "./usePoll";
import type { ManifestResponse, ManifestTask } from "@/lib/types";

const INTERVAL = 15_000;
const TASK_ID_RE = /^[a-z][a-z0-9_]*\.[a-z][a-z0-9_]*$/;

/**
 * Curriculum management (Requirement 2.2).
 *
 * Every mutation goes out through `/api/control` -> `helixis task`, which is
 * the only writer of tasks.user.yaml. This component therefore never has to
 * know the file format — it collects fields, shows what the engine said, and
 * re-reads the merged manifest.
 *
 * Client-side validation here is a courtesy, not a gate: it catches the obvious
 * mistakes before a round trip, and the engine independently re-validates
 * everything (id shape, bench-ref resolution, script paths) before writing.
 */

type Form = {
  id: string;
  domain: string;
  type: "bench" | "real";
  split: "train" | "heldout";
  prompt: string;
  benchRef: string;
  verify: string;
  reset: string;
};

const BLANK: Form = {
  id: "",
  domain: "",
  type: "bench",
  split: "train",
  prompt: "",
  benchRef: "",
  verify: "",
  reset: "",
};

function validate(form: Form): Partial<Record<keyof Form, string>> {
  const errors: Partial<Record<keyof Form, string>> = {};
  if (!form.id.trim()) errors.id = "Required.";
  else if (!TASK_ID_RE.test(form.id.trim()))
    errors.id = "Must be `domain.snake_case_action` — lowercase, one dot.";

  if (form.type === "real") {
    if (!form.prompt.trim()) errors.prompt = "A real task needs a prompt.";
    if (!form.verify.trim()) errors.verify = "Path to verify.py is required.";
    if (!form.reset.trim()) errors.reset = "Path to reset.py is required.";
  }
  return errors;
}

export function TaskManager() {
  const manifest = usePoll<ManifestResponse>("/api/manifest", INTERVAL);
  const [form, setForm] = useState<Form>(BLANK);
  const [errors, setErrors] = useState<Partial<Record<keyof Form, string>>>({});
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<{ tone: "ok" | "err"; text: string } | null>(null);
  const [open, setOpen] = useState(false);
  const [showBench, setShowBench] = useState(false);

  const tasks = useMemo(() => manifest.data?.tasks ?? [], [manifest.data]);
  const userTasks = useMemo(() => tasks.filter((t) => t.origin === "user"), [tasks]);
  const shown = showBench ? tasks : userTasks;

  function set<K extends keyof Form>(key: K, value: Form[K]) {
    setForm((f) => ({ ...f, [key]: value }));
    setErrors((e) => ({ ...e, [key]: undefined }));
  }

  async function submit() {
    const found = validate(form);
    setErrors(found);
    if (Object.keys(found).length) return;

    setBusy(true);
    setResult(null);
    try {
      const res = await fetch("/api/control", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({
          action: "task-add",
          id: form.id.trim(),
          domain: form.domain.trim() || undefined,
          type: form.type,
          split: form.split,
          prompt: form.type === "real" ? form.prompt : undefined,
          bench_ref: form.type === "bench" ? form.benchRef.trim() || undefined : undefined,
          verify: form.type === "real" ? form.verify.trim() : undefined,
          reset: form.type === "real" ? form.reset.trim() : undefined,
        }),
      });
      const json = await res.json();
      if (!res.ok) throw new Error(json.error || "The engine rejected the task.");
      setResult({ tone: "ok", text: json.output || json.note });
      setForm(BLANK);
      setOpen(false);
      void manifest.refetch();
    } catch (err) {
      setResult({ tone: "err", text: (err as Error).message });
    } finally {
      setBusy(false);
    }
  }

  async function retire(task: ManifestTask) {
    setBusy(true);
    setResult(null);
    try {
      const res = await fetch("/api/control", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ action: "task-remove", id: task.id }),
      });
      const json = await res.json();
      if (!res.ok) throw new Error(json.error || "Removal failed.");
      setResult({ tone: "ok", text: json.output || json.note });
      void manifest.refetch();
    } catch (err) {
      setResult({ tone: "err", text: (err as Error).message });
    } finally {
      setBusy(false);
    }
  }

  if (manifest.data && !manifest.data.available) {
    return (
      <Card title="Curriculum" subtitle="Your own tasks, merged with the frozen bench set.">
        <EmptyState
          icon={ListPlus}
          title="Task manifest unavailable"
          hint={manifest.data.error ?? undefined}
        />
      </Card>
    );
  }

  return (
    <Card
      title="Curriculum"
      subtitle={
        <>
          {userTasks.length} user task{userTasks.length === 1 ? "" : "s"} on top of the frozen
          bench set. Written by <code className="font-mono">helixis task</code>.
        </>
      }
      refreshing={manifest.refreshing}
      action={
        <button
          type="button"
          onClick={() => setOpen((v) => !v)}
          aria-expanded={open}
          className="btn-primary rounded-md px-2.5 py-1.5 text-xs font-medium"
        >
          {open ? "Cancel" : "Add task"}
        </button>
      }
    >
      <div className="flex flex-col gap-3">
        {open ? (
          <div className="flex flex-col gap-3 rounded-lg border border-hairline p-3">
            <Field label="Task id" error={errors.id} hint="domain.snake_case_action">
              <Input
                value={form.id}
                onChange={(e) => set("id", e.target.value)}
                placeholder="ops.weekly_vendor_digest"
                className="h-8 text-xs"
              />
            </Field>

            <div className="grid grid-cols-2 gap-3">
              <Field label="Domain" hint="defaults to the id's first segment">
                <Input
                  value={form.domain}
                  onChange={(e) => set("domain", e.target.value)}
                  placeholder="ops"
                  className="h-8 text-xs"
                />
              </Field>
              <Field label="Type">
                <Select
                  value={form.type}
                  onChange={(v) => set("type", v as Form["type"])}
                  options={[
                    ["bench", "bench — graded by AutomationBench"],
                    ["real", "real — graded by verify.py"],
                  ]}
                />
              </Field>
            </div>

            {form.type === "bench" ? (
              <Field
                label="Bench task id"
                hint="optional — defaults to the task id above"
              >
                <Input
                  value={form.benchRef}
                  onChange={(e) => set("benchRef", e.target.value)}
                  placeholder="sales.qualify_lead"
                  className="h-8 text-xs"
                />
              </Field>
            ) : (
              <>
                <Field label="Prompt" error={errors.prompt}>
                  <textarea
                    value={form.prompt}
                    onChange={(e) => set("prompt", e.target.value)}
                    rows={4}
                    placeholder="Compile the weekly vendor digest from…"
                    className="w-full rounded-md border border-hairline bg-surface px-2 py-1.5 text-xs text-ink"
                  />
                </Field>
                <div className="grid grid-cols-2 gap-3">
                  <Field label="verify.py" error={errors.verify}>
                    <Input
                      value={form.verify}
                      onChange={(e) => set("verify", e.target.value)}
                      placeholder="app/real_tier/my_task/verify.py"
                      className="h-8 text-xs"
                    />
                  </Field>
                  <Field label="reset.py" error={errors.reset}>
                    <Input
                      value={form.reset}
                      onChange={(e) => set("reset", e.target.value)}
                      placeholder="app/real_tier/my_task/reset.py"
                      className="h-8 text-xs"
                    />
                  </Field>
                </div>
                <p className="text-[10px] leading-relaxed text-ink-muted">
                  Real tasks are graded by their own end-state checks (which must include at
                  least one negative assertion) and run through the real-tier driver, not a
                  mocked epoch. Like all real-tier work they stay out of the headline curve.
                </p>
              </>
            )}

            <Field label="Split">
              <Select
                value={form.split}
                onChange={(v) => set("split", v as Form["split"])}
                options={[
                  ["train", "train — the default"],
                  ["heldout", "held-out — changes the measuring stick"],
                ]}
              />
            </Field>
            {form.split === "heldout" ? (
              <p
                className="text-[10px] leading-relaxed"
                style={{ color: "var(--status-warning)" }}
              >
                Adding to the held-out split changes what the generalization curve measures.
                The curve is annotated “curriculum changed” at this epoch, and held-out
                numbers from before and after are not directly comparable.
              </p>
            ) : null}

            <div>
              <button
                type="button"
                onClick={submit}
                disabled={busy}
                className="btn-primary rounded-md px-3 py-1.5 text-xs font-medium disabled:opacity-40"
              >
                {busy ? "…" : "Add task"}
              </button>
            </div>
          </div>
        ) : null}

        {result ? (
          <pre
            className="max-h-32 overflow-auto whitespace-pre-wrap rounded-md border border-hairline p-2 text-[11px] leading-relaxed"
            style={{
              color: result.tone === "err" ? "var(--status-critical)" : "var(--text-secondary)",
            }}
          >
            {result.text}
          </pre>
        ) : null}

        {(manifest.data?.warnings.length ?? 0) > 0 ? (
          <ul className="flex flex-col gap-1 rounded-md border border-hairline p-2 text-[11px] text-ink-muted">
            {manifest.data?.warnings.map((w) => (
              <li key={`${w.taskId}-${w.message}`}>
                <span className="font-medium text-ink-secondary">{w.taskId}</span>: {w.message}
              </li>
            ))}
          </ul>
        ) : null}

        <div className="flex items-center gap-1.5">
          <FilterChip active={!showBench} onClick={() => setShowBench(false)} count={userTasks.length}>
            User tasks
          </FilterChip>
          <FilterChip active={showBench} onClick={() => setShowBench(true)} count={tasks.length}>
            All
          </FilterChip>
        </div>

        {shown.length === 0 ? (
          <EmptyState
            icon={ListPlus}
            title="No user tasks yet"
            hint="Add a task to train on work you actually do. The frozen 30-task bench set stays untouched and keeps the headline curve comparable."
          />
        ) : (
          <ul className="max-h-72 divide-y divide-hairline overflow-auto">
            {shown.map((t) => (
              <li key={t.id} className="flex items-start gap-2 py-2">
                <div className="min-w-0 flex-1">
                  <div className="flex flex-wrap items-center gap-1.5">
                    <span
                      className={`truncate font-mono text-[11px] ${t.retired ? "text-ink-muted line-through" : "text-ink"}`}
                    >
                      {t.id}
                    </span>
                    {t.origin === "user" ? <Pill tone="good">user</Pill> : null}
                    {t.type === "real" ? <Pill tone="warning">real</Pill> : null}
                    {/* Requirement 3.3: a curriculum that grew itself says so. */}
                    {t.source === "miner" ? <Pill tone="neutral">mined</Pill> : null}
                    {t.draft ? <Pill tone="critical">draft</Pill> : null}
                    {t.retired ? <Pill tone="neutral">retired</Pill> : null}
                  </div>
                  <div className="mt-0.5 text-[10px] text-ink-muted">
                    {t.split === "heldout" ? "held-out" : t.split} · {t.domain}
                    {t.benchRef && t.benchRef !== t.id ? ` · grades as ${t.benchRef}` : ""}
                  </div>
                  {t.draft ? (
                    <div className="mt-1 text-[10px] leading-relaxed text-ink-muted">
                      Excluded from every run: its verify.py/reset.py are still
                      model-written drafts. Finish them, rename off{" "}
                      <code className="font-mono">.draft</code>, then remove{" "}
                      <code className="font-mono">draft: true</code>.
                    </div>
                  ) : null}
                </div>
                {t.origin === "user" && !t.retired ? (
                  <button
                    type="button"
                    onClick={() => retire(t)}
                    disabled={busy}
                    className="shrink-0 rounded-md border border-hairline-strong px-2 py-1 text-[11px] font-semibold text-ink transition-colors hover:bg-sunken disabled:opacity-40"
                    title="Excluded from future epochs; recorded episodes are preserved."
                  >
                    Retire
                  </button>
                ) : null}
              </li>
            ))}
          </ul>
        )}
      </div>
    </Card>
  );
}

function Field({
  label,
  hint,
  error,
  children,
}: {
  label: string;
  hint?: string;
  error?: string;
  children: React.ReactNode;
}) {
  return (
    <label className="flex flex-col gap-1">
      <span className="text-xs font-medium text-ink-secondary">{label}</span>
      {children}
      {error ? (
        <span className="text-[10px]" style={{ color: "var(--status-critical)" }}>
          {error}
        </span>
      ) : hint ? (
        <span className="text-[10px] text-ink-muted">{hint}</span>
      ) : null}
    </label>
  );
}

function Select({
  value,
  onChange,
  options,
}: {
  value: string;
  onChange: (v: string) => void;
  options: [string, string][];
}) {
  return (
    <select
      value={value}
      onChange={(e) => onChange(e.target.value)}
      className="h-8 rounded-md border border-hairline bg-surface px-2 text-xs text-ink"
    >
      {options.map(([v, label]) => (
        <option key={v} value={v}>
          {label}
        </option>
      ))}
    </select>
  );
}
