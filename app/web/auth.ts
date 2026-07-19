import NextAuth from "next-auth";
import Credentials from "next-auth/providers/credentials";

/**
 * Single-operator auth: one email/password pair supplied via environment
 * variables. This is a gate for the mutating controls (start runs, approve
 * policy proposals), not a multi-user account system.
 *
 *  - HELIXIS_AUTH_EMAIL / HELIXIS_AUTH_PASSWORD define the one valid login.
 *  - If either is unset, authorize() fails CLOSED: nobody can sign in, and the
 *    login page explains which variables to set.
 *  - Sessions are stateless JWTs (no database), signed with AUTH_SECRET.
 */

/**
 * Constant-time string compare without node:crypto — this module is bundled
 * into the middleware (edge runtime), where node builtins are unavailable.
 */
function safeEqual(a: string, b: string): boolean {
  const len = Math.max(a.length, b.length);
  let diff = a.length === b.length ? 0 : 1;
  for (let i = 0; i < len; i += 1) {
    diff |= (a.charCodeAt(i) || 0) ^ (b.charCodeAt(i) || 0);
  }
  return diff === 0;
}

export function credentialsConfigured(): boolean {
  return Boolean(process.env.HELIXIS_AUTH_EMAIL && process.env.HELIXIS_AUTH_PASSWORD);
}

/**
 * Widen the session cookie to a parent domain (`.example.com`) so the Control
 * UI and TUI subdomains receive it.
 *
 * Needed because those two are served from their OWN origins by Caddy, which
 * gates them with `forward_auth` against /api/auth/verify. A host-only cookie
 * is never sent to `ui.` or `tui.`, so the probe would 401 forever and the user
 * would bounce between the subdomain and /login.
 *
 * Unset by default: on a laptop everything is one origin and a domain
 * attribute would only broaden exposure for no gain. deploy/deploy.sh sets it
 * on --public deploys.
 *
 * ONLY the session token is widened. The CSRF cookie keeps its `__Host-`
 * prefix, which FORBIDS a Domain attribute — setting one there makes browsers
 * silently drop the cookie and every sign-in fails with MissingCSRF. CSRF is
 * only needed on the login form's own origin anyway.
 */
const cookieDomain = process.env.AUTH_COOKIE_DOMAIN?.trim() || undefined;
const useSecureCookies = (process.env.AUTH_URL ?? "").startsWith("https://");

export const { handlers, auth, signIn, signOut } = NextAuth({
  session: { strategy: "jwt" },
  pages: { signIn: "/login" },
  // Auth.js merges this per cookie NAME, not deeply, so the whole entry has to
  // be spelled out — including the `__Secure-` prefix, which the browser
  // requires to match the `secure` flag or it rejects the cookie outright.
  ...(cookieDomain
    ? {
        cookies: {
          sessionToken: {
            name: useSecureCookies
              ? "__Secure-authjs.session-token"
              : "authjs.session-token",
            options: {
              httpOnly: true,
              sameSite: "lax" as const,
              path: "/",
              secure: useSecureCookies,
              domain: cookieDomain,
            },
          },
        },
      }
    : {}),
  providers: [
    Credentials({
      name: "Operator",
      credentials: {
        email: { label: "Email", type: "email" },
        password: { label: "Password", type: "password" },
      },
      authorize(credentials) {
        const expectedEmail = process.env.HELIXIS_AUTH_EMAIL;
        const expectedPassword = process.env.HELIXIS_AUTH_PASSWORD;
        if (!expectedEmail || !expectedPassword) return null; // fail closed
        const email = typeof credentials?.email === "string" ? credentials.email : "";
        const password = typeof credentials?.password === "string" ? credentials.password : "";
        const ok =
          safeEqual(email.trim().toLowerCase(), expectedEmail.trim().toLowerCase()) &&
          safeEqual(password, expectedPassword);
        return ok ? { id: "operator", email: expectedEmail, name: "Operator" } : null;
      },
    }),
  ],
  callbacks: {
    authorized({ auth: session, request }) {
      const { pathname } = request.nextUrl;
      // The login page and the auth endpoints themselves must stay reachable.
      if (pathname.startsWith("/login") || pathname.startsWith("/api/auth")) return true;
      return Boolean(session?.user);
    },
  },
});
