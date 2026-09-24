import { clsx, type ClassValue } from "clsx";
import { twMerge } from "tailwind-merge";

/**
 * shadcn's class combiner: `clsx` for conditionals, `tailwind-merge` so a caller's
 * utility wins over a component's default for the same CSS property instead of both
 * landing in `class` and the later one in the stylesheet winning arbitrarily.
 *
 * Vendored library code depends on this name and signature; it is not ours to rename.
 */
export function cn(...inputs: ClassValue[]): string {
  return twMerge(clsx(inputs));
}
