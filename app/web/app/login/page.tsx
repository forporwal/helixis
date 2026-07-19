"use client";

import { Suspense, useState } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import { signIn } from "next-auth/react";
import { HelixisLogo } from "@/components/Logo";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";

/**
 * Operator sign-in. One credential pair, defined by HELIXIS_AUTH_EMAIL /
 * HELIXIS_AUTH_PASSWORD on the dashboard host.
 *
 * Still deliberately spare — this is a gate, not a product surface, and it
 * offers exactly the one method that exists. No social buttons, no "sign up",
 * no "forgot password": every one of those would be a control that goes
 * nowhere. What it does now carry is the mark, so the gate is recognisably
 * part of the product, and an explanation of *why* there is a login at all.
 */

function LoginForm() {
  const router = useRouter();
  const search = useSearchParams();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    const res = await signIn("credentials", { email, password, redirect: false });
    setBusy(false);
    if (res?.error) {
      setError(
        "Sign-in failed. Check the email and password — they must match HELIXIS_AUTH_EMAIL and HELIXIS_AUTH_PASSWORD in the dashboard's environment (if those are unset, sign-in is disabled).",
      );
      return;
    }
    router.push(search.get("callbackUrl") ?? "/");
    router.refresh();
  }

  return (
    <div className="w-full max-w-[380px]">
      {/* Identity sits above the card, not inside it — the card is the form. */}
      <div className="mb-6 flex flex-col items-center text-center">
        <HelixisLogo size="lg" />
        <h1 className="mt-4 text-xl font-semibold tracking-tight text-ink">
          Sign in to Helixis
        </h1>
        <p className="mt-1.5 text-xs leading-relaxed text-ink-secondary">
          Controls on this dashboard start epochs and approve containment policy
          changes, so it sits behind a login.
        </p>
      </div>

      <form
        onSubmit={submit}
        className="rounded-2xl border border-hairline bg-surface p-6"
        style={{ boxShadow: "var(--shadow-card)" }}
      >
        <label htmlFor="email" className="block text-xs font-medium text-ink-secondary">
          Email
        </label>
        <Input
          id="email"
          type="email"
          required
          autoComplete="username"
          autoFocus
          placeholder="operator@example.com"
          value={email}
          onChange={(e) => setEmail(e.target.value)}
          className="mt-1.5"
        />

        <label
          htmlFor="password"
          className="mt-4 block text-xs font-medium text-ink-secondary"
        >
          Password
        </label>
        <Input
          id="password"
          type="password"
          required
          autoComplete="current-password"
          placeholder="••••••••"
          value={password}
          onChange={(e) => setPassword(e.target.value)}
          className="mt-1.5"
        />

        {error ? (
          <p
            role="alert"
            className="mt-4 rounded-lg border px-3 py-2 text-xs leading-relaxed"
            style={{
              color: "var(--status-critical)",
              borderColor: "color-mix(in srgb, var(--status-critical) 35%, transparent)",
              background: "color-mix(in srgb, var(--status-critical) 8%, transparent)",
            }}
          >
            {error}
          </p>
        ) : null}

        <Button type="submit" disabled={busy} className="mt-5 h-10 w-full">
          {busy ? "Signing in…" : "Sign in"}
        </Button>
      </form>

      <p className="mt-5 text-center text-[11px] leading-relaxed text-ink-muted">
        Single-operator deployment. Credentials come from the dashboard host&rsquo;s
        environment — there is no account to create.
      </p>
    </div>
  );
}

export default function LoginPage() {
  return (
    <main className="flex min-h-screen flex-1 items-center justify-center bg-page px-4 py-10">
      <Suspense>
        <LoginForm />
      </Suspense>
    </main>
  );
}
