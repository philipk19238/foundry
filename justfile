set dotenv-load := false

default:
    @just --list

# Install the venv and apply migrations to the local database
setup:
    uv sync
    uv run alembic upgrade head

# Serve the UI and API on :8000; runs are carried out in this process
dev:
    uv run uvicorn foundry.server.server:app --reload --reload-dir src --port 8000

migrate:
    uv run alembic upgrade head

# Generate a migration after changing src/foundry/app/db/models
revision message:
    uv run alembic revision --autogenerate -m "{{ message }}"

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
    uv run pytest {{ args }}

# Lean proofs and planted-bug checks, on Modal
prove:
    cd formal/foundry_rules && ~/.agents/skills/formal-verification/scripts/formal lean && ~/.agents/skills/formal-verification/scripts/formal mutate FoundryRules.mutations

# TLA+ models, on Modal
model:
    cd formal/runs && ~/.agents/skills/formal-verification/scripts/formal check Runs.tla
