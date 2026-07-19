"use client";

import * as React from "react";
import { Slot } from "@radix-ui/react-slot";
import { cva, type VariantProps } from "class-variance-authority";
import { cn } from "@/lib/utils";

const badgeVariants = cva(
  "inline-flex items-center gap-1 whitespace-nowrap rounded-full border px-2 py-0.5 text-[11px] font-medium transition-colors [&_svg]:pointer-events-none [&_svg]:size-3 [&_svg]:shrink-0",
  {
    variants: {
      variant: {
        neutral: "border-hairline bg-sunken text-ink-secondary",
        good: "border-transparent bg-good/10 text-good",
        warning: "border-transparent text-warning",
        critical: "border-transparent text-critical",
        primary: "border-transparent bg-primary text-on-primary",
        outline: "border-hairline-strong bg-transparent text-ink-secondary",
      },
    },
    defaultVariants: {
      variant: "neutral",
    },
  },
);

function Badge({
  className,
  variant,
  asChild = false,
  ...props
}: React.ComponentProps<"span"> &
  VariantProps<typeof badgeVariants> & { asChild?: boolean }) {
  const Comp = asChild ? Slot : "span";
  return (
    <Comp
      data-slot="badge"
      className={cn(badgeVariants({ variant, className }))}
      {...props}
    />
  );
}

export { Badge, badgeVariants };
