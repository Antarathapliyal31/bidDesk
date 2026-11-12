import { CheckIcon, WrenchIcon, XIcon } from "lucide-react";

import { cn } from "@/lib/utils";

/**
 * One tool invocation inside a span (SPEC §10.3 tool.called +
 * tool.result). Shown when a SpanRow is expanded and its span
 * carries a `tool`. Compact, monospaced previews — SPEC caps both
 * sides at ≤ 300 chars server-side, but we still truncate visually
 * so one long preview never blows up the row.
 */
type Props = {
  tool: {
    name: string;
    argsPreview: string;
    ok?: boolean;
    resultPreview?: string;
  };
};

export function ToolCallDetail({ tool }: Props) {
  const hasResult = tool.ok !== undefined;
  const okTrue = tool.ok === true;
  return (
    <div className="mt-2 rounded-md border border-border bg-muted/40 px-3 py-2 text-xs">
      <div className="flex items-center gap-1.5 font-medium text-foreground">
        <WrenchIcon className="size-3.5 text-muted-foreground" aria-hidden />
        <span className="font-mono">{tool.name}</span>
        {hasResult ? (
          <span
            className={cn(
              "ml-auto inline-flex items-center gap-1 rounded px-1.5 py-0.5 text-[10px] font-medium",
              okTrue
                ? "bg-done-green/10 text-done-green-text"
                : "bg-fail-red/10 text-fail-red-text",
            )}
          >
            {okTrue ? (
              <CheckIcon className="size-3" aria-hidden />
            ) : (
              <XIcon className="size-3" aria-hidden />
            )}
            {okTrue ? "ok" : "failed"}
          </span>
        ) : null}
      </div>
      {tool.argsPreview ? (
        <div className="mt-1 text-muted-foreground">
          <span className="text-[10px] font-medium uppercase tracking-wide">args</span>
          <span className="ml-1.5 truncate font-mono">{tool.argsPreview}</span>
        </div>
      ) : null}
      {tool.resultPreview ? (
        <div className="mt-0.5 text-muted-foreground">
          <span className="text-[10px] font-medium uppercase tracking-wide">result</span>
          <span className="ml-1.5 truncate font-mono">{tool.resultPreview}</span>
        </div>
      ) : null}
    </div>
  );
}
