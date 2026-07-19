# Helixis dashboard

Next.js dashboard for the Helixis engine: learning curve, task grid, episode
transcripts, wiki browser, containment feed, engine job console and operator
controls.

## Setup

```bash
pnpm install
cp .env.example .env.local   # then edit
pnpm dev                     # http://localhost:3000
```

## Authentication

The whole app (pages and APIs) sits behind a NextAuth credentials login —
the controls can start engine runs and approve containment policy proposals,
so it is never served unauthenticated.

Required environment variables (see `.env.example`):

| Variable | Purpose |
| --- | --- |
| `AUTH_SECRET` | Signs the session JWTs. Generate with `openssl rand -base64 32`. |
| `HELIXIS_AUTH_EMAIL` | The one valid operator email. |
| `HELIXIS_AUTH_PASSWORD` | The one valid operator password. |

If the email/password pair is unset, sign-in **fails closed**: nobody can log
in and the login page says which variables to set. There is no user database —
this is a single-operator gate, not an account system.

Under docker compose, set `HELIXIS_DASHBOARD_AUTH_SECRET`,
`HELIXIS_AUTH_EMAIL` and `HELIXIS_AUTH_PASSWORD` in the repo-root `.env`.

## Pages

- `/` — the dashboard (curve, controls, containment, task grid, wiki, jobs).
- `/runs/[epoch]/[split]/[taskId]` — full episode transcript: grading
  assertions, agent reasoning, tool calls and tool results. Reached by
  clicking any cell in the task grid or a skill's source-episode chips.
- `/login` — operator sign-in.

## Data sources

Read-only by design: SQLite index at `runs/helixis.db` (`HELIXIS_DB` to
override), trajectory JSONL under the runs directory, and `wiki/`
(`HELIXIS_WIKI`). Mutating actions shell out to the `helixis` / `openshell`
CLIs with allow-listed, validated argument arrays — never a shell.
