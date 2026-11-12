/**
 * Bid editing hook (SPEC §11.3). Mounts the pure `bidDraftReducer`
 * with:
 *   - TanStack Query for the initial fetch and server-cache
 *     consistency (invalidates `['bid', runId]` on save success).
 *   - Debounced autosave (800 ms) after every queued op.
 *   - `beforeunload` guard while dirty.
 *
 * Phase 5 merge:
 *   - Replace `getFixtureBid` with
 *     `fetch(`/api/runs/${runId}`).then(r => r.ok ? r.json() : throw)`
 *     (reads the `bid` key out of the detail response).
 *   - Replace `applyOps` + fixture reprice with `fetch` PATCH /
 *     POST calls.
 * The reducer contract, the autosave timing, and every call site
 * stay the same.
 */
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useCallback, useEffect, useReducer, useRef } from "react";

import {
  applyOps as fixtureApplyOps,
  fakeReprice as fixtureReprice,
  finalize as fixtureFinalize,
  getFixtureBid,
  type FixtureBidEnvelope,
} from "@/dev-fixtures/bid";
import { withDelay } from "@/dev-fixtures/delay";
import {
  bidDraftReducer,
  initialBidDraftState,
  isDirty,
  saveState,
  type BidApplier,
  type BidDraftState,
} from "@/lib/bidDraftReducer";

import type { ApiError } from "./ApiError";
import type { Bid, EditOp, RepriceProposal } from "./bid";

const AUTOSAVE_MS = 800;
const PATCH_LATENCY_MS = 400;

/**
 * Module-level reprice store so the fixture PATCH can resolve
 * ApplyReprice ops against the proposal that `requestReprice`
 * minted earlier. Phase 5 merge: the backend's finalize/apply
 * endpoints own this state.
 */
const REPRICE_STORE: Map<string, RepriceProposal> = new Map();

export type UseBidDraftResult = {
  bid: Bid | null;
  /**
   * Immutable agent revision 0. UI diffs each draft line against
   * the matching `line_id` here (SPEC §9.7). Null before the
   * fetch resolves.
   */
  agentBid: Bid | null;
  serverRevision: number | null;
  state: BidDraftState;
  dirty: boolean;
  save: "saved" | "saving" | "unsaved" | "error";
  isPending: boolean;
  queueOp: (op: EditOp) => void;
  requestReprice: (lineId: string) => Promise<RepriceProposal | null>;
  finalize: () => Promise<Bid | null>;
  dismissError: () => void;
};

