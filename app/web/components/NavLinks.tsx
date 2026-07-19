"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import {
  BookOpen,
  FlaskConical,
  LayoutDashboard,
  ListChecks,
  ShieldCheck,
  type LucideIcon,
} from "lucide-react";
import { cn } from "@/lib/utils";

/**
 * The five product surfaces, split the way the product now reads: the agent you
 * use, then the machinery that improves it.
 *
 * "Agent" holds Home (launch it), Wiki (the memory it runs with), and
 * Containment (the boundary it runs inside) — all things that describe the
 * running agent. "Train" holds Lab and Tasks, which describe the loop that
 * produces it. The seam matters because Home stopped being a results page: a
 * rail that still filed it under "Results" would argue with the page itself.
 */
export const NAV_SECTIONS: {
  label: string;
  items: { href: string; label: string; icon: LucideIcon }[];
}[] = [
  {
    label: "Agent",
    items: [
      { href: "/", label: "Home", icon: LayoutDashboard },
      { href: "/wiki", label: "Wiki", icon: BookOpen },
      { href: "/containment", label: "Containment", icon: ShieldCheck },
    ],
  },
  {
    label: "Train",
    items: [
      { href: "/lab", label: "Lab", icon: FlaskConical },
      { href: "/tasks", label: "Tasks", icon: ListChecks },
    ],
  },
];

export function NavLinks({ collapsed = false }: { collapsed?: boolean }) {
  const pathname = usePathname();

  return (
    <>
      {NAV_SECTIONS.map((section) => (
        <div key={section.label}>
          {collapsed ? (
            <div className="mx-2.5 mt-4 mb-2 border-t border-hairline" />
          ) : (
            <p className="mt-6 px-2 text-[11px] font-semibold uppercase tracking-wider text-ink-muted">
              {section.label}
            </p>
          )}
          <nav className={cn("flex flex-col gap-1 text-sm", collapsed ? "mt-0" : "mt-3")}>
            {section.items.map((item) => {
              // Every path starts with "/", so the home link needs an exact
              // match or it would highlight on all five pages.
              const active =
                item.href === "/" ? pathname === "/" : pathname.startsWith(item.href);
              const Icon = item.icon;
              return (
                <Link
                  key={item.href}
                  href={item.href}
                  aria-current={active ? "page" : undefined}
                  title={collapsed ? item.label : undefined}
                  className={cn(
                    "flex items-center gap-3 rounded-xl py-2 font-medium transition-colors",
                    collapsed ? "justify-center px-0" : "px-2.5",
                    active
                      ? "bg-surface text-primary"
                      : "text-ink-secondary hover:bg-surface/70 hover:text-ink dark:hover:bg-sunken",
                  )}
                  style={active ? { boxShadow: "var(--shadow-card)" } : undefined}
                >
                  <span
                    aria-hidden
                    className={cn(
                      "flex size-7 shrink-0 items-center justify-center rounded-lg transition-colors",
                      active ? "bg-primary/12 text-primary" : "text-ink-muted",
                    )}
                  >
                    <Icon className="size-4" />
                  </span>
                  {collapsed ? <span className="sr-only">{item.label}</span> : item.label}
                </Link>
              );
            })}
          </nav>
        </div>
      ))}
    </>
  );
}
