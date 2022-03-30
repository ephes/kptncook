# KptnCook

A small command line client for downloading [KptnCook](https://www.kptncook.com/) recipes. Atm it's only possible to download the three
recipes for today. If you know how to get the data for other days/oids
of recipes, please let me know :).

Thanks to [this blogpost](https://medium.com/analytics-vidhya/reversing-and-analyzing-the-cooking-app-kptncook-my-recipe-collection-5b5b04e5a085) for the url to get the json for todays recipes.

It's in pre alpha status.

# Installation

```shell
$ pipx install kptncook
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
  http                 List all recipes for today the kptncook site.
  save_todays_recipes  Save recipes for today from kptncook site.
  sync                 Fetch recipes for today from api, save them to...
  sync_with_mealie     Sync recipes from KptnCook with mealie.
```

## Environment

Set environment variables via `~/.kptncook/.env` dotenv file or directly in your shell. You'll
need to set at least the `KPTNCOOK_API_KEY` variable. If you want to sync the recipes with mealie,
you also have to set some additional variables. Here's an example:

```shell
KPTNCOOK_API_KEY=6q7QNKy-oIgk-IMuWisJ-jfN7s6
MEALIE_URL=https://mealie.staging.django-cast.com/api
MEALIE_USERNAME=jochen
MEALIE_PASSWORD=password  # this is wrong
```

# Contribute

## Install Development Version

- Checkout source repository
- Create a virtualenv

Inside the virtualenv install flit (not via pipx!):
```shell
$ python -m pip install flit
```

Install a symlinked development version of the package:
```
$ flit install -s
```
## Run Tests

Flit should have already installed pytest:

```shell
$ pytest
```
