set dotenv-load := false

default:
    @just --list

# Install the venv and apply migrations to the local database
setup:
    uv sync
    FOUNDRY_ENV=local uv run alembic upgrade head

# Serve the UI and API on :8000; runs are carried out in this process
dev:
    FOUNDRY_ENV=local uv run uvicorn foundry.server.server:app --reload --reload-dir src --port 8000

# Migrate the real database in .env
migrate:
    uv run alembic upgrade head

# Migrate the local SQLite database in .env.local
migrate-local:
    FOUNDRY_ENV=local uv run alembic upgrade head

# Generate a migration after changing src/foundry/app/db/models
revision message:
    FOUNDRY_ENV=local uv run alembic revision --autogenerate -m "{{ message }}"

# Publish the keys in .env and .env.shared as Foundry's named Modal secrets (dev environment only)
secrets:
    uv run python -m foundry.worker secrets

# Deploy the Modal app (dev environment only); run `just secrets` first
deploy:
    uv run python -m foundry.worker deploy

format *paths=".":
    uv run ruff format {{ paths }}
    uv run ruff check --fix {{ paths }}

check *paths=".":
    uv run ruff check {{ paths }}
    uv run ruff format --check {{ paths }}

lint-py:
    uv run ty check

test *args:
    FOUNDRY_ENV=local uv run pytest {{ args }}

# Stress and failure scenarios for the live crawl, against a fake portal on this machine (`--list` names them)
stress *names:
    uv run python -m stress.run {{ names }}

# Lean proofs and planted-bug checks, on Modal
prove:
    cd formal/foundry_rules && ~/.agents/skills/formal-verification/scripts/formal lean && ~/.agents/skills/formal-verification/scripts/formal mutate FoundryRules.mutations

# TLA+ models, on Modal
model:
    cd formal/runs && ~/.agents/skills/formal-verification/scripts/formal check Runs.tla
