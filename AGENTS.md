# AGENTS.md (kptncook)

## Project structure
- `src/kptncook/`: CLI entrypoint, API clients, models, exporters.
- `tests/`: pytest suite and fixtures.
- `notebooks/`: exploratory notebooks (not part of test runs).
- `README.md`, `CHANGELOG.md`: user docs and release notes.

## Build, test, and development commands
- Install deps: `uv sync` (or `just install`).
- Lint/format: `just lint` (ruff format + ruff check).
- Type checks: `just typecheck` (mypy).
- Tests: `just test` or `just test-one tests/test_file.py::TestClass::test_case`.
- Pre-commit hooks: `uv run pre-commit install`.
- Before declaring work done, `just lint`, `just typecheck`, and `just test` must pass.

## Coding style
- Python 3.10+ with type hints for public interfaces.
- Ruff handles formatting and linting; prefer minimal, focused changes.

## Documentation
- Update `CHANGELOG.md` for user-visible changes.

## Beads workflow (required)
- This repo uses Beads for issue tracking, and `.beads/` is committed.
- Onboard: `bd onboard` (fallback: `bd init` + `bd hooks install`).
- If your global gitignore ignores `.beads/`, remove `**/.beads/` or use `git add -f`.
- Start work by reading context and deps: `bd --no-daemon --no-db show <id>` and `bd --no-daemon --no-db dep tree <id>`.
- Comment markers must start with one of: `Ready for review:`, `LGTM`, `Changes requested:`.

## Beadsflow
- Use the local checkout: `uv run --project ../beadsflow beadsflow run <epic-id> ...`.
- Helpers exist in `justfile`: `just beadsflow-dry <epic-id>`, `just beadsflow-once <epic-id>`, `just beadsflow-run <epic-id>`.

## Commits and push
- Do not run `git commit`, `git push`, or `bd sync` unless the user explicitly asks.

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
