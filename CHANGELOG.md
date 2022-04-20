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
