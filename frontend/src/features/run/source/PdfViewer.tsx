/**
 * Lazy wrapper for the react-pdf renderer. `React.lazy` + dynamic
 * import keeps pdfjs (~300KB gzipped) out of the main bundle — the
 * chunk only loads when the user opens their first citation.
 *
 * Fallback: a skeleton shaped like a PDF page, not a spinner (SPEC
 * §11.4). Suspense in the parent gates everything downstream.
 */
import { lazy, Suspense } from "react";

import type { SourceRef } from "@/api";
import { Skeleton } from "@/components/ui/skeleton";

const PdfViewerImpl = lazy(() => import("./PdfViewerImpl"));

type Props = {
  pdfUrl: string;
  source: SourceRef;
};

export function PdfViewer({ pdfUrl, source }: Props) {
  return (
    <Suspense fallback={<PdfLoadingSkeleton />}>
      <PdfViewerImpl pdfUrl={pdfUrl} source={source} />
    </Suspense>
  );
}

function PdfLoadingSkeleton() {
  return (
    <div
      aria-busy="true"
      aria-label="Loading PDF viewer"
      className="flex h-full items-start justify-center overflow-hidden bg-muted/30 p-6"
    >
      <div className="flex w-full max-w-md flex-col gap-2 rounded-sm border border-border bg-background p-4 shadow-sm">
        <Skeleton className="h-3 w-2/3" />
        <Skeleton className="h-3 w-full" />
        <Skeleton className="h-3 w-5/6" />
        <Skeleton className="h-3 w-3/4" />
        <Skeleton className="h-3 w-full" />
        <Skeleton className="h-3 w-4/5" />
        <Skeleton className="mt-4 h-24 w-full" />
        <Skeleton className="h-3 w-5/6" />
      </div>
    </div>
  );
}
