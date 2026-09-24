/**
 * Vendored from shadcn/ui, themed for this repository (`contracts/tokens.md`).
 * No shadow, and the invalid state is a ring rather than a colour alone (FR-006).
 */
import * as React from "react";

import { cn } from "@/lib/utils";

function Input({ className, type, ...props }: React.ComponentProps<"input">) {
  return (
    <input
      type={type}
      data-slot="input"
      className={cn(
        "border-input bg-surface text-ink placeholder:text-ink-muted focus-visible:ring-ring flex h-9 w-full min-w-0 rounded-md border px-3 py-1 text-base transition-colors focus-visible:ring-2 focus-visible:outline-none disabled:cursor-not-allowed disabled:opacity-50 aria-invalid:border-destructive aria-invalid:ring-destructive/30",
        className,
      )}
      {...props}
    />
  );
}

export { Input };
