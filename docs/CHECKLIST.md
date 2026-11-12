# Demo checklist

Live state at Phase-9 close. Each row maps a SPEC §11.4 required
state or a §15 phase deliverable to its current status.

Legend: `✅` shipped and tested · `✓` shipped, manual-verified ·
`⚠` partial / known gap · `×` out of scope for this branch.

---

## SPEC §11.4 — required states

| Situation | Where | Status | Notes |
|---|---|---|---|
| No runs yet | History / sidebar | ✅ | `HistoryPage` empty state + "Create your first bid" CTA. |
| No input provided | New bid | ✅ | Submit stays `aria-disabled`; disabled reason rendered in SubmitBar. |
| Invalid file | New bid | ✅ | `validatePdfFile` + `pdfErrorMessage`; "Fix the file to continue" + inline error. |
| Connecting / reconnecting | Run header | ✅ | `ConnectionChip` surfaces Live / Reconnecting / Replay / Connecting. |
| Sub-agent retrying | Trace | ✅ | `RetryNote` renders inside expanded `SpanRow` when `span.retry` is set. Backend hasn't emitted a real retry yet; detail slot unit-tested. |
| Sub-agent timeout (final) | Trace + lines | ✅ | `SpanRow` state="failed" → red dot; ItemsStream renders "Timed out" chip on affected priced line. |
| Schema validation failure | Trace | ✅ | `ValidationErrorDetail` renders inside expanded `SpanRow`; amber if `will_retry`, red if the retry didn't recover. |
| Partial results | Bid | ✅ | `BidHeader` renders a partial banner from `bid.partial_reasons`. |
| No materials found | Run | ✅ | Run outcome `no_materials` → run marked `completed`, bid tab disabled, "waiting for the bid to finish" copy. (Could use a dedicated empty-result card; §9.3 calls it out — see "known gaps".) |
| Run failed / interrupted | Run | ✅ | `StatusBadge` + ConnectionChip surface the state; TraceView header shows the outcome detail. |
| Saving / save failed | FinalizeBar | ✅ | Pill rotates Saved → Saving… → Unsaved → "Couldn't save — Retry". |
| Finalize blocked | FinalizeBar | ✅ | Disabled with "N lines need review" link that jumps to the first open row. |
| Loading any page | All | ✅ | Skeletons shaped like the final layout (`SkeletonLines`, `ItemRow` skeleton). No bare spinners. |

## SPEC §15 — deliverables by phase

| Phase | Deliverable | Status | Shipped on |
|---|---|---|---|
| 0 | Scaffold | ✅ | pre-fork |
| 1 | Data & samples | ✅ | `main` |
| 2 | Runtime core | ✅ | `main` |
| 3 | Document Analyst | ✅ | `main` |
| 4 | Pricing Agent | ✅ | `main` |
| 5 | Orchestrator + API | ✅ | `main` |
| 6 | Frontend foundation | ✅ | `frontend-shell` |
| 7 | Live run view | ✅ | `frontend-phase7` (d02df08) |
| 8 | Bid workspace | ✅ | `frontend-phase7` (bd22540) |
| 9 | Hardening & delivery | ✓ | this commit |

## Keyboard (SPEC §11.5)

| Shortcut | Where | Status |
|---|---|---|
| `⌘K` / `Ctrl+K` | Any | ✅ command menu |
| `n` | Any | ✅ nav to `/new` |
| `g h` | Any | ✅ chord → `/runs` |
| `?` | Any | ✅ shortcuts sheet |
| `j` / `k` | Bid lines | ✅ |
| `Enter` | Bid cell | ✅ |
| `Esc` | Editing cell | ✅ |
| `a` | Bid line | ✅ accept |
| `]` / `[` | Bid lines | ✅ next/prev flagged |
| `r` | Bid line | ✅ re-price |
| `⌘↵` | Bid | ✅ finalize (always fires, even when single-key is off) |

## Design system (SPEC §11.5)

| Check | Status |
|---|---|
| Inter variable font | ✅ |
| 4px spacing grid | ✅ |
| Zinc scale + single indigo accent | ✅ |
| Semantic colours with `-text` variants, WCAG AA | ✅ `tests/tokens-contrast.test.ts` (55 assertions) |
| 1px borders, radius 6, no shadows beyond popovers | ✅ |
| 120–180 ms ease-out motion, `prefers-reduced-motion` | ✅ |
| Lucide icons, 16px, stroke 1.5 | ✅ |
| `font-variant-numeric: tabular-nums` on numbers, right-align money | ✅ |

## Failure injection (SPEC §10.6) — demo controls

- `DemoFaultControls` card on `/new` (collapsed by default).
- Visible only when `useFaults()` returns `enabled: true`
  (gated by `demo_faults_enabled` in `/api/meta`).
- Faults currently in the fixture: `pricing_timeout_once`,
  `analyst_malformed_once`, `budget_exhaustion` — all shipped
  as chip controls with description + disabled-reason wiring.

## Known gaps / what's broken

- **No `no_materials` dedicated empty-result card** — SPEC §9.3
  wants a card listing what the analyst read; today the Bid
  tab's placeholder copy covers it. Minor polish, not blocking.
- **Shortcuts sheet does not list Bid-tab shortcuts** —
  `useGlobalShortcuts` covers the global combos; Bid-page j/k/
  etc. are only in `docs/CHECKLIST.md` + SPEC §11.5. Adding them
  to `?` sheet is < 1 hour of work.
- **History diff detail view** — `BidDiff` counts are on the
  History row chips; there's no side-by-side rendering yet.
- **`StockCell`** component — SPEC §11.2 enumerates it; the
  LinesTable shows `short` chips from `match.shortfall`
  instead. Fine for the demo, swap in when the backend emits
  real `transfer_options` with supplier data.
- **Stale-revision reload banner** — the reducer handles 409
  (`saveStale`), but `FinalizeBar` doesn't render a dedicated
  "This bid changed elsewhere" button. Latent in fixture mode
  (no concurrent writers).
- **Playwright e2e** — one spec covers the happy path (paste →
  run → bid → edit → finalize). Reconnect, stream-replay, and
  fault-injected failures aren't scripted yet.

## Running the demo

```bash
make setup     # once
make seed      # catalog + sample pdfs
make samples   # (if not already present)
make dev       # backend :8000 + frontend :5173

# Optional: Playwright end-to-end (no backend required — fixture mode)
cd frontend
npx playwright install chromium
npm run e2e
```

Core demo paths to walk during a review:
1. `/new` → paste list sample → Run → Live tab fills in, Bid
   auto-selects when `bid.ready` arrives.
2. On a priced line, click the unit price → enter a new value
   + reason → Save pill rotates → totals update server-side.
3. Press `r` on a focused line → RepriceDialog shows before /
   after → Apply.
4. Fix any open flags via `a` (accept) or edit → FinalizeBar
   enables → `⌘↵` finalizes → History row shows the diff
   summary.
5. Click a History row → it opens its own terminal state
   instantly (no replay animation); the SourcePanel opens that
   run's PDF.
