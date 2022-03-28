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
  http  List all recipes for today the kptncook site.
  sync  Sync recipes for today from kptncook site.
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
