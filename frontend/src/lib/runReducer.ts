/**
 * Pure, idempotent reducer for a run's live view (SPEC §11.3).
 *
 * The reducer takes the current `RunViewState` and one `RunEvent`
 * (SPEC §10.3 discriminated union) and returns the next state.
 *
 * Invariants:
 *   - Idempotent: any event whose `id <= state.lastEventId` is a
 *     noop. "Replay-then-live" + reconnect paths converge.
 *   - Agent-lane restore (`restoreAgentLane`): when a span ends,
 *     the owning agent's `activeSpanId` moves to the nearest
 *     RUNNING ancestor span owned by that same agent. If an
 *     ancestor exists the lane stays "working" — even on a terminal
 *     child failure — because the parent still has work to do;
 *     only `errorKind` is recorded. Lane flips to "failed" only
 *     when NO running same-agent ancestor remains.
 *   - `run.finished` closes any still-running spans and transitions
 *     any still-"working" lane to "done" (unless already "failed").
 *   - Exhaustiveness: the switch's default-arm is a `never` assertion
 *     so adding a new RunEvent type without a case here fails tsc.
 *
 * Transport state (`connection` — connecting / live / reconnecting /
 * closed) is NOT driven from events; `useRunStream` owns it alongside
 * this reducer.
 */

import type {
  AgentName,
  BudgetNotice,
  ConflictView,
  InputKind,
  Outcome,
  PageRoute,
  PricedLineView,
  RunEvent,
  RunStatus,
} from "@/api";

/**
 * Transport state driven by `useRunStream` (not by SSE events).
 *   - connecting   — pre-first-event (fetch /events or opening SSE).
 *   - live         — SSE is open, events may arrive.
 *   - reconnecting — SSE dropped; the hook is retrying.
 *   - closed       — the stream ended (run.finished) or the hook
 *                     gave up (unknown runId / unrecoverable error).
 */
export type ConnectionState = "connecting" | "live" | "reconnecting" | "closed";

export type AgentLaneView = {
  // "interrupted" = run.finished arrived with a terminal status
  // ("partial" | "failed") while the lane was still working. Not
  // "failed" (the lane didn't error itself); not "done" (the work
  // never completed). UI colour is amber / neutral with a text label.
  state: "idle" | "working" | "done" | "failed" | "interrupted";
  activity: string | null;
  activeSpanId: string | null;
  errorKind: string | null;
};

export type SpanView = {
  id: string;
  parentId: string | null;
  agent: AgentName | null;
  name: string;
  label: string | null;
  // "interrupted" = run.finished arrived with a terminal status while
  // this span was still running. durationMs is still filled from the
  // ts delta so the timeline keeps an honest bar.
  state: "running" | "finished" | "failed" | "interrupted";
  startedAtTs: string;
  durationMs: number | null;
  summary: string | null;
  errorKind: string | null;
  errorMessage: string | null;
  willRetry: boolean;
  tool: { name: string; argsPreview: string; ok?: boolean; resultPreview?: string } | null;
  retry: { attempt: number; delay_s: number; reason: string } | null;
  validation: { boundary: string; errors: string[]; will_retry: boolean } | null;
  injected: boolean;
};

export type ItemStreamView = {
  id: string;
  kind: "material" | "labor" | "exclusion" | "note" | "unreadable";
  description: string;
  quantity_text: string | null;
  unit_raw: string | null;
  page: number | null;
  verified: boolean;
  read_via: "text" | "vision" | null;
  notes: string[];
  priced: PricedLineView | null;
  flags: Array<{ reason_code: string; message: string | null }>;
};

export type FlagView = { item_id: string; reason_code: string; message: string | null };

export type RunViewState = {
  lastEventId: number;
  status: RunStatus;
  outcome: Outcome | null;
  outcomeDetail: string | null;
  startedAtTs: string | null;
  inputKind: InputKind | null;
  inputName: string | null;
  pageCount: number | null;
  branchId: string | null;
  currentStep: string | null;
  agents: Record<AgentName, AgentLaneView>;
  spans: Record<string, SpanView>;
  rootSpanIds: string[];
  pagesByNo: Record<number, { route: PageRoute }>;
  items: Record<string, ItemStreamView>;
  itemOrder: string[];
  flags: FlagView[];
  conflicts: ConflictView[];
  budget: BudgetNotice | null;
  bidReady: {
    revision: number;
    generated_by: "orchestrator" | "fallback";
    open_flags: number;
  } | null;
};

