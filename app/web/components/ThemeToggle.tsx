"use client";

import { Moon, Sun } from "lucide-react";
import { useTheme } from "next-themes";

/**
 * Light/dark switch, styled as a rail row to match the other footer controls.
 * The icon is chosen by CSS off the `dark` class rather than by state —
 * next-themes stamps the class before first paint, so this avoids both a
 * hydration mismatch and a mounted-flag effect.
 */
export function ThemeToggle({ collapsed = false }: { collapsed?: boolean }) {
  const { resolvedTheme, setTheme } = useTheme();

  return (
    <button
      type="button"
      aria-label="Toggle theme"
      title={collapsed ? "Toggle theme" : undefined}
      onClick={() => setTheme(resolvedTheme === "dark" ? "light" : "dark")}
      className={
        collapsed
          ? "flex w-full items-center justify-center rounded-lg py-2 text-ink-muted transition-colors hover:bg-sunken hover:text-ink"
          : "flex w-full items-center gap-3 rounded-lg px-2.5 py-2 text-left text-sm font-medium text-ink-secondary transition-colors hover:bg-sunken hover:text-ink"
      }
    >
      <span
        aria-hidden
        className="flex size-7 shrink-0 items-center justify-center text-ink-muted"
      >
        <Sun className="hidden size-4 dark:block" />
        <Moon className="size-4 dark:hidden" />
      </span>
      {collapsed ? null : (
        <>
          <span className="hidden dark:inline">Light mode</span>
          <span className="dark:hidden">Dark mode</span>
        </>
      )}
    </button>
  );
}
