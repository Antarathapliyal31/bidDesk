/**
 * Pure, testable reducer for the bid-editing flow (SPEC §11.3).
 *
 * The reducer owns three slots:
 *   - `serverDraft`   — last authoritative Bid from the server.
 *                       Replaced wholesale on fetch + after every
 *                       successful save (server totals win).
 *   - `localDraft`    — serverDraft + pendingOps applied
 *                       optimistically, so the UI stays snappy
 *                       while a save is in flight.
 *   - `pendingOps`    — ops queued since the last successful save.
 *                       The autosave debounce coalesces across
 *                       edits; a `saveStarted` snapshot promotes
 *                       them to `inFlightOps`.
 *
 * Invariants:
 *   - Dirty = pendingOps.length > 0 || inFlightOps.length > 0.
 *   - On save success, inFlightOps are cleared and localDraft is
 *     replaced by the server's new draft (plus any ops queued
 *     while the save was in flight, re-applied).
 *   - On 409 (stale_revision) we park the local state in an
 *     `error = "stale"` slot — the UI prompts the user to reload.
 *   - On 422 (invalid_input) we park with per-op errors but keep
 *     local state so the user can fix the invalid value.
 *   - Pure: every action returns a new state object; the applier
 *     passed in is pure too (see `BidApplier` below).
 */
import type { Bid, EditOp } from "@/api";

/** Applies ops to a bid and returns the new bid. Supplied by
 *  the fixture (today) or a real server simulation. */
export type BidApplier = (bid: Bid, ops: EditOp[]) => Bid;

export type BidDraftError =
  | { kind: "stale"; serverDraft: Bid }
  | { kind: "invalid"; perOpErrors: Array<{ index: number; message: string }> }
  | { kind: "transport"; message: string };

export type BidDraftState = {
  serverDraft: Bid | null;
  localDraft: Bid | null;
  /**
   * Immutable agent revision 0 (SPEC §9.1). Seeded once on
   * `fetchSucceeded`; never mutated by the reducer. BidHeader +
   * BidLineRow diff against this (SPEC §9.7).
   */
  agentBid: Bid | null;
  pendingOps: EditOp[];
  inFlightOps: EditOp[];
  saving: boolean;
  error: BidDraftError | null;
};

export const initialBidDraftState: BidDraftState = {
  serverDraft: null,
  localDraft: null,
  agentBid: null,
  pendingOps: [],
  inFlightOps: [],
  saving: false,
  error: null,
};

export type BidDraftAction =
  | { type: "fetchSucceeded"; bid: Bid; agentBid: Bid }
  | { type: "opQueued"; op: EditOp; applier: BidApplier }
  | { type: "saveStarted" }
  | { type: "saveSucceeded"; bid: Bid; applier: BidApplier }
  | { type: "saveStale"; serverDraft: Bid }
  | { type: "saveInvalid"; perOpErrors: Array<{ index: number; message: string }> }
  | { type: "saveTransportError"; message: string }
  | { type: "errorDismissed" };

export function bidDraftReducer(state: BidDraftState, action: BidDraftAction): BidDraftState {
  switch (action.type) {
    case "fetchSucceeded": {
      // Fresh load — adopt the server bid as both server and
      // local; snapshot agentBid once (never touched again by
      // this reducer). A new fetch during an in-flight save is
      // unusual and we don't try to be clever about it.
      return {
        ...initialBidDraftState,
        serverDraft: action.bid,
        localDraft: action.bid,
        agentBid: action.agentBid,
      };
    }
    case "opQueued": {
      if (state.localDraft === null) return state;
      const nextLocal = action.applier(state.localDraft, [action.op]);
      return {
        ...state,
        localDraft: nextLocal,
        pendingOps: [...state.pendingOps, action.op],
        // Any new op clears a prior "stale" / "invalid" banner
        // because the user is now making a different edit.
        error: null,
      };
    }
    case "saveStarted": {
      if (state.pendingOps.length === 0) return state;
      return {
        ...state,
        inFlightOps: state.pendingOps,
        pendingOps: [],
        saving: true,
      };
    }
    case "saveSucceeded": {
      // Server totals win: adopt the returned bid as new server
      // draft. If more ops queued WHILE we were saving, replay
      // them locally on top of the server's new state.
      const nextLocal =
        state.pendingOps.length > 0
          ? action.applier(action.bid, state.pendingOps)
          : action.bid;
      return {
        ...state,
        serverDraft: action.bid,
        localDraft: nextLocal,
        inFlightOps: [],
        saving: false,
        error: null,
      };
    }
    case "saveStale": {
      // The server's current revision is newer than ours. Keep
      // local state so the user can see what they'd lose; the UI
      // prompts them to reload.
      return {
        ...state,
        saving: false,
        error: { kind: "stale", serverDraft: action.serverDraft },
      };
    }
    case "saveInvalid": {
      // 4xx validation failures are terminal for the in-flight
      // batch — dropping `inFlightOps` is what stops the autosave
      // debounce from re-hammering the backend with a body that
      // will keep 422ing (DECISIONS F13). The user sees the error
      // via `error.kind === "invalid"` and can edit again to
      // retry under new ops.
      return {
        ...state,
        inFlightOps: [],
        saving: false,
        error: { kind: "invalid", perOpErrors: action.perOpErrors },
      };
    }
    case "saveTransportError": {
      // The ops that failed to save stay in `pendingOps` so the
      // next debounce tick retries them. UI shows "Couldn't save —
      // Retry" alongside the Save state.
      return {
        ...state,
        pendingOps: [...state.inFlightOps, ...state.pendingOps],
        inFlightOps: [],
        saving: false,
        error: { kind: "transport", message: action.message },
      };
    }
    case "errorDismissed":
      return { ...state, error: null };
    default: {
      const _exhaustive: never = action;
      return _exhaustive;
    }
  }
}

export function isDirty(state: BidDraftState): boolean {
  return state.pendingOps.length > 0 || state.inFlightOps.length > 0;
}

export function saveState(state: BidDraftState): "saved" | "saving" | "unsaved" | "error" {
  if (state.error?.kind === "transport") return "error";
  if (state.saving) return "saving";
  if (state.pendingOps.length > 0) return "unsaved";
  return "saved";
}
