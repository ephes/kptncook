0.0.5 - 2022-04-19
==================
### Refactoring
 - use kptncook api client instead of repository like mealie
    - #11 issue by @ephes / @gloriousDan
### Features
 - new cli command `kptncook kptncook_access_token` fetches the access token from the kptncook api
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
