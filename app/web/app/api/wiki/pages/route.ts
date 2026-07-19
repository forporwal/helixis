import fs from "node:fs";
import path from "node:path";
import { NextResponse } from "next/server";
import { WIKI_DIR } from "@/lib/paths";
import type { WikiHistoryEntry, WikiPage, WikiPagesResponse } from "@/lib/types";

export const dynamic = "force-dynamic";

/**
 * The generated wiki overview pages (`wiki/pages/*.md`) plus the tail of the
 * wiki's history log. These are the engine's own synthesis of what it learned —
 * the most readable artifact the system produces.
 *
 * Only names matching a strict pattern directly inside `wiki/pages` are read;
 * nothing client-supplied touches the filesystem.
 */

const PAGE_NAME = /^[A-Za-z0-9_-]+\.md$/;
const HISTORY_TAIL = 40;

function titleFrom(name: string, body: string): string {
  const h1 = /^#\s+(.+)$/m.exec(body);
  if (h1) return h1[1].trim();
  return name.replace(/\.md$/, "").replace(/-/g, " ");
}

function summarize(rec: Record<string, unknown>): string {
  const event = String(rec.event ?? "event");
  if (event === "skills_evolved") {
    const skills = Array.isArray(rec.skills) ? rec.skills : [];
    return `Epoch ${rec.epoch ?? "?"}: distilled ${skills.length} skill${skills.length === 1 ? "" : "s"} (${skills.join(", ")}) from ${rec.n_failures_considered ?? "?"} failures — generation ${rec.generation ?? "?"}.`;
  }
  if (event === "pages_regenerated") {
    const pages = Array.isArray(rec.pages) ? rec.pages : [];
    return `Regenerated overview pages: ${pages.join(", ")}.`;
  }
  // Unknown event: show it honestly rather than dropping it.
  const rest = Object.entries(rec)
    .filter(([k]) => k !== "ts" && k !== "event")
    .map(([k, v]) => `${k}=${typeof v === "object" ? JSON.stringify(v) : String(v)}`)
    .join(" ");
  return `${event}: ${rest}`.trim();
}

export function GET() {
  const pagesDir = path.join(WIKI_DIR, "pages");
  const pages: WikiPage[] = [];
  let names: string[] = [];
  try {
    names = fs.readdirSync(pagesDir).filter((n) => PAGE_NAME.test(n)).sort();
  } catch {
    names = [];
  }
  for (const name of names) {
    try {
      const body = fs.readFileSync(path.join(pagesDir, name), "utf8");
      pages.push({ name, title: titleFrom(name, body), body });
    } catch {
      /* page listed but unreadable — skip */
    }
  }

  const history: WikiHistoryEntry[] = [];
  try {
    const raw = fs.readFileSync(path.join(WIKI_DIR, "history.jsonl"), "utf8");
    const lines = raw.split("\n").filter((l) => l.trim());
    for (const line of lines.slice(-HISTORY_TAIL).reverse()) {
      try {
        const rec = JSON.parse(line) as Record<string, unknown>;
        history.push({
          ts: typeof rec.ts === "string" ? rec.ts : null,
          summary: summarize(rec),
        });
      } catch {
        /* malformed line */
      }
    }
  } catch {
    /* no history yet */
  }

  const body: WikiPagesResponse = {
    pages,
    history,
    empty: pages.length === 0 && history.length === 0,
  };
  return NextResponse.json(body);
}
