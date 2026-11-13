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
import { isApiError } from "./ApiError";
import type { Bid, EditOp, RepriceProposal } from "./bid";
import { apiFetch, isFixtureMode } from "./client";

const AUTOSAVE_MS = 800;
const PATCH_LATENCY_MS = 400;

// Retry policy for transport failures (network drop + 5xx). 4xx
// errors other than 409 never retry — the request is malformed and
// will keep 422-looping forever (see DECISIONS F13 for the real
// bug this guards against).
const RETRY_BASE_MS = 400;
const RETRY_MAX_MS = 4_000;
const RETRY_MAX_ATTEMPTS = 3;

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
  // Transport-retry bookkeeping. One attempt counter per hook
  // instance — a 5xx or network drop backs off exponentially and
  // gives up after RETRY_MAX_ATTEMPTS; a successful save resets it.
  const retryAttemptRef = useRef(0);
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

  // Initial fetch. Fixture mode resolves to a scripted envelope;
  // real mode reads `GET /api/runs/{id}` and pulls the `bid` +
  // `agent_bid` keys out. Shape matches `FixtureBidEnvelope`.
  const query = useQuery<FixtureBidEnvelope | null, ApiError>({
    queryKey: ["bid", runId],
    queryFn: async () => {
      if (isFixtureMode()) return withDelay(getFixtureBid(runId), 150);
      const res = await apiFetch(`/api/runs/${runId}`);
      const detail = (await res.json()) as { bid: Bid | null; agent_bid: Bid | null };
      if (!detail.bid || !detail.agent_bid) return null;
      return { bid: detail.bid, agent_bid: detail.agent_bid };
    },
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
      let serverBid: Bid;
      if (isFixtureMode()) {
        serverBid = await withDelay(applier(current.serverDraft, opsToSave), PATCH_LATENCY_MS);
      } else {
        const res = await apiFetch(`/api/runs/${runId}/bid`, {
          method: "PATCH",
          headers: { "content-type": "application/json" },
          body: JSON.stringify({
            draft_revision: current.serverDraft.revision,
            ops: opsToSave,
          }),
        });
        const payload = (await res.json()) as { bid: Bid };
        serverBid = payload.bid;
      }
      dispatch({ type: "saveSucceeded", bid: serverBid, applier });
      retryAttemptRef.current = 0;
      qc.setQueryData<FixtureBidEnvelope>(["bid", runId], (prev) =>
        prev ? { ...prev, bid: serverBid } : { bid: serverBid, agent_bid: serverBid },
      );
    } catch (err) {
      // Stale revision (409): the backend returns the server's
      // current Bid in `details.current` — reseed the reducer so
      // the user can redo their edit against the fresh revision.
      if (isApiError(err) && err.code === "stale_revision") {
        const current = (err.details as { current?: Bid } | undefined)?.current;
        if (current) {
          const prev = qc.getQueryData<FixtureBidEnvelope>(["bid", runId]);
          qc.setQueryData<FixtureBidEnvelope>(["bid", runId], (p) =>
            p ? { ...p, bid: current } : { bid: current, agent_bid: current },
          );
          dispatch({
            type: "fetchSucceeded",
            bid: current,
            agentBid: prev?.agent_bid ?? current,
          });
          retryAttemptRef.current = 0;
          return;
        }
      }
      // 4xx other than 409 → the request is malformed; retrying
      // it won't help. Surface it as `saveInvalid` so the UI can
      // show the per-op error and the autosave debounce stops
      // firing on these ops (they move out of `pendingOps`).
      if (
        isApiError(err) &&
        err.status >= 400 &&
        err.status < 500 &&
        err.code !== "stale_revision"
      ) {
        const perOpErrors = extractPerOpErrors(err, opsToSave.length);
        dispatch({ type: "saveInvalid", perOpErrors });
        retryAttemptRef.current = 0;
        return;
      }
      // Network drop (code === "network_error") or 5xx → honour
      // the retry policy. Reducer's `saveTransportError` puts the
      // ops back in `pendingOps`; here we add a backoff on top of
      // the autosave debounce so we don't hammer the server.
      const attempt = retryAttemptRef.current + 1;
      if (attempt > RETRY_MAX_ATTEMPTS) {
        retryAttemptRef.current = 0;
        const message =
          err instanceof Error ? err.message : "Save failed after multiple retries.";
        dispatch({ type: "saveInvalid", perOpErrors: [{ index: -1, message }] });
        return;
      }
      retryAttemptRef.current = attempt;
      const delay = Math.min(RETRY_BASE_MS * 2 ** (attempt - 1), RETRY_MAX_MS);
      await new Promise((resolve) => setTimeout(resolve, delay));
      const message = err instanceof Error ? err.message : "Save failed";
      dispatch({ type: "saveTransportError", message });
    }
  }, [applier, qc, runId]);

  // Pull per-op errors out of an `invalid_input` envelope (new
  // RequestValidationError handler in app.api.errors — DECISIONS
  // F13). Falls back to a single "all ops rejected" entry when
  // the envelope doesn't carry the breakdown.
  function extractPerOpErrors(
    err: unknown,
    opCount: number,
  ): Array<{ index: number; message: string }> {
    if (!isApiError(err)) return [{ index: -1, message: "Save failed" }];
    const details = err.details as
      | { ops?: Array<{ index: number | null; error: string }> }
      | undefined;
    if (details?.ops && details.ops.length > 0) {
      return details.ops.map((entry) => ({
        index: entry.index ?? -1,
        message: entry.error,
      }));
    }
    const fallback = err.message || "The server rejected the edit.";
    return Array.from({ length: Math.max(opCount, 1) }, (_, i) => ({
      index: i,
      message: fallback,
    }));
  }

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
    if (stateRef.current.pendingOps.length > 0) {
      await runSave();
    }
    const current = stateRef.current;
    if (!current.serverDraft || !current.agentBid) return null;
    let finalized: Bid;
    if (isFixtureMode()) {
      finalized = await withDelay(fixtureFinalize(current.serverDraft), 200);
    } else {
      const res = await apiFetch(`/api/runs/${runId}/bid/finalize`, {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ draft_revision: current.serverDraft.revision }),
      });
      const payload = (await res.json()) as { bid: Bid };
      finalized = payload.bid;
    }
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
