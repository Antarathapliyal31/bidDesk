/**
 * Project-wide input limits. These mirror the backend `Settings`
 * fields in `backend/app/config.py`:
 *   - `max_upload_mb` (default 20)
 *   - `max_pages` (default 60)
 *   - `max_text_chars` (default 50_000)
 *
 * `MAX_UPLOAD_BYTES = MB × 1024 × 1024` (binary MB). The backend
 * enforcer lives in `api/runs.py` — a **Phase 5** deliverable
 * (SPEC §6.1 top line + §15 "Phase 5. Orchestrator + API: all
 * endpoints"). Phase 3 is the Document Analyst agent itself; its
 * `agents/analyst/routing.py` does per-page routing but not the
 * upload-size / page-count guardrails. If the Phase 5 code ever
 * switches to decimal MB × 1000 × 1000, a ~20 MB file could pass
 * the client and fail the server — keep them in lockstep.
 *
 * `MAX_TEXT_CHARS` is compared against **code-point length** on both
 * sides (Python's `len(s)` counts code points, not UTF-16 units) —
 * use `codePointLength()` from `features/new-bid/validation.ts`.
 *
 * TODO (Phase 7 / §13): once `/api/meta` returns live limits, read
 * them from there and treat these as fallbacks only.
 */
export const MAX_UPLOAD_MB = 20;
export const MAX_UPLOAD_BYTES = MAX_UPLOAD_MB * 1024 * 1024;
export const MAX_PAGES = 60;
export const MAX_TEXT_CHARS = 50_000;
