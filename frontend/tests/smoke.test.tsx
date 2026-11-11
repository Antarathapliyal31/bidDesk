/**
 * Phase-6 smoke: the full app renders with its providers; the primary
 * nav landmark and main landmark are reachable; `/` lands on New bid.
 * Deeper shell tests (skip link, title, scroll/focus on nav) live in
 * tests/AppShell.test.tsx.
 */
import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import App from "../src/App";

describe("App", () => {
  it("renders the primary nav landmark", () => {
    render(<App />);
    expect(screen.getByRole("navigation", { name: "Primary" })).toBeInTheDocument();
  });

  it("exposes a main landmark", () => {
    render(<App />);
    expect(screen.getByRole("main")).toBeInTheDocument();
  });

  it("'/' lands on the New bid page (heading visible)", () => {
    render(<App />);
    expect(screen.getByRole("heading", { level: 1, name: /new bid/i })).toBeInTheDocument();
  });
});
