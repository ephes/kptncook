# Access Token + First-Run UX Notes (2026-01-22)

## Context
- Repo: kptncook CLI.
- Related issues:
  - #64: Missing .env file and content after install.
  - #65: Can't retrieve kptncook-access-token (401 Unauthorized).

## Issue #64 (Missing .env content)
- Reported symptom (2026-01-22): Running `kptncook` after install fails with Pydantic validation error for `kptncook_api_key`.
- User workaround: create `~/.kptncook/.env` with content like `KPTNCOOK_API_KEY=unknown` so command runs.
- Root cause: Settings require `KPTNCOOK_API_KEY`, but the CLI exits during import when missing, and error message is raw Pydantic output.
- Desired outcome: Provide a friendly, actionable message and scaffold a `.env` file with placeholders.

## Issue #65 (Access token retrieval 401)
- Reported symptom (2026-01-22): `kptncook kptncook-access-token` prompts for email/password, then fails with `401 Unauthorized` from `https://mobile.kptncook.com/auth/login`.
- Observations:
  - Access token flow uses `POST /auth/login` with `{email, password}` and includes `kptnkey` header derived from `KPTNCOOK_API_KEY`.
  - If the API key is missing or invalid, the login request can fail with 401.
  - Interactive prompt accepts raw user input; no additional validation or guidance beyond the raw HTTP error.
- Desired outcome: Improve error guidance so users know to verify credentials and `KPTNCOOK_API_KEY` (not a placeholder), and consider writing the access token to `.env`.

## Current CLI flow
- Settings are instantiated at import time in `src/kptncook/config.py`.
- Missing required config raises `ValidationError`, prints `validation error: ...`, and exits.
- `kptncook-access-token`:
  - Uses password manager commands (optional) or interactive prompts.
  - Calls `/auth/login` and prints access token or raw exception message.

## Proposed fixes (high level)
- Provide a friendly error when required env vars are missing, including:
  - Location of `.env` (`~/.kptncook/.env`)
  - Example lines to add
  - Mention that placeholder API keys will not work
- Scaffold `.env` (only if missing or empty) with commented placeholders.
- Improve `kptncook-access-token` error handling to show a clear message for 401s and to hint about API key validity and credential checks.

## Notes from maintainer
- Access-token retrieval works with 1Password integration using:
  - `KPTNCOOK_USERNAME_COMMAND="op read op://Personal/KptnCook/username"`
  - `KPTNCOOK_PASSWORD_COMMAND="op read op://Personal/KptnCook/password"`

## Open questions
- Should `kptncook-access-token` optionally write the token to `.env`?
- Should the CLI allow running commands that don’t require `KPTNCOOK_API_KEY` without failing at import?

## Implementation notes (planned/updated)
- Add config scaffolding: create `~/.kptncook/.env` with a starter template when missing/empty.
- Enforce non-empty `KPTNCOOK_API_KEY` with a validator so blank placeholders still trigger the friendly error.
- Improve access-token error handling:
  - Special-case HTTP 401 to hint about credential validity and `KPTNCOOK_API_KEY`.
  - Show a short instruction to add `KPTNCOOK_ACCESS_TOKEN` to the `.env` file after success.

## Update (2026-01-23)
- Added a `kptncook-setup` entrypoint to scaffold the `.env` file with the README API key
  and optionally fetch/save an access token without importing runtime settings.
- The .env template now pre-fills the default API key.
- Fix: `kptncook-setup` now uses a top-level module (`kptncook_setup`) so it doesn't
  import `kptncook.__init__` (which requires config settings). This allows setup to
  run before any env vars are present.