export const initialRunViewState: RunViewState = {
  lastEventId: 0,
  status: "running",
  outcome: null,
  outcomeDetail: null,
  startedAtTs: null,
  inputKind: null,
  inputName: null,
  pageCount: null,
  branchId: null,
  currentStep: null,
  agents: {
    orchestrator: { state: "idle", activity: null, activeSpanId: null, errorKind: null },
    document_analyst: { state: "idle", activity: null, activeSpanId: null, errorKind: null },
    pricing_agent: { state: "idle", activity: null, activeSpanId: null, errorKind: null },
  },
  spans: {},
  rootSpanIds: [],
  pagesByNo: {},
  items: {},
  itemOrder: [],
  flags: [],
  conflicts: [],
  budget: null,
  bidReady: null,
};

// ─── Helpers ───────────────────────────────────────────────────────

function updateAgent(
  state: RunViewState,
  name: AgentName,
  patch: Partial<AgentLaneView>,
): RunViewState {
  const current = state.agents[name];
  return {
    ...state,
    agents: { ...state.agents, [name]: { ...current, ...patch } },
  };
}

function putSpan(state: RunViewState, span: SpanView): RunViewState {
  const existed = state.spans[span.id] !== undefined;
  const nextRoots =
    span.parentId === null && !existed ? [...state.rootSpanIds, span.id] : state.rootSpanIds;
  return {
    ...state,
    spans: { ...state.spans, [span.id]: span },
    rootSpanIds: nextRoots,
  };
}

function patchSpan(state: RunViewState, id: string, patch: Partial<SpanView>): RunViewState {
  const current = state.spans[id];
  if (!current) return state;
  return {
    ...state,
    spans: { ...state.spans, [id]: { ...current, ...patch } },
  };
}

/**
 * Walk up `span.parentId` looking for the nearest ancestor that is
 * still "running" and belongs to `agent`. Returns its id, or null.
 */
function findRunningAncestor(
  spans: Record<string, SpanView>,
  startId: string,
  agent: AgentName,
): string | null {
  const start = spans[startId];
  if (!start) return null;
  let cursor = start.parentId;
  const seen = new Set<string>();
  while (cursor !== null) {
    if (seen.has(cursor)) return null; // cycle guard
    seen.add(cursor);
    const node = spans[cursor];
    if (!node) return null;
    if (node.state === "running" && node.agent === agent) return node.id;
    cursor = node.parentId;
  }
  return null;
}

/**
 * Called on span.finished and terminal span.failed.
 *
 * Decision table:
 *
 *   ancestor? | terminalFailure | → lane.state  | activeSpanId  | errorKind
 *   ----------+-----------------+---------------+---------------+----------------
 *   yes       | false           | working       | ancestor      | unchanged
 *   yes       | true            | working       | ancestor      | errorKind
 *   no        | false           | idle          | null          | unchanged
 *   no        | true            | failed        | null          | errorKind
 *
 * So a terminal child failure with a running same-agent ancestor
 * keeps the lane "working" (the parent still has work to do) and
 * just records the last errorKind for display.
 */
function restoreAgentLane(
  state: RunViewState,
  agent: AgentName,
  endedSpanId: string,
  terminalFailure: boolean,
  errorKind: string | null,
): RunViewState {
  const current = state.agents[agent];

  // If the ending span isn't the lane's active span, don't move
  // the lane. Still record errorKind if this was a terminal failure
  // so the badge reflects the last thing that went wrong.
  if (current.activeSpanId !== endedSpanId) {
    if (terminalFailure && errorKind !== null) {
      return updateAgent(state, agent, { errorKind });
    }
    return state;
  }

  const ancestor = findRunningAncestor(state.spans, endedSpanId, agent);

  if (ancestor !== null) {
    // Running parent exists — the lane keeps going on it.
    return updateAgent(state, agent, {
      state: "working",
      activeSpanId: ancestor,
      errorKind: terminalFailure ? errorKind : current.errorKind,
    });
  }

  // No running ancestor: the lane is done. Clean finish → idle;
  // terminal failure → failed.
  return updateAgent(state, agent, {
    state: terminalFailure ? "failed" : "idle",
    activeSpanId: null,
    errorKind: terminalFailure ? errorKind : current.errorKind,
  });
}

