"use client";

import { ChevronUp, LogOut } from "lucide-react";
import { signOut } from "next-auth/react";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "./ui/dropdown-menu";

/**
 * Account control, pinned to the foot of the navigation rail.
 *
 * It used to sit in a global header; the header is gone, so this is now the
 * bottom-most row of the rail and the menu opens upward. The chevron points up
 * for the same reason — it should predict where the panel appears.
 */
export function UserMenu({
  email,
  collapsed = false,
}: {
  email?: string | null;
  collapsed?: boolean;
}) {
  const initial = (email?.[0] ?? "?").toUpperCase();

  const avatar = (
    <span
      aria-hidden
      className="flex size-7 shrink-0 items-center justify-center rounded-full bg-primary text-[11px] font-semibold text-on-primary"
    >
      {initial}
    </span>
  );

  return (
    <DropdownMenu>
      <DropdownMenuTrigger
        title={collapsed ? (email ?? "Account") : undefined}
        aria-label="Account"
        className={
          collapsed
            ? "flex w-full items-center justify-center rounded-lg py-2 transition-colors hover:bg-sunken data-[state=open]:bg-sunken"
            : "flex w-full items-center gap-3 rounded-lg px-2.5 py-2 text-left text-sm font-medium text-ink transition-colors hover:bg-sunken data-[state=open]:bg-sunken"
        }
      >
        {avatar}
        {collapsed ? null : (
          <>
            <span className="min-w-0 flex-1 truncate text-xs">
              {email ?? "Operator"}
            </span>
            <ChevronUp aria-hidden className="size-4 shrink-0 text-ink-muted" />
          </>
        )}
      </DropdownMenuTrigger>
      <DropdownMenuContent side="top" align="start" className="w-56">
        <DropdownMenuLabel className="truncate">
          {email ?? "Not signed in"}
        </DropdownMenuLabel>
        <DropdownMenuSeparator />
        <DropdownMenuItem onSelect={() => void signOut({ callbackUrl: "/login" })}>
          <LogOut aria-hidden />
          Sign out
        </DropdownMenuItem>
      </DropdownMenuContent>
    </DropdownMenu>
  );
}
