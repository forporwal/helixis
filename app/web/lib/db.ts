import fs from "node:fs";
import Database from "better-sqlite3";
import { DB_PATH } from "./paths";

/**
 * The engine owns this database; the dashboard is strictly a reader.
 *
 * Two invariants matter here:
 *  1. READ-ONLY. We never take a write lock, so we can never block a running epoch.
 *  2. NEVER THROW on a missing file. A judge who opens the dashboard before the
 *     first run must see an honest empty state, not a stack trace. Every caller
 *     goes through `query()`, which degrades to a supplied fallback.
 */

let handle: Database.Database | null = null;
let handleMtimeMs = -1;

function open(): Database.Database | null {
  if (!fs.existsSync(DB_PATH)) {
    close();
    return null;
  }
  // The engine writes in WAL mode and may recreate the file between polls.
  // Re-open if the inode's mtime moved backwards (i.e. a fresh file).
  const stat = fs.statSync(DB_PATH);
  if (handle && stat.mtimeMs < handleMtimeMs) close();
  handleMtimeMs = stat.mtimeMs;

  if (handle) return handle;
  try {
    handle = new Database(DB_PATH, { readonly: true, fileMustExist: true });
    // A reader must not upgrade the journal mode; `query` only reads.
    handle.pragma("busy_timeout = 3000");
    return handle;
  } catch {
    handle = null;
    return null;
  }
}

function close() {
  try {
    handle?.close();
  } catch {
    /* already gone */
  }
  handle = null;
}

/**
 * Run a read query, returning `fallback` if the database is absent, locked,
 * or missing the table (an engine mid-migration is not a dashboard crash).
 */
export function query<T>(sql: string, params: unknown[] = [], fallback: T[] = []): T[] {
  const db = open();
  if (!db) return fallback;
  try {
    return db.prepare(sql).all(...(params as never[])) as T[];
  } catch {
    return fallback;
  }
}

export function queryOne<T>(sql: string, params: unknown[] = []): T | null {
  const rows = query<T>(sql, params);
  return rows.length ? rows[0] : null;
}

export function dbExists(): boolean {
  return fs.existsSync(DB_PATH);
}

/**
 * Does a column exist yet?
 *
 * The engine adds columns on open, but the dashboard is a pure reader and never
 * migrates — so between an app deploy and the engine's next start, the database
 * legitimately lacks the newest column. `query()` would swallow the resulting
 * "no such column" and hand back the empty fallback, which reads on screen as
 * "no episodes recorded" for a database full of real results. Silently showing
 * an empty curve is far worse than showing an unfiltered one, so callers check
 * here and drop the filter instead.
 *
 * Not cached: a poll costs one PRAGMA, and caching would pin the "missing"
 * answer for the lifetime of the server process — i.e. right through the engine
 * run that adds the column.
 */
export function hasColumn(table: string, column: string): boolean {
  const rows = query<{ name: string }>(`PRAGMA table_info(${table})`);
  return rows.some((r) => r.name === column);
}

/** Parse a TEXT column the engine stores as a JSON string. */
export function parseJson<T>(raw: unknown, fallback: T): T {
  if (typeof raw !== "string" || !raw) return fallback;
  try {
    return JSON.parse(raw) as T;
  } catch {
    return fallback;
  }
}
