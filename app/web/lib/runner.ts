import { spawn, type ChildProcess } from "node:child_process";
import { resolveBin } from "./cli";
import { REPO_ROOT_DIR } from "./paths";

/**
 * Registry for engine processes this dashboard started.
 *
 * An epoch runs for minutes, so control actions spawn and return immediately
 * rather than blocking the request. We keep the handle so "stop" has something
 * real to signal.
 *
 * Honesty constraint: the dashboard can only stop a process it started itself.
 * If the engine was launched from a terminal, we say so rather than pretending
 * the button did something.
 */

export type Job = {
  id: string;
  command: string[];
  startedAt: string;
  status: "running" | "exited" | "failed";
  exitCode: number | null;
  log: string[];
};

type Entry = { job: Job; child: ChildProcess };

// Survives HMR in dev; module state is per server process.
const globalStore = globalThis as unknown as { __helixisJobs?: Map<string, Entry> };
const jobs: Map<string, Entry> = (globalStore.__helixisJobs ??= new Map());

function pushLog(job: Job, chunk: string) {
  for (const line of chunk.split("\n")) {
    if (line.trim()) job.log.push(line);
  }
  // Keep the tail bounded — this is a liveness readout, not an archive.
  if (job.log.length > 400) job.log.splice(0, job.log.length - 400);
}

/**
 * Spawn an engine command. `args` must already be validated by the caller;
 * `shell: false` guarantees no argument can be interpreted by a shell.
 */
export function startJob(bin: string, args: string[]): { ok: true; job: Job } | { ok: false; kind: "missing" | "failed"; message: string } {
  let child: ChildProcess;
  const resolved = resolveBin(bin);
  try {
    child = spawn(resolved, args, {
      cwd: REPO_ROOT_DIR,
      shell: false, // never a shell
      stdio: ["ignore", "pipe", "pipe"],
      windowsHide: true,
    });
  } catch (err) {
    return { ok: false, kind: "failed", message: (err as Error).message };
  }

  const job: Job = {
    id: `${Date.now()}-${Math.random().toString(36).slice(2, 8)}`,
    command: [bin, ...args],
    startedAt: new Date().toISOString(),
    status: "running",
    exitCode: null,
    log: [],
  };
  const entry: Entry = { job, child };
  jobs.set(job.id, entry);

  child.stdout?.on("data", (d: Buffer) => pushLog(job, d.toString()));
  child.stderr?.on("data", (d: Buffer) => pushLog(job, d.toString()));
  child.on("error", (err: NodeJS.ErrnoException) => {
    job.status = "failed";
    pushLog(job, err.code === "ENOENT" ? `${bin} not found on PATH` : err.message);
  });
  child.on("exit", (code) => {
    job.status = code === 0 ? "exited" : "failed";
    job.exitCode = code;
  });

  return { ok: true, job };
}

export function listJobs(): Job[] {
  return [...jobs.values()]
    .map((e) => e.job)
    .sort((a, b) => b.startedAt.localeCompare(a.startedAt))
    .slice(0, 20);
}

export function runningJobs(): Job[] {
  return listJobs().filter((j) => j.status === "running");
}

/** SIGTERM every running job we own. Returns how many were signalled. */
export function stopAll(): number {
  let n = 0;
  for (const { job, child } of jobs.values()) {
    if (job.status === "running" && child.pid) {
      try {
        child.kill("SIGTERM");
        n += 1;
      } catch {
        /* already dead */
      }
    }
  }
  return n;
}
