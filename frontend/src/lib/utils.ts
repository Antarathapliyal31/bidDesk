/**
 * `cn` — the shadcn/ui className merger. Combines clsx (conditional)
 * with tailwind-merge (deduplicates conflicting Tailwind utilities so
 * later classes win). Every primitive in components/ui imports this.
 */
import { clsx, type ClassValue } from "clsx";
import { twMerge } from "tailwind-merge";

export function cn(...inputs: ClassValue[]): string {
  return twMerge(clsx(inputs));
}
