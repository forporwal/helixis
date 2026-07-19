export { auth as middleware } from "@/auth";

/**
 * Everything behind the login except static assets. The `authorized` callback
 * in auth.ts decides per-request: pages redirect to /login, API calls get 401.
 */
export const config = {
  matcher: ["/((?!_next/static|_next/image|favicon\\.ico|.*\\.svg$).*)"],
};
