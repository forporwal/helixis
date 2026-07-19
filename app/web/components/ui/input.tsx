"use client";

import * as React from "react";
import { cn } from "@/lib/utils";

function Input({ className, type, ...props }: React.ComponentProps<"input">) {
  return (
    <input
      type={type}
      className={cn(
        "flex h-9 w-full rounded-lg border border-hairline bg-surface px-3 py-1 text-sm text-ink transition-colors placeholder:text-ink-muted focus:border-hairline-strong focus-visible:outline-2 disabled:cursor-not-allowed disabled:opacity-50",
        className,
      )}
      {...props}
    />
  );
}

export { Input };
