/**
 * Helixis Claw — the agent the user actually opens.
 *
 * The two URLs live here rather than in the sidebar because home is now the
 * primary way in and the rail is secondary access; both render the same links,
 * so the same constants have to feed both or they drift.
 */

// SERVER ONLY. These are functions rather than exported constants, and they
// read a non-`NEXT_PUBLIC_` variable, both deliberately.
//
// The token used to be baked in as the literal `helixis-local` with a
// NEXT_PUBLIC_NEMOCLAW_UI_URL escape hatch. That escape hatch could not work:
// `NEXT_PUBLIC_*` is inlined at BUILD time, but the token is generated per
// install and handed to the sandbox at CREATE time by claw-init.sh. So a fresh
// install with a real NEMOCLAW_GATEWAY_TOKEN got a launch card still pointing
// at the published dev default, and the Control UI answered
// "unauthorized: gateway token mismatch".
//
// Reading it here, at request time, means the dashboard and the sandbox both
// resolve the same `.env` value and a token change needs a container restart
// rather than an image rebuild. The token reaches the browser only as part of
// the href these produce — which is the same exposure the old constant had,
// and unavoidable given the Control UI takes its token from the URL.
//
// The token must be in the URL FRAGMENT — OpenClaw's Control UI reads #token=…,
// not a query param, and scrubs it from the address bar after storing it. A
// fragment is never sent to any server, which is also why the reachability
// probe below strips it before fetching.
const DEV_FALLBACK_TOKEN = "helixis-local";

function gatewayToken(): string {
  return process.env.NEMOCLAW_GATEWAY_TOKEN || DEV_FALLBACK_TOKEN;
}

/**
 * NEMOCLAW_UI_URL / NEMOCLAW_TUI_URL are ORIGINS (`https://ui.example.com`),
 * not complete hrefs — any path or fragment on them is discarded.
 *
 * They used to be returned verbatim, which quietly broke the thing they exist
 * for: the token fragment is appended below, so an override without one
 * produced a link the Control UI answers with "unauthorized: gateway token
 * mismatch". Requiring callers to hand-write `#token=…` would also mean pasting
 * the live credential into .env in a second place, where it can drift from the
 * value claw-init baked into the sandbox.
 */
function clawOrigin(envVar: string | undefined, port: string): string {
  const fallback = `http://localhost:${port}`;
  const raw = (envVar || "").trim();
  if (!raw) return fallback;
  try {
    // Keep only scheme://host[:port]; drop any path, query or fragment so the
    // suffixes below are always appended to a clean origin.
    return new URL(raw).origin;
  } catch {
    // A malformed override should not take the launch card down with it.
    return fallback;
  }
}

export function clawUiUrl(): string {
  const origin = clawOrigin(process.env.NEMOCLAW_UI_URL, process.env.NEMOCLAW_UI_PORT || "18789");
  return `${origin}/#token=${encodeURIComponent(gatewayToken())}`;
}

// Browser TUI served by ttyd inside the sandbox. It uses HTTP basic auth
// (user "helixis", password = the same gateway token), so the browser prompts
// on first open — credentials can't ride in the URL.
export function clawTuiUrl(): string {
  const origin = clawOrigin(process.env.NEMOCLAW_TUI_URL, process.env.NEMOCLAW_TUI_PORT || "18790");
  return `${origin}/`;
}

/** How to bring the gateway back up, quoted verbatim in the card's down state. */
export const CLAW_COMPOSE_HINT = "docker compose up -d nemoclaw";

/**
 * Origin the server probes for reachability.
 *
 * Deliberately its own variable: the browser reaches the gateway at
 * `localhost:18789`, but the web container reaches it at `nemoclaw:18789`, and
 * probing the browser's URL from inside Docker would report every healthy
 * gateway as down. Falls back to the public URL's origin for local `next dev`.
 */
function probeUrl(): string | null {
  const raw = process.env.NEMOCLAW_GATEWAY_URL ?? clawUiUrl();
  try {
    const u = new URL(raw);
    // Origin only — never the fragment, which carries the token.
    return u.origin;
  } catch {
    return null;
  }
}

/**
 * Is the gateway answering? Never throws, never waits long: a down gateway is a
 * card state on home, and home polls every 4s, so a slow probe would make the
 * whole page feel stalled. Any response at all (including 401/404) counts as up
 * — we are testing that something is listening, not that we are authorized.
 */
export async function probeGateway(timeoutMs = 1000): Promise<boolean> {
  const url = probeUrl();
  if (!url) return false;
  try {
    const res = await fetch(url, {
      method: "GET",
      cache: "no-store",
      redirect: "manual",
      signal: AbortSignal.timeout(timeoutMs),
    });
    return res.status > 0;
  } catch {
    return false;
  }
}
