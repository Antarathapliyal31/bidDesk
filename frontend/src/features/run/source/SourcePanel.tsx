/**
 * Slide-over source panel (SPEC §11.2 / §11.5 citation links).
 *
 * Mounts once per RunPage, subscribes to `useSourcePanel()`. When
 * the user clicks a CitationLink, the context fires `openSource`
 * and this component opens a right-side Radix Dialog with:
 *   - header   — "p. N · filename" + close button
 *   - callout  — the cited quote OR, for vision sources, a "Read
 *                from image — check this page" note
 *   - viewer   — lazy `<PdfViewer />` for text PDFs; placeholder
 *                for paste runs (no PDF URL)
 *
 * Dialog (not Sheet) because Radix doesn't ship a Sheet primitive
 * and our tailwind side-slide styles work directly on DialogContent.
 * Overlay dims the rest; Esc + outside-click close via Radix.
 */
import { CameraIcon, FileTextIcon, QuoteIcon } from "lucide-react";

import {
  Dialog,
  DialogClose,
  DialogContent,
  DialogDescription,
  DialogTitle,
} from "@/components/ui/dialog";

import { PdfViewer } from "./PdfViewer";
import { useSourcePanel } from "./SourcePanelContext";

function filenameFromUrl(url: string | null): string | null {
  if (url === null) return null;
  const tail = url.split("/").pop();
  return tail ?? null;
}

export function SourcePanel() {
  const { open, source, pdfUrl, close } = useSourcePanel();

  return (
    <Dialog open={open} onOpenChange={(v) => (!v ? close() : undefined)}>
      <DialogContent
        showCloseButton={false}
        className="fixed top-0 right-0 left-auto h-screen w-full max-w-[min(100vw,640px)] translate-x-0 translate-y-0 rounded-none border-l p-0 duration-150 data-[state=closed]:slide-out-to-right data-[state=open]:slide-in-from-right sm:max-w-[640px]"
      >
        <div className="flex h-full flex-col">
          <header className="flex items-center gap-2 border-b border-border px-4 py-3">
            <HeaderIcon source={source} />
            <div className="min-w-0 flex-1">
              <DialogTitle className="truncate text-sm font-semibold">
                {source?.page !== undefined && source?.page !== null
                  ? `p. ${source.page}`
                  : "Source"}
                {filenameFromUrl(pdfUrl) ? (
                  <span className="ml-2 font-normal text-muted-foreground">
                    · {filenameFromUrl(pdfUrl)}
                  </span>
                ) : null}
              </DialogTitle>
              <DialogDescription className="sr-only">
                Source panel showing the cited location in the input document.
              </DialogDescription>
            </div>
            <DialogClose
              className="rounded-md p-1 text-muted-foreground hover:bg-muted focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
              aria-label="Close source panel"
            >
              <XIcon />
            </DialogClose>
          </header>

          {source !== null ? (
            <SourceCallout source={source} />
          ) : null}

          <div className="min-h-0 flex-1">
            {source === null ? null : pdfUrl === null ? (
              <NoPdfState />
            ) : (
              <PdfViewer pdfUrl={pdfUrl} source={source} />
            )}
          </div>
        </div>
      </DialogContent>
    </Dialog>
  );
}

function HeaderIcon({ source }: { source: { read_via?: "text" | "vision" | null } | null }) {
  if (source?.read_via === "vision") {
    return <CameraIcon className="size-4 text-muted-foreground" aria-hidden />;
  }
  return <FileTextIcon className="size-4 text-muted-foreground" aria-hidden />;
}

function SourceCallout({
  source,
}: {
  source: { quote: string | null; read_via: "text" | "vision" | null; verified: boolean };
}) {
  if (source.read_via === "vision") {
    return (
      <div className="border-b border-border bg-amber-50/40 px-4 py-2.5 text-xs text-warn-amber-text dark:bg-amber-500/5">
        <div className="flex items-start gap-2">
          <CameraIcon className="mt-0.5 size-3.5 shrink-0" aria-hidden />
          <p>Read from image — check this page.</p>
        </div>
      </div>
    );
  }
  if (!source.quote) return null;
  return (
    <div className="border-b border-border bg-muted/30 px-4 py-2.5">
      <div className="flex items-start gap-2 text-xs">
        <QuoteIcon className="mt-0.5 size-3.5 shrink-0 text-muted-foreground" aria-hidden />
        <blockquote className="min-w-0 italic text-foreground">{source.quote}</blockquote>
      </div>
      {!source.verified ? (
        <p className="mt-1.5 text-[11px] text-warn-amber-text">
          Unverified — the exact quote wasn't found on the page; using best-effort highlight.
        </p>
      ) : null}
    </div>
  );
}

function NoPdfState() {
  return (
    <div className="flex h-full items-start justify-center px-6 pt-10 text-sm text-muted-foreground">
      This run didn't have a PDF source (pasted list or uploaded file no
      longer available).
    </div>
  );
}

// Inline so SourcePanel stays one file; lucide's XIcon pattern.
function XIcon(props: { className?: string }) {
  return (
    <svg
      xmlns="http://www.w3.org/2000/svg"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.5"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden
      className={props.className ?? "size-4"}
    >
      <path d="M18 6 6 18" />
      <path d="m6 6 12 12" />
    </svg>
  );
}
