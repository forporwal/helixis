"use client";

import * as React from "react";
import { cn } from "@/lib/utils";

function Kbd({ className, ...props }: React.ComponentProps<"kbd">) {
  return (
    <kbd
      data-slot="kbd"
      className={cn(
        "inline-flex h-4 min-w-4 items-center justify-center rounded border border-hairline bg-sunken px-1.5 font-mono text-[10px] leading-none text-ink-muted",
        className,
      )}
      {...props}
    />
  );
}

export { Kbd };
