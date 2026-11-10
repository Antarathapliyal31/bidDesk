.PHONY: setup seed samples dev dev-backend dev-frontend test test-backend test-frontend lint gen-api check-contract clean help

PYTHON ?= python3.11
VENV := backend/.venv
PIP := $(VENV)/bin/pip
PY := $(VENV)/bin/python

help:
	@echo "BidDesk make targets:"
	@echo "  setup           create venv + install backend[dev] + npm install"
	@echo "  seed            build backend/data/catalog.db (Phase 1)"
	@echo "  samples         generate samples/*.pdf (Phase 1)"
	@echo "  dev             run backend (:8000) and frontend (:5173) together"
	@echo "  test            run backend pytest + frontend vitest"
	@echo "  lint            ruff check + eslint"
	@echo "  gen-api         regen frontend/openapi.json + src/api/schema.gen.ts (Phase 6)"
	@echo "  check-contract  fail if generated contract files are stale (Phase 6)"
	@echo "  clean           remove venv, node_modules, caches"

setup: $(VENV)/bin/python frontend/node_modules
	@echo "setup complete."

$(VENV)/bin/python:
	$(PYTHON) -m venv $(VENV)
	$(PIP) install -U pip wheel
	$(PIP) install -e "backend[dev]"

frontend/node_modules: frontend/package.json
	cd frontend && npm install

seed:
	@echo "seed: not yet implemented (Phase 1)."

samples:
	@echo "samples: not yet implemented (Phase 1)."

dev-backend:
	cd backend && ../$(VENV)/bin/uvicorn app.main:app --reload --port 8000

dev-frontend:
	cd frontend && npm run dev

# Run both in parallel; Ctrl-C stops both.
dev:
	@$(MAKE) -j 2 dev-backend dev-frontend

test: test-backend test-frontend

test-backend:
	cd backend && ../$(VENV)/bin/pytest -q

test-frontend:
	cd frontend && npm test -- --run

lint:
	cd backend && ../$(VENV)/bin/ruff check . && ../$(VENV)/bin/ruff format --check .
	cd frontend && npm run lint

gen-api:
	@echo "gen-api: not yet implemented (Phase 6)."

check-contract:
	@echo "check-contract: not yet implemented (Phase 6)."

clean:
	rm -rf $(VENV) frontend/node_modules frontend/dist \
	       backend/.pytest_cache backend/.ruff_cache \
	       $(shell find backend -type d -name __pycache__)
