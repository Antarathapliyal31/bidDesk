import { FilePlusIcon, HistoryIcon, KeyboardIcon } from "lucide-react";
import { NavLink } from "react-router-dom";

import { useRuns, type RunStatus } from "@/api";
import { Kbd } from "@/components/Kbd";
import { cn } from "@/lib/utils";

import { useShortcutsSheet } from "./ShortcutsContext";

/**
 * Left sidebar: brand, primary nav, the five most recent runs
 * (fed by `useRuns()` — same query the History page uses, so the
 * cache is shared), and a clickable "Keyboard shortcuts" footer.
 *
 * Landmark: the outer container is a plain <div>; the navigation
 * role lives on the inner <nav aria-label="Primary">. Active links
 * get a 2px primary indicator plus font-semibold so active and hover
 * never look identical (hover uses bg-accent/60).
 *
 * The <ul>s carry role="list" because Safari/VoiceOver drops list
 * semantics when `list-style: none` is applied (which Tailwind's
 * reset does).
 */
const NAV = [
  { to: "/new", label: "New bid", icon: FilePlusIcon },
  { to: "/runs", label: "History", icon: HistoryIcon },
] as const;

const DOT_CLASS: Record<RunStatus, string> = {
  running: "bg-run-blue",
  completed: "bg-done-green",
  partial: "bg-warn-amber",
  failed: "bg-fail-red",
  interrupted: "bg-zinc-400",
};

function StatusDot({ status }: { status: RunStatus }) {
  return (
    <span
      className={cn("size-2 shrink-0 rounded-full", DOT_CLASS[status])}
      aria-label={status}
      title={status}
    />
  );
}

export function Sidebar() {
  const { setOpen: openShortcuts } = useShortcutsSheet();
  const runsQuery = useRuns();
  const allRuns = runsQuery.data?.pages.flatMap((p) => p.items) ?? [];
  const recent = allRuns.slice(0, 5);

  return (
    <div className="flex w-60 shrink-0 flex-col border-r border-border bg-card">
      <div className="flex h-14 items-center gap-2 border-b border-border px-4">
        <span
          className="inline-flex size-6 items-center justify-center rounded-md bg-primary text-xs font-semibold text-primary-foreground"
          aria-hidden
        >
          B
        </span>
        <span className="text-sm font-semibold tracking-tight">BidDesk</span>
      </div>

      <nav aria-label="Primary" className="px-2 py-3">
        <ul role="list" className="flex flex-col gap-0.5">
          {NAV.map(({ to, label, icon: Icon }) => (
            <li key={to}>
              <NavLink
                to={to}
                className={({ isActive }) =>
                  cn(
                    "relative flex items-center gap-2 rounded-md px-2.5 py-1.5 text-sm transition-colors",
                    isActive
                      ? "bg-accent font-semibold text-foreground"
                      : "font-medium text-muted-foreground hover:bg-accent/60 hover:text-foreground",
                  )
                }
              >
                {({ isActive }) => (
                  <>
                    {isActive ? (
                      <span
                        aria-hidden
                        className="absolute left-0 top-1/2 h-4 w-0.5 -translate-y-1/2 rounded-full bg-primary"
                      />
                    ) : null}
                    <Icon className="size-4" aria-hidden />
                    {label}
                  </>
                )}
              </NavLink>
            </li>
          ))}
        </ul>
      </nav>

      <h2 className="px-4 pb-2 pt-4 text-xs font-medium uppercase tracking-wide text-muted-foreground">
        Recent runs
      </h2>
      <div className="flex-1 overflow-y-auto px-2">
        {runsQuery.isPending ? (
          <p className="px-2.5 py-1.5 text-xs text-muted-foreground">Loading…</p>
        ) : recent.length === 0 ? (
          <p className="px-2.5 py-1.5 text-xs text-muted-foreground">
            Runs will appear here after your first bid.
          </p>
        ) : (
          <ul role="list" className="flex flex-col gap-0.5">
            {recent.map((run) => {
              const name = run.project_name ?? run.input_name;
              return (
                <li key={run.id}>
                  <NavLink
                    to={`/runs/${run.id}`}
                    title={name}
                    className={({ isActive }) =>
                      cn(
                        "flex items-center gap-2 rounded-md px-2.5 py-1.5 text-xs transition-colors",
                        isActive
                          ? "bg-accent text-foreground"
                          : "text-muted-foreground hover:bg-accent/60 hover:text-foreground",
                      )
                    }
                  >
                    <StatusDot status={run.status} />
                    <span className="truncate">{name}</span>
                  </NavLink>
                </li>
              );
            })}
          </ul>
        )}
      </div>

      <div className="border-t border-border p-2">
        <button
          type="button"
          onClick={() => openShortcuts(true)}
          aria-haspopup="dialog"
          className="flex w-full items-center gap-2 rounded-md px-2.5 py-1.5 text-sm text-muted-foreground transition-colors hover:bg-accent/60 hover:text-foreground"
        >
          <KeyboardIcon className="size-4" aria-hidden />
          <span className="flex-1 text-left">Keyboard shortcuts</span>
          <Kbd>?</Kbd>
        </button>
      </div>
    </div>
  );
}
