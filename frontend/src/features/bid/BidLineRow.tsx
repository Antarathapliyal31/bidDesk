/**
 * One row of the LinesTable. Each cell reads its own slice of the
 * BidLine; edits are dispatched up via callbacks. Row focus is
 * controlled (focusedLineId in the parent) so `j`/`k` keyboard
 * nav can scroll the right row into view.
 *
 * Status column surfaces the review state — "needs review" (amber
 * with reasons tooltip), "accepted" (neutral check), "resolved"
 * (muted). The origin markers next to the price / qty cells make
 * agent-vs-user provenance visible per SPEC §9.4.
 */
import { CheckIcon, PackageXIcon, XIcon } from "lucide-react";
import { useEffect, useRef } from "react";

import type { BidLine, Confidence } from "@/api";
import { CitationLink } from "@/components/CitationLink";
import { ConfidencePill } from "@/components/ConfidencePill";
import { Money } from "@/components/Money";
import { OriginMarker } from "@/components/OriginMarker";
import { fieldChanged, type DiffField } from "@/lib/bidDiff";
import { reasonCodeLabel } from "@/lib/reasonCodes";
import { cn } from "@/lib/utils";

import { EditableCell } from "./cells/EditableCell";
import { GRID_COLS } from "./LinesTable";

/**
 * Field identifier for the inline cells. Lives at module level
 * (not a TS namespace) so `@typescript-eslint/no-namespace` is
 * happy. Callers import `type BidLineField` from this module.
 */
export type BidLineField = "quantity" | "unit_price" | "sku" | "description";

type Props = {
  line: BidLine;
  /**
   * Matching agent-origin line (same `line_id`, SPEC §9.7). Null
   * when the draft line was user-added — DiffMarker then renders
   * nothing and the line shows under "added" in the header count.
   */
  agentLine: BidLine | null;
  focused: boolean;
  editingField: BidLineField | null;
  pdfUrl: string | null;
  onFocus: () => void;
  onStartEdit: (field: BidLineField) => void;
  onCommitEdit: (field: BidLineField, value: string) => void;
  onCancelEdit: () => void;
  onAccept: () => void;
  onDelete: () => void;
  onReprice: () => void;
};

