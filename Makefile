UV_ENV = UV_CACHE_DIR=/tmp/commerce-trace-uv-cache
PYTEST_ENV = PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 PYTHONPATH=..

.PHONY: sync init backend frontend test typecheck lint frontend-test build evaluate replay-memory ablation

sync:
	cd backend && $(UV_ENV) uv sync --extra test --extra data
	cd frontend && npm install

init:
	$(UV_ENV) uv run --project backend commerce-trace init --profile test --if-empty

backend:
	$(UV_ENV) uv run --project backend uvicorn commerce_trace.api:app --reload --port 8000

frontend:
	cd frontend && npm run dev

test:
	cd backend && $(PYTEST_ENV) $(UV_ENV) uv run pytest -p pytest_asyncio.plugin tests ../data_generator/test_generate.py
	cd frontend && npm test

typecheck:
	cd backend && $(UV_ENV) uv run mypy src/commerce_trace
	cd frontend && npm run typecheck

lint:
	cd backend && $(UV_ENV) uv run ruff check src tests ../data_generator

frontend-test:
	cd frontend && npm test

build:
	cd frontend && npm run build

evaluate:
	$(UV_ENV) uv run --project backend commerce-trace evaluate

replay-memory:
	$(UV_ENV) uv run --project backend commerce-trace replay-memory

ablation:
	$(UV_ENV) uv run --project backend commerce-trace ablation
