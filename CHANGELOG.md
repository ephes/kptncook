Unreleased
==========

### Features
- #68 Add a `help` command (with `--all`) and #71 an `ls` alias for `list-recipes`.

### Fixes
- #75 Only show ingredient group title ("You need" / "Pantry") on the first
  ingredient per section in Mealie export instead of on every ingredient.
- #64 Clarify first-run configuration messaging and note that commands should be
  re-run after scaffolding the `.env` file.
- #74 Skip step image upload failures during Mealie sync while still persisting
  recipe metadata for de-duplication.
- #69 Avoid printing recipe JSON when `--save` is used for discovery lists,
  ingredient-based searches, and onboarding.
- #73 Improve error handling when resolving share URLs in `search-by-id`.
- #70 Expand the `.env` template with commented Mealie settings and optional config.
- #72 Log non-duplicate Mealie creation errors instead of silently ignoring them.

0.0.28 - 2026-01-27
===================

### Fixes
- #66 Package the `kptncook-setup` entrypoint module so setup works after install.
- #67 Make favorites backup tolerant of API response shape changes and improve
  identifier extraction.

0.0.27 - 2026-01-24
===================

### Features
- Export step ingredient references to Mealie for cooking mode linking.
- Added `MEALIE_API_TOKEN` support as an alternative to username/password for Mealie sync.
- Added `kptncook-setup` helper to scaffold the `.env` file with the default API key
  and optionally fetch an access token.

### Fixes
- #61 Fix crash when `KPTNCOOK_HOME` is set by normalizing and expanding the configured root path
  (thanks @alexdetsch).
- #36 Handle recipes without cover images in Mealie sync (avoid crash).
- Improve first-run configuration errors by scaffolding `~/.kptncook/.env` and guiding
  missing `KPTNCOOK_API_KEY` setup.
- Provide clearer error messaging for access token retrieval failures.

0.0.26 - 2025-12-25
===================

### Features
- Added `discovery-screen` command to list discovery lists and quick search entries.
- Added `discovery-list` command to fetch discovery list recipes (latest/recommended or curated/automated list IDs).
- Added `dailies` command and API support to fetch full daily recipes with optional filters.
- Added `onboarding` command to fetch tagged onboarding recipes.
- Added `ingredients-popular` and `recipes-with-ingredients` commands with API support
  for ingredient discovery and ingredient-based recipe searches.
- Added an API helper to resolve recipe summary identifiers via `/recipes/search`.
- #54 Added `delete-recipes` command to remove recipes from the local repository by
  index or oid to avoid re-syncing unwanted items.
- #50 Added a Tandoor exporter module and `export-recipes-to-tandoor` command for
  recipe.zip generation (thanks @michael-arndt-gcx).
- Added step-ingredient models and recipe type keywords for Tandoor exports.
- #38 Added optional ingredient grouping by `ingredient.typ` across exporters with
  configurable labels (thanks @ValleBL).
- #41 Exported KptnCook active tags to Mealie and Tandoor, deduping the base
  kptncook tag while preserving recipe type keywords (thanks @Kadz93).

### Fixes
- Fixed discovery list fetching to use the correct API paths and show quick-search
  entries and ingredient names in the CLI output.
- Allow comma-separated onboarding tags in the CLI and de-duplicate inputs.
- #36 Handle Mealie 422 validation errors without masking the response (thanks @dvogt23).
- #60 Extend localized field fallbacks to handle singular/plural title payloads and
  fill missing ingredient titles more robustly.
- #55 Follow redirects when fetching KptnCook images for Mealie sync and avoid JSON parsing
  on non-JSON error responses.
- #818 Apply locale fallback (de -> en -> any) for exporter localized strings instead of
  hard-coding German.
- Use locale fallback when listing or deleting recipes in the CLI.
- Replaced raw debug prints with logging for cleaner CLI output during exports/sync.

