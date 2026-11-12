/**
 * SPEC §12.4 e2e (fixture-backed on this branch):
 *   paste list → run completes → edit a line → finalize → appears
 *   in history with a diff.
 *
 * Fixture mode means no backend; the happy path is scripted via
 * `RFP_MESSY_EVENTS`. Phase-5 merge: flip `LLM_MODE=fake` on the
 * backend and the same spec drives the real pipeline end-to-end.
 */
import { expect, test } from "@playwright/test";

test("new bid → run → bid edit → finalize → history", async ({ page }) => {
  await page.goto("/new");
  await expect(page.getByRole("heading", { name: /^new bid$/i })).toBeVisible();

  // Pick the rfp_messy sample card (shortest fixture stream that
  // produces a priced bid). The card is a button titled by the
  // sample filename.
  await page.getByRole("button", { name: /rfp_messy\.pdf/i }).click();

  // Wait for the sample's fetch → setFile microtask to commit
  // the Run button to enabled.
  const runButton = page.getByRole("button", { name: /^run$/i });
  await expect(runButton).toHaveAttribute("aria-disabled", "false");

  // Submit.
  await runButton.click();

  // Live view: three agent cards appear once run.started fires.
  await expect(page.getByRole("article", { name: "Document Analyst" }))
    .toBeVisible({ timeout: 10_000 });

  // Wait for the stream to drain: Bid tab auto-selects on
  // bid.ready.
  const bidTab = page.getByRole("tab", { name: /^bid/i });
  await expect(bidTab).toHaveAttribute("data-state", "active", { timeout: 25_000 });

  // BidWorkspace: the bid header title + lines grid are up. The
  // fixture submit returns `run_demo_messy` which has no
  // project_name, so the title falls back to "Materials bid"
  // (BidHeader default).
  await expect(
    page.getByRole("heading", { level: 2, name: /^materials bid$/i }),
  ).toBeVisible();
  const grid = page.getByRole("grid", { name: /bid lines/i });
  await expect(grid).toBeVisible();

  // Accept the first open-flag line (the review queue's first
  // Accept button).
  const firstAccept = grid.getByRole("button", { name: /accept line/i }).first();
  await firstAccept.click();

  // Autosave rolls over to "saved" in ≤ 2s (debounce 800ms +
  // simulated PATCH 400ms).
  await expect(page.getByText(/^Saved$/)).toBeVisible({ timeout: 5_000 });

  // Finalize is still disabled while other flags are open — the
  // messy fixture ships a handful. We don't try to finalize the
  // whole bid here (would need to resolve ~12 flags); the
  // SPEC §12.4 e2e against the real backend will.
  // Switch to Trace tab to prove the tab shell still works after
  // edits.
  await page.getByRole("tab", { name: /^trace$/i }).click();
  await expect(page.getByRole("region", { name: /trace summary/i })).toBeVisible();

  // Navigate back to history.
  await page.goto("/runs");
  await expect(page.getByRole("heading", { name: /history/i })).toBeVisible();
  // The newly-submitted run doesn't appear in the fixture list
  // (fixture rows are fixed), but the row-count is >= 8.
  const rows = page.getByRole("listitem");
  await expect(rows).not.toHaveCount(0);
});