export function useBidDraft(runId: string): UseBidDraftResult {
  const qc = useQueryClient();
  const [state, dispatch] = useReducer(bidDraftReducer, initialBidDraftState);
  const stateRef = useRef<BidDraftState>(state);
  // Mirror state into the ref in an effect (not during render) so
  // scheduled callbacks (autosave, finalize) see the latest ops.
  useEffect(() => {
    stateRef.current = state;
  }, [state]);

  // The applier is the "backend" for this branch — pure, so we
  // can wrap it once and pass it into the reducer. The reducer
  // never knows it's a fixture.
  const applier: BidApplier = useCallback(
    (bid, ops) => fixtureApplyOps(bid, ops, REPRICE_STORE),
    [],
  );

  // Initial fetch (TanStack Query). The fixture returns an
  // envelope that matches SPEC §13 `GET /api/runs/:id` — the
  // current bid plus the agent revision 0 for diffing. Phase 5
  // merge: swap `getFixtureBid` for `fetch`, same envelope.
  const query = useQuery<FixtureBidEnvelope | null, ApiError>({
    queryKey: ["bid", runId],
    queryFn: () => withDelay(getFixtureBid(runId), 150),
    staleTime: Infinity,
    retry: false,
  });

  // Seed the reducer once the query resolves with a fresh bid.
  // Read `state.localDraft` through the ref so this effect's deps
  // stay honest about what triggers a reseed (the query data).
  useEffect(() => {
    const envelope = query.data;
    if (!envelope) return;
    const current = stateRef.current;
    if (current.localDraft && current.localDraft.revision >= envelope.bid.revision) return;
    dispatch({
      type: "fetchSucceeded",
      bid: envelope.bid,
      agentBid: envelope.agent_bid,
    });
  }, [query.data]);

  // Pure async save. Reads from stateRef so scheduled runs see
  // the latest ops without re-closing over state. Declared BEFORE
  // the debounce effect so React Compiler doesn't trip over
  // forward-referencing.
  const runSave = useCallback(async () => {
    const current = stateRef.current;
    if (current.serverDraft === null) return;
    if (current.pendingOps.length === 0) return;
    const opsToSave = current.pendingOps;
    dispatch({ type: "saveStarted" });
    try {
      // Simulate a PATCH: revision guard + apply + delay.
      const serverBid = await withDelay(
        applier(current.serverDraft, opsToSave),
        PATCH_LATENCY_MS,
      );
      // Stale revision guard would go here in prod. Fixture
      // mode has no concurrent writers.
      dispatch({ type: "saveSucceeded", bid: serverBid, applier });
      // Update the cache with the new envelope. agent_bid is
      // immutable so we reuse the previously-cached value.
      qc.setQueryData<FixtureBidEnvelope>(["bid", runId], (prev) =>
        prev ? { ...prev, bid: serverBid } : { bid: serverBid, agent_bid: serverBid },
      );
    } catch (err) {
      const message = err instanceof Error ? err.message : "Save failed";
      dispatch({ type: "saveTransportError", message });
    }
  }, [applier, qc, runId]);

  // Autosave debounce. Any pendingOps → schedule a save.
  const saveTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  useEffect(() => {
    if (state.pendingOps.length === 0) return;
    if (state.saving) return;
    if (saveTimerRef.current) clearTimeout(saveTimerRef.current);
    saveTimerRef.current = setTimeout(() => {
      void runSave();
    }, AUTOSAVE_MS);
    return () => {
      if (saveTimerRef.current) clearTimeout(saveTimerRef.current);
    };
  }, [state.pendingOps.length, state.saving, runSave]);

  const queueOp = useCallback(
    (op: EditOp) => {
      dispatch({ type: "opQueued", op, applier });
    },
    [applier],
  );

  const requestReprice = useCallback(
    async (lineId: string): Promise<RepriceProposal | null> => {
      const bid = stateRef.current.localDraft;
      if (!bid) return null;
      const proposal = await withDelay(fixtureReprice(bid, lineId), 300);
      if (proposal) REPRICE_STORE.set(proposal.proposal_id, proposal);
      return proposal;
    },
    [],
  );

  const finalize = useCallback(async (): Promise<Bid | null> => {
    // Flush any pending save first so we finalize the server
    // state, not a stale one.
    if (stateRef.current.pendingOps.length > 0) {
      await runSave();
    }
    const current = stateRef.current;
    if (!current.serverDraft || !current.agentBid) return null;
    const finalized = await withDelay(fixtureFinalize(current.serverDraft), 200);
    dispatch({ type: "fetchSucceeded", bid: finalized, agentBid: current.agentBid });
    qc.setQueryData<FixtureBidEnvelope>(["bid", runId], (prev) =>
      prev ? { ...prev, bid: finalized } : { bid: finalized, agent_bid: finalized },
    );
    return finalized;
  }, [qc, runId, runSave]);

  const dismissError = useCallback(() => {
    dispatch({ type: "errorDismissed" });
  }, []);

  // beforeunload warning when dirty.
  const dirty = isDirty(state);
  useEffect(() => {
    if (!dirty) return;
    function onBeforeUnload(e: BeforeUnloadEvent) {
      e.preventDefault();
      e.returnValue = "";
    }
    window.addEventListener("beforeunload", onBeforeUnload);
    return () => window.removeEventListener("beforeunload", onBeforeUnload);
  }, [dirty]);

  return {
    bid: state.localDraft,
    agentBid: state.agentBid,
    serverRevision: state.serverDraft?.revision ?? null,
    state,
    dirty,
    save: saveState(state),
    isPending: query.isPending,
    queueOp,
    requestReprice,
    finalize,
    dismissError,
  };
}
