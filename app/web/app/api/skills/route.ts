import fs from "node:fs";
import path from "node:path";
import { NextResponse } from "next/server";
import { parse as parseYaml } from "yaml";
import { parseJson, query } from "@/lib/db";
import { WIKI_DIR } from "@/lib/paths";
import type { SkillItem, SkillsResponse } from "@/lib/types";

export const dynamic = "force-dynamic";

type SkillRow = {
  name: string;
  description: string;
  category: string;
  generation: number;
  created_epoch: number;
  source_episodes: string;
  path: string;
  created_at: string;
};

type DistillRow = {
  epoch: number;
  generation: number;
  n_failures: number;
  n_skills: number;
  gated_out: number;
};

/** Strip YAML frontmatter, returning the markdown body the wiki page renders. */
function splitFrontmatter(raw: string): { data: Record<string, unknown>; body: string } {
  if (!raw.startsWith("---")) return { data: {}, body: raw };
  const end = raw.indexOf("\n---", 3);
  if (end === -1) return { data: {}, body: raw };
  const head = raw.slice(4, end);
  const body = raw.slice(end + 4).replace(/^\r?\n/, "");
  try {
    const data = parseYaml(head);
    return { data: data && typeof data === "object" ? data : {}, body };
  } catch {
    return { data: {}, body };
  }
}

/**
 * Read a SKILL.md, but only if it really lives under the wiki directory.
 * The path column is engine-written, yet a path-escape check costs nothing
 * and keeps this route from becoming an arbitrary-file reader.
 */
function readSkillBody(skillPath: string): string | null {
  if (!skillPath) return null;
  const abs = path.resolve(skillPath);
  const root = path.resolve(WIKI_DIR);
  if (abs !== root && !abs.startsWith(root + path.sep)) return null;
  try {
    return fs.readFileSync(abs, "utf8");
  } catch {
    return null;
  }
}

/** "epoch-3/sales.champion_change_alert" -> a link back to the failure that taught this. */
function toSourceLink(ref: string) {
  const m = /^epoch-(\d+)\/(.+)$/.exec(ref);
  return m
    ? { label: ref, epoch: Number(m[1]), taskId: m[2] }
    : { label: ref, epoch: null, taskId: ref };
}

export function GET() {
  const rows = query<SkillRow>(
    `SELECT name, description, category, generation, created_epoch,
            source_episodes, path, created_at
     FROM skills
     ORDER BY created_epoch DESC, generation DESC, name`,
  );

  const skills: SkillItem[] = rows.map((r) => {
    const raw = readSkillBody(r.path);
    const { data, body } = raw ? splitFrontmatter(raw) : { data: {}, body: "" };
    const sourceEpisodes = parseJson<string[]>(r.source_episodes, []);
    return {
      name: r.name,
      // Prefer the DB row; fall back to frontmatter if the row is sparse.
      description: r.description || String(data.description ?? ""),
      category: r.category || String(data.category ?? "general"),
      generation: r.generation,
      createdEpoch: r.created_epoch,
      // Frontmatter is the only home for this: the skills table predates it,
      // and SKILL.md travels with the wiki.
      sourceTier: String(data.source_tier ?? "mocked"),
      sourceEpisodes,
      sourceLinks: sourceEpisodes.map(toSourceLink),
      path: r.path,
      createdAt: r.created_at,
      body,
      bodyAvailable: raw !== null,
    };
  });

  const distill = query<DistillRow>(
    `SELECT epoch, generation, n_failures, n_skills, gated_out
     FROM distill_runs ORDER BY epoch`,
  );
  let cumulative = 0;
  const growth = distill.map((d) => {
    cumulative += d.n_skills;
    return {
      epoch: d.epoch,
      generation: d.generation,
      nSkills: d.n_skills,
      cumulative,
      nFailures: d.n_failures,
      gatedOut: d.gated_out === 1,
    };
  });

  // wiki/state.json is the engine's authoritative generation counter.
  let generation = rows.reduce((m, r) => Math.max(m, r.generation), 0);
  try {
    const state = JSON.parse(fs.readFileSync(path.join(WIKI_DIR, "state.json"), "utf8"));
    if (typeof state.generation === "number") generation = state.generation;
  } catch {
    /* wiki not initialized yet */
  }

  const body: SkillsResponse = {
    skills,
    growth,
    generation,
    empty: rows.length === 0,
  };
  return NextResponse.json(body);
}
