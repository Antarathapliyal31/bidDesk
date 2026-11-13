// @vitest-environment node
/**
 * Pure bidDraftReducer behavior (SPEC §11.3). The reducer is the
 * state engine for the Bid tab; this suite pins every transition
 * independent of autosave timing or React.
 *
 * `BidApplier` is faked here — tests don't exercise the fixture's
 * op semantics, just the reducer's bookkeeping.
 */
import { describe, expect, it } from "vitest";

import type { Bid, EditOp } from "@/api";
import {
  bidDraftReducer,
  initialBidDraftState,
  isDirty,
  saveState,
  type BidApplier,
  type BidDraftState,
} from "@/lib/bidDraftReducer";

function makeBid(revision: number): Bid {
  return {
    revision,
    kind: "draft",
    generated_by: "orchestrator",
    partial_reasons: [],
    lines: [],
    exclusions: [],
    labor: [],
    unreadable: [],
    conflicts: [],
    assumptions: [],
    contractor_questions: [],
    summary: "",
    totals: {
      subtotal_cents: 0,
      tax_rate_ppm: 66_250,
      tax_cents: 0,
      total_cents: 0,
      priced_lines: 0,
      open_flags: 0,
    },
  };
}

function makeAgent(): Bid {
  return { ...makeBid(0), kind: "agent" };
}

// Simple applier: bumps `revision` on the bid so we can tell
// applied-vs-not and assert the reducer doesn't auto-bump.
const bumpRevisionApplier: BidApplier = (bid, ops) => ({
  ...bid,
  revision: bid.revision + ops.length,
});

const noopApplier: BidApplier = (bid) => bid;

const QTY_OP: EditOp = { op: "set_qty", line_id: "line_01", sale_qty: "40" };
const ACCEPT_OP: EditOp = { op: "accept_line", line_id: "line_01" };

