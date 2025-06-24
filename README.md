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

First, create the configuration directory and `.env` file:

```shell
$ mkdir -p ~/.kptncook
$ touch ~/.kptncook/.env
```

Then set environment variables in the `~/.kptncook/.env` file (or directly in your shell). You'll need to set at least the `KPTNCOOK_API_KEY` variable. If you want to sync the recipes with mealie, you also have to set some additional variables.

**Important:** The `.env` file must be created in the `~/.kptncook/` directory, NOT in the installation directory or by editing the `kptncook` executable.

If you want to back up your favorite receipts from KptnCook, you have to set the `KPTNCOOK_ACCESS_TOKEN` variable as well. You can obtain the access token by running the `kptncook kptncook-access_token` command. But you need a kptncook account to do that.
Beware: If you don't have a kptncook account, you'll lose all your favorites by creating a new one.

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

### Full Configuration Example

```shell
KPTNCOOK_API_KEY=6q7QNKy-oIgk-IMuWisJ-jfN7s6
KPTNCOOK_ACCESS_TOKEN=9353xxxx-xxxx-4fe1-xxxx-xxx4a173805  # replace with correct token
MEALIE_URL=https://mealie.staging.django-cast.com/api
MEALIE_USERNAME=jochen
MEALIE_PASSWORD=password  # replace with correct password

# Optional: Password manager integration
KPTNCOOK_USERNAME_COMMAND="op read op://Personal/KptnCook/username"
KPTNCOOK_PASSWORD_COMMAND="op read op://Personal/KptnCook/password"
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
