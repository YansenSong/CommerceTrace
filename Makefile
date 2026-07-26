UV_ENV = UV_CACHE_DIR=/tmp/commerce-trace-uv-cache
PYTEST_ENV = PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 PYTHONPATH=..

.PHONY: sync test typecheck lint frontend-test build up down evaluate replay-memory ablation

sync:
	cd backend && $(UV_ENV) uv sync --extra test --extra data --extra memory
	cd frontend && npm install

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

up:
	docker compose up --build

down:
	docker compose down

evaluate:
	$(UV_ENV) uv run --project backend commerce-trace evaluate

replay-memory:
	$(UV_ENV) uv run --project backend commerce-trace replay-memory

ablation:
	$(UV_ENV) uv run --project backend commerce-trace ablation
