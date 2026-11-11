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

## 7. PT framing seeded only in SYP (East Coast realism) — 2026-11-10
Decision: Pressure-treated dimensional lumber is seeded exclusively in
SYP (Southern Yellow Pine). SPF is seeded untreated only. The catalog
has no SPF-PT rows.
Because: Real LBM distributors on the East Coast stock PT lumber in SYP
because SYP's open cell structure accepts the treatment chemistry; SPF
is kiln-dried and generally untreated. A demo defending every
non-obvious choice to an interviewer can't have SPF-PT on the shelf.
Alternatives considered: Seed SPF-PT too — simplifies the species
defaults playbook (always SPF) but misrepresents the industry.
Trade-off / known downside: The ambiguous-PT example doesn't need a
species assumption anymore (catalog has one option; no choice was
made). This is codified by the new `assumption_discipline` playbook —
assumptions are reserved for defensible judgment calls between real
catalog alternatives, not for filling in fields the catalog doesn't
offer alternatives for. See `backend/app/catalog/semantic/examples.yaml`
example #1 and `playbooks.yaml.assumption_discipline`.
