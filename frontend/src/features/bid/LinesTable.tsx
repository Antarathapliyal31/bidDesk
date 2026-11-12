/**
 * Bid lines table (SPEC §11.2). CSS grid row layout — matches the
 * ItemsStream's visual rhythm so the Live → Bid transition stays
 * legible. Each row is a disclosure of one BidLine.
 *
 * Columns:
 *   Src · Line (desc + sku) · Qty · Unit · Unit price · Ext ·
 *   Confidence · Origins · Flags
 *
 * The LinesTable is a dumb renderer: all mutations go through the
 * BidWorkspace's `queueOp` dispatcher. Review-state resolution
 * happens server-side after a save; the UI shows the local
 * (already-resolved) state optimistically.
 */
import type { Bid, BidLine } from "@/api";
import { findAgentLine } from "@/lib/bidDiff";
import { cn } from "@/lib/utils";

import { BidLineRow, type BidLineField } from "./BidLineRow";

type Props = {
  lines: BidLine[];
  /** Immutable agent revision 0 for diffing (SPEC §9.7). */
  agentBid: Bid | null;
  focusedLineId: string | null;
  editingField: { lineId: string; field: BidLineField } | null;
  pdfUrl: string | null;
  onFocusLine: (lineId: string) => void;
  onStartEdit: (lineId: string, field: BidLineField) => void;
  onCommitEdit: (lineId: string, field: BidLineField, value: string) => void;
  onCancelEdit: () => void;
  onAccept: (lineId: string) => void;
  onDelete: (lineId: string) => void;
  onReprice: (lineId: string) => void;
};

export const GRID_COLS =
  "grid-cols-[52px_minmax(0,1.5fr)_90px_110px_110px_110px_110px]";

export function LinesTable({
  lines,
  agentBid,
  focusedLineId,
  editingField,
  pdfUrl,
  onFocusLine,
  onStartEdit,
  onCommitEdit,
  onCancelEdit,
  onAccept,
  onDelete,
  onReprice,
}: Props) {
  return (
    <section aria-label="Bid lines" className="overflow-hidden rounded-md border border-border bg-card">
      <div
        className={cn(
          "grid gap-4 border-b border-border bg-muted/50 px-4 py-2 text-xs font-medium uppercase tracking-wide text-muted-foreground",
          GRID_COLS,
        )}
      >
        <span>Src</span>
        <span>Line</span>
        <span className="text-right">Qty</span>
        <span>Unit</span>
        <span className="text-right">Unit price</span>
        <span className="text-right">Ext.</span>
        <span>Status</span>
      </div>
      <div role="grid" aria-label="Bid lines" className="divide-y divide-border">
        {lines.map((line) => (
          <BidLineRow
            key={line.line_id}
            line={line}
            agentLine={findAgentLine(agentBid, line.line_id)}
            focused={line.line_id === focusedLineId}
            editingField={editingField?.lineId === line.line_id ? editingField.field : null}
            pdfUrl={pdfUrl}
            onFocus={() => onFocusLine(line.line_id)}
            onStartEdit={(field) => onStartEdit(line.line_id, field)}
            onCommitEdit={(field, value) => onCommitEdit(line.line_id, field, value)}
            onCancelEdit={onCancelEdit}
            onAccept={() => onAccept(line.line_id)}
            onDelete={() => onDelete(line.line_id)}
            onReprice={() => onReprice(line.line_id)}
          />
        ))}
      </div>
    </section>
  );
}
