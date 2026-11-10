# WALKTHROUGH

One line per important file: *what it is and why it exists*, in plain
English. Used by Antara during the live presentation to narrate the
code. Append entries as files land.

## Repo root
- `CLAUDE.md` — how I work on this repo (process, golden rules, phase gates).
- `SPEC.md` — single source of truth for what BidDesk does and why.
- `Makefile` — one command for every workflow (setup, test, dev, gen-api).
  Antara runs these live; keeps the demo reliable.
- `.env.example` — the full config surface. Copy to `.env`; `.env` is
  gitignored so secrets never ship.
- `.gitignore` — excludes secrets, venv, node_modules, seeded DBs,
  uploads, editor + OS files.

## docs/
- `DECISIONS.md` — ADR-lite. One entry per non-obvious decision so
  interviewers can ask "why this, not that" and I have the answer written
  down.
- `WALKTHROUGH.md` — this file.

## backend/
- `pyproject.toml` — Python package metadata, pinned dep ranges, ruff
  + pytest config. `requires-python = ">=3.11"` so async + PEP 604
  union types just work.
- `app/__init__.py` — package marker. Empty.
- `app/main.py` — FastAPI app factory. Phase 0 exposes `/api/health`;
  the real lifespan (DB init, mark interrupted runs) comes in Phase 2.
- `app/config.py` — `Settings` via `pydantic-settings`. Reads `.env`;
  `ANTHROPIC_API_KEY` is optional at import time so tests and
  `make setup` work without it. `require_anthropic_key()` is the choke
  point that fails loudly when a real call is attempted without a key.
- `tests/test_smoke.py` — Phase 0 smoke: the app boots, health returns
  ok, and the key-optional contract works.

## frontend/
- `package.json` — pinned deps (versions chosen for shadcn 4.23
  compatibility, see DECISIONS #3); `engines.node >= 22`.
- `.nvmrc` — pins Node 24.22.2 (satisfies jsdom@31's `>=24.15.0`); `engines.node >= 24.15`.
- `tsconfig.json` — strict TypeScript: `strict`, `noUnusedLocals`,
  `noUncheckedIndexedAccess`, `exactOptionalPropertyTypes`. Catches most
  bugs at `tsc`-time before they hit the UI.
- `vite.config.ts` — Vite + React + Tailwind v4 plugin. Dev server
  proxies `/api` to the backend on :8000; Vitest config lives here too.
- `eslint.config.js` — ESLint 9 flat config. Uses the `globals` package
  (not the old boolean form) for browser/node globals.
- `index.html` — Vite entry; mounts `#root`.
- `src/main.tsx` — React bootstrap; imports `tokens.css` so Tailwind
  loads on every page.
- `src/App.tsx` — Phase 0 placeholder. Real `AppShell` + router land in
  Phase 6.
- `src/styles/tokens.css` — Tailwind v4 entry (`@import "tailwindcss"`).
  Real design tokens (Inter, 4px grid, zinc scale, indigo accent) land
  in Phase 6 per SPEC §11.5.
- `tests/setup.ts` — loads `@testing-library/jest-dom/vitest` so
  `toBeInTheDocument()` and the other matchers work in every test.
- `tests/smoke.test.tsx` — Phase 0 smoke: App renders.
