/**
 * Vendored from shadcn/ui, themed for this repository (`contracts/tokens.md`).
 *
 * The shadow every variant ships with is removed. `destructive` is deliberately kept
 * narrow: it maps onto `--color-attention`, which FR-005 reserves for "a person is
 * needed", so the variant is a claim and not a colour — use it only where it is true.
 */
import { Slot } from "@radix-ui/react-slot";
import { cva, type VariantProps } from "class-variance-authority";
import * as React from "react";

import { cn } from "@/lib/utils";

const buttonVariants = cva(
  "inline-flex items-center justify-center gap-2 rounded-md text-base font-medium whitespace-nowrap transition-colors focus-visible:ring-ring focus-visible:ring-2 focus-visible:ring-offset-1 focus-visible:outline-none disabled:pointer-events-none disabled:opacity-50 [&_svg]:pointer-events-none [&_svg]:shrink-0 [&_svg:not([class*='size-'])]:size-4",
  {
    variants: {
      variant: {
        default: "bg-accent text-surface hover:bg-accent-dark",
        outline:
          "border border-rule bg-surface text-ink hover:bg-surface-sunken",
        ghost: "text-ink hover:bg-surface-sunken",
        destructive: "bg-attention text-surface hover:brightness-95",
        link: "text-accent-dark underline-offset-4 hover:underline",
      },
      size: {
        default: "h-9 px-4 py-2",
        sm: "h-8 px-3 text-sm",
        lg: "h-10 px-6",
        icon: "size-9",
      },
    },
    defaultVariants: {
      variant: "default",
      size: "default",
    },
  },
);

function Button({
  className,
  variant,
  size,
  asChild = false,
  type,
  ...props
}: React.ComponentProps<"button"> &
  VariantProps<typeof buttonVariants> & {
    asChild?: boolean;
  }) {
  const Comp = asChild ? Slot : "button";
  return (
    <Comp
      data-slot="button"
      // `type="button"` by default, which the source this was vendored from leaves to
      // the caller. HTML defaults a button inside a form to `submit`, so the first
      // `<form>` anyone adds would silently turn every unmarked button on that screen
      // into one that submits it. There is no form here today; this is the primitive
      // being fixed at source rather than 22 call sites each remembering. A caller that
      // genuinely wants a submit button still passes `type="submit"`.
      type={asChild ? type : (type ?? "button")}
      className={cn(buttonVariants({ variant, size, className }))}
      {...props}
    />
  );
}

export { Button, buttonVariants };
