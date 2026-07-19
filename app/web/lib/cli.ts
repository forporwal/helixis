import { execFile } from "node:child_process";
import fs from "node:fs";
import path from "node:path";
import { promisify } from "node:util";
import { REPO_ROOT_DIR } from "./paths";

const execFileAsync = promisify(execFile);

/**
 * The engine is installed into a repo-local virtualenv (`app/engine/.venv`),
 * which is NOT on the ambient PATH. Whoever starts `next dev` would otherwise
 * have to remember to activate it first, and the dashboard would report the
 * engine as missing when it is sitting right there in the repo. So resolve in
 * this order: explicit override, repo-local venv, then plain PATH.
 */
export function resolveBin(bin: string): string {
  if (bin !== "helixis") return bin;

  const override = process.env.HELIXIS_CLI;
  if (override) return path.resolve(override);

  const venvBin = path.join(
    REPO_ROOT_DIR,
    "app",
    "engine",
    ".venv",
    process.platform === "win32" ? "Scripts" : "bin",
    process.platform === "win32" ? "helixis.exe" : "helixis",
  );
  try {
    fs.accessSync(venvBin, fs.constants.X_OK);
    return venvBin;
  } catch {
    return bin; // fall back to PATH
  }
}

/**
 * Command execution rules for this dashboard:
 *
 *  - Every command runs through `execFile` with an ARGUMENT ARRAY. There is no
 *    shell, so nothing in an argument can be interpreted as a shell metacharacter.
 *  - Callers must validate every user-supplied argument against an allow-list or
 *    a strict pattern BEFORE calling. Validation lives at the route boundary.
 *  - A missing binary is a 503, never an unhandled rejection.
 */

export type ExecResult =
  | { ok: true; stdout: string; stderr: string }
  | { ok: false; kind: "missing" | "failed"; message: string; stdout?: string; stderr?: string };

export async function run(
  bin: string,
  args: string[],
  opts: { cwd?: string; timeoutMs?: number } = {},
): Promise<ExecResult> {
  const resolved = resolveBin(bin);
  try {
    const { stdout, stderr } = await execFileAsync(resolved, args, {
      cwd: opts.cwd,
      timeout: opts.timeoutMs ?? 30_000,
      maxBuffer: 4 * 1024 * 1024,
      shell: false, // never a shell — the whole point
      windowsHide: true,
    });
    return { ok: true, stdout, stderr };
  } catch (err) {
    const e = err as NodeJS.ErrnoException & { stdout?: string; stderr?: string };
    if (e.code === "ENOENT") {
      return { ok: false, kind: "missing", message: `\`${bin}\` is not installed or not on PATH` };
    }
    return {
      ok: false,
      kind: "failed",
      message: e.message || `\`${bin}\` exited with an error`,
      stdout: e.stdout,
      stderr: e.stderr,
    };
  }
}

/** Cheap probe so the UI can disable controls instead of offering dead buttons. */
export async function isAvailable(bin: string): Promise<boolean> {
  const resolved = resolveBin(bin);
  // An absolute path came from the override or the repo-local venv, and
  // `which` does not answer questions about those.
  if (path.isAbsolute(resolved)) {
    try {
      fs.accessSync(resolved, fs.constants.X_OK);
      return true;
    } catch {
      return false;
    }
  }
  const res = await run(process.platform === "win32" ? "where" : "which", [resolved], {
    timeoutMs: 3000,
  });
  return res.ok && res.stdout.trim().length > 0;
}
