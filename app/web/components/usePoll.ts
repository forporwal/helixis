"use client";

import { useCallback, useEffect, useRef, useState } from "react";

/**
 * Poll a JSON endpoint on an interval.
 *
 * Two behaviours matter for a live dashboard:
 *  - The previous payload is HELD across refetches, so a poll never flashes a
 *    skeleton or jumps the layout. `refreshing` just dims the panel.
 *  - A transport error keeps the last good data on screen and surfaces the
 *    error alongside it, rather than blanking a panel that was fine a second ago.
 */
/**
 * How long a refetch must be outstanding before the UI admits to it.
 *
 * `refreshing` dims its panel to 0.6 opacity. On a laptop the request takes
 * ~15ms and nobody sees it; against a VPS it takes 200-500ms, so every poll
 * became a visible flicker — several times a second across a page with six
 * pollers. Latency turned an invisible affordance into the most distracting
 * thing on screen.
 *
 * Below this threshold the refresh is silent (the previous payload is still on
 * screen and still correct, so there is nothing to tell the user). Above it the
 * request is genuinely slow and the dim is real feedback.
 */
const REFRESH_HINT_DELAY_MS = 500;

export function usePoll<T>(url: string, intervalMs = 4000) {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const mounted = useRef(true);
  const inFlight = useRef(false);
  const hintTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  const clearHint = useCallback(() => {
    if (hintTimer.current) {
      clearTimeout(hintTimer.current);
      hintTimer.current = null;
    }
  }, []);

  const fetchNow = useCallback(async () => {
    if (inFlight.current) return;
    inFlight.current = true;
    // Arm the dim rather than applying it: a fast poll resolves before this
    // fires and never touches the DOM.
    clearHint();
    hintTimer.current = setTimeout(() => {
      if (mounted.current) setRefreshing(true);
    }, REFRESH_HINT_DELAY_MS);
    try {
      const res = await fetch(url, { cache: "no-store" });
      if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);
      const json = (await res.json()) as T;
      if (!mounted.current) return;
      setData(json);
      setError(null);
    } catch (err) {
      if (!mounted.current) return;
      setError((err as Error).message);
    } finally {
      clearHint();
      if (mounted.current) {
        setLoading(false);
        setRefreshing(false);
      }
      inFlight.current = false;
    }
  }, [url, clearHint]);

  useEffect(() => {
    mounted.current = true;
    // Kick the first fetch onto a task rather than calling it inline: fetchNow
    // sets state synchronously, and doing that in the effect body triggers a
    // cascading render (react-hooks/set-state-in-effect).
    const first = setTimeout(() => void fetchNow(), 0);

    // Only poll a VISIBLE tab. A dashboard left open in a background tab
    // otherwise keeps hitting the server forever — pointless load on a shared
    // 2-vCPU box, and on a metered connection pointless traffic. Refetch
    // immediately on return so the first thing the user sees is current.
    const onVisibility = () => {
      if (document.visibilityState === "visible") void fetchNow();
    };
    document.addEventListener("visibilitychange", onVisibility);

    const id = setInterval(() => {
      if (document.visibilityState === "visible") void fetchNow();
    }, intervalMs);

    return () => {
      mounted.current = false;
      clearTimeout(first);
      clearInterval(id);
      clearHint();
      document.removeEventListener("visibilitychange", onVisibility);
    };
  }, [fetchNow, intervalMs, clearHint]);

  return { data, error, loading, refreshing, refetch: fetchNow };
}
