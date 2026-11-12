.PHONY: setup seed samples samples-fe check-samples dev dev-backend dev-frontend test test-backend test-frontend lint check gen-api check-contract clean-db clean help

PYTHON ?= python3.11
VENV := backend/.venv
PIP := $(VENV)/bin/pip
PY := $(VENV)/bin/python

help:
	@echo "BidDesk make targets:"
	@echo "  setup           create venv + install backend[dev] + npm install"
	@echo "  seed            build backend/data/catalog.db (Phase 1)"
	@echo "  samples         generate samples/*.pdf (Phase 1) + mirror to frontend/public/samples/"
	@echo "  samples-fe      mirror samples/*.pdf + list_paste.txt → frontend/public/samples/"
	@echo "  check-samples   fail if frontend/public/samples/ drifts from samples/ (byte-exact)"
	@echo "  dev             run backend (:8000) and frontend (:5173) together"
	@echo "  test            run backend pytest + frontend vitest"
	@echo "  lint            ruff check + ruff format --check + eslint"
	@echo "  check           lint + check-samples + test (CI-equivalent sweep)"
	@echo "  gen-api         regen frontend/openapi.json + src/api/schema.gen.ts (Phase 6)"
	@echo "  check-contract  fail if generated contract files are stale (Phase 6)"
	@echo "  clean-db        delete backend/data/app.db (run after schema changes)"
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
	cd backend && ../$(VENV)/bin/python -m app.catalog.seed

samples:
	$(VENV)/bin/python samples/make_samples.py
	@$(MAKE) --no-print-directory samples-fe

# Phase-7 only. Mirrors the backend's generated sample files into
# frontend/public/samples/ so the New-bid "Start from a sample" cards
# can fetch them offline. Removed at Phase 5 merge when
# `GET /api/samples/{key}/file` lands.
samples-fe:
	@mkdir -p frontend/public/samples
	@cp samples/rfp_clean.pdf    frontend/public/samples/rfp_clean.pdf
	@cp samples/rfp_messy.pdf    frontend/public/samples/rfp_messy.pdf
	@cp samples/rfp_scanned.pdf  frontend/public/samples/rfp_scanned.pdf
	@cp samples/list_paste.txt   frontend/public/samples/list_paste.txt
	@echo "samples-fe: 4 files mirrored into frontend/public/samples/"

# Phase-7 only. Byte-exact drift check so a stale checkout fails loudly
# instead of serving a different sample than the backend produced.
# `cmp -s` exits 0 only when files are identical.
check-samples:
	@set -e; \
	for f in rfp_clean.pdf rfp_messy.pdf rfp_scanned.pdf list_paste.txt; do \
	  if ! cmp -s samples/$$f frontend/public/samples/$$f; then \
	    echo "check-samples: $$f drifts between samples/ and frontend/public/samples/ — run 'make samples-fe'"; \
	    exit 1; \
	  fi; \
	done
	@echo "check-samples: 4 files match."

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

check: lint check-samples test

gen-api:
	@cd backend && ../$(VENV)/bin/python -c "import json; from app.main import create_app; print(json.dumps(create_app().openapi(), indent=2))" > ../frontend/openapi.json
	cd frontend && ./node_modules/.bin/openapi-typescript openapi.json -o src/api/schema.gen.ts
	@echo "gen-api: wrote frontend/openapi.json + src/api/schema.gen.ts"

check-contract:
	@set -e; \
	cd backend && ../$(VENV)/bin/python -c "import json; from app.main import create_app; print(json.dumps(create_app().openapi(), indent=2))" > ../frontend/openapi.json.check; \
	cd ..; \
	if ! cmp -s frontend/openapi.json frontend/openapi.json.check; then \
	  rm -f frontend/openapi.json.check; \
	  echo "check-contract: frontend/openapi.json is stale — run 'make gen-api'"; exit 1; \
	fi; \
	rm -f frontend/openapi.json.check; \
	cd frontend && ./node_modules/.bin/openapi-typescript openapi.json -o src/api/schema.gen.ts.check 2>/dev/null; \
	if ! cmp -s src/api/schema.gen.ts src/api/schema.gen.ts.check; then \
	  rm -f src/api/schema.gen.ts.check; \
	  echo "check-contract: src/api/schema.gen.ts is stale — run 'make gen-api'"; exit 1; \
	fi; \
	rm -f src/api/schema.gen.ts.check; \
	echo "check-contract: contract files match."

# Delete the runtime run DB so the next backend start rebuilds
# it from the current schema.sql. Needed any time a CHECK
# constraint or table shape changes — `CREATE IF NOT EXISTS`
# won't retrofit an existing file. The seeded catalog.db is
# left alone; rerun `make seed` if you also want to rebuild it.
clean-db:
	rm -f backend/data/app.db backend/data/app.db-shm backend/data/app.db-wal
	@echo "clean-db: removed backend/data/app.db (+ WAL sidecars if any)."

clean:
	rm -rf $(VENV) frontend/node_modules frontend/dist \
	       backend/.pytest_cache backend/.ruff_cache \
	       $(shell find backend -type d -name __pycache__)