### Infrastructure
- Added a justfile with common dev commands and beadsflow helpers.
- Added beadsflow configuration for local Beads automation.
- Added AGENTS.md for repo-specific workflow guidance.
- Added Ruff to dev dependencies to support `just lint`.
- Excluded notebooks from Ruff formatting and linting.
- Updated clean targets to skip `.venv/`, `.beads/`, and `.git/`.
- Added a GitHub-to-Beads importer script and just helper with dry-run support.
- Added a basic Dockerfile and .dockerignore for container builds.

### Documentation
- Added quick README examples for automated discovery lists and multi-tag onboarding saves.
- Clarified required `--tag` and `--ingredient-id` flags for onboarding and
  ingredient-based commands in the README usage section.
- Added quick usage snippets for discovery, dailies, onboarding, and ingredient
  commands in the README.
- Added curated and recommended discovery list examples to the README quick usage snippets.
- Documented discovery, dailies, onboarding, and ingredient-based commands with
  examples (including recipeFilter/zone) and required flags in the README.
- Documented discovery list short flags and onboarding tag slug examples in the
  README usage section.
- Added a discovery-screen -> discovery-list workflow example and clarified save
  options for dailies, discovery lists, and ingredient-based commands in the
  README usage section.
- Expanded README discovery and ingredient usage examples for automated lists
  and repeatable ingredient ids.
- Clarified discovery list output format and recipe summary resolution in the
  README usage section.
- Clarified required discovery list flags and popular ingredient output format
  in the README usage section.
- Updated the README intro and ingredient guidance for discovery, dailies,
  onboarding, and ingredient-based commands, and corrected the access-token
  helper name.
- Clarified README guidance on access-token requirements for ingredient-based
  commands and onboarding recipe resolution.
- Clarified discovery list type requirements and list-id usage in the README
  usage section.
- Documented Beads onboarding and `.beads/` commit policy.
- Documented required quality gates and beadsflow usage.
- Updated pre-commit install instructions to use `uv run`.
- Documented the GitHub issue import helper.
- Documented the `export-recipes-to-tandoor` command in the README.
- Added Docker usage instructions to the README.

0.0.25 - 2025-06-28
===================

### Fixes
- #57 Fixed the url for fetching the access token

### Documentation
- Clarified `.env` file setup instructions to prevent users from editing the executable
  - Added explicit directory and file creation steps
  - Added warning about correct file location
  - Added troubleshooting section for common configuration errors

0.0.24 - 2025-06-12
===================

### Features
- Added password manager integration for KptnCook authentication
  - Support for retrieving credentials via shell commands (1Password, pass, Bitwarden, etc.)
  - New environment variables: KPTNCOOK_USERNAME_COMMAND and KPTNCOOK_PASSWORD_COMMAND
  - Automatic fallback to interactive prompts if password manager commands fail

0.0.23 - 2025-06-12
===================

### Infrastructure
- Migrated build system from flit to uv
- Moved package to src layout (kptncook/ → src/kptncook/)
- Replaced Black, isort, and flake8 with Ruff for linting and formatting
- Updated all documentation to use uv commands instead of flit/pipx
- Changed from project.optional-dependencies to dependency-groups for dev dependencies

0.0.22 - 2025-02-21
===================

- #48 uid can also have a length of 7 @Nero3ooo
- Some minor fixes for pre-commit hooks / mypy etc @ephes
- #47 fix nutrition format @david-askari

0.0.21 - 2024-09-16
===================

- #39 fixed encoding issues on windows by setting utf-8 encoding as default @ephes

0.0.20 - 2024-09-13
===================

### Fixes

- #23 make recipe export skip invalid recipes @ephes
- #32 remove unescaped newlines from json for paprika export @ephes

0.0.19 - 2024-03-12
===================

### Fixes
- fix json for paprika export
    - #35 PR by @ton-An

0.0.18 - 2024-03-02
===================

### Fixes
- fix image synchronization
    - #34 PR by @alexdetsch

