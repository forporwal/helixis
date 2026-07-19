import { parseJson, query, queryOne } from "./db";
import type { ProposalEpisode, Split, TaskProposal } from "./types";

/**
 * Reading mined task proposals (spec 05).
 *
 * Shared by `/api/actions` (which turns pending ones into feed rows), the list
 * route and the detail route — three readers of one table is three chances to
 * map a status or a JSON column differently, and a feed that disagrees with the
 * detail page about what is pending is worse than no feed.
 *
 * Read-only, like everything else the dashboard does. Decisions go out through
 * `/api/task-proposals/[id]` -> `helixis proposal approve|reject`, which is the
 * only writer.
 */

type Row = {
  id: string;
  fingerprint: string;
  status: string;
  title: string;
  domain: string;
  task_type: string;
  draft_yaml: string;
  verify_draft: string;
  reset_draft: string;
  source_episode_ids: string;
  occurrences: number;
  model_id: string;
  created_at: string;
  resolved_at: string | null;
  reason: string | null;
};

const STATUSES = new Set(["pending", "approved", "rejected", "invalid"]);

function toProposal(r: Row): TaskProposal {
  return {
    id: r.id,
    fingerprint: r.fingerprint,
    // An unrecognized status is reported as `invalid` rather than passed
    // through: the union is what the UI switches on, and a row that widens it
    // at runtime would render as nothing at all.
    status: (STATUSES.has(r.status) ? r.status : "invalid") as TaskProposal["status"],
    title: r.title,
    domain: r.domain,
    taskType: r.task_type,
    draftYaml: r.draft_yaml,
    verifyDraft: r.verify_draft,
    resetDraft: r.reset_draft,
    sourceEpisodeIds: parseJson<number[]>(r.source_episode_ids, []),
    occurrences: r.occurrences,
    modelId: r.model_id,
    createdAt: r.created_at,
    resolvedAt: r.resolved_at,
    reason: r.reason,
  };
}

const COLUMNS = `id, fingerprint, status, title, domain, task_type, draft_yaml,
  verify_draft, reset_draft, source_episode_ids, occurrences, model_id,
  created_at, resolved_at, reason`;

export function listTaskProposals(status?: string): TaskProposal[] {
  const rows = status
    ? query<Row>(
        `SELECT ${COLUMNS} FROM task_proposals WHERE status = ?
         ORDER BY created_at DESC LIMIT 100`,
        [status],
      )
    : query<Row>(
        `SELECT ${COLUMNS} FROM task_proposals ORDER BY created_at DESC LIMIT 100`,
      );
  return rows.map(toProposal);
}

export function getTaskProposal(id: string): TaskProposal | null {
  const row = queryOne<Row>(`SELECT ${COLUMNS} FROM task_proposals WHERE id = ?`, [id]);
  return row ? toProposal(row) : null;
}

/**
 * Resolve stored episode ids into trajectory-viewer links (Req 2.1).
 *
 * The evidence has to be one click away or approval is just trust with extra
 * steps. Ids are numbers straight from the database, so they are interpolated
 * as placeholders, not concatenated.
 */
export function proposalEpisodes(ids: number[]): ProposalEpisode[] {
  const clean = ids.filter((i) => Number.isInteger(i));
  if (!clean.length) return [];
  const placeholders = clean.map(() => "?").join(",");
  const rows = query<{
    id: number;
    epoch: number;
    split: string;
    task_id: string;
    finished_at: string;
  }>(
    `SELECT id, epoch, split, task_id, finished_at FROM episodes
     WHERE id IN (${placeholders}) ORDER BY finished_at`,
    clean,
  );
  return rows.map((r) => ({
    id: r.id,
    epoch: r.epoch,
    split: r.split as Split,
    taskId: r.task_id,
    finishedAt: r.finished_at,
    href: `/runs/${r.epoch}/${encodeURIComponent(r.split)}/${encodeURIComponent(r.task_id)}`,
  }));
}
