import { useId } from "react";

import { InlineError } from "@/components/InlineError";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { cn } from "@/lib/utils";

import { MAX_TEXT_CHARS } from "./limits";
import { codePointLength } from "./validation";

/**
 * Paste-a-list editor. The textarea has a real <label> (sr-only so
 * the visual layout stays clean). `aria-describedby` always points at
 * the hint + counter, and adds the error id when the paste is over
 * the limit.
 *
 * The error text is fixed — "Over the 50,000-character limit…" — so
 * assistive tech doesn't re-announce it on every keystroke. The live
 * counter is a plain element (no aria-live) so screen readers aren't
 * flooded; a user who hits the limit hears the alert once.
 *
 * Counting is code points (Python's `len(s)`), not UTF-16 code units
 * — see validation.ts.
 */
type Props = {
  value: string;
  onChange: (next: string) => void;
};

export function PasteListEditor({ value, onChange }: Props) {
  const inputId = useId();
  const hintId = useId();
  const counterId = useId();
  const errId = useId();

  const count = codePointLength(value);
  const tooLong = count > MAX_TEXT_CHARS;

  const describedBy = tooLong
    ? `${hintId} ${counterId} ${errId}`
    : `${hintId} ${counterId}`;

  return (
    <div>
      <Label htmlFor={inputId} className="sr-only">
        Materials list
      </Label>
      <Textarea
        id={inputId}
        placeholder={
          "Paste a materials list, one item per line.\n" +
          "(40) 2x6x16 PT #2\n" +
          "12 SHT OSB 7/16\n" +
          "6 BDL shingles"
        }
        value={value}
        onChange={(e) => onChange(e.target.value)}
        rows={10}
        aria-invalid={tooLong || undefined}
        aria-describedby={describedBy}
        className={cn("resize-y font-mono text-sm", tooLong && "border-fail-red-text")}
      />
      <div className="mt-1.5 flex items-center justify-between gap-3">
        <p id={hintId} className="text-xs text-muted-foreground">
          One item per line. Abbreviations like LF, SQ, SHT are understood.
        </p>
        <p
          id={counterId}
          className={cn(
            "tabular shrink-0 text-xs",
            tooLong ? "text-fail-red-text" : "text-muted-foreground",
          )}
        >
          {count.toLocaleString()} / {MAX_TEXT_CHARS.toLocaleString()}
        </p>
      </div>
      {tooLong ? (
        <InlineError id={errId}>
          Over the {MAX_TEXT_CHARS.toLocaleString()}-character limit. Shorten the list or
          upload a PDF.
        </InlineError>
      ) : null}
    </div>
  );
}
