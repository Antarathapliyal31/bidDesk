import { ArrowRightIcon, LoaderIcon } from "lucide-react";

import type { Confidence, RepriceProposal } from "@/api";
import { ConfidencePill } from "@/components/ConfidencePill";
import { Money } from "@/components/Money";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";

/**
 * Re-price before/after dialog (SPEC §9.5 / §11.2). The parent
 * controls open state so the keyboard hook can close it on Esc
 * via Radix's own dismiss handler (fires through onOpenChange).
 *
 * Nothing is applied until the user clicks "Apply" — the dialog
 * is advisory. "Discard" closes without a change.
 */
type Props = {
  proposal: RepriceProposal | null;
  loading: boolean;
  onApply: () => void;
  onClose: () => void;
  hasUserOverrides: boolean;
};

export function RepriceDialog({
  proposal,
  loading,
  onApply,
  onClose,
  hasUserOverrides,
}: Props) {
  const open = loading || proposal !== null;
  return (
    <Dialog open={open} onOpenChange={(v) => (!v ? onClose() : undefined)}>
      <DialogContent className="sm:max-w-xl">
        <DialogHeader>
          <DialogTitle>Re-price line</DialogTitle>
          <DialogDescription>
            The pricing agent returned a proposal. Nothing is
            applied until you choose Apply.
          </DialogDescription>
        </DialogHeader>

        {loading || !proposal ? (
          <div className="flex items-center gap-2 py-6 text-sm text-muted-foreground">
            <LoaderIcon className="size-4 animate-spin motion-reduce:animate-none" aria-hidden />
            Running the pricing agent…
          </div>
        ) : (
          <div className="grid grid-cols-[1fr_auto_1fr] items-stretch gap-3">
            <SideCard label="Before" side={proposal.before} />
            <div className="flex items-center justify-center">
              <ArrowRightIcon className="size-5 text-muted-foreground" aria-hidden />
            </div>
            <SideCard label="After" side={proposal.after} />
          </div>
        )}

        {hasUserOverrides && proposal ? (
          <p className="text-xs text-warn-amber-text">
            This line has manual edits. Applying overwrites them —
            consider adding a note after.
          </p>
        ) : null}

        <DialogFooter>
          <Button variant="outline" onClick={onClose} disabled={loading}>
            Discard
          </Button>
          <Button onClick={onApply} disabled={loading || !proposal}>
            Apply
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

function SideCard({
  label,
  side,
}: {
  label: string;
  side: RepriceProposal["before"] | RepriceProposal["after"];
}) {
  const confidence: Confidence | null = side.confidence;
  return (
    <div className="rounded-md border border-border bg-muted/20 p-3 text-sm">
      <div className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">
        {label}
      </div>
      <div className="mt-1 space-y-1">
        <p className="truncate font-medium">{side.product_name ?? side.sku ?? "—"}</p>
        {side.sku ? (
          <p className="truncate font-mono text-[11px] text-muted-foreground">{side.sku}</p>
        ) : null}
        <p className="tabular text-xs text-muted-foreground">
          {side.sale_qty ?? "—"} {side.sale_unit ?? ""} @{" "}
          <Money cents={side.unit_price_cents} className="text-xs" /> ={" "}
          <Money cents={side.extended_cents} className="text-xs font-medium" />
        </p>
        {confidence ? <ConfidencePill confidence={confidence} /> : null}
      </div>
    </div>
  );
}
