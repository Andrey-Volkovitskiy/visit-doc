/**
 * Vendored from shadcn/ui, themed for this repository (`contracts/tokens.md`).
 *
 * The list is a rail of hairline-separated tabs rather than the source's filled pill
 * group: the chosen direction builds structure from rules and tint, and an active tab is
 * marked by its underline and weight as well as by colour (FR-006).
 */
import * as TabsPrimitive from "@radix-ui/react-tabs";
import * as React from "react";

import { cn } from "@/lib/utils";

function Tabs({
  className,
  ...props
}: React.ComponentProps<typeof TabsPrimitive.Root>) {
  return (
    <TabsPrimitive.Root
      data-slot="tabs"
      className={cn("flex flex-col gap-4", className)}
      {...props}
    />
  );
}

function TabsList({
  className,
  ...props
}: React.ComponentProps<typeof TabsPrimitive.List>) {
  return (
    <TabsPrimitive.List
      data-slot="tabs-list"
      className={cn(
        "border-rule-soft flex w-fit items-center gap-1 border-b",
        className,
      )}
      {...props}
    />
  );
}

function TabsTrigger({
  className,
  ...props
}: React.ComponentProps<typeof TabsPrimitive.Trigger>) {
  return (
    <TabsPrimitive.Trigger
      data-slot="tabs-trigger"
      className={cn(
        "text-ink-muted data-[state=active]:text-accent-dark data-[state=active]:border-accent focus-visible:ring-ring -mb-px inline-flex items-center justify-center border-b-2 border-transparent px-3 py-2 text-base font-medium whitespace-nowrap transition-colors focus-visible:ring-2 focus-visible:outline-none data-[state=active]:font-semibold disabled:pointer-events-none disabled:opacity-50",
        className,
      )}
      {...props}
    />
  );
}

function TabsContent({
  className,
  ...props
}: React.ComponentProps<typeof TabsPrimitive.Content>) {
  return (
    <TabsPrimitive.Content
      data-slot="tabs-content"
      // No `outline-none` here, which the source this was vendored from ships. Radix
      // makes a tab panel focusable (`tabindex="0"`) so its content is reachable by
      // keyboard, and suppressing the outline means a reader who tabs into it is given
      // no sign of where they are. Measured in a real browser: the panel was the one
      // focusable thing on the page with neither an outline nor a ring (FR-039). The
      // global `:focus-visible` rule in `app.css` now reaches it, as it reaches the
      // thread's own scroll container.
      className={cn("flex-1", className)}
      {...props}
    />
  );
}

export { Tabs, TabsContent, TabsList, TabsTrigger };
