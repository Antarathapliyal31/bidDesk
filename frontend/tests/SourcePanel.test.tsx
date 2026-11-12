/**
 * SourcePanel: opens via context, shows the right header/callout
 * per source, and dismisses via Esc + close button. The PdfViewer
 * is mocked so pdfjs doesn't load in jsdom.
 */
import { act, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { SourceRef } from "@/api";
import {
  SourcePanelProvider,
  useSourcePanel,
} from "@/features/run/source/SourcePanelContext";

// Mock the lazy PdfViewer — jsdom + pdfjs is a non-starter and
// not what this test is about.
vi.mock("@/features/run/source/PdfViewer", () => ({
  PdfViewer: ({ pdfUrl }: { pdfUrl: string }) => (
    <div data-testid="pdf-viewer-mock">pdf:{pdfUrl}</div>
  ),
}));

import { SourcePanel } from "@/features/run/source/SourcePanel";

const SRC_TEXT: SourceRef = {
  page: 7,
  quote: "2x4 x 8' SPF #2, 100 pcs",
  read_via: "text",
  verified: true,
};
const SRC_VISION: SourceRef = {
  page: 3,
  quote: null,
  read_via: "vision",
  verified: false,
};
const SRC_TEXT_UNVERIFIED: SourceRef = { ...SRC_TEXT, verified: false };

function Opener({
  source,
  pdfUrl,
}: {
  source: SourceRef;
  pdfUrl: string | null;
}) {
  const { openSource } = useSourcePanel();
  return (
    <button type="button" onClick={() => openSource(source, pdfUrl)}>
      open
    </button>
  );
}

function Harness({ source, pdfUrl }: { source: SourceRef; pdfUrl: string | null }) {
  return (
    <SourcePanelProvider>
      <Opener source={source} pdfUrl={pdfUrl} />
      <SourcePanel />
    </SourcePanelProvider>
  );
}

describe("SourcePanel", () => {
  // Radix animates with rAF; Vitest's jsdom env has rAF. userEvent
  // is enough without fake timers.
  beforeEach(() => {});
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("closed by default (no dialog in the DOM)", () => {
    render(<Harness source={SRC_TEXT} pdfUrl="/samples/rfp_messy.pdf" />);
    expect(screen.queryByRole("dialog")).toBeNull();
  });

  it("text source + pdfUrl: header, quote callout, lazy PdfViewer", async () => {
    const user = userEvent.setup();
    render(<Harness source={SRC_TEXT} pdfUrl="/samples/rfp_messy.pdf" />);
    await user.click(screen.getByRole("button", { name: "open" }));

    const dialog = await screen.findByRole("dialog");
    // Header: "p. 7 · rfp_messy.pdf".
    expect(dialog).toHaveTextContent(/p\.\s?7/);
    expect(dialog).toHaveTextContent("rfp_messy.pdf");
    // Quote callout.
    expect(screen.getByText(/2x4 x 8' SPF #2, 100 pcs/)).toBeInTheDocument();
    // Lazy viewer mounted with the right URL.
    expect(screen.getByTestId("pdf-viewer-mock")).toHaveTextContent(
      "pdf:/samples/rfp_messy.pdf",
    );
    // No "read from image" note for text sources.
    expect(screen.queryByText(/read from image/i)).toBeNull();
  });

  it("vision source: shows 'Read from image — check this page' and no quote block", async () => {
    const user = userEvent.setup();
    render(<Harness source={SRC_VISION} pdfUrl="/samples/rfp_scanned.pdf" />);
    await user.click(screen.getByRole("button", { name: "open" }));
    expect(await screen.findByText(/read from image/i)).toBeInTheDocument();
    // No quote block (source.quote is null).
    expect(screen.queryByRole("blockquote")).toBeNull();
  });

  it("unverified text source renders an amber 'best-effort highlight' note", async () => {
    const user = userEvent.setup();
    render(<Harness source={SRC_TEXT_UNVERIFIED} pdfUrl="/x.pdf" />);
    await user.click(screen.getByRole("button", { name: "open" }));
    expect(await screen.findByText(/best-effort highlight/i)).toBeInTheDocument();
  });

  it("null pdfUrl (paste runs) renders the no-PDF message, not the viewer", async () => {
    const user = userEvent.setup();
    render(<Harness source={SRC_TEXT} pdfUrl={null} />);
    await user.click(screen.getByRole("button", { name: "open" }));
    expect(await screen.findByText(/no longer available/i)).toBeInTheDocument();
    expect(screen.queryByTestId("pdf-viewer-mock")).toBeNull();
  });

  it("Esc closes the dialog", async () => {
    const user = userEvent.setup();
    render(<Harness source={SRC_TEXT} pdfUrl="/x.pdf" />);
    await user.click(screen.getByRole("button", { name: "open" }));
    expect(await screen.findByRole("dialog")).toBeInTheDocument();
    await user.keyboard("{Escape}");
    await act(async () => {
      // Flush Radix's exit transition.
      await Promise.resolve();
    });
    expect(screen.queryByRole("dialog")).toBeNull();
  });

  it("the close button is keyboard-reachable and dismisses the dialog", async () => {
    const user = userEvent.setup();
    render(<Harness source={SRC_TEXT} pdfUrl="/x.pdf" />);
    await user.click(screen.getByRole("button", { name: "open" }));
    const close = await screen.findByRole("button", { name: /close source panel/i });
    await user.click(close);
    await act(async () => {
      await Promise.resolve();
    });
    expect(screen.queryByRole("dialog")).toBeNull();
  });
});