0.0.17 - 2023-12-10
===================

### Features
- Downloading more than 999 favorites from kptncook triggers timeout -> removed timeout
    - #30 issue reported by @brotkrume

0.0.16 - 2023-12-10
===================

### Features
- If a sharing link is provided, get the recipe id from the redirect location
    - #30 issue reported by @brotkrume

0.0.15 - 2023-11-06
===================

Paprika export was broken. Thanks to @patryk-31 for reporting.

### Fixes
- The paprika export template was missing -> moved the template into the export module
    - #29 issue reported by @m4um4u1

0.0.14 - 2023-10-30
===================

Forgot some lines in the last release :-( thanks to @ca-dmin for reporting.

0.0.13 - 2023-10-28
===================

Pydantic2 compatibility.

### Fixes
- Fixed missing default values for mealie Recipe model
    - #28 issue reported by @m4um4u1

0.0.12 - 2023-10-21
===================
Ignore pydantic DeprecationWarnings.

### Fixes
- Fixed broken `KptnCookClient.to_url` method
    - #23 use urljoin instead of f-string @ephes

0.0.11 - 2023-10-05
===================
Pydantic >= 2 compatibility and Python 3.12 support.

0.0.10 - 2023-02-26
===================
Export recipes to [Paprika Recipe Manager](https://www.paprikaapp.com/)

### Features
- Export recipes to Paprika Recipe Manager
    - #22 PR by @luebbert42

0.0.9 - 2022-12-12
==================
No soup for you!

### Fixes
- removed wrongfully added recipe yield reduction
    - #21  PR by @alexdetsch


0.0.8 - 2022-12-04
==================
Added units, foods, tags and step images

### Features
- Added units and food types  to recipe ingredients
    - #20  PR by @alexdetsch
- Added tags to recipes (only adds `kptncook` at the moment
   - #20 PR by @alexdetsch
- Added step images to recipe instructions
   - #20 PR by @alexdetsch

### Refactoring
- Review and refactoring
   - #20  PR by @ephes
- Updated pre-commit hooks
   - by @ephes

### Fixes
- Documented python / mealie version requirements
    - #20 PR by @alexdetsch


0.0.7 - 2022-05-05
==================
### Fixes
 - Increased fetch access token timeout to 60 seconds
    - #16 issue by @ephes

0.0.6 - 2022-04-25
==================
### Fixes
 - Better name "kptncook-today" for the command fetching the 3 kptncook recipes for today
    - #14 issue by @ephes
 - Fixed __all__ exports
    - #14 issue by @ephes

0.0.5 - 2022-04-20
==================
### Refactoring
 - use kptncook api client instead of repository like mealie
    - #11 issue by @ephes / @gloriousDan
### Features
 - new cli command `kptncook kptncook-access_token` fetches the access token from the kptncook api
    - #11 issue by @ephes / @gloriousDan
 - new cli command `kptncook list-recipes` lists all locally stored recipes
    - #11 issue by @ephes / @gloriousDan
 - new cli command `kptncook backup-favorites` fetches all favorites from the kptncook api and stores them locally
    - #11 issue by @ephes / @gloriousDan
 - new cli command `kptncook search-by-id` searches for a recipe by id (url, uid or oid) and stores it locally
    - #11 issue by @ephes / @gloriousDan

0.0.4 - 2022-04-08
==================
### Fixes
 - Ignore exception when recipe already exists in mealie
    - #7 issue by @ephes
 - Removed explicit dependency on click < 8.1 (fixed in typer 0.4.1)
    - #9 issue by @ephes

0.0.3 - 2022-03-30
==================
### Fixes
 - explicit dependency on click < 8.1
    - #5 issue by @ephes

0.0.2 - 2022-03-30
==================

### Features
 - make kptncook installable via pip (flit)
    - #1 issue by @ephes

0.0.1 - 2022-03-28
==================

### Features
 - initial commit by @ephes
