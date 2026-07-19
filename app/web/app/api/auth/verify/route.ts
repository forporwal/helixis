import { auth } from "@/auth";

/**
 * Session probe for Caddy's `forward_auth`.
 *
 * The Control UI and the TUI are separate origins served by Caddy, so Next.js
 * middleware never sees their requests. Caddy calls THIS endpoint with the
 * browser's cookies before proxying either of them, and forwards the request
 * only on a 2xx. That is what puts the dashboard's email/password login in
 * front of two surfaces whose own auth is a single static token.
 *
 * Requires the session cookie to be sent to `ui.`/`tui.` subdomains, which is
 * why AUTH_COOKIE_DOMAIN widens it to `.<domain>` on public deployments (see
 * auth.ts). Without that the cookie stays host-only, this always returns 401,
 * and every request bounces to /login in a loop.
 *
 * Returns 401 rather than a redirect: Caddy owns the redirect, because only it
 * knows the original URL to send the user back to. Body is deliberately empty —
 * this answers "is there a session", nothing more.
 */
export async function GET() {
  const session = await auth();
  if (!session?.user) {
    return new Response(null, {
      status: 401,
      // Belt and braces: an intermediary must never cache an auth decision.
      headers: { "Cache-Control": "no-store" },
    });
  }
  return new Response(null, {
    status: 200,
    headers: { "Cache-Control": "no-store" },
  });
}