describe("bidDraftReducer", () => {
  it("fetchSucceeded seeds server/local drafts AND snapshots agentBid", () => {
    const bid = makeBid(1);
    const agentBid = makeAgent();
    const next = bidDraftReducer(initialBidDraftState, {
      type: "fetchSucceeded",
      bid,
      agentBid,
    });
    expect(next.serverDraft).toBe(bid);
    expect(next.localDraft).toBe(bid);
    expect(next.agentBid).toBe(agentBid);
    expect(next.pendingOps).toHaveLength(0);
    expect(next.inFlightOps).toHaveLength(0);
    expect(next.saving).toBe(false);
    expect(isDirty(next)).toBe(false);
    expect(saveState(next)).toBe("saved");
  });

  it("opQueued runs the applier on localDraft and appends the op to pendingOps", () => {
    const bid = makeBid(1);
    let state = bidDraftReducer(initialBidDraftState, {
      type: "fetchSucceeded",
      bid,
      agentBid: makeAgent(),
    });
    state = bidDraftReducer(state, {
      type: "opQueued",
      op: QTY_OP,
      applier: bumpRevisionApplier,
    });
    expect(state.localDraft?.revision).toBe(2);
    expect(state.serverDraft?.revision).toBe(1);
    expect(state.pendingOps).toEqual([QTY_OP]);
    expect(isDirty(state)).toBe(true);
    expect(saveState(state)).toBe("unsaved");
  });

  it("opQueued is a noop when localDraft is null (nothing loaded yet)", () => {
    const state = bidDraftReducer(initialBidDraftState, {
      type: "opQueued",
      op: QTY_OP,
      applier: bumpRevisionApplier,
    });
    expect(state).toEqual(initialBidDraftState);
  });

  it("saveStarted moves pendingOps → inFlightOps and sets saving=true", () => {
    let state = bidDraftReducer(initialBidDraftState, {
      type: "fetchSucceeded",
      bid: makeBid(1),
      agentBid: makeAgent(),
    });
    state = bidDraftReducer(state, {
      type: "opQueued",
      op: QTY_OP,
      applier: noopApplier,
    });
    state = bidDraftReducer(state, { type: "saveStarted" });
    expect(state.pendingOps).toHaveLength(0);
    expect(state.inFlightOps).toEqual([QTY_OP]);
    expect(state.saving).toBe(true);
    expect(saveState(state)).toBe("saving");
  });

  it("saveStarted is a noop when there's nothing pending", () => {
    const seed = bidDraftReducer(initialBidDraftState, {
      type: "fetchSucceeded",
      bid: makeBid(1),
      agentBid: makeAgent(),
    });
    const after = bidDraftReducer(seed, { type: "saveStarted" });
    expect(after).toBe(seed);
  });

  it("saveSucceeded adopts the server bid; pending ops queued mid-save replay on top", () => {
    let state = bidDraftReducer(initialBidDraftState, {
      type: "fetchSucceeded",
      bid: makeBid(1),
      agentBid: makeAgent(),
    });
    state = bidDraftReducer(state, {
      type: "opQueued",
      op: QTY_OP,
      applier: noopApplier,
    });
    state = bidDraftReducer(state, { type: "saveStarted" });
    // While the save was in flight, user queued ANOTHER op.
    state = bidDraftReducer(state, {
      type: "opQueued",
      op: ACCEPT_OP,
      applier: bumpRevisionApplier,
    });
    const serverBid = makeBid(2);
    state = bidDraftReducer(state, {
      type: "saveSucceeded",
      bid: serverBid,
      applier: bumpRevisionApplier,
    });
    expect(state.serverDraft).toBe(serverBid);
    // Local = server + the one op queued during the save.
    expect(state.localDraft?.revision).toBe(3);
    expect(state.inFlightOps).toHaveLength(0);
    expect(state.pendingOps).toEqual([ACCEPT_OP]);
    expect(state.saving).toBe(false);
    // agentBid stays immutable across save cycles.
    expect(state.agentBid?.kind).toBe("agent");
  });

  it("saveStale parks local state; UI surfaces the conflict", () => {
    let state: BidDraftState = bidDraftReducer(initialBidDraftState, {
      type: "fetchSucceeded",
      bid: makeBid(1),
      agentBid: makeAgent(),
    });
    state = bidDraftReducer(state, {
      type: "opQueued",
      op: QTY_OP,
      applier: noopApplier,
    });
    state = bidDraftReducer(state, { type: "saveStarted" });
    const serverBid = makeBid(5);
    state = bidDraftReducer(state, { type: "saveStale", serverDraft: serverBid });
    expect(state.saving).toBe(false);
    expect(state.error).toEqual({ kind: "stale", serverDraft: serverBid });
    expect(state.localDraft?.revision).toBe(1);
  });

  it("saveInvalid stores per-op errors", () => {
    let state = bidDraftReducer(initialBidDraftState, {
      type: "fetchSucceeded",
      bid: makeBid(1),
      agentBid: makeAgent(),
    });
    state = bidDraftReducer(state, {
      type: "opQueued",
      op: QTY_OP,
      applier: noopApplier,
    });
    state = bidDraftReducer(state, { type: "saveStarted" });
    const perOpErrors = [{ index: 0, message: "quantity must be positive" }];
    state = bidDraftReducer(state, { type: "saveInvalid", perOpErrors });
    expect(state.saving).toBe(false);
    expect(state.error).toEqual({ kind: "invalid", perOpErrors });
  });

  it("saveTransportError pulls inFlightOps BACK into pendingOps (retry on next tick)", () => {
    let state = bidDraftReducer(initialBidDraftState, {
      type: "fetchSucceeded",
      bid: makeBid(1),
      agentBid: makeAgent(),
    });
    state = bidDraftReducer(state, {
      type: "opQueued",
      op: QTY_OP,
      applier: noopApplier,
    });
    state = bidDraftReducer(state, { type: "saveStarted" });
    state = bidDraftReducer(state, {
      type: "saveTransportError",
      message: "net down",
    });
    expect(state.saving).toBe(false);
    expect(state.inFlightOps).toHaveLength(0);
    expect(state.pendingOps).toEqual([QTY_OP]);
    expect(state.error).toEqual({ kind: "transport", message: "net down" });
    expect(saveState(state)).toBe("error");
  });

  it("errorDismissed clears the banner without touching state", () => {
    const state: BidDraftState = {
      ...initialBidDraftState,
      error: { kind: "transport", message: "oops" },
    };
    const next = bidDraftReducer(state, { type: "errorDismissed" });
    expect(next.error).toBeNull();
  });

  it("opQueued clears a prior stale/invalid error (user is making a new edit)", () => {
    let state: BidDraftState = bidDraftReducer(initialBidDraftState, {
      type: "fetchSucceeded",
      bid: makeBid(1),
      agentBid: makeAgent(),
    });
    state = { ...state, error: { kind: "invalid", perOpErrors: [] } };
    state = bidDraftReducer(state, {
      type: "opQueued",
      op: QTY_OP,
      applier: noopApplier,
    });
    expect(state.error).toBeNull();
  });
});
