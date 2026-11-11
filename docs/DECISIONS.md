# DECISIONS

ADR-lite. One entry per non-obvious decision. Append only; earlier entries
stay even if later ones supersede them (add a "superseded by #N" note).

Format:

```
## N. Short title — YYYY-MM-DD
Decision: ...
Because: ...
Alternatives considered: ...
Trade-off / known downside: ...
```

---

## 1. No agent framework — 2026-11-09
Decision: Hand-write the orchestrator loop (SPEC §7.1) and the in-process
delegation runtime (SPEC §4.2–4.3). Do not use LangGraph, CrewAI, AutoGen,
LlamaIndex agents, or Semantic Kernel.
Because: SPEC §1.1 bans frameworks used as black boxes. Antara has to
present and defend every line live, so the control flow must be readable
top-to-bottom. The "agentic-ness" is a tool-use loop with typed envelopes
— a framework adds abstraction without buying us anything at three agents.
Alternatives considered: LangGraph (opaque state machine, heavy install);
CrewAI (role abstractions misalign with our capability-ownership rule).
Trade-off / known downside: We re-implement budgets, retries, validation,
tracing. All intentional — see SPEC §4.3 and §10.

## 2. No Redux / Zustand — 2026-11-09
Decision: Server state via TanStack Query; live run state via `useReducer`
+ pure `runReducer` (SPEC §11.3); bid draft state via pure `bidDraftReducer`.
Because: State is either server cache (fetched + revalidated) or scoped
to one page. A global store adds ceremony with no payoff. Pure reducers
are trivially testable and match the "stream → state is a pure function"
invariant we want to defend in the interview.
Alternatives considered: Zustand (cheap but still a global); Redux
Toolkit (overkill at this scale).
Trade-off / known downside: If future features need cross-page shared
live state, we'll add Zustand then, not now.

## 3. Pinned frontend versions — 2026-11-09
Decision: React 19.3.1, TypeScript 6.0.3, Vite 8.3.7, Tailwind CSS 4.3.5
(+ `@tailwindcss/vite` 4.3.5), Vitest 5.1.0, ESLint 10.14.0 (flat config),
typescript-eslint 8.72.2, `@testing-library/react` 16.3.3,
`@testing-library/jest-dom` 7.0.1, jsdom 31.0.0. Node `>=22` (`.nvmrc` = 22).
Because: These are the current npm `latest` dist-tags (verified
2026-11-09) and the combination shadcn CLI 4.23 happily initializes
against. Tailwind v4 is the current shadcn default, so we use its Vite
plugin (`@import "tailwindcss"` in CSS, no `tailwind.config.ts` needed
for defaults, no PostCSS config). ESLint flat config (`eslint.config.js`)
is required at ESLint 9+.
Alternatives considered: Tailwind v3 LTS (slower upgrade later); pre-pin
to an older React 18 (shadcn supports it but current defaults target 19);
TypeScript 7.0.2 (current npm `latest`, but blocked — see below).
Trade-off / known downside: Tailwind v4 config file lands in Phase 6
when shadcn init writes it; until then our tokens.css is a one-liner.

### 3a. TypeScript pin (6.0.3 vs latest 7.0.2) — 2026-11-09
`typescript-eslint@8.72.2` declares peer `typescript@">=4.8.4 <6.1.0"`.
TS 7.0.2 is npm `latest` but the lint tooling has not released a
compatible version yet (no `@next` or `@rc` tag for TS 7 support as of
2026-11-09). We pin to TS 6.0.3 — the top of the accepted range,
current stable, and a tiny delta from 7 for our usage (strict type
checking, bundler module resolution). Revisit when
`typescript-eslint@>=9` ships.
Trade-off: We're one major version behind on TS until the lint
ecosystem catches up. Acceptable because our code doesn't use any
TS 7-only syntax.

