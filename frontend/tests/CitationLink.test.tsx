/**
 * CitationLink: "p. N" chip opens the SourcePanel via context.
 *   - renders "—" when the source has no page.
 *   - renders a button with aria-label "page N" for keyboard users.
 *   - click calls openSource with the source + pdfUrl.
 */
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

import { CitationLink } from "@/components/CitationLink";
import {
  SourcePanelProvider,
  useSourcePanel,
} from "@/features/run/source/SourcePanelContext";

function Probe() {
  const { open, source, pdfUrl } = useSourcePanel();
  return (
    <div data-testid="probe">
      {open ? "open" : "closed"}:{source?.page ?? "none"}:{pdfUrl ?? "none"}
    </div>
  );
}

function Harness(props: React.ComponentProps<typeof CitationLink>) {
  return (
    <SourcePanelProvider>
      <CitationLink {...props} />
      <Probe />
    </SourcePanelProvider>
  );
}

describe("CitationLink", () => {
  it("renders an em-dash with no interactive affordance when page is null", () => {
    render(
      <Harness
        source={{ page: null, quote: null, read_via: null, verified: false }}
        pdfUrl="/x.pdf"
      />,
    );
    expect(screen.queryByRole("button")).toBeNull();
    expect(screen.getByText("—")).toBeInTheDocument();
  });

  it("renders a labelled button with the page number and short text 'p.N'", () => {
    render(
      <Harness
        source={{ page: 7, quote: null, read_via: "text", verified: true }}
        pdfUrl="/x.pdf"
      />,
    );
    const btn = screen.getByRole("button", { name: "page 7" });
    expect(btn).toHaveTextContent("p.7");
  });

  it("click opens the panel with the source + pdfUrl", async () => {
    const user = userEvent.setup();
    render(
      <Harness
        source={{ page: 7, quote: "SPF #2 2x4", read_via: "text", verified: true }}
        pdfUrl="/samples/rfp_messy.pdf"
      />,
    );
    expect(screen.getByTestId("probe")).toHaveTextContent("closed:none:none");
    await user.click(screen.getByRole("button", { name: "page 7" }));
    expect(screen.getByTestId("probe")).toHaveTextContent("open:7:/samples/rfp_messy.pdf");
  });
});
