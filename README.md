# KptnCook

A small command line client for downloading [KptnCook](https://www.kptncook.com/) recipes. Atm it's only possible to download the three
recipes for today. If you know how to get the data for other days/oids
of recipes, please let me know :).

Thanks to [this blogpost](https://medium.com/analytics-vidhya/reversing-and-analyzing-the-cooking-app-kptncook-my-recipe-collection-5b5b04e5a085) for the url to get the json for today's recipes.

It's in pre alpha status and currently slightly unmaintained. If you want to step in, please let me know.

# Dependencies
* Python >=3.10
* Mealie >=v1.0

# Installation

```shell
$ uvx install kptncook
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
  kptncook-access-token     Get access token for kptncook.
  kptncook-today            List all recipes for today from the kptncook...
  list-recipes              List all locally saved recipes.
  save-todays-recipes       Save recipes for today from kptncook site.
  search-by-id              Search for a recipe by id in kptncook api, id...
  sync                      Fetch recipes for today from api, save them to...
  sync-with-mealie          Sync locally saved recipes with mealie.
  export-recipes-to-paprika  Export a recipe by id or all recipes to Paprika app
```

## Environment

Set environment variables via `~/.kptncook/.env` dotenv file or directly in your shell. You'll need to set at least the `KPTNCOOK_API_KEY` variable. If you want to sync the recipes with mealie, you also have to set some additional variables.

If you want to back up your favorite receipts from KptnCook, you have to set the `KPTNCOOK_ACCESS_TOKEN` variable as well. You can obtain the access token by running the `kptncook kptncook-access_token` command. But you need a kptncook account to do that.
Beware: If you don't have a kptncook account, you'll lose all your favorites by creating a new one.

Here's an example:

```shell
KPTNCOOK_API_KEY=6q7QNKy-oIgk-IMuWisJ-jfN7s6
KPTNCOOK_ACCESS_TOKEN=9353xxxx-xxxx-4fe1-xxxx-xxx4a173805  # replace with correct token
MEALIE_URL=https://mealie.staging.django-cast.com/api
MEALIE_USERNAME=jochen
MEALIE_PASSWORD=password  # replace with correct password
```

# Contribute

## Install Development Version

- Checkout source repository
- Install uv if not already installed

Install the development environment:
```shell
$ uv sync
```

Install the git pre-commit hooks:
```shell
$ uvx run pre-commit install
```

## Run Tests

Run tests using uv:

```shell
$ uv run pytest
```

## Publish a Release

After running the tests, publish the package to PyPI using uv:

```shell
$ uv publish --token your_token
```