export function BidLineRow({
  line,
  agentLine,
  focused,
  pdfUrl,
  onFocus,
  onStartEdit,
  onCommitEdit,
  onAccept,
  onDelete,
  onReprice,
}: Props) {
  const rowRef = useRef<HTMLDivElement>(null);

  // Scroll the focused row into view when `j`/`k` moves focus.
  useEffect(() => {
    if (focused && rowRef.current) {
      rowRef.current.scrollIntoView({ block: "nearest" });
    }
  }, [focused]);

  const confidence: Confidence | null = line.match?.confidence ?? null;

  return (
    <div
      ref={rowRef}
      role="row"
      aria-selected={focused}
      aria-rowindex={undefined}
      data-line-id={line.line_id}
      onFocus={onFocus}
      onClick={onFocus}
      tabIndex={focused ? 0 : -1}
      className={cn(
        "grid items-center gap-4 px-4 py-2 text-sm transition-colors",
        GRID_COLS,
        focused && "bg-muted/40",
        line.review.state === "resolved" && "opacity-70",
      )}
    >
      {/* Src — citation chip */}
      <div role="gridcell">
        <CitationLink source={line.source} pdfUrl={pdfUrl} />
      </div>

      {/* Line — description + product_name + sku + review badge */}
      <div role="gridcell" className="min-w-0">
        <EditableCell
          value={line.description}
          label="Description"
          onCommit={(v) => onCommitEdit("description", v)}
          className="font-medium"
        />
        <DiffMarker line={line} agentLine={agentLine} field="description" />
        {line.product_name && line.product_name !== line.description ? (
          <p className="truncate pl-1 text-xs text-muted-foreground">
            {line.product_name}
          </p>
        ) : null}
        <div className="mt-1 flex flex-wrap items-center gap-1.5 pl-1 text-xs">
          {line.sku ? (
            <>
              <EditableCell
                value={line.sku}
                label="SKU"
                onCommit={(v) => onCommitEdit("sku", v)}
                className="inline-flex h-5 w-auto px-1 text-[11px] font-mono"
              />
              <DiffMarker line={line} agentLine={agentLine} field="sku" inline />
            </>
          ) : (
            <button
              type="button"
              onClick={() => onStartEdit("sku")}
              className="text-xs text-muted-foreground underline-offset-2 hover:underline"
            >
              + add SKU
            </button>
          )}
          <OriginMarker origin={line.origins.sku} />
          <ReviewPill line={line} />
        </div>
      </div>

      {/* Qty — editable */}
      <div role="gridcell" className="flex items-center justify-end gap-1">
        <div className="min-w-0 flex-1 text-right">
          <EditableCell
            value={line.sale_qty ?? ""}
            label="Quantity"
            onCommit={(v) => onCommitEdit("quantity", v)}
            align="right"
            inputMode="decimal"
            className="w-16"
          />
          <DiffMarker line={line} agentLine={agentLine} field="sale_qty" align="right" />
        </div>
        <OriginMarker origin={line.origins.quantity} />
      </div>

      {/* Unit — read-only */}
      <div role="gridcell" className="text-xs text-muted-foreground">
        {line.sale_unit ?? "—"}
      </div>

      {/* Unit price — always via the price-override dialog so edits
          carry a required `override_reason`. */}
      <div role="gridcell" className="flex min-w-0 items-start justify-end gap-1">
        <div className="min-w-0 flex-1 text-right">
          <button
            type="button"
            onClick={() => onStartEdit("unit_price")}
            className="h-6 w-full rounded px-1 text-right tabular hover:bg-muted/40 focus-visible:bg-muted focus-visible:outline-none"
            aria-label="Unit price. Enter to edit — requires an override reason."
          >
            {line.unit_price_cents === null ? (
              <span className="text-xs text-muted-foreground">—</span>
            ) : (
              <Money cents={line.unit_price_cents} />
            )}
          </button>
          <DiffMarker line={line} agentLine={agentLine} field="unit_price_cents" align="right" />
          {line.override_reason ? (
            <p className="truncate text-right text-[10px] text-injected-violet-text" title={line.override_reason}>
              override: {line.override_reason}
            </p>
          ) : null}
        </div>
        <OriginMarker origin={line.origins.unit_price} />
      </div>

      {/* Ext. — read-only, computed by server */}
      <div role="gridcell" className="text-right tabular">
        {line.extended_cents !== null ? <Money cents={line.extended_cents} /> : "—"}
      </div>

      {/* Status — match status + confidence + row actions */}
      <div role="gridcell" className="flex flex-wrap items-center gap-1.5">
        <StatusChip status={line.match?.status ?? null} />
        {confidence ? <ConfidencePill confidence={confidence} /> : null}
        {line.review.state === "open" ? (
          <button
            type="button"
            onClick={onAccept}
            aria-label="Accept line"
            className="inline-flex size-6 items-center justify-center rounded border border-border bg-background text-done-green-text hover:bg-muted"
            title="Accept this line (a)"
          >
            <CheckIcon className="size-3.5" aria-hidden />
          </button>
        ) : null}
        <button
          type="button"
          onClick={onReprice}
          aria-label="Re-price line"
          className="inline-flex size-6 items-center justify-center rounded border border-border bg-background text-muted-foreground hover:bg-muted"
          title="Re-price (r)"
        >
          <RepriceIcon />
        </button>
        <button
          type="button"
          onClick={onDelete}
          aria-label="Delete line"
          className="inline-flex size-6 items-center justify-center rounded border border-border bg-background text-muted-foreground hover:bg-muted hover:text-fail-red-text"
          title="Delete line"
        >
          <XIcon className="size-3.5" aria-hidden />
        </button>
      </div>
    </div>
  );
}

