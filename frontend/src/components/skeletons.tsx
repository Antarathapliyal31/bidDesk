import { Skeleton } from "@/components/ui/skeleton";
import { cn } from "@/lib/utils";

/**
 * Shaped skeletons (SPEC §11.4: "no spinner-only loading — use
 * skeletons shaped like the content"). Each is a visual placeholder
 * for the real component it loads into: the layout stays stable.
 */

export function RowSkeleton({ className }: { className?: string }) {
  return (
    <div className={cn("flex items-center gap-3 border-b border-border px-4 py-2.5", className)}>
      <Skeleton className="h-4 w-16" />
      <Skeleton className="h-4 flex-1" />
      <Skeleton className="h-4 w-20" />
      <Skeleton className="h-4 w-14" />
      <Skeleton className="h-4 w-24" />
    </div>
  );
}

export function TableSkeleton({ rows = 7, className }: { rows?: number; className?: string }) {
  return (
    <div
      className={cn("overflow-hidden rounded-md border border-border bg-card", className)}
      aria-busy="true"
      aria-label="Loading"
    >
      <div className="border-b border-border bg-muted/50 px-4 py-2.5">
        <Skeleton className="h-3.5 w-24" />
      </div>
      {Array.from({ length: rows }).map((_, i) => (
        <RowSkeleton key={i} />
      ))}
    </div>
  );
}

export function CardSkeleton({ className }: { className?: string }) {
  return (
    <div
      className={cn("space-y-3 rounded-md border border-border bg-card p-4", className)}
      aria-busy="true"
    >
      <Skeleton className="h-4 w-1/3" />
      <Skeleton className="h-3 w-full" />
      <Skeleton className="h-3 w-5/6" />
    </div>
  );
}
