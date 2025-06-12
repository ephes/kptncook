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

- **`kptncook/__init__.py`**: Main CLI entry point using Typer
- **`kptncook/api.py`**: KptnCook API client for fetching recipes
- **`kptncook/models.py`**: Pydantic models for KptnCook recipe data
- **`kptncook/mealie.py`**: Mealie API client and recipe conversion
- **`kptncook/paprika.py`**: Paprika export functionality
- **`kptncook/repositories.py`**: Local JSON storage repository
- **`kptncook/config.py`**: Settings management with Pydantic

### Data Flow

1. **Fetch**: KptnCook API → Pydantic models
2. **Store**: Models → Local JSON repository (`~/.kptncook/kptncook.json`)
3. **Sync**: Local repository → Mealie API or Paprika export

## Development Setup

### Dependencies

- Python >=3.10
- Main libs: httpx, pydantic, typer, rich
- Dev tools: pytest, mypy, pre-commit, jupyterlab

### Installation

```bash
# Clone and setup dev environment
python -m pip install flit
flit install -s

# Install pre-commit hooks
pre-commit install
```

### Running Tests

```bash
pytest
```

### Code Style

- Uses Black formatter (via pre-commit)
- isort for imports
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

## Common Tasks

### Adding New Features

1. Check existing patterns in similar modules
2. Use Pydantic for data validation
3. Add CLI command in `__init__.py`
4. Write tests in `tests/`
5. Update type hints

### Debugging

- Jupyter notebooks in `notebooks/` for exploration
- Rich library for pretty printing
- Check `~/.kptncook/kptncook.json` for stored data

### Testing Approach

- pytest with fixtures in `conftest.py`
- Mock HTTP requests when testing API clients
- Test data in `tests/fixtures/`

## Important Considerations

1. **API Limitations**: Only fetches today's recipes (limitation acknowledged in README)
2. **Data Storage**: All recipes stored in single JSON file
3. **Mealie Sync**: Only adds new recipes, doesn't update existing
4. **Error Handling**: Some recipe parsing errors are caught and logged

## Publishing

```bash
# Run tests first
pytest

# Publish to PyPI
flit publish
```

## Notes for Contributors

- Project needs maintainers
- Focus on stability over new features
- Preserve backward compatibility
- Document any API discoveries
- Consider migration to async httpx for better performance
