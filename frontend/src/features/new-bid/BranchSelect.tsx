import { RefreshCwIcon } from "lucide-react";
import { useId } from "react";

import { useBranches } from "@/api";
import { InlineError } from "@/components/InlineError";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";

/**
 * Branch picker. Reads via `useBranches()` so the component is
 * fixture-free — Phase 5 merge swaps the hook body to a real fetch
 * without touching this file. `aria-required` marks the field.
 *
 * States:
 *   - pending  → disabled select, "Loading branches…" placeholder.
 *   - error    → InlineError "Couldn't load branches." + Try again
 *                button that calls `refetch()`.
 *   - success  → the real combobox.
 *
 * An optional `error` prop (separate from the query's error) lets
 * the parent surface a validation message linked via
 * `aria-describedby`.
 */
type Props = {
  value: string | null;
  onChange: (next: string) => void;
  id?: string;
  error?: string;
};

export function BranchSelect({ value, onChange, id, error }: Props) {
  const generatedId = useId();
  const triggerId = id ?? generatedId;
  const errId = `${triggerId}-error`;
  const loadErrId = `${triggerId}-load-error`;
  const { data: branches, isPending, isError, refetch } = useBranches();

  if (isError) {
    return (
      <div className="space-y-1.5">
        <Label htmlFor={triggerId}>Branch</Label>
        <div>
          <InlineError id={loadErrId}>Couldn't load branches.</InlineError>
          <button
            type="button"
            onClick={() => void refetch()}
            className="mt-2 inline-flex h-8 items-center gap-1.5 rounded-md border border-border bg-background px-3 text-xs font-medium hover:bg-muted"
          >
            <RefreshCwIcon className="size-3.5" aria-hidden />
            Try again
          </button>
        </div>
      </div>
    );
  }

  return (
    <div className="space-y-1.5">
      <Label htmlFor={triggerId}>Branch</Label>
      <Select value={value ?? ""} onValueChange={onChange} disabled={isPending}>
        <SelectTrigger
          id={triggerId}
          aria-required="true"
          aria-invalid={error ? true : undefined}
          aria-describedby={error ? errId : undefined}
          className="w-full"
        >
          <SelectValue placeholder={isPending ? "Loading branches…" : "Choose a branch"} />
        </SelectTrigger>
        <SelectContent>
          {(branches ?? []).map((b) => (
            <SelectItem key={b.id} value={b.id}>
              {b.name} <span className="text-muted-foreground">· {b.city}, {b.state}</span>
            </SelectItem>
          ))}
        </SelectContent>
      </Select>
      {error ? <InlineError id={errId}>{error}</InlineError> : null}
    </div>
  );
}
