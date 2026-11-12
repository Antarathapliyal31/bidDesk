import { useEffect, useRef, useState } from "react";

import { cn } from "@/lib/utils";

/**
 * Small inline editor. The display mode shows the current value;
 * clicking (or pressing Enter when focused) flips to an `<input>`
 * seeded with the current value. Enter commits (`onCommit`), Esc
 * cancels, blur commits.
 *
 * Strictly a dumb primitive — the parent decides what op to fire
 * on commit and whether the value is valid. The input's role is
 * "textbox" by default; number mode uses `inputMode="decimal"` so
 * mobile keyboards show the right keys.
 *
 * `label` is for screen readers so each cell announces its field
 * (e.g. "Quantity", "Unit price"). `width` keeps the display and
 * input widths identical so the table doesn't shift when editing.
 */
type Props = {
  value: string;
  label: string;
  onCommit: (next: string) => void;
  disabled?: boolean;
  invalid?: string | null;
  align?: "left" | "right";
  inputMode?: "text" | "decimal";
  className?: string;
  placeholder?: string;
};

export function EditableCell({
  value,
  label,
  onCommit,
  disabled = false,
  invalid = null,
  align = "left",
  inputMode = "text",
  className,
  placeholder,
}: Props) {
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(value);
  const inputRef = useRef<HTMLInputElement | null>(null);

  // No prop→state sync effect needed: `draft` is seeded at the
  // moment the user enters edit mode (via `startEditing`), and
  // display mode reads `value` directly. An external value change
  // while a cell is editing stays local until the user commits
  // or cancels — avoids stomping an in-flight keystroke.

  function startEditing() {
    if (disabled) return;
    setDraft(value);
    setEditing(true);
  }

  function commit() {
    setEditing(false);
    if (draft !== value) onCommit(draft);
  }

  function cancel() {
    setEditing(false);
    setDraft(value);
  }

  useEffect(() => {
    if (editing && inputRef.current) {
      inputRef.current.focus();
      inputRef.current.select();
    }
  }, [editing]);

  const alignClass = align === "right" ? "text-right" : "text-left";

  if (editing) {
    return (
      <input
        ref={inputRef}
        type="text"
        inputMode={inputMode}
        value={draft}
        aria-label={label}
        aria-invalid={invalid ? true : undefined}
        placeholder={placeholder}
        onChange={(e) => setDraft(e.target.value)}
        onBlur={commit}
        onKeyDown={(e) => {
          if (e.key === "Enter") {
            e.preventDefault();
            commit();
          }
          if (e.key === "Escape") {
            e.preventDefault();
            cancel();
          }
          // Swallow j/k/etc. while editing — the parent's
          // keyboard hook should NOT nav rows while a cell has
          // a live input (hook checks for editable target too).
          e.stopPropagation();
        }}
        className={cn(
          "h-6 w-full rounded border border-ring bg-background px-1 text-sm tabular outline-none focus:ring-1 focus:ring-ring",
          alignClass,
          invalid && "border-fail-red",
          className,
        )}
      />
    );
  }

  return (
    <button
      type="button"
      onClick={startEditing}
      disabled={disabled}
      aria-label={`${label}: ${value || "empty"}. Enter to edit.`}
      className={cn(
        "flex h-6 w-full items-center rounded px-1 text-sm transition-colors hover:bg-muted/40 focus-visible:bg-muted focus-visible:outline-none",
        alignClass,
        align === "right" && "justify-end",
        disabled && "cursor-not-allowed opacity-50 hover:bg-transparent",
        className,
      )}
    >
      <span className="tabular truncate">{value || placeholder || "—"}</span>
    </button>
  );
}
