/**
 * Money renders integer cents as formatted currency (SPEC §2).
 * The null case must render an sr-only "No value" string and must
 * never render "$0.00" — a missing price is not a free price.
 */
import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { Money } from "../src/components/Money";

describe("Money", () => {
  it("formats integer cents as USD with two decimals", () => {
    render(<Money cents={12_345} />);
    expect(screen.getByText("$123.45")).toBeInTheDocument();
  });

  it("formats values ≥ 1000 with a thousands separator", () => {
    render(<Money cents={1_234_567} />);
    expect(screen.getByText("$12,345.67")).toBeInTheDocument();
  });

  it("renders a dash plus sr-only 'No value' for null", () => {
    const { container } = render(<Money cents={null} />);
    // sr-only text is in the DOM, readable by assistive tech.
    expect(screen.getByText("No value")).toBeInTheDocument();
    // Never renders a zero price for missing data.
    expect(container.textContent).not.toContain("$0.00");
    // Visible dash is aria-hidden.
    const dash = container.querySelector("[aria-hidden]");
    expect(dash?.textContent).toBe("—");
  });

  it("renders a dash plus sr-only 'No value' for undefined", () => {
    render(<Money cents={undefined} />);
    expect(screen.getByText("No value")).toBeInTheDocument();
  });

  it("renders $0.00 only when cents is explicitly 0", () => {
    render(<Money cents={0} />);
    expect(screen.getByText("$0.00")).toBeInTheDocument();
  });

  it("formats negative cents with a leading minus (credit / refund)", () => {
    render(<Money cents={-1250} />);
    expect(screen.getByText("-$12.50")).toBeInTheDocument();
  });

  it("supports a non-USD currency via the currency prop", () => {
    render(<Money cents={100_00} currency="EUR" />);
    expect(screen.getByText(/€100\.00/)).toBeInTheDocument();
  });
});