/**
 * Minimal diff marker. If the field has been edited away from the
 * agent's original value, render the old value with a strikethrough
 * next to the current one. Display-only — the parent is responsible
 * for the edit flow.
 */
function DiffMarker({
  line,
  agentLine,
  field,
  inline = false,
  align = "left",
}: {
  line: BidLine;
  agentLine: BidLine | null;
  field: DiffField;
  inline?: boolean;
  align?: "left" | "right";
}) {
  const change = fieldChanged(line, agentLine, field);
  if (change === null) return null;
  const label =
    field === "unit_price_cents"
      ? change.from === null
        ? "—"
        : formatCents(Number(change.from))
      : change.from === null
        ? "empty"
        : change.from;
  return (
    <span
      className={cn(
        "ml-1 text-[10px] text-muted-foreground",
        inline ? "inline" : "block",
        align === "right" && "text-right",
      )}
      title="Edited by you"
    >
      <span className="line-through">{label}</span>
    </span>
  );
}

function formatCents(cents: number): string {
  const sign = cents < 0 ? "-" : "";
  const abs = Math.abs(cents);
  return `${sign}$${(abs / 100).toFixed(2)}`;
}

function ReviewPill({ line }: { line: BidLine }) {
  if (line.review.state === "none") return null;
  if (line.review.state === "resolved") {
    const labelMap = { edit: "edited", accept: "accepted", delete: "deleted" } as const;
    const by = line.review.resolved_by;
    return (
      <span
        className="inline-flex items-center gap-0.5 rounded border border-border bg-muted px-1 text-[10px] font-medium text-muted-foreground"
        title={`Review resolved: ${by ? labelMap[by] : "resolved"}`}
      >
        resolved
      </span>
    );
  }
  const reasons = line.review.reasons.map(reasonCodeLabel).join(", ");
  return (
    <span
      className="inline-flex items-center gap-0.5 rounded border border-warn-amber/30 bg-warn-amber/10 px-1 text-[10px] font-medium text-warn-amber-text"
      title={reasons}
    >
      needs review
    </span>
  );
}

function StatusChip({ status }: { status: NonNullable<BidLine["match"]>["status"] | null }) {
  if (!status || status === "matched") return null;
  if (status === "needs_review") {
    return (
      <span
        className="inline-flex items-center gap-1 rounded border border-warn-amber/30 bg-warn-amber/10 px-1.5 py-0.5 text-[10px] font-medium text-warn-amber-text"
        title="Server flagged this line for human review."
      >
        Needs review
      </span>
    );
  }
  if (status === "no_match") {
    return (
      <span className="inline-flex items-center gap-1 text-xs text-fail-red-text">
        <PackageXIcon className="size-3.5" aria-hidden />
        No match
      </span>
    );
  }
  if (status === "skipped") {
    return <span className="text-xs text-muted-foreground">Skipped</span>;
  }
  if (status === "failed" || status === "timeout") {
    return (
      <span className="inline-flex items-center gap-1 text-xs text-fail-red-text">
        <PackageXIcon className="size-3.5" aria-hidden />
        {status === "timeout" ? "Timed out" : "Failed"}
      </span>
    );
  }
  if (status === "substitute") {
    return (
      <span className="inline-flex items-center gap-0.5 rounded border border-warn-amber/30 bg-warn-amber/10 px-1 text-[10px] font-medium text-warn-amber-text">
        sub
      </span>
    );
  }
  return null;
}

function RepriceIcon() {
  return (
    <svg
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.5"
      strokeLinecap="round"
      strokeLinejoin="round"
      className="size-3.5"
      aria-hidden
    >
      <path d="M3 3v5h5" />
      <path d="M3.05 13A9 9 0 1 0 6 5.3L3 8" />
      <path d="M12 7v5l3 3" />
    </svg>
  );
}
