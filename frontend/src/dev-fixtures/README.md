# dev-fixtures/

**TEMPORARY — delete when `schema.gen.ts` lands in Phase 6 proper.**

This folder exists because the `frontend-shell` branch builds the
Phase 6 UI shell (tokens, AppShell, New bid, History) **before** the
backend contract is finalized. There is no `openapi.json` on this
branch yet, so there is nothing for `openapi-typescript` to generate
`src/api/schema.gen.ts` from.

Every file here uses locally-declared `Fx*` types that intentionally
mirror the expected backend shapes.

**Import scope (enforced by ESLint on this branch):**
`@/dev-fixtures/*` can only be imported from `src/features/**` page
files. `src/components/**`, `src/lib/**`, `src/app/**` and tests must
not touch fixtures — those layers should be fixture-agnostic so the
swap in Phase 6 proper is a mechanical refactor.

**Phase 6 proper flips the rule to a total ban:** the ESLint rule
becomes `patterns: ["@/dev-fixtures/*"]` with no `allow`, so a
lingering import anywhere in the tree fails CI.

## Exit criteria (Phase 6 proper on `main`)

1. `make gen-api` produces `frontend/openapi.json` +
   `frontend/src/api/schema.gen.ts`.
2. Pages switch to typed API hooks (`useRuns()`, `useBranches()`)
   built on `src/api/client.ts`.
3. This whole folder is deleted in a single commit:
   `refactor: replace dev-fixtures with generated API types`.
4. The DECISIONS.md entry #7 is marked superseded.

## Audit

Every import of `@/dev-fixtures/*` must disappear at that point.
`rg '@/dev-fixtures' frontend/src` should return no results.
