# Justfile for kptncook project development

# Default recipe - show available commands
default:
    @just --list

# Install Python dependencies via uv
install:
    uv sync

# Run the full test suite
test:
    uv run pytest

# Run a specific test (pass path or node id)
test-one TARGET:
    uv run pytest {{TARGET}} -v

# Run type checks with mypy
typecheck:
    uv run mypy src

# Lint and format with Ruff
lint:
    uv run ruff format .
    uv run ruff check .

# Remove build artifacts
clean-build:
    rm -fr build/
    rm -fr dist/
    rm -fr *.egg-info

# Remove Python file artifacts
clean-pyc:
    find . \( -path "./.venv" -o -path "./.beads" -o -path "./.git" \) -prune -o -name '*.pyc' -exec rm -f {} +
    find . \( -path "./.venv" -o -path "./.beads" -o -path "./.git" \) -prune -o -name '*.pyo' -exec rm -f {} +
    find . \( -path "./.venv" -o -path "./.beads" -o -path "./.git" \) -prune -o -name '*~' -exec rm -f {} +

# Remove all build and Python artifacts
clean: clean-build clean-pyc

# Beadsflow autopilot helpers (local checkout)
beadsflow-dry EPIC:
    uv run --project ../beadsflow beadsflow run {{EPIC}} --dry-run --verbose

beadsflow-once EPIC:
    uv run --project ../beadsflow beadsflow run {{EPIC}} --once --verbose

beadsflow-run EPIC:
    uv run --project ../beadsflow beadsflow run {{EPIC}} --interval 30 --verbose

# Import GitHub issues into Beads epics
beads-import-gh-issues *ARGS:
    uv run python scripts/import_github_issues_to_beads.py {{ARGS}}
