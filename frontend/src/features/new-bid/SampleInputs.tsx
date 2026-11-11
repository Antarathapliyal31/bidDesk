import {
  ClipboardListIcon,
  FileIcon,
  FileImageIcon,
  FileTextIcon,
  FileWarningIcon,
  RefreshCwIcon,
} from "lucide-react";
import { useState } from "react";

import { useSamples, type Sample } from "@/api";
import { InlineError } from "@/components/InlineError";
import { CardSkeleton } from "@/components/skeletons";
import { cn } from "@/lib/utils";

/**
 * Sample input cards (SPEC §11.4 empty state). Clicking a card
 * prefills the form and announces the selection via a polite live
 * region; focus moves to the parent's Submit button so the next
 * keystroke is Enter.
 *
 * Samples come from `useSamples()` — no fixture awareness.
 *
 * States:
 *   - pending → four CardSkeletons inside an `aria-busy` region +
 *               sr-only "Loading samples…".
 *   - error   → InlineError + Try again (refetch).
 *   - success → the card grid.
 */
const ICONS: Record<Sample["key"], React.ComponentType<{ className?: string }>> = {
  clean: FileTextIcon,
  messy: FileWarningIcon,
  scanned: FileImageIcon,
  paste: ClipboardListIcon,
};

type Props = {
  onPick: (sample: Sample) => void;
  /** Focused after a successful pick so the next keystroke is Enter. */
  submitRef?: React.RefObject<HTMLButtonElement | null>;
  className?: string;
};

export function SampleInputs({ onPick, submitRef, className }: Props) {
  const { data: samples, isPending, isError, refetch } = useSamples();
  const [announce, setAnnounce] = useState("");

  function handlePick(s: Sample) {
    onPick(s);
    setAnnounce(`Loaded sample: ${s.name}`);
    // defer the focus call to the end of the current microtask so
    // React has committed the state update before focus shifts.
    queueMicrotask(() => submitRef?.current?.focus());
  }

  if (isPending) {
    return (
      <div
        aria-busy="true"
        className={cn("grid grid-cols-1 gap-3 sm:grid-cols-2", className)}
      >
        <span className="sr-only">Loading samples…</span>
        {[0, 1, 2, 3].map((i) => (
          <CardSkeleton key={i} />
        ))}
      </div>
    );
  }

  if (isError) {
    return (
      <div className={className}>
        <InlineError>Couldn't load samples.</InlineError>
        <button
          type="button"
          onClick={() => void refetch()}
          className="mt-2 inline-flex h-8 items-center gap-1.5 rounded-md border border-border bg-background px-3 text-xs font-medium hover:bg-muted"
        >
          <RefreshCwIcon className="size-3.5" aria-hidden />
          Try again
        </button>
      </div>
    );
  }

  return (
    <>
      <div className={cn("grid grid-cols-1 gap-3 sm:grid-cols-2", className)}>
        {(samples ?? []).map((s) => {
          const Icon = ICONS[s.key] ?? FileIcon;
          return (
            <button
              key={s.key}
              type="button"
              onClick={() => handlePick(s)}
              className="flex items-start gap-3 rounded-md border border-border bg-card p-3 text-left transition-colors hover:bg-muted/40"
            >
              <div className="mt-0.5 flex size-8 shrink-0 items-center justify-center rounded-md bg-muted">
                <Icon className="size-4 text-muted-foreground" aria-hidden />
              </div>
              <div className="min-w-0 flex-1">
                <p className="truncate text-sm font-medium">{s.name}</p>
                <p className="mt-0.5 line-clamp-2 text-xs text-muted-foreground">{s.description}</p>
                <p className="mt-1 text-xs text-muted-foreground">{s.size_label}</p>
              </div>
            </button>
          );
        })}
      </div>
      <div aria-live="polite" className="sr-only">
        {announce}
      </div>
    </>
  );
}