## 4. Model IDs from the live key — 2026-11-09
Decision: Use the SPEC §2 defaults — `claude-sonnet-5-5` for Orchestrator
and Analyst (vision); `claude-haiku-5-5` for Analyst text-only and
Pricing. Confirmed via `GET /v1/models` on 2026-11-09 against the real
key — both IDs are visible to this account.
Because: SPEC §2 chose Sonnet 5.5 for orchestrator judgment and Haiku 5.5
for the narrow tool-heavy roles; the models exist and are the latest in
their tier (Sonnet 5.5 released 2026-09-28, Haiku 5.5 released 2026-10-07).
Alternatives considered: `claude-opus-5-5` (stronger judgment, higher
latency + cost — kept as a tunable in `.env` if orchestrator quality
disappoints during Phase 5 real-run testing). `claude-fable-*` is in the
account but undocumented to us; not using it for a core agent role.
Trade-off / known downside: If Sonnet 5.5 is noisy on complex RFPs,
switching the orchestrator to Opus 5.5 is a one-line `.env` edit — record
the switch here as a superseding entry.

## 5. DEMO_FAULTS_ENABLED default off — 2026-11-09
Decision: `Settings.demo_faults_enabled` defaults to `False` in code.
`.env.example` keeps `DEMO_FAULTS_ENABLED=true` so a fresh dev clone has
the demo path live from first run.
Because: Production (where no `.env` exists and env vars are set
explicitly) should never accept failure-injection inputs unless someone
opts in. SPEC §14's literal example has it at `true` but the intent is
"on for the demo, off in prod" — the config default now encodes that
intent instead of leaving it to operator discipline.
Trade-off: If someone runs the backend in prod and forgets to copy
`.env.example` or set the var, the UI's fault-controls panel won't
appear — intended behaviour, but worth documenting so the absence
isn't mistaken for a bug.

## 6. ANTHROPIC_API_KEY optional at import — 2026-11-09
Decision: `Settings.anthropic_api_key` is `str | None` and defaults to
`None`. Code paths that hit the real API must call
`config.require_anthropic_key()` which raises a clear `RuntimeError`.
Because: Tests, `make setup`, and `make samples` should run without a
real key. `LLM_MODE=fake` is the test/e2e path; failing at import would
break CI.
Alternatives considered: Require the key at import (simpler but blocks
tests). Lazy getter inside `AnthropicLLM.__init__` (same effect but
hides the invariant from readers).
Trade-off / known downside: If someone forgets to set the key before a
real run, they'll see the failure one call in, not at startup — the
error message is explicit about adding it to `.env`.

---

## Branch-local entries (`frontend-shell`)

Numbered `F1, F2, …` to avoid a merge clash with `main`. When this
branch lands we'll renumber these as `7, 8, …` (or whatever's next)
and fold them into the shared sequence.

## F1. Temporary `src/dev-fixtures/` with local `Fx*` types — 2026-11-10
Decision: The Phase-6 UI shell consumes typed fixtures from
`frontend/src/dev-fixtures/` with locally-declared `Fx*` types instead
of `components["schemas"][*]` from `src/api/schema.gen.ts`.
Because: `schema.gen.ts` is generated from the backend OpenAPI, which
doesn't exist on this branch — Phases 2–5 haven't landed here. Blocking
on them would push frontend work past day 3 and violate SPEC §15's
"balance rule" that frontend starts no later than day 3.
This exception violates CLAUDE.md §3's rule that "types for API data
come only from `schema.gen.ts`". It is contained by:
  - `src/dev-fixtures/README.md` lists exit criteria.
  - ESLint `no-restricted-imports` scopes `@/dev-fixtures/*` (alias
    and relative) to `src/features/**` and `tests/**` only. Verified
    by `tests/fixtures-import-rule.test.ts`.
  - All types are prefixed `Fx*` so an accidental import outside
    those paths is grep-able.
Alternatives considered: Mock backend with hand-written OpenAPI JSON
(more upfront work than fixtures, same risk of drift). Build the shell
on `main` after Phase 5 lands (loses parallelism). Let pages
hand-write API interfaces (same rule violation with no exit criteria).
Exit: Phase 6 proper on `main` runs `make gen-api`, pages swap to
`useRuns()`/`useBranches()` hooks on `src/api/client.ts`, and a single
commit deletes `src/dev-fixtures/` and flips the ESLint rule to a hard
ban. Mark this entry superseded then.