// ─── Reducer ───────────────────────────────────────────────────────

export function runReducer(state: RunViewState, event: RunEvent): RunViewState {
  if (event.id <= state.lastEventId) return state;
  const base: RunViewState = { ...state, lastEventId: event.id };

  switch (event.type) {
    case "run.started":
      return {
        ...base,
        startedAtTs: event.ts,
        inputKind: event.payload.input_kind,
        inputName: event.payload.input_name,
        pageCount: event.payload.page_count,
        branchId: event.payload.branch_id,
      };

    case "run.status":
      return { ...base, status: event.payload.status, currentStep: event.payload.current_step };

    case "span.started": {
      const spanId = event.span_id ?? `span_${event.id}`;
      const span: SpanView = {
        id: spanId,
        parentId: event.parent_span_id,
        agent: event.agent,
        name: event.payload.name,
        label: event.payload.label,
        state: "running",
        startedAtTs: event.ts,
        durationMs: null,
        summary: null,
        errorKind: null,
        errorMessage: null,
        willRetry: false,
        tool: null,
        retry: null,
        validation: null,
        injected: false,
      };
      const next = putSpan(base, span);
      if (event.agent) {
        return updateAgent(next, event.agent, {
          state: "working",
          activeSpanId: spanId,
        });
      }
      return next;
    }

    case "span.finished": {
      if (!event.span_id) return base;
      const patched = patchSpan(base, event.span_id, {
        state: "finished",
        durationMs: event.payload.duration_ms,
        summary: event.payload.summary,
      });
      if (!event.agent) return patched;
      return restoreAgentLane(patched, event.agent, event.span_id, false, null);
    }

    case "span.failed": {
      if (!event.span_id) return base;
      const patched = patchSpan(base, event.span_id, {
        state: "failed",
        durationMs: event.payload.duration_ms,
        errorKind: event.payload.error_kind,
        errorMessage: event.payload.message,
        willRetry: event.payload.will_retry,
        injected: event.payload.error_kind === "injected",
      });
      if (!event.agent) return patched;
      // Retryable failure: a retry.scheduled + new span.started will
      // arrive. Lane stays working; errorKind stays on the span
      // (don't bubble to the lane).
      if (event.payload.will_retry) return patched;
      return restoreAgentLane(
        patched,
        event.agent,
        event.span_id,
        true,
        event.payload.error_kind,
      );
    }

    case "agent.activity":
      if (!event.agent) return base;
      return updateAgent(base, event.agent, { activity: event.payload.text });

    case "tool.called":
      if (!event.span_id) return base;
      return patchSpan(base, event.span_id, {
        tool: { name: event.payload.tool, argsPreview: event.payload.args_preview },
      });

    case "tool.result": {
      if (!event.span_id) return base;
      const span = base.spans[event.span_id];
      if (!span) return base;
      return patchSpan(base, event.span_id, {
        tool: {
          name: span.tool?.name ?? event.payload.tool,
          argsPreview: span.tool?.argsPreview ?? "",
          ok: event.payload.ok,
          resultPreview: event.payload.result_preview,
        },
      });
    }

    case "retry.scheduled":
      if (!event.span_id) return base;
      return patchSpan(base, event.span_id, {
        retry: {
          attempt: event.payload.attempt,
          delay_s: event.payload.delay_s,
          reason: event.payload.reason,
        },
      });

    case "validation.failed":
      if (!event.span_id) return base;
      return patchSpan(base, event.span_id, {
        validation: {
          boundary: event.payload.boundary,
          errors: event.payload.errors,
          will_retry: event.payload.will_retry,
        },
      });

    case "page.routed":
      return {
        ...base,
        pagesByNo: {
          ...base.pagesByNo,
          [event.payload.page_no]: { route: event.payload.route },
        },
      };

    case "item.extracted": {
      const v = event.payload.item;
      if (base.items[v.id]) return base; // dedup by id
      const item: ItemStreamView = {
        id: v.id,
        kind: v.kind,
        description: v.description,
        quantity_text: v.quantity_text,
        unit_raw: v.unit_raw,
        page: v.source.page,
        verified: v.source.verified,
        read_via: v.source.read_via,
        notes: v.notes,
        priced: null,
        flags: [],
      };
      return {
        ...base,
        items: { ...base.items, [v.id]: item },
        itemOrder: [...base.itemOrder, v.id],
      };
    }

    case "item.flagged": {
      const flag: FlagView = {
        item_id: event.payload.item_id,
        reason_code: event.payload.reason_code,
        message: event.payload.message,
      };
      const current = base.items[event.payload.item_id];
      const next: RunViewState = { ...base, flags: [...base.flags, flag] };
      if (current) {
        next.items = {
          ...base.items,
          [event.payload.item_id]: {
            ...current,
            flags: [...current.flags, { reason_code: flag.reason_code, message: flag.message }],
          },
        };
      }
      return next;
    }

    case "item.priced": {
      const line = event.payload.line;
      const current = base.items[line.item_id];
      if (!current) return base; // can't price what we haven't seen
      return {
        ...base,
        items: { ...base.items, [line.item_id]: { ...current, priced: line } },
      };
    }

    case "conflict.flagged":
      return { ...base, conflicts: [...base.conflicts, event.payload.conflict] };

    case "budget.exhausted":
      return {
        ...base,
        budget: { budget: event.payload.budget, detail: event.payload.detail },
      };

    case "bid.ready":
      return {
        ...base,
        bidReady: {
          revision: event.payload.revision,
          generated_by: event.payload.generated_by,
          open_flags: event.payload.open_flags,
        },
      };

    case "run.finished": {
      // "partial" / "failed" → running work that never got to finish
      // is "interrupted", not "finished"/"done". "completed" keeps
      // the clean terminal state. Lanes that had already "failed"
      // keep their failed state + errorKind.
      const terminal =
        event.payload.status === "partial" || event.payload.status === "failed";
      const spanTerminalState: SpanView["state"] = terminal ? "interrupted" : "finished";
      const laneWorkingTerminalState: AgentLaneView["state"] = terminal ? "interrupted" : "done";

      // Close any still-running spans with best-effort duration.
      const finishedAt = new Date(event.ts).getTime();
      const nextSpans: Record<string, SpanView> = { ...base.spans };
      for (const [id, span] of Object.entries(base.spans)) {
        if (span.state === "running") {
          const started = new Date(span.startedAtTs).getTime();
          const dur = Number.isFinite(started) ? Math.max(0, finishedAt - started) : null;
          nextSpans[id] = {
            ...span,
            state: spanTerminalState,
            durationMs: dur,
            summary: span.summary ?? "closed by run.finished",
          };
        }
      }

      // Which agents ran ≥1 span this run? Walk spans once.
      const ranASpan: Record<AgentName, boolean> = {
        orchestrator: false,
        document_analyst: false,
        pricing_agent: false,
      };
      for (const span of Object.values(base.spans)) {
        if (span.agent) ranASpan[span.agent] = true;
      }

      // Lane transitions on run.finished:
      //   working → done (completed) / interrupted (partial|failed).
      //   idle + ran ≥1 span → done (both outcomes). They finished
      //     their part cleanly; keep errorKind if any.
      //   idle + never ran a span → still idle.
      //   failed → failed (unchanged, keep errorKind).
      //   done / interrupted (already terminal) → unchanged.
      const agents: Record<AgentName, AgentLaneView> = { ...base.agents };
      for (const name of Object.keys(agents) as AgentName[]) {
        const lane = agents[name];
        if (lane.state === "working") {
          agents[name] = { ...lane, state: laneWorkingTerminalState, activeSpanId: null };
        } else if (lane.state === "idle" && ranASpan[name]) {
          agents[name] = { ...lane, state: "done" };
        }
      }
      return {
        ...base,
        spans: nextSpans,
        agents,
        status: event.payload.status,
        outcome: event.payload.outcome,
        outcomeDetail: event.payload.outcome_detail,
      };
    }

    default: {
      // Exhaustiveness guard: TypeScript narrows `event` to `never`
      // here if the switch covers every RunEvent member. Adding a
      // new union member without a case triggers a compile error.
      // At runtime, the unknown event still advances `lastEventId`.
      const _exhaustive: never = event;
      void _exhaustive;
      return base;
    }
  }
}
