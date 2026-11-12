import { ChevronRightIcon, type LucideIcon } from "lucide-react";
import { useState } from "react";

import type { AgentName } from "@/api";
import type { SpanView } from "@/lib/runReducer";
import type { SpanTree } from "@/lib/trace";
import { cn } from "@/lib/utils";

import { InjectedBadge } from "./trace-details/InjectedBadge";
import { RetryNote } from "./trace-details/RetryNote";
import { ToolCallDetail } from "./trace-details/ToolCallDetail";
import { ValidationErrorDetail } from "./trace-details/ValidationErrorDetail";

/**
 * One row of the trace timeline (SPEC §11.2). Renders:
 *   - header: expand toggle, agent chip, span name + label,
 *     duration, injected badge, state dot.
 *   - details (when expanded): ToolCallDetail · RetryNote ·
 *     ValidationErrorDetail · child SpanRows.
 *
 * Disclosure pattern: the header is a <button aria-expanded>; the
 * details live in a <div role="region"> below. This keeps keyboard
 * + screen-reader handling simple (Space/Enter toggles; aria-controls
 * links the two) without reaching for a full <tree> role.
 *
 * Nested rows are indented via a left-side rail (1px line) + 16px
 * padding per depth, so the hierarchy reads even when a row collapses.
 */
type SpanState = SpanView["state"];

const STATE_DOT: Record<SpanState, string> = {
  running: "bg-run-blue animate-pulse motion-reduce:animate-none",
  finished: "bg-done-green",
  failed: "bg-fail-red",
  interrupted: "bg-warn-amber",
};

const STATE_LABEL: Record<SpanState, string> = {
  running: "running",
  finished: "finished",
  failed: "failed",
  interrupted: "interrupted",
};

type AgentChipSpec = { label: string; className: string };
const AGENT_CHIP: Record<AgentName, AgentChipSpec> = {
  document_analyst: { label: "DOC", className: "bg-sky-500/15 text-sky-700 dark:text-sky-300" },
  orchestrator: { label: "ORC", className: "bg-indigo-500/15 text-indigo-700 dark:text-indigo-300" },
  pricing_agent: { label: "PRC", className: "bg-emerald-500/15 text-emerald-700 dark:text-emerald-300" },
};

function AgentChip({ agent }: { agent: AgentName | null }) {
  if (agent === null) {
    return (
      <span className="inline-flex h-5 w-10 items-center justify-center rounded bg-muted text-[10px] font-semibold text-muted-foreground">
        SYS
      </span>
    );
  }
  const spec = AGENT_CHIP[agent];
  return (
    <span
      className={cn(
        "inline-flex h-5 w-10 items-center justify-center rounded text-[10px] font-semibold",
        spec.className,
      )}
    >
      {spec.label}
    </span>
  );
}

function formatDuration(ms: number): string {
  if (ms < 1000) return `${ms}ms`;
  return `${(ms / 1000).toFixed(1)}s`;
}

type Props = {
  span: SpanView;
  tree: SpanTree;
  spans: Record<string, SpanView>;
  depth: number;
  /** Optional icon override (SYS/SUM headings get a different glyph). */
  icon?: LucideIcon;
};

export function SpanRow({ span, tree, spans, depth }: Props) {
  // Expand by default when the span is still running OR has failed —
  // those are the ones the estimator should look at first. Finished
  // spans start collapsed so the timeline stays scannable.
  const [open, setOpen] = useState<boolean>(
    span.state === "running" || span.state === "failed",
  );
  const headerId = `span-header-${span.id}`;
  const panelId = `span-panel-${span.id}`;
  const childIds = tree.childrenById[span.id] ?? [];
  const hasDetails =
    span.tool !== null ||
    span.retry !== null ||
    span.validation !== null ||
    childIds.length > 0 ||
    span.errorMessage !== null ||
    span.summary !== null;

  const indentPx = depth * 16;

  return (
    <li className="relative">
      <div
        className="relative"
        style={{ paddingLeft: indentPx ? indentPx + 12 : 0 }}
      >
        {depth > 0 ? (
          <span
            aria-hidden
            className="absolute left-0 top-0 h-full border-l border-border/60"
            style={{ left: indentPx - 8 }}
          />
        ) : null}
        <button
          type="button"
          id={headerId}
          aria-expanded={open}
          aria-controls={hasDetails ? panelId : undefined}
          onClick={() => setOpen((v) => !v)}
          className={cn(
            "flex w-full items-center gap-2 rounded-md px-2 py-1.5 text-left transition-colors hover:bg-muted/50",
            "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
          )}
        >
          <ChevronRightIcon
            className={cn(
              "size-3.5 shrink-0 text-muted-foreground transition-transform duration-150 motion-reduce:transition-none",
              open && "rotate-90",
              !hasDetails && "invisible",
            )}
            aria-hidden
          />
          <AgentChip agent={span.agent} />
          <span className="min-w-0 flex-1 truncate text-sm">
            <span className="font-medium font-mono text-foreground">{span.name}</span>
            {span.label ? (
              <span className="ml-2 text-muted-foreground">{span.label}</span>
            ) : null}
          </span>
          {span.injected ? <InjectedBadge /> : null}
          {span.durationMs !== null ? (
            <span className="tabular text-xs text-muted-foreground">
              {formatDuration(span.durationMs)}
            </span>
          ) : null}
          <span
            aria-label={STATE_LABEL[span.state]}
            className={cn("ml-1 size-2 shrink-0 rounded-full", STATE_DOT[span.state])}
          />
        </button>
        {open && hasDetails ? (
          <div
            id={panelId}
            role="region"
            aria-labelledby={headerId}
            className="pb-1 pl-9 pr-2"
          >
            {span.summary ? (
              <p className="mt-1 text-xs text-muted-foreground">{span.summary}</p>
            ) : null}
            {span.errorMessage ? (
              <p className="mt-1 rounded-md border border-fail-red/30 bg-fail-red/10 px-2 py-1 text-xs text-fail-red-text">
                {span.errorKind ? (
                  <span className="mr-1 font-mono text-[10px] uppercase tracking-wide">
                    {span.errorKind}
                  </span>
                ) : null}
                {span.errorMessage}
              </p>
            ) : null}
            {span.tool ? <ToolCallDetail tool={span.tool} /> : null}
            {span.retry ? <RetryNote retry={span.retry} /> : null}
            {span.validation ? <ValidationErrorDetail validation={span.validation} /> : null}
            {childIds.length > 0 ? (
              <ul className="mt-1 space-y-0.5">
                {childIds.map((childId) => {
                  const child = spans[childId];
                  if (!child) return null;
                  return (
                    <SpanRow
                      key={childId}
                      span={child}
                      tree={tree}
                      spans={spans}
                      depth={depth + 1}
                    />
                  );
                })}
              </ul>
            ) : null}
          </div>
        ) : null}
      </div>
    </li>
  );
}
