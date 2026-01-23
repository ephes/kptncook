# KptnCook

A small command line client for downloading [KptnCook](https://www.kptncook.com/) recipes, including today's picks, discovery screens and lists,
dailies, onboarding tags, and ingredient-based searches. It can also sync
to Mealie and export recipes to Paprika or Tandoor.

Thanks to [this blogpost](https://medium.com/analytics-vidhya/reversing-and-analyzing-the-cooking-app-kptncook-my-recipe-collection-5b5b04e5a085) for the url to get the json for today's recipes.

It's in pre alpha status and currently slightly unmaintained. If you want to step in, please let me know.

# Dependencies
* Python >=3.10
* Mealie >=v1.0

# Installation

```shell
$ uvx install kptncook
```

# Docker

Build the image from this repository:

```shell
$ docker build -t kptncook .
```

The container sets `KPTNCOOK_HOME=/data`. Mount that directory and provide the
required environment variables. For Mealie auth, set `MEALIE_API_TOKEN` or
`MEALIE_USERNAME`/`MEALIE_PASSWORD`:

```shell
$ docker run --rm -v ~/.kptncook:/data \
    -e KPTNCOOK_API_KEY=6q7QNKy-oIgk-IMuWisJ-jfN7s6 \
    -e MEALIE_URL=https://mealie.example.com/api \
    -e MEALIE_USERNAME=user \
    -e MEALIE_PASSWORD=pass \
    kptncook sync
```

Alternatively, set `MEALIE_API_TOKEN=...` instead of `MEALIE_USERNAME` and
`MEALIE_PASSWORD`.

To back up favorites, also set `KPTNCOOK_ACCESS_TOKEN`:

```shell
$ docker run --rm -v ~/.kptncook:/data \
    -e KPTNCOOK_API_KEY=... \
    -e KPTNCOOK_ACCESS_TOKEN=... \
    -e MEALIE_URL=https://mealie.example.com/api \
    -e MEALIE_USERNAME=user \
    -e MEALIE_PASSWORD=pass \
    kptncook backup-favorites
```

# Usage

## help

```shell
Usage: kptncook [OPTIONS] COMMAND [ARGS]...

Options:
  --install-completion [bash|zsh|fish|powershell|pwsh]
                                  Install completion for the specified
                                  shell.
  --show-completion [bash|zsh|fish|powershell|pwsh]
                                  Show completion for the specified shell,
                                  to copy it or customize the installation.
  --help                          Show this message and exit.

Commands:
  backup-favorites          Store kptncook favorites in local repository.
  dailies                   List daily recipes from the kptncook site.
  delete-recipes            Delete recipes from the local repository.
  discovery-list            List recipes from a discovery list.
  discovery-screen          List discovery screen lists and quick search entries.
  ingredients-popular       List popular ingredients.
  kptncook-access-token     Get access token for kptncook.
  kptncook-today            List all recipes for today from the kptncook...
  list-recipes              List all locally saved recipes.
  onboarding                List onboarding recipes by tags.
  recipes-with-ingredients  List recipes that match ingredient ids.
  save-todays-recipes       Save recipes for today from kptncook site.
  search-by-id              Search for a recipe by id in kptncook api, id...
  sync                      Fetch recipes for today from api, save them to...
  sync-with-mealie          Sync locally saved recipes with mealie.
  export-recipes-to-paprika  Export a recipe by id or all recipes to Paprika app
  export-recipes-to-tandoor  Export a recipe by id or all recipes to Tandoor
```

## Quick examples

Short snippets for the new discovery/dailies/onboarding/ingredient commands
(see the detailed sections below for flag descriptions):

```shell
$ kptncook discovery-screen
$ kptncook discovery-screen --no-quick-search
$ kptncook discovery-list --list-type latest
$ kptncook discovery-list --list-type recommended
$ kptncook discovery-list --list-type curated --list-id 12345
$ kptncook discovery-list --list-type automated --list-id 67890
$ kptncook dailies --recipe-filter veggie --save
$ kptncook onboarding --tag rt:diet_vegetarian
$ kptncook onboarding --tag "low-carb,high-protein" --save
$ kptncook ingredients-popular
$ kptncook recipes-with-ingredients --ingredient-id 123,456 --save
```

### Export metadata

Exports to Mealie and Tandoor include KptnCook active tags as tags/keywords
(verbatim).

## Delete recipes

Use indices from `kptncook list-recipes` or pass one or more `--oid` values.

```shell
$ kptncook delete-recipes 0 2
$ kptncook delete-recipes --oid 635a68635100007500061cd7 --oid 635a68635100007500061cd8
$ kptncook delete-recipes 0 --force
```

## Dailies

Filter dailies by API fields such as recipeFilter (for example `veggie`), zone
(timezone offset like `+02:00`), and subscription status (`--subscribed` and
`--not-subscribed` are mutually exclusive). Add `--save` to store the daily
recipes in the local repository.

```shell
$ kptncook dailies
$ kptncook dailies --zone +02:00 --recipe-filter veggie --save
$ kptncook dailies --subscribed
$ kptncook dailies --not-subscribed --save
```

## Discovery

Use `discovery-screen` to list discovery list ids and types. `discovery-list`
requires `--list-type` (`latest`, `recommended`, `curated`, `automated`).
List types are case-insensitive; short flags are `-t` for `--list-type` and
`-i` for `--list-id`. For `curated` and `automated`, pass the list id from
`discovery-screen` with `--list-id`. For `latest` and `recommended`, omit
`--list-id`.
Use `--no-quick-search` if you only want discovery list ids, and add `--save`
to `discovery-list` to store the resolved recipes locally.
Discovery screen entries are printed as `id | title | type` so you can copy the
list id into `discovery-list`. Discovery list entries are recipe summaries; the
CLI resolves them to full recipes before printing or saving.

```shell
$ kptncook discovery-screen
$ kptncook discovery-screen --no-quick-search
$ kptncook discovery-list --list-type recommended
$ kptncook discovery-list --list-type latest
# list ids for curated/automated lists come from discovery-screen output
$ kptncook discovery-list -t curated -i 12345 --save
$ kptncook discovery-list --list-type automated --list-id 67890
```

## Ingredients

Ingredient discovery and ingredient-based recipes require
`KPTNCOOK_ACCESS_TOKEN`. Ingredient ids can be repeated or comma-separated; use
the `_id.$oid` value from `kptncook ingredients-popular` (output is `id | name`
for easy copy/paste). Use `kptncook kptncook-access-token` if you need to
generate one.
The `--ingredient-id` flag is required and repeatable for
`recipes-with-ingredients` (`-i` is the short flag).
Add `--save` to `recipes-with-ingredients` to store matched recipes locally.
Recipes are resolved to full recipe payloads before printing or saving.

```shell
$ kptncook ingredients-popular
$ kptncook recipes-with-ingredients -i 123 -i 456 --save
$ kptncook recipes-with-ingredients --ingredient-id 123,456
```

## Onboarding recipes

The `--tag` flag is required and repeatable; tags accept repeated values or
comma-separated lists. Tags are KptnCook tag slugs (for example
`rt:diet_vegetarian`). Recipes are resolved to full recipe payloads before
printing or saving.

```shell
$ kptncook onboarding --tag rt:diet_vegetarian
$ kptncook onboarding --tag "low-carb,high-protein" --save
```

## Environment

First, create the configuration directory and `.env` file:

```shell
$ mkdir -p ~/.kptncook
$ touch ~/.kptncook/.env
```

Alternatively, you can run the setup helper to create the `.env` file, prefill the
default API key, and optionally fetch an access token:

```shell
$ kptncook-setup
```

Then set environment variables in the `~/.kptncook/.env` file (or directly in your shell). You'll need to set at least the `KPTNCOOK_API_KEY` variable. If you want to sync the recipes with mealie, set `MEALIE_API_TOKEN` or `MEALIE_USERNAME`/`MEALIE_PASSWORD`.

**Important:** The `.env` file must be created in the `~/.kptncook/` directory, NOT in the installation directory or by editing the `kptncook` executable.

If you want to back up your favorite recipes from KptnCook, you have to set the `KPTNCOOK_ACCESS_TOKEN` variable as well. You can obtain the access token by running the `kptncook kptncook-access-token` command. But you need a kptncook account to do that.
Beware: If you don't have a kptncook account, you'll lose all your favorites by creating a new one.

Optional API defaults for discovery/dailies/onboarding requests:

- `KPTNCOOK_LANG` (default `de`)
- `KPTNCOOK_STORE` (default `de`)
- `KPTNCOOK_PREFERENCES` (for example `rt:diet_vegetarian,`)

### Password Manager Integration

You can retrieve KptnCook credentials from a password manager instead of typing them interactively. Set these environment variables:

- `KPTNCOOK_USERNAME_COMMAND`: Shell command to retrieve username
- `KPTNCOOK_PASSWORD_COMMAND`: Shell command to retrieve password

Example for 1Password CLI:
```shell
KPTNCOOK_USERNAME_COMMAND="op read op://Personal/KptnCook/username"
KPTNCOOK_PASSWORD_COMMAND="op read op://Personal/KptnCook/password"
```

Example for pass (password-store):
```shell
KPTNCOOK_USERNAME_COMMAND="pass show kptncook/username"
KPTNCOOK_PASSWORD_COMMAND="pass show kptncook/password"
```

### Ingredient Grouping (Optional)

To split ingredient lists by `ingredient.typ` (e.g., "regular" vs "basic") across
all exporters, set the toggle below. You can also customize the section labels.

```shell
KPTNCOOK_GROUP_INGREDIENTS_BY_TYP=true
KPTNCOOK_INGREDIENT_GROUP_LABELS="regular:You need,basic:Pantry"
```

### Full Configuration Example

```shell
KPTNCOOK_API_KEY=6q7QNKy-oIgk-IMuWisJ-jfN7s6
KPTNCOOK_ACCESS_TOKEN=9353xxxx-xxxx-4fe1-xxxx-xxx4a173805  # replace with correct token
MEALIE_URL=https://mealie.staging.django-cast.com/api
# Mealie auth (choose one)
MEALIE_API_TOKEN=mealie-api-token
# or:
MEALIE_USERNAME=jochen
MEALIE_PASSWORD=password  # replace with correct password

# Optional: API defaults
KPTNCOOK_LANG=de
KPTNCOOK_STORE=de
KPTNCOOK_PREFERENCES=rt:diet_vegetarian,

# Optional: Password manager integration
KPTNCOOK_USERNAME_COMMAND="op read op://Personal/KptnCook/username"
KPTNCOOK_PASSWORD_COMMAND="op read op://Personal/KptnCook/password"

# Optional: Ingredient grouping
KPTNCOOK_GROUP_INGREDIENTS_BY_TYP=true
KPTNCOOK_INGREDIENT_GROUP_LABELS="regular:You need,basic:Pantry"
```

# Troubleshooting

## Common Issues

### "SyntaxError: invalid decimal literal" after defining API key

This error occurs if you accidentally edited the `kptncook` executable file instead of creating a `.env` file.

**Solution:**
1. Restore the original `kptncook` executable (reinstall if needed)
2. Create the configuration directory: `mkdir -p ~/.kptncook`
3. Create the `.env` file: `touch ~/.kptncook/.env`
4. Add your environment variables to `~/.kptncook/.env`

### "Field required" validation errors

This happens when the required environment variables are not set. Make sure you have created the `.env` file in the correct location (`~/.kptncook/.env`) and added at least the `KPTNCOOK_API_KEY` variable.

# Contribute

## Install Development Version

- Checkout source repository
- Install uv if not already installed
- Install just if not already installed

Install the development environment:
```shell
$ uv sync
```

Install the git pre-commit hooks:
```shell
$ uv run pre-commit install
```

## Beads Setup (Required)

This repo uses Beads for issue tracking, and `.beads/` is committed.

```shell
$ bd onboard
```

If `bd onboard` is not available:

```shell
$ bd init
$ bd hooks install
```

If your global gitignore ignores `.beads/`, remove `**/.beads/` or use `git add -f`.

## Quality Gates (Required)

```shell
$ just lint
$ just typecheck
$ just test
```

Target a single test:

```shell
$ just test-one tests/test_file.py::TestClass::test_case
```

## Run Tests (Direct)

Run tests using uv:

```shell
$ uv run pytest
```

## Beadsflow

Use the local beadsflow checkout:

```shell
$ just beadsflow-dry <epic-id>
$ just beadsflow-once <epic-id>
$ just beadsflow-run <epic-id>
```

## GitHub Issue Import

Import GitHub issues into Beads epics (open issues by default), including
comments. The importer is idempotent and uses `external_ref` as `gh-<number>`.

```shell
$ just beads-import-gh-issues
$ just beads-import-gh-issues --repo OWNER/REPO --state open --limit 500
$ just beads-import-gh-issues --dry-run
```

## Publish a Release

After running the tests, publish the package to PyPI using uv:

```shell
$ uv publish --token your_token
```
