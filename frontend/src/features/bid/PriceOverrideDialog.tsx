/**
 * Price-override dialog (SPEC §9.4 `set_unit_price`). Replaces
 * the inline edit for `unit_price_cents` so every price change
 * carries an `override_reason` — the audit trail the estimator
 * needs when the bid is handed back to the contractor.
 *
 * Both fields are required; Apply is disabled until both are
 * valid (unit_price is a non-negative integer, reason is
 * non-empty).
 */
import { useState } from "react";

import type { BidLine } from "@/api";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { InlineError } from "@/components/InlineError";
import { Money } from "@/components/Money";

type Props = {
  line: BidLine | null;
  onApply: (unitPriceCents: number, reason: string) => void;
  onClose: () => void;
};

export function PriceOverrideDialog({ line, onApply, onClose }: Props) {
  const open = line !== null;
  // Remount the dialog body per `line` so the draft fields are
  // seeded by the mount, not by a prop→state sync effect (which
  // React Compiler flags as a cascading render). Parent controls
  // open-ness via `line`; `key` guarantees a fresh mount.
  return (
    <Dialog open={open} onOpenChange={(v) => (!v ? onClose() : undefined)}>
      <DialogContent className="sm:max-w-md" key={line?.line_id ?? "closed"}>
        {line ? (
          <PriceOverrideBody line={line} onApply={onApply} onClose={onClose} />
        ) : null}
      </DialogContent>
    </Dialog>
  );
}

function PriceOverrideBody({
  line,
  onApply,
  onClose,
}: {
  line: BidLine;
  onApply: Props["onApply"];
  onClose: Props["onClose"];
}) {
  const [price, setPrice] = useState<string>(
    line.unit_price_cents !== null ? String(line.unit_price_cents) : "",
  );
  const [reason, setReason] = useState<string>(line.override_reason ?? "");
  const [error, setError] = useState<string | null>(null);

  function tryApply() {
    const parsed = Number(price);
    if (!Number.isInteger(parsed) || parsed < 0) {
      setError("Unit price must be a whole number of cents (≥ 0).");
      return;
    }
    const trimmed = reason.trim();
    if (!trimmed) {
      setError("Please note why — the reason shows next to the price on the bid.");
      return;
    }
    if (trimmed.length > 200) {
      setError("Reason is capped at 200 characters.");
      return;
    }
    onApply(parsed, trimmed);
  }

  return (
    <>
      <DialogHeader>
        <DialogTitle>Override unit price</DialogTitle>
        <DialogDescription>
          Catalog price is{" "}
          {line.catalog_unit_price_cents !== null ? (
            <Money
              cents={line.catalog_unit_price_cents}
              className="font-medium text-foreground"
            />
          ) : (
            "not available"
          )}
          . Overrides require a short note so the estimator can defend the bid later.
        </DialogDescription>
      </DialogHeader>

      <div className="space-y-3">
        <label className="block text-xs font-medium text-muted-foreground">
          New unit price (cents)
          <input
            type="text"
            inputMode="decimal"
            value={price}
            onChange={(e) => {
              setPrice(e.target.value);
              setError(null);
            }}
            className="mt-1 block h-9 w-full rounded-md border border-border bg-background px-2 text-sm tabular focus-visible:border-ring focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring"
            placeholder="e.g. 1575"
            autoFocus
          />
        </label>
        <label className="block text-xs font-medium text-muted-foreground">
          Reason (≤ 200 chars)
          <textarea
            value={reason}
            onChange={(e) => {
              setReason(e.target.value);
              setError(null);
            }}
            className="mt-1 block min-h-[64px] w-full rounded-md border border-border bg-background px-2 py-1.5 text-sm focus-visible:border-ring focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring"
            placeholder="Negotiated discount, catalog price stale, special order…"
            maxLength={200}
          />
        </label>
        {error ? <InlineError>{error}</InlineError> : null}
      </div>

      <DialogFooter>
        <Button variant="outline" onClick={onClose}>
          Cancel
        </Button>
        <Button onClick={tryApply} disabled={!price.trim() || !reason.trim()}>
          Apply override
        </Button>
      </DialogFooter>
    </>
  );
}
