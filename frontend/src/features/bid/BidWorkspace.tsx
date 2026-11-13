/**
 * Editable bid workspace (SPEC §11.2). Replaces
 * `BidWorkspacePlaceholder`.
 *
 * State flow:
 *   - `useBidDraft(runId)` owns the authoritative Bid: fetch,
 *     apply ops optimistically, autosave (800 ms debounce),
 *     flush-and-finalize.
 *   - Focus / editing-field state is page-local (one row
 *     focused, maybe one cell editing). The bid keyboard hook
 *     drives j/k/]/[/a/r/⌘↵.
 *   - Reprice state is a small local tuple (loading /
 *     proposal) — the dialog closes by clearing it.
 *
 * Everything below is a dumb renderer: the LinesTable, Sections,
 * TotalsBar, FinalizeBar don't know about ops. They surface
 * callbacks; this component decides which op to queue.
 */
import { useCallback, useMemo, useState } from "react";
import { toast } from "sonner";

import type { EditOp, RepriceProposal } from "@/api";
import { useBidDraft } from "@/api";
import type { RunViewState } from "@/lib/runReducer";

import { BidHeader } from "./BidHeader";
import type { BidLineField } from "./BidLineRow";
import { FinalizeBar } from "./FinalizeBar";
import { LinesTable } from "./LinesTable";
import { PriceOverrideDialog } from "./PriceOverrideDialog";
import { RepriceDialog } from "./RepriceDialog";
import { ReviewQueue } from "./ReviewQueue";
import { BidSections } from "./Sections";
import { TotalsBar } from "./TotalsBar";
import { useBidKeyboard } from "./useBidKeyboard";

type Props = {
  runId: string;
  state: RunViewState;
  projectName: string | null;
  branchName: string | null;
  pdfUrl: string | null;
  active: boolean;
};

