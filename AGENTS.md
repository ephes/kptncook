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
- Git hooks: `uv run prek install -f`.
- Before declaring work done, `just lint`, `just typecheck`, and `just test` must pass.

## Coding style
- Python 3.10+ with type hints for public interfaces.
- Ruff handles formatting and linting; prefer minimal, focused changes.

## Documentation
- Update `CHANGELOG.md` for user-visible changes.
- Release process: see `README.md#release-process`.
- Files in `specs/` are ephemeral and for local consumption only. Do not track or commit them.

## Commits and push
- Do not run `git commit` or `git push` unless the user explicitly asks.

## Landing the Plane (Session Completion)

**When ending a work session**, you MUST complete ALL steps below. Work is NOT complete until `git push` succeeds.

**MANDATORY WORKFLOW:**

1. **File issues for remaining work** - Create issues for anything that needs follow-up
2. **Run quality gates** (if code changed) - Tests, linters, builds
3. **Update issue status** - Close finished work, update in-progress items
4. **PUSH TO REMOTE** - This is MANDATORY:
   ```bash
   git pull --rebase
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
