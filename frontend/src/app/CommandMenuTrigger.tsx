import { SearchIcon } from "lucide-react";

import { Kbd } from "@/components/Kbd";
import { cn } from "@/lib/utils";

import { useCommandMenu } from "./CommandMenuContext";
import { MOD_KEY_LABEL, MOD_KEY_LABEL_SR } from "./platform";

/**
 * Top-right search field that opens the ⌘K menu. Looks like a search
 * input so the affordance is discoverable without knowing the key;
 * the Kbd pills on the right label the shortcut. It's a real button,
 * not a text input — searching lives inside the command menu itself.
 *
 * Accessible name comes from the button's text content: the "Search"
 * span (always readable by AT, visually hidden below `sm`) plus the
 * two Kbd pills' aria-labels (e.g. "Command K" / "Control K").
 */
export function CommandMenuTrigger({ className }: { className?: string }) {
  const { setOpen } = useCommandMenu();
  return (
    <button
      type="button"
      onClick={() => setOpen(true)}
      className={cn(
        "inline-flex h-8 w-full max-w-72 items-center gap-2 rounded-md border border-border bg-muted px-2.5 text-left text-sm text-muted-foreground transition-colors hover:border-zinc-300 hover:text-foreground dark:hover:border-zinc-700",
        className,
      )}
    >
      <SearchIcon className="size-4 shrink-0" aria-hidden />
      <span className="sr-only flex-1 truncate sm:not-sr-only">Search</span>
      <span className="ml-auto flex items-center gap-1">
        <Kbd label={MOD_KEY_LABEL_SR}>{MOD_KEY_LABEL}</Kbd>
        <Kbd>K</Kbd>
      </span>
    </button>
  );
}