export function BidWorkspace({
  runId,
  state,
  projectName,
  branchName,
  pdfUrl,
  active,
}: Props) {
  const draft = useBidDraft(runId);
  const bid = draft.bid;
  const agentBid = draft.agentBid;

  // Focus + editing state.
  const [focusedLineId, setFocusedLineId] = useState<string | null>(null);
  const [editing, setEditing] = useState<
    { lineId: string; field: BidLineField } | null
  >(null);

  // Reprice dialog state.
  const [reprice, setReprice] = useState<{
    lineId: string;
    proposal: RepriceProposal | null;
    loading: boolean;
  } | null>(null);
  // Price-override dialog is modal: unit-price edits ALWAYS go
  // through it so `override_reason` is required (SPEC §9.4 +
  // Phase 8 review).
  const [priceOverrideLineId, setPriceOverrideLineId] = useState<string | null>(null);
  const [finalizing, setFinalizing] = useState(false);

  // Derive the "effective" focused line: use the explicit
  // focusedLineId when set; otherwise default to the first
  // open-review line (or null when none). No setState-in-effect
  // dance — the derivation runs on every render and stays
  // consistent with `bid`.
  const defaultFocusId = useMemo(() => {
    if (!bid) return null;
    return bid.lines.find((l) => l.review.state === "open")?.line_id ?? null;
  }, [bid]);
  const effectiveFocusedLineId = focusedLineId ?? defaultFocusId;

  const openFlagLines = useMemo(
    () => (bid ? bid.lines.filter((l) => l.review.state === "open") : []),
    [bid],
  );

  // ─── Op dispatchers ─────────────────────────────────────────────

  const queueOp = draft.queueOp;

  const commitEdit = useCallback(
    (lineId: string, field: BidLineField, value: string) => {
      setEditing(null);
      if (!bid) return;
      const line = bid.lines.find((l) => l.line_id === lineId);
      if (!line) return;
      if (field === "quantity") {
        const parsed = Number(value);
        if (!Number.isFinite(parsed) || parsed <= 0) {
          toast.error("Quantity must be a positive number.");
          return;
        }
        if ((line.sale_qty ?? null) === value) return;
        queueOp({ op: "set_qty", line_id: lineId, sale_qty: value });
        return;
      }
      if (field === "unit_price") {
        // Unit-price edits always go through the override dialog
        // so `override_reason` is required (SPEC §9.4 +
        // Phase 8 review). Flip the cell back to display mode
        // and open the modal.
        setPriceOverrideLineId(lineId);
        return;
      }
      if (field === "sku") {
        const trimmed = value.trim();
        if (trimmed === (line.sku ?? "")) return;
        queueOp({ op: "set_sku", line_id: lineId, sku: trimmed });
        return;
      }
      if (field === "description") {
        const trimmed = value.trim();
        if (!trimmed) {
          toast.error("Description can't be empty.");
          return;
        }
        if (trimmed === line.description) return;
        queueOp({ op: "set_description", line_id: lineId, description: trimmed });
        return;
      }
      const _exhaustive: never = field;
      return _exhaustive;
    },
    [bid, queueOp],
  );

  const acceptLine = useCallback(
    (lineId: string) => {
      queueOp({ op: "accept_line", line_id: lineId });
    },
    [queueOp],
  );

  const deleteLine = useCallback(
    (lineId: string) => {
      queueOp({ op: "delete_line", line_id: lineId });
    },
    [queueOp],
  );

  const requestReprice = useCallback(
    async (lineId: string) => {
      setReprice({ lineId, proposal: null, loading: true });
      const proposal = await draft.requestReprice(lineId);
      setReprice({ lineId, proposal, loading: false });
    },
    [draft],
  );

  const applyReprice = useCallback(() => {
    if (!reprice?.proposal) return;
    const op: EditOp = {
      op: "apply_proposal",
      line_id: reprice.proposal.line_id,
      proposal_id: reprice.proposal.proposal_id,
    };
    queueOp(op);
    setReprice(null);
  }, [reprice, queueOp]);

  const applyPriceOverride = useCallback(
    (unitPriceCents: number, reason: string) => {
      if (!priceOverrideLineId) return;
      queueOp({
        op: "set_unit_price",
        line_id: priceOverrideLineId,
        unit_price_cents: unitPriceCents,
        override_reason: reason,
      });
      setPriceOverrideLineId(null);
    },
    [priceOverrideLineId, queueOp],
  );

  const finalize = useCallback(async () => {
    if (!bid) return;
    if (bid.totals.open_flags > 0) {
      toast.error(`${bid.totals.open_flags} lines still need review.`);
      return;
    }
    setFinalizing(true);
    try {
      await draft.finalize();
      toast.success("Bid finalized.");
    } catch (e) {
      const msg = e instanceof Error ? e.message : "Finalize failed";
      toast.error(msg);
    } finally {
      setFinalizing(false);
    }
  }, [bid, draft]);

  // ─── Keyboard ──────────────────────────────────────────────────

  const keyboardApi = useMemo(
    () => ({
      moveFocus: (delta: 1 | -1) => {
        if (!bid) return;
        if (effectiveFocusedLineId === null) {
          setFocusedLineId(bid.lines[0]?.line_id ?? null);
          return;
        }
        const idx = bid.lines.findIndex((l) => l.line_id === effectiveFocusedLineId);
        if (idx === -1) return;
        const nextIdx = (idx + delta + bid.lines.length) % bid.lines.length;
        const next = bid.lines[nextIdx];
        if (next) setFocusedLineId(next.line_id);
      },
      startEditDefault: () => {
        if (effectiveFocusedLineId === null) return;
        setEditing({ lineId: effectiveFocusedLineId, field: "quantity" });
      },
      cancelEdit: () => setEditing(null),
      acceptFocused: () => {
        if (effectiveFocusedLineId === null) return;
        acceptLine(effectiveFocusedLineId);
      },
      jumpFlag: (direction: 1 | -1) => {
        if (openFlagLines.length === 0) return;
        if (effectiveFocusedLineId === null) {
          const first = openFlagLines[0];
          if (first) setFocusedLineId(first.line_id);
          return;
        }
        const idx = openFlagLines.findIndex((l) => l.line_id === effectiveFocusedLineId);
        const start = idx === -1 ? (direction === 1 ? -1 : openFlagLines.length) : idx;
        const nextIdx =
          (start + direction + openFlagLines.length) % openFlagLines.length;
        const next = openFlagLines[nextIdx];
        if (next) setFocusedLineId(next.line_id);
      },
      repriceFocused: () => {
        if (effectiveFocusedLineId === null) return;
        void requestReprice(effectiveFocusedLineId);
      },
      finalize: () => void finalize(),
    }),
    [bid, effectiveFocusedLineId, openFlagLines, acceptLine, requestReprice, finalize],
  );

  useBidKeyboard(active, keyboardApi);

  // ─── Loading / empty ─────────────────────────────────────────────

  if (draft.isPending) {
    return (
      <div className="mx-auto max-w-5xl px-6 py-10 text-sm text-muted-foreground">
        Loading bid…
      </div>
    );
  }
  if (!bid) {
    if (state.bidReady) {
      return (
        <div className="mx-auto max-w-5xl px-6 py-10 text-sm text-muted-foreground">
          Bid not available yet. Try reloading.
        </div>
      );
    }
    return (
      <div className="mx-auto max-w-5xl px-6 py-10 text-sm text-muted-foreground">
        Waiting for the bid to finish…
      </div>
    );
  }

  const branchId =
    branchName?.includes("North Jersey")
      ? "BR-NJ-01"
      : branchName?.includes("Long Island")
        ? "BR-NY-01"
        : branchName?.includes("Lehigh")
          ? "BR-PA-01"
          : null;

  const openFlagsCount = bid.totals.open_flags;

  return (
    <div className="mx-auto max-w-5xl space-y-5 px-6 py-6">
      <BidHeader
        projectName={projectName}
        branchName={branchName}
        branchId={branchId}
        bid={bid}
        agentBid={agentBid}
      />
      <ReviewQueue
        lines={bid.lines}
        focusedLineId={effectiveFocusedLineId}
        onJump={setFocusedLineId}
      />
      <LinesTable
        lines={bid.lines}
        agentBid={agentBid}
        focusedLineId={effectiveFocusedLineId}
        editingField={editing}
        pdfUrl={pdfUrl}
        onFocusLine={setFocusedLineId}
        onStartEdit={(lineId, field) => {
          // Unit price is special — it always goes through the
          // required-reason modal (SPEC §9.4 + review bullet 1).
          if (field === "unit_price") {
            setPriceOverrideLineId(lineId);
            return;
          }
          setEditing({ lineId, field });
        }}
        onCommitEdit={commitEdit}
        onCancelEdit={() => setEditing(null)}
        onAccept={acceptLine}
        onDelete={deleteLine}
        onReprice={(lineId) => void requestReprice(lineId)}
      />
      <TotalsBar totals={bid.totals} />
      <BidSections bid={bid} pdfUrl={pdfUrl} />
      <FinalizeBar
        save={draft.save}
        openFlags={openFlagsCount}
        isFinal={bid.kind === "final"}
        finalizing={finalizing}
        onFinalize={finalize}
        onJumpToFirstFlag={() => {
          const first = openFlagLines[0];
          if (first) setFocusedLineId(first.line_id);
        }}
        onRetrySave={draft.dismissError}
      />
      <RepriceDialog
        proposal={reprice?.proposal ?? null}
        loading={!!reprice?.loading}
        onApply={applyReprice}
        onClose={() => setReprice(null)}
        hasUserOverrides={
          !!(
            reprice?.lineId &&
            bid.lines.find((l) => l.line_id === reprice.lineId)?.origins.unit_price !==
              "agent"
          )
        }
      />
      <PriceOverrideDialog
        line={
          priceOverrideLineId
            ? (bid.lines.find((l) => l.line_id === priceOverrideLineId) ?? null)
            : null
        }
        onApply={applyPriceOverride}
        onClose={() => setPriceOverrideLineId(null)}
      />
    </div>
  );
}
