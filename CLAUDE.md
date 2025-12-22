# KptnCook CLI - Project Guide for Claude

## Project Overview

KptnCook is a Python command-line client for downloading recipes from the KptnCook cooking app. It can fetch daily recipes, backup favorites, and sync with Mealie (a self-hosted recipe manager) or export to Paprika.

**Project Status**: Pre-alpha, slightly unmaintained (looking for maintainers)

## Key Features

1. **Download daily recipes** from KptnCook API
2. **Backup favorite recipes** (requires KptnCook account)
3. **Sync with Mealie** (self-hosted recipe manager)
4. **Export to Paprika** app format
5. **Search recipes by ID** or sharing URL
6. **Local JSON storage** of recipes

## Architecture

### Core Components

- **`src/kptncook/__init__.py`**: Main CLI entry point using Typer
- **`src/kptncook/api.py`**: KptnCook API client for fetching recipes
- **`src/kptncook/models.py`**: Pydantic models for KptnCook recipe data
- **`src/kptncook/mealie.py`**: Mealie API client and recipe conversion
- **`src/kptncook/paprika.py`**: Paprika export functionality
- **`src/kptncook/repositories.py`**: Local JSON storage repository
- **`src/kptncook/config.py`**: Settings management with Pydantic

### Data Flow

1. **Fetch**: KptnCook API → Pydantic models
2. **Store**: Models → Local JSON repository (`~/.kptncook/kptncook.json`)
3. **Sync**: Local repository → Mealie API or Paprika export

## Development Setup

### Dependencies

- Python >=3.10
- Main libs: httpx, pydantic, typer, rich
- Dev tools: pytest, mypy, pre-commit, jupyterlab
- Build tool: uv

### Installation

```bash
# Clone and setup dev environment
uv sync

# Install pre-commit hooks
uv run pre-commit install
```

### Quality Gates (Required)

All of `just lint`, `just typecheck`, and `just test` must pass before declaring work done.

```bash
just lint
just typecheck
just test
```

### Running Tests (Direct)

```bash
uv run pytest
```

### Code Style

- Uses Ruff for linting and formatting (via pre-commit or `just lint`)
- Type hints encouraged
- Follow existing patterns in codebase

## Environment Configuration

Create `~/.kptncook/.env`:

```env
KPTNCOOK_API_KEY=6q7QNKy-oIgk-IMuWisJ-jfN7s6
KPTNCOOK_ACCESS_TOKEN=<your-token>  # Optional, for favorites
MEALIE_URL=https://your-mealie-instance/api
MEALIE_USERNAME=username
MEALIE_PASSWORD=password
```

## API Keys & Authentication

- **KPTNCOOK_API_KEY**: Required, hardcoded in examples
- **KPTNCOOK_ACCESS_TOKEN**: For accessing favorites (use `kptncook kptncook-access-token` command)
- **Mealie credentials**: For syncing recipes

### Password Manager Integration

The `kptncook-access-token` command supports retrieving credentials from password managers:

- **KPTNCOOK_USERNAME_COMMAND**: Shell command to retrieve username
- **KPTNCOOK_PASSWORD_COMMAND**: Shell command to retrieve password

Examples:
```bash
# 1Password
KPTNCOOK_USERNAME_COMMAND="op read op://Personal/KptnCook/username"
KPTNCOOK_PASSWORD_COMMAND="op read op://Personal/KptnCook/password"

# pass (password-store)
KPTNCOOK_USERNAME_COMMAND="pass show kptncook/username"
KPTNCOOK_PASSWORD_COMMAND="pass show kptncook/password"
```

## Common Tasks

### Adding New Features

1. Check existing patterns in similar modules
2. Use Pydantic for data validation
3. Add CLI command in `__init__.py`
4. Write tests in `tests/`
5. Update type hints
6. Update `CHANGELOG.md` for user-visible changes

### Debugging

- Jupyter notebooks in `notebooks/` for exploration
- Rich library for pretty printing
- Check `~/.kptncook/kptncook.json` for stored data

### Testing Approach

- pytest with fixtures in `conftest.py`
- Mock HTTP requests when testing API clients
- Test data in `tests/fixtures/`

## Beads and Beadsflow

- Beads is the issue tracker; `.beads/` is committed.
- Onboard with `bd onboard` (fallback: `bd init` + `bd hooks install`).
- If your global gitignore ignores `.beads/`, remove `**/.beads/` or use `git add -f`.
- Use local beadsflow: `uv run --project ../beadsflow beadsflow run <epic-id> ...` or the `just` helpers.
- Comment markers must start with one of: `Ready for review:`, `LGTM`, `Changes requested:`.

## Commits and Push

Do not run `git commit`, `git push`, or `bd sync` unless the user explicitly asks.

## Landing the Plane (Session Completion)

**When ending a work session**, you MUST complete ALL steps below. Work is NOT complete until `git push` succeeds.

**MANDATORY WORKFLOW:**

1. **File issues for remaining work** - Create issues for anything that needs follow-up
2. **Run quality gates** (if code changed) - Tests, linters, builds
3. **Update issue status** - Close finished work, update in-progress items
4. **PUSH TO REMOTE** - This is MANDATORY:
   ```bash
   git pull --rebase
   bd sync
   git push
   git status  # MUST show "up to date with origin"
   ```
5. **Clean up** - Clear stashes, prune remote branches
6. **Verify** - All changes committed AND pushed
7. **Hand off** - Provide context for next session

**CRITICAL RULES:**
- Work is NOT complete until `git push` succeeds
- NEVER stop before pushing - that leaves work stranded locally
- NEVER say "ready to push when you are" - YOU must push
- If push fails, resolve and retry until it succeeds

## Important Considerations

1. **API Limitations**: Only fetches today's recipes (limitation acknowledged in README)
2. **Data Storage**: All recipes stored in single JSON file
3. **Mealie Sync**: Only adds new recipes, doesn't update existing
4. **Error Handling**: Some recipe parsing errors are caught and logged

## Publishing

```bash
# Run tests first
uv run pytest

# Publish to PyPI
uv publish --token your_token
```

## Notes for Contributors

- Project needs maintainers
- Focus on stability over new features
- Preserve backward compatibility
- Document any API discoveries
- Consider migration to async httpx for better performance