## F2. Design tokens: semantic `-text` variants + WCAG contrast test — 2026-11-10
Decision: Keep the bright semantic colors (`--color-done-green`,
`--color-warn-amber`, etc.) for icons and dots only. Add darker
`--color-done-green-text`, `--color-warn-amber-text`, … variants for
text in light mode, and remap them to lighter shades in `.dark` so
both themes hit WCAG AA (≥ 4.5:1). Keep `--primary = indigo-600` and
`--destructive = #dc2626` in both themes — white on indigo-500 and
white on red-500 fail AA (~4.2 and ~3.8). Light-mode
`--muted-foreground = zinc-600` (not zinc-500) on `--muted = zinc-100`.
Because: SPEC §11.5 calls out WCAG AA and "never color alone". Shipping
bright green/amber as text would quietly fail AA — the kind of thing a
demo reviewer spots instantly.
How to enforce: `tests/tokens-contrast.test.ts` parses tokens.css,
resolves every `var(--…)` reference, and asserts every shadcn fg/bg
slot + every semantic `-text` on `--background`/`--card`/`--muted`
against 4.5:1 in both modes. A failing self-test proves the checker
itself can fail. 48 assertions in all.
Trade-off: Two semantic shades per color instead of one; developers
pick `*-text` for text, bright for dots/icons. The test catches drift
automatically so this won't silently decay.

## F3. Hand-written shadcn setup + tokens.css overwrite guard — 2026-11-10
Decision: Write `components.json` by hand (not via `shadcn init`
prompts) with `tailwind.config = ""` (Tailwind v4, no config file),
`tailwind.css = src/styles/tokens.css`, `tsx = true`, `rsc = false`,
`baseColor = zinc`, `cssVariables = true`, `style = new-york`,
`iconLibrary = lucide`. Treat `src/styles/tokens.css` as ours — the
shadcn CLI must never rewrite its color blocks. After every `shadcn
init`/`add`, `git diff src/styles/tokens.css` must show either no
change or a single `@import` addition; the contrast test must still
pass. If the CLI ever injects its own `:root`/`.dark`/`@theme` (e.g.
oklch defaults), revert those lines.
Because: shadcn's generated `:root`/`.dark` use `oklch(...)` and
different slot shapes than ours. Letting it write over the tokens would
unseat the WCAG assertions in F2 and the Linear-reference palette in
SPEC §11.5.
Alternatives considered: Run `shadcn init` interactively and manually
revert (fragile, easy to miss); fork shadcn into vendored primitives
(too much maintenance).
Trade-off: Every future `shadcn add` needs the diff check. Mitigation:
scripted into the phase-summary workflow.

## F4. Sonner theme reads `.dark` from `<html>` — 2026-11-10
Decision: Rewrote `src/components/ui/sonner.tsx` to drop `next-themes`
(shadcn's default). The component reads `document.documentElement`'s
class list directly and a `MutationObserver` keeps it in sync when
`.dark` toggles.
Because: We already own the dark-mode source of truth — an inline
script in `index.html` sets `.dark` from `prefers-color-scheme` before
React mounts, and `app/theme.ts` keeps it in sync after. Adding
`next-themes` just to feed one toast theme would be a provider for a
one-call hook.
Trade-off: One shadcn primitive diverges from the stock output.
Documented at the top of `sonner.tsx`.

## F5. `tw-animate-css` as the Tailwind v4 animation source — 2026-11-10
Decision: Install `tw-animate-css` and add one `@import "tw-animate-css";`
near the top of `src/styles/tokens.css`. Do NOT install
`tailwindcss-animate` (Tailwind v3-only plugin).
Because: shadcn primitives (dialog, dropdown, popover, tooltip) emit
`animate-in`, `fade-in-0`, `zoom-in-95`, etc. — classes provided by
`tailwindcss-animate` under v3 and by `tw-animate-css` under v4. Without
it, open/close transitions snap instead of easing.
Trade-off: This is the single acceptable addition to tokens.css
mentioned in F3. Documented in that entry's overwrite guard.

---

## Superseded notes

### #3 Pinned frontend versions — superseded 2026-11-10 by F3, F5
- `.nvmrc` bumped from `24.22.2` to `26` (we're on Node 26.13.0);
  `package.json` keeps `engines.node >= 24.15`.
- `components.json` is **hand-written**, not generated — see F3.
- Confirmed: no `tailwind.config.ts` under Tailwind v4; all theme
  tokens live in `src/styles/tokens.css` via `@theme` / `@theme inline`.
- `tw-animate-css` added (not `tailwindcss-animate`) — see F5.
