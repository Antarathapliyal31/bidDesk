/**
 * The actual react-pdf renderer. Separate module so the `React.lazy`
 * wrapper above can split pdfjs out of the main bundle.
 *
 * Worker setup: pdfjs runs a dedicated web worker. We point it at
 * the bundled worker via Vite's `?url` import so there's no CDN
 * dependency at runtime.
 *
 * Best-effort highlight: `customTextRenderer` is called once per
 * text item on the rendered page. We wrap items whose normalized
 * text is a substring of the normalized quote in `<mark>`. Across-
 * item matches aren't attempted — a miss falls back to the callout
 * above the viewer.
 *
 * Vision sources: `read_via = "vision"` means the analyst read the
 * page as an image. There is no text layer to highlight; the caller
 * renders a "Read from image" callout and we just show the page.
 */
import { useCallback, useMemo } from "react";
import { Document, Page, pdfjs } from "react-pdf";
import "react-pdf/dist/Page/AnnotationLayer.css";
import "react-pdf/dist/Page/TextLayer.css";

import workerSrc from "pdfjs-dist/build/pdf.worker.mjs?url";

import type { SourceRef } from "@/api";
import { escapeHtml, matchesQuote, normalizeQuote } from "@/lib/quote";

pdfjs.GlobalWorkerOptions.workerSrc = workerSrc;

// pdf.js is heavy (CMap + standard fonts). We ship the core only —
// the live-run PDFs are small, text-heavy, and don't need CJK fonts.
// If a real run surfaces missing-glyph warnings we revisit.
const OPTIONS = {
  cMapUrl: undefined,
  cMapPacked: true,
  standardFontDataUrl: undefined,
} as const;

type Props = {
  pdfUrl: string;
  source: SourceRef;
};

export default function PdfViewerImpl({ pdfUrl, source }: Props) {
  const normalizedQuote = useMemo(
    () => (source.quote ? normalizeQuote(source.quote) : ""),
    [source.quote],
  );
  const shouldHighlight = source.read_via === "text" && normalizedQuote.length > 0;

  // Pass-through when !shouldHighlight so the text layer still
  // renders (and selection keeps working) but nothing is marked.
  const textRenderer = useCallback(
    ({ str }: { str: string }) => {
      if (!shouldHighlight) return escapeHtml(str);
      return matchesQuote(str, normalizedQuote)
        ? `<mark>${escapeHtml(str)}</mark>`
        : escapeHtml(str);
    },
    [shouldHighlight, normalizedQuote],
  );

  const pageNumber = Math.max(1, source.page ?? 1);

  return (
    <div className="flex h-full flex-col overflow-auto bg-muted/30">
      <Document
        file={pdfUrl}
        options={OPTIONS}
        loading={
          <div className="flex items-center justify-center px-6 py-10 text-sm text-muted-foreground">
            Loading PDF…
          </div>
        }
        error={
          <div
            role="alert"
            className="m-6 rounded-md border border-fail-red/30 bg-fail-red/10 px-4 py-3 text-sm text-fail-red-text"
          >
            Couldn't load the PDF for this run.
          </div>
        }
        noData={
          <div className="px-6 py-10 text-sm text-muted-foreground">No PDF available.</div>
        }
      >
        <Page
          pageNumber={pageNumber}
          width={520}
          renderAnnotationLayer={false}
          renderTextLayer
          customTextRenderer={textRenderer}
        />
      </Document>
    </div>
  );
}
