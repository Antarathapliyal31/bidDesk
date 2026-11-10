/**
 * Phase 0 smoke: the App renders and shows the project name.
 * Also proves Vitest + RTL + jsdom are wired correctly.
 */
import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import App from "../src/App";

describe("App", () => {
  it("renders the project name", () => {
    render(<App />);
    expect(screen.getByRole("heading", { name: /biddesk/i })).toBeInTheDocument();
  });
});
