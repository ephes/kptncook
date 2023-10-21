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
