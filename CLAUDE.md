# CLAUDE.md — How to work on BidDesk

You are building BidDesk, Antara's take-home for Atreyus AI. **Read `SPEC.md` fully at the start of every session.** SPEC.md says *what* to build; this file says *how* to work. Antara must present and defend every line of this code live, so clarity beats cleverness.

## 1. Golden rules
1. **Never commit or push without Antara's explicit "yes" in this session.** At the end of each phase: run all checks, show a summary and the proposed commit message, then wait. Never `git push`, never force-push, never rewrite history.
2. **One phase at a time** (SPEC §15). At the start of a phase, post a short plan (files to create/change, tests to write) and wait for "go". Do not start the next phase without being asked.
3. **When SPEC.md is ambiguous or seems wrong, stop and ask.** Do not guess. If you make a judgment call that SPEC doesn't cover, add an entry to `docs/DECISIONS.md` and mention it in your phase summary.
4. **The LLM decides, the code guarantees.** Prices, quantities, unit conversions, totals, confidence levels, SKU existence, quote verification, item accounting: always deterministic code, never the model.
5. **No agent frameworks** (LangGraph, CrewAI, AutoGen, LlamaIndex agents, Semantic Kernel, etc.). The orchestration loop is hand-written per SPEC §7.1.
6. **No raw SQL from any model.** Agents reach data only through validated tools and the semantic-layer query builder. Identifiers come from the YAML allowlist; values are always bound parameters (`?`).
7. **Typed boundaries everywhere.** Every value crossing an agent, tool, HTTP or SSE boundary is a Pydantic model from `backend/app/contracts/`. Validate in both directions. Use `model_config = ConfigDict(extra="forbid")` on contract models.
8. **Money is integer cents.** No floats for money anywhere (Python or TS). Quantities use `Decimal` in Python, string or number in JSON per the contract.
9. **Tests are written in the same phase as the code.** No phase is done with failing or skipped tests. Unit tests never call the real Anthropic API; use `FakeLLM`.
10. **Secrets:** read `ANTHROPIC_API_KEY` from `.env`; never print it, log it, or commit `.env`. `.gitignore` must include `.env`, `backend/data/`, `node_modules/`, `__pycache__/`.

## 2. Workflow per phase
1. Re-read the relevant SPEC sections. Post the plan. Wait for "go".
2. Implement in small, reviewable steps. Prefer several small files over one large one.
3. Write tests alongside (SPEC §12 lists exactly which).
4. Run checks:
   - Backend: `cd backend && ruff check . && ruff format --check . && pytest -q`
   - Frontend (from Phase 6): `cd frontend && npm run typecheck && npm run lint && npm test -- --run`
   - Contract (from Phase 6): `make check-contract`
5. Update `docs/WALKTHROUGH.md`: one line per new important file — *what it is and why it exists* in plain English (Antara uses this to present).
6. Post the phase summary:
   - what was built (bullets), files added/changed
   - tests added and their results
   - decisions made (with DECISIONS.md entries)
   - anything not done / known issues — be honest
   - the proposed commit message
7. **Wait for Antara's approval before `git add`/`git commit`.**

Commit message format: `phase N: <short summary>` + body bullets. Keep commits per phase (or smaller if Antara asks).

## 3. Code style
### Backend (Python 3.11+)
- FastAPI + Pydantic v2 + aiosqlite. Plain SQL in `db/repo.py`; no ORM.
- Type hints on every function. `ruff` for lint + format (line length 100).
- Pure functions for checks and math (`verify.py`, `confidence.py`, `units.py`, `totals.py`, `edits.py`, `diff.py`, `gap_rules.py`): no I/O, easy to unit test.
- Every module starts with a 2–4 line docstring: what it does and why it exists.
- Wrap each delegate call, LLM call, tool handler and heavy step in `trace_span` (SPEC §10.2).
- Errors: raise specific exceptions (`BudgetExhausted`, `InvalidInput`, `AgentTimeout`…); API returns the single error shape `{error: {code, message, details?}}`.
- Anthropic client: `AsyncAnthropic(max_retries=0)`; all retries go through `runtime/retry.py`.
- Keep prompts in `prompts.py` files as constants; document-derived text must go through `llm/spotlight.py`.
- Log through the event system, not `print`.

### Frontend (React + TypeScript strict)
- Types for API data come **only** from `src/api/schema.gen.ts` (generated). Never hand-write a duplicate interface for an API shape. Regenerate with `make gen-api` after any backend contract change and commit both `openapi.json` and `schema.gen.ts`.
- Components: `components/ui` = restyled shadcn primitives (no business logic); `components/` = shared domain-agnostic pieces; `features/*` = page-local. Promote to shared only when used twice.
- Live run state lives in the pure `lib/runReducer.ts`; bid editing state in the pure `lib/bidDraftReducer.ts`. Both are unit tested. No Redux/Zustand.
- Follow the design system in SPEC §11.5 exactly: tokens in `styles/tokens.css`, Inter, 4px grid, one accent color, tabular numerals, lucide icons, restrained motion with `prefers-reduced-motion`. Never ship default-looking shadcn/Tailwind screens; every screen must look intentional.
- Every async surface has designed loading, empty and error states (SPEC §11.4). Never render `undefined`, raw JSON, or a blank screen. No spinner-only loading — use skeletons shaped like the content.
- Keyboard support per SPEC §11.5; visible focus rings; all interactive elements reachable by keyboard.

## 4. Things to never do
- Never let a model output set a price, total, stock number or confidence level.
- Never add a tool that updates or deletes `items` or writes the catalog DB.
- Never silently drop an item; every item ends up in exactly one bid section (SPEC §7.3).
- Never invent building-code section numbers. Use `(verify)` or `null` + `basis: "common practice"` (SPEC §7.4).
- Never use real brand names in the seeded catalog; prices are synthetic and the README says so.
- Never mock the Anthropic API in the demo path. `LLM_MODE=fake` is only for tests and the e2e test.
- Never hide a failure: injected or real failures must appear in the trace with their kind.
- Never add auth, billing, multi-tenancy, a vector DB, or Redis.
- Never commit generated data files (`backend/data/*.db`, uploads).

## 5. Running locally
```
make setup          # python venv + pip install -e backend[dev]; npm install in frontend
make seed           # build backend/data/catalog.db
make samples        # generate samples/*.pdf
make dev            # uvicorn on :8000 + vite on :5173 (proxy /api -> :8000)
make test           # backend + frontend tests
make gen-api        # regenerate OpenAPI + TS types
make check-contract # fail if generated contract files are stale
```

## 6. When something fails
- A test fails: fix the code or the test with a reason; never delete or skip a test to go green without telling Antara.
- A real model run behaves badly: capture the run id and the trace, describe what happened, propose a fix (prompt, schema, guardrail), and ask before tuning limits. Record tuned limits in DECISIONS.md.
- You're unsure whether something matches SPEC: ask.

## 7. Explaining your work
Antara will be asked to walk through code on both sides live. After each phase, in the summary, add a short **"How to explain this"** section: 3–6 bullets on the key idea, the main trade-off, and one likely interviewer question with a good answer.
