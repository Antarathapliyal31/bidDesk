# BidDesk

Atreyus AI second-round take-home. An LBM distributor's estimator uploads an
RFP (PDF or pasted list), picks a branch, and watches three agents produce a
priced, editable bid with every line traceable to its source page.

Core principle: **the LLM decides, the code guarantees.**

The authoritative spec is [`SPEC.md`](./SPEC.md). Process rules are in
[`CLAUDE.md`](./CLAUDE.md). This README is the Phase-0 stub — the full
version (architecture diagram, trade-offs, failure-injection demo,
"what's broken") is written in Phase 9.

## Quickstart

Prerequisites: Python 3.11+ and Node 24.15+ (`.nvmrc` pins 24.22.2 — `nvm install && nvm use` works).

```bash
cp .env.example .env
# paste your ANTHROPIC_API_KEY into .env
make setup      # creates backend/.venv, installs backend[dev] + frontend deps
make test       # runs backend pytest + frontend vitest
make dev        # backend on :8000, frontend on :5173 (proxies /api -> :8000)
```

Catalog prices in the seeded DB are **synthetic**. No real brand names are used.
