/**
 * Integration: New-bid → pick rfp_messy → Run → RunPage renders.
 *
 * Covers the bug where fixture-mode submits landed on
 * "We couldn't find that run". Any `run_demo_*` id now falls back
 * to the rfp_messy scripted stream (see dev-fixtures/sse/index.ts);
 * `useCreateRun` returns `run_demo_messy` directly for every
 * submit. This test proves the round trip: form submit navigates
 * to the run page, the stream fires, the Live view renders.
 *
 * Real timers (fixtures use real `setTimeout`s with ~600/800ms
 * delays) + `userEvent`. `waitFor` absorbs the timing; the test
 * only asserts on the first wave of events, so we never wait for a
 * full fixture drain.
 */
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { Providers } from "@/app/Providers";
import { createTestQueryClient } from "@/app/queryClient";
import { AppRoutes } from "@/app/router";

describe("New bid → Run (fixture mode)", () => {
  let fetchSpy: ReturnType<typeof vi.spyOn>;

  beforeEach(() => {
    fetchSpy = vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
      const url =
        typeof input === "string"
          ? input
          : input instanceof URL
            ? input.toString()
            : (input as Request).url;
      if (url.endsWith("/samples/rfp_messy.pdf")) {
        // A tiny non-empty blob so validatePdfFile accepts it.
        return new Response(new Blob([new Uint8Array(32)], { type: "application/pdf" }), {
          status: 200,
        });
      }
      throw new Error(`Unmocked fetch: ${url}`);
    });
  });

  afterEach(() => {
    fetchSpy.mockRestore();
  });

  it(
    "choose rfp_messy, submit, land on RunPage with Live view and first events rendered",
    async () => {
      const user = userEvent.setup();

      render(
        <Providers
          queryClient={createTestQueryClient()}
          Router={({ children }) => (
            <MemoryRouter initialEntries={["/new"]}>{children}</MemoryRouter>
          )}
        >
          <AppRoutes />
        </Providers>,
      );

      // Click the rfp_messy sample card once useSamples resolves.
      const card = await screen.findByRole("button", { name: /rfp_messy\.pdf/i });
      await user.click(card);

      // Fetch → blob → setFile is async; wait until the Run button
      // is actually enabled (aria-disabled="false").
      const runBtn = await screen.findByRole("button", { name: /^run$/i });
      await waitFor(() => {
        expect(runBtn).toHaveAttribute("aria-disabled", "false");
      });

      await user.click(runBtn);

      // Mutation + navigate + first scripted events. Header title
      // flips to the input name (rfp_messy.pdf) when `input.received`
      // lands — gives us a single assertion that proves:
      //   (a) navigate succeeded (we're on RunPage, not stuck on /new)
      //   (b) useRunStream found the fixture (otherwise title would
      //       stay as the runId)
      await waitFor(
        () => {
          expect(
            screen.getByRole("heading", { level: 1, name: /rfp_messy\.pdf/i }),
          ).toBeInTheDocument();
        },
        { timeout: 4000 },
      );

      // Not the "run not found" empty state.
      expect(
        screen.queryByRole("heading", { level: 2, name: /couldn't find that run/i }),
      ).not.toBeInTheDocument();

      // Live view mounted — three agent cards.
      expect(screen.getByRole("article", { name: "Document Analyst" })).toBeInTheDocument();
      expect(screen.getByRole("article", { name: "Orchestrator" })).toBeInTheDocument();
      expect(screen.getByRole("article", { name: "Pricing Agent" })).toBeInTheDocument();

      // At least one lane has left Idle (Document Analyst's span
      // starts early in rfp_messy.ts, within ~500ms of RunPage
      // mount — the fixture driver fires one event per 150ms).
      await waitFor(
        () => {
          const analyst = screen.getByRole("article", { name: "Document Analyst" });
          expect(within(analyst).queryByText("Idle")).toBeNull();
        },
        { timeout: 3000 },
      );
    },
    10_000,
  );
});
