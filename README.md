# KptnCook

A small command line client for downloading [KptnCook](https://www.kptncook.com/) recipes, including today's picks, discovery screens and lists,
dailies, onboarding tags, and ingredient-based searches. It can also sync
to Mealie and export recipes to Paprika, Tandoor, or Markdown.

Thanks to [this blogpost](https://medium.com/analytics-vidhya/reversing-and-analyzing-the-cooking-app-kptncook-my-recipe-collection-5b5b04e5a085) for the url to get the json for today's recipes.

It's in pre alpha status and currently slightly unmaintained. If you want to step in, please let me know.

## Disclaimer

This is an unofficial tool. It is not affiliated with, endorsed by, or
supported by KptnCook. "KptnCook" is used only to describe which service the
tool works with.

- It talks to KptnCook's undocumented mobile API, which can change or stop
  working at any time.
- It is meant for personal use, such as backing up your own favorites into
  your own recipe manager. Recipe texts and images remain the property of
  their rights holders; do not republish them.
- You are responsible for how you use it, including complying with
  KptnCook's terms of service.

## Maintenance disclosure

This repository is maintained with substantial assistance from coding agents.
Most routine implementation, investigation, documentation, triage, and
release-preparation work may be performed through agent-assisted workflows.
Changes are still reviewed and accepted by the maintainer, and human
contributors are welcome.

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
required environment variables. Passing configuration with `-e` is the
recommended approach for Docker: it avoids the need for an interactive setup and
works the same on every run. For Mealie auth, set `MEALIE_API_TOKEN` or
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

The guided setup is available as the `kptncook setup` subcommand, so it is
reachable through the container's `kptncook` entrypoint. Because it prompts for
your KptnCook account credentials, run it with an interactive TTY (`-it`). The
`.env` it writes lives at `$KPTNCOOK_HOME/.env` (i.e. `/data/.env`), so the
mounted volume persists it across runs:

```shell
$ docker run --rm -it -v ~/.kptncook:/data kptncook setup
```

To fetch only the access token after the API key is already configured, use
`docker run --rm -it -v ~/.kptncook:/data kptncook kptncook-access-token`.

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
  kptncook-access-token     Fetch and save the KptnCook access token.
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
  export-recipes-to-markdown Export a recipe by id or all recipes to Markdown files
```

Recipe IDs can be short share UIDs or 24 ASCII hexadecimal characters (case
insensitive). You can paste recipe URLs with query strings or fragments, or
text containing an OID. Short UIDs take precedence when both are present.
Local single-recipe exports to Paprika, Tandoor and Markdown accept either the
stored short UID or OID (including parsed recipe URLs). A missing selector
match or multiple matching recipes produces an error; selection never fetches
from the network.

Share URLs are resolved using only the response headers, without reading bodies
or following redirects. Only 301/302 responses with a Location are accepted.
Resolution has a 15-second elapsed budget; a reply observed after it is discarded
before recipe lookup or storage. HTTPX socket timeouts are capped by the remaining
budget (connect at most 5 seconds); in-flight I/O follows HTTPX's per-operation
timeout rules. Transport diagnostics do not include the share URL or secrets.

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

### Mealie sync

`sync-with-mealie` (and `sync`) creates every locally saved recipe that is not
yet in Mealie. Recipes are matched by the KptnCook id stored in the Mealie
recipe's extras (`source: kptncook`), not by name. A native ZIP import creates
the complete recipe with those identity extras already present. Distinct
KptnCook recipes with the same title can both be imported: Mealie assigns a
unique name and slug, which subsequent updates preserve.

Step-media URL components are percent-encoded and the final HTML attribute is
escaped, so server-returned filenames remain data. Existing instruction markup
is preserved. Conversion DEBUG logs contain only recipe identity and counts;
cover and nested step image URLs (including API keys) are never logged there.
With logging enabled, the request lines of httpx and httpcore's DEBUG trace
show `[REDACTED]` in place of API keys, tokens, URL credentials and secret
header values.

Mealie recipe slugs are admitted as single route components: nonempty Unicode
letters/numbers, hyphens and underscores only, percent-encoded when building
requests. Slash, backslash, dot segments, percent escapes, query/fragment
separators and controls are refused before authenticated follow-up requests.
Step photos are uploaded only when the imported instructions have exactly the
same unique texts in the same order as the source. Missing, inserted, reordered
or repeated texts omit all step photos with a bounded warning, retaining the
imported text recipe and server-assigned instruction fields.

Cover and step images are best-effort follow-ups. A media failure does not
undo the core import, and step-image updates patch only the instructions.
The summary means:

* `Created N recipes`: recipes newly created in Mealie.
* `Failed to sync N recipes`: each line names the recipe, its KptnCook id and
  the reason (HTTP status, connection error, timeout or local processing
  error). Other recipes are still synced, and the command exits with status 1.
  Name-collision errors are failures, not successful identity matches.

Cooperating sync processes on this machine hold one advisory lock across the
complete inventory and create sequence. The lock covers all accounts on the same
canonical Mealie API target (host casing, default ports and trailing slashes are
normalized), including rotated tokens. Locks are per operating-system user,
independent of `KPTNCOOK_HOME`, under `~/.cache/kptncook/sync-locks/`. Keep these
stable files while syncs may be running. A competing sync waits for the current
owner to finish for at most 30 seconds, then reports that sync is busy. Retry after
the current sync finishes. Timeout or interruption closes the authenticated client
without reading or modifying recipes. Filesystem/locking errors stop sync; exceptions and process exit
release ownership. This does not coordinate different machines or
manual Mealie clients and does not promise exactly-once delivery.

An organizer POST that returns Mealie's explicit uniqueness response (`409` with
`detail.message` equal to `This item already exists.`) triggers one inventory
refresh. A single matching organizer ID is reused (casefolded for tags, exact
names for foods/units). Ambiguous or absent matches, generic conflicts, legacy
400 responses and transport failures remain failures; the POST is never retried.

Units, foods and tags are listed once per sync, not once per recipe. The lookup
is built only from a complete inventory and then updated with organizers this
sync created (or found through the conflict refresh above); each new sync,
login or token change reads Mealie again. If several existing organizers with
different IDs match one requested name (for example tags `Vegan` and `vegan`),
that recipe fails before any unit, food, tag or recipe is created, naming the
ambiguous organizer. Merge or rename the duplicates in Mealie, then sync again;
kptncook never deletes or merges remote organizers.

A timeout or malformed response may occur after Mealie committed the import.
The client does not retry or delete the recipe after such an ambiguous result.
Run the sync again: it first checks stored identity extras and skips any
recipe already imported. Do not delete a recipe merely because its import
response was lost.

Image downloads for Paprika and Tandoor covers, Markdown images and Mealie step assets are
limited to 10 MiB per image and 60 seconds for the entire transfer (including
redirects). Failed, oversized, or slow images are skipped; recipe text is
still exported or imported. Downloads request uncompressed responses; servers
that send compressed images despite that request are skipped to bound memory.

The API key is attached only to image URLs on an allowed image host: HTTPS,
the default port, no user name or password in the URL, and a host name that
exactly matches one of KptnCook's image hosts (`d2am1qai33sroc.cloudfront.net`,
`images.kptncook.com`) or an entry in `KPTNCOOK_IMAGE_HOSTS`. Every redirect
target is checked the same way before it is requested. Images from any other
URL in recipe data are skipped with a warning that names neither the URL nor
the key; recipe text is still exported or imported. Mealie covers are downloaded through the same
checked downloader and uploaded as image bytes; Mealie is never asked to fetch
a keyed URL itself, since its scraper would follow redirects unchecked.
Configured hosts must be names, not IP addresses in any spelling.

Sync requires a complete, validated remote recipe inventory, including every
recipe detail. An invalid recipe or unreadable imported identity stops the run
before any organizer or archive creates, with a bounded positional diagnostic.
Check the indicated upstream record and rerun; invalid records are never silently skipped.
Each recipe detail must describe the inventory row it was requested for: the
same server ID (when the row carries one) and the requested slug. A detail for a
different recipe, or a recipe renamed while the inventory is read, stops the run
before any write; run the sync again to read a consistent inventory.
Recipe and organizer inventories allow at most 1,000 pages and 50,000 items.
An acquisition defaults to a 120-second elapsed budget, checked before and after each
response and before returning an inventory. Sync shares that budget across the
recipe listing and its detail reads. Request socket timeouts are capped by the
remaining budget; in-flight requests follow HTTPX's per-operation timeout rules.
A successful reply observed after the deadline is discarded before any creates.
Large libraries with sequential detail reads may need a larger budget: set
`MEALIE_INVENTORY_TIMEOUT_SECONDS=7200`, for example. This setting must be finite,
positive and at most 86,400 seconds. Rerunning a library that consistently exceeds
its budget needs a larger setting. Library users can pass `inventory_timeout=`
to `MealieApiClient` without loading KptnCook settings.
Changing totals, mismatched reported counts/page numbers, repeated identities and
empty pages in a multi-page inventory abort the acquisition before writes.

Mealie organizer pages must contain a list of objects and a nonnegative integer
`total_pages` (zero is allowed only for an empty inventory). Invalid pages abort
listing before organizer creation. Successful password login requires a nonempty
string token; malformed replies leave any existing authorization unchanged.

Library callers own healthy clients returned by `get_mealie_client()` and should
close them or use a context manager. Failed authentication closes the new client.
Sync, favorites backup, login, today/dailies, discovery, onboarding, ingredient
queries and recipe search close their own clients on success and every failure;
closing a wrapper around a borrowed HTTPX client keeps that shared client usable.
Request headers with the same exact key override wrapper defaults, and explicit
timeouts override the
HTTPX client's timeout. `tests/client_ownership_test.py` checks these on real
HTTPX requests with a synthetic transport, including exceptional context exits.

### Export metadata

Exports to Mealie and Tandoor include KptnCook active tags as tags/keywords
(verbatim).

All exporters preserve the recipe's explicit `fixedPortionCount`, falling back
to the standard two portions when it is absent. KptnCook stores ingredient
quantities for a single serving, also for fixed-portion recipes such as a batch
of cookies, so exporters scale them to the portion count they write. Explicit
portion counts must be positive integers (no booleans or coercion). Ingredient and step-ingredient quantities
must be finite; zero, fractions and missing quantities remain supported.
Whole-recipe preparation and cooking times are strict integer minutes from 0 to
525600 (365 days); booleans, strings and fractional values are refused.
Preparation time is required. Missing/null cooking time stays unknown; explicit
zero stays zero in every exporter. Timer
bounds must be nonnegative integers, with maximum at least minimum when both
are present. Exact/zero, max-only and ranged timers are retained without
mutating source lists. Invalid numeric recipes are reported through the existing
invalid-stored-entry warnings and excluded from export/import.
Tandoor and Markdown use numeric `servings`,
Paprika uses a decimal string, and Mealie uses the string `<count> Portionen`.
Markdown front-matter string values are quoted as YAML scalars,
so punctuation and newlines in tags and URLs retain their string values.

The Markdown export (`export-recipes-to-markdown`) writes one `.md` file per
recipe into an `export_md` directory under the KptnCook home, with YAML front
matter (full title, servings, prep/cook time, link, image, tags) followed by ingredients
and instructions. Images are downloaded into its sibling `assets/` directory
with random filenames and relative references; copy that directory along with
the notes. Authentication keys and signed image URLs appear only in download
requests, never in the generated Markdown or asset filenames. Failed image
downloads omit image metadata/body markup while retaining the recipe text.
Library `render_recipe()` performs no image I/O and omits images unless passed
an explicit local `image_path`. Image authentication preserves existing query
pairs/fragments and replaces any old `kptnkey` value.
Section headings and image alt text follow `KPTNCOOK_LANG`
(German, English, Spanish, French, and Portuguese are recognized; other
languages fall back to English). Recipe titles, comments, steps and ingredient
labels across all exporters prefer that same configured language. Missing or
empty translations fall back in order to German, English, Spanish, French, then
Portuguese, without changing the original recipe payload. `<timer>` placeholders in steps are
replaced with their durations. If a timer is missing or has no duration metadata,
the placeholder and its punctuation stay in every export: Paprika keeps the
literal `<timer>`, while the rendered Markdown, Tandoor and Mealie step texts
write it as the code span `` `<timer>` `` so renderers show it instead of
hiding it as an HTML tag. Later timers keep their positions; no duration is
invented.
Check the source recipe for unresolved timing instructions before cooking.

Markdown is final cookbook text, so its serving count is configurable:
`kptncook export-recipes-to-markdown --servings 4` (or `-s 4`) scales standard
recipes to four servings instead of two. Recipes with a fixed portion count
keep that count. The `servings` front matter shows the count the quantities
were scaled to.

Existing Markdown files, including manual notes, are never overwritten:
repeated exports create new files. If a title is taken,
the recipe id and then a counter are appended (`Title-<recipe id>.md`,
`Title-<recipe id>-2.md`). Complete notes are published without replacing files, so concurrent exports
retain distinct names. Command output lists the actual paths.

The Paprika and Tandoor exports write their files into the current directory
and never overwrite an existing file. If the preferred name (the asciified
recipe title, or `allrecipes.paprikarecipes` for a Paprika export of several
recipes) is taken, for example by an earlier export or by a second recipe with
the same title, the recipe id and then a counter are appended
(`Title-<recipe id>.zip`, `Title-<recipe id>-2.zip`, `allrecipes-2.paprikarecipes`).
The command output lists the names that were actually written.
Title-derived names are limited to 240 UTF-8 bytes, including extension and any
recipe ID/counter suffix. The display title stem is shortened on character
boundaries after transliteration, when applicable; suffixes remain intact and
every collision still selects a new name. Long or localized titles stay complete
inside the recipe JSON and Markdown's quoted `title` front matter.
Archives and Markdown notes are fully written and flushed in sibling staging
before atomic no-clobber publication. Markdown assets are published completely
before the note that references them. The destination filesystem must support
hard links (FAT/exFAT and some network mounts do not); otherwise export stops
with an error naming the directory and publishes no partial final file.
Normal failures remove attempt-owned staging and assets. Forced termination may
leave hidden `.kptncook-*` staging or complete unreferenced Markdown assets;
final export names always contain complete files. This is atomic visibility,
not a guarantee of survival after a power loss.
Paprika serializes and compresses one recipe at a time, so cover/JSON memory
does not grow with the number of exported recipes. Its small single-recipe and
bulk serialization helpers remain available to library callers; the bulk helper
still materializes its results when called directly.
Paprika preserves quotes, backslashes, whitespace and Unicode in recipe text.
All three local exporters preflight every recipe ID before writing any output:
IDs must be portable single filename components of at most 128 bytes (no slash,
backslash, NUL, dot segments, reserved names or overlong components). Valid opaque
IDs remain unchanged in recipe content; IDs do not need to be 24-digit OIDs.
An unsafe ID refuses the batch before downloads or file creation. This check
covers ID components; other filesystem failures remain subject to the existing
export error behavior.
Absent or ambiguous Paprika covers are optional, like failed downloads: every
recipe's text remains in the archive with empty photo fields.
Its temporary archive files are removed on success and failure; only the final
export persists. Export errors are reported instead of silently omitting recipes.

### HTTP diagnostics

HTTP failures display the status and caller-supplied action, or a fixed transport
category (timeout, connection, network, protocol or HTTP error). Raw exceptions,
request URLs and arbitrary response messages are omitted, so echoed API keys,
bearer tokens and signed URLs cannot enter diagnostics. Only a small allowlist
of known protocol details is displayed as plain text (for example
`missing_kptnkey` and recipe uniqueness messages). JSON details must fit in
16 KiB and use `application/json` or an `application/*+json` media type,
case-insensitively. Malformed, unexpected and oversized bodies are ignored.
CLI error boundaries escape Rich markup before rendering.

### Repository warnings

Incoming recipes are validated before any repository write.
Store reads accept regular files only (symlinks are refused) and are limited to
64 MiB of UTF-8 JSON, including files that grow while being read. Invalid encoding,
nonregular files and oversized stores stop reads and mutations without replacing
the primary or recovery snapshot. A missing store remains an empty repository.
Library deletion calls deduplicate requested IDs in encounter order, reporting
each deleted or missing ID once and committing at most once.
Identifier-based reads and mutations reject records whose `_id.$oid` is not a
nonempty string. The error identifies the record position without exposing its
payload; mutations leave the repository and recovery backup unchanged. Repair
the malformed record manually before retrying.

Repeated OIDs within a stored snapshot or an incoming batch are refused, even
when their payloads are identical. The bounded error identifies both positions;
mutations leave main and backup bytes unchanged. Errors distinguish incoming feed
batches from stored snapshots; a repeated daily/today feed is refused in full,
including exact copies. Check the feed and retry rather than editing the local
repository for an incoming-batch error. A single incoming record may
still replace its existing unique identity, as before.

Repository mutations use the existing portable `portalocker` backend on supported
POSIX and Windows systems. Every mutation must acquire the stable sibling
`kptncook.json.lock` before rereading and replacing data; lock failures refuse the
write with a repository error. Use a local filesystem with working advisory locks,
and retain lock files while writers may run. Read-only recipe/export access
remains available when locking fails.

Repository-backed commands now warn if `kptncook.json` contains stored entries
that can no longer be parsed into full recipes. The command continues with the
valid recipes instead of silently dropping the bad entries. If you need to
recover, inspect `~/.kptncook/kptncook.json` and the previous snapshot in
`~/.kptncook/kptncook.json.backup`.

Writes serialize complete UTF-8 staging within the same 64 MiB limit as reads
before touching the primary or recovery backup. Over-capacity additions fail
without changing either snapshot; export and remove unneeded recipes, then retry
the smaller batch. Every successful write remains reloadable.

Before replacing the repository, its previous contents are copied to a sibling
temporary backup, flushed and fsynced, then atomically moved over `.backup`.
If copying, syncing or replacing the backup fails, the write reports an error
and leaves both the main file and the existing recovery snapshot intact.
Temporary files are cleaned up on failure. The main file is also replaced
atomically; a later main-write failure leaves the completed backup available.

## Delete recipes

Use indices from `kptncook list-recipes` or pass one or more `--oid` values.

```shell
$ kptncook delete-recipes 0 2
$ kptncook delete-recipes --oid 635a68635100007500061cd7 --oid 635a68635100007500061cd8
$ kptncook delete-recipes 0 --force
```

## Dailies

Filter dailies by API fields such as recipeFilter (for example `veggie`) and
zone (timezone offset like `+02:00`). Add `--save` to store the daily recipes
in the local repository.

By default, no subscription status is sent. `--not-subscribed` sends
`isSubscribed=false`, as the free KptnCook app does; in a comparison on
2026-10-27 this changed only the retailer data in the response, which no
exporter uses. There is no option to claim a subscription.

```shell
$ kptncook dailies
$ kptncook dailies --zone +02:00 --recipe-filter veggie --save
$ kptncook dailies --not-subscribed --save
```

## Discovery

Use `discovery-screen` to list discovery list ids and types. `discovery-list`
requires `--list-type` (`latest`, `recommended`, `curated`, `automated`).
List types are case-insensitive; short flags are `-t` for `--list-type` and
`-i` for `--list-id`. For `curated` and `automated`, pass the list id from
`discovery-screen` with `--list-id`. For `latest` and `recommended`, omit
`--list-id`.

The CLI and public client accept only these four list types. List IDs are single
opaque path components containing Unicode letters/numbers or `-._~`; `.` and `..`,
percent escapes, separators, controls and query/fragment markers are refused
before authenticated requests. Unicode IDs are percent-encoded in the route.
Outer spaces are trimmed, as in the CLI's other list input normalization.

Recipe identifiers retain the supported `identifier`, `uid`, `_id`/`$oid`,
`id`, `recipeId`/`recipe_id` and recipe/summary wrapper shapes, up to 16 dictionary
levels. Cycles or deeper wrappers stop resolution with a payload-free error.
Favorites backup rejects the whole identifier batch before recipe search or
storage, including when another favorite is valid; no partial backup is saved.
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
default API key, and optionally fetch an access token. It is available both as a
`kptncook` subcommand and as a standalone command:

```shell
$ kptncook setup
# or, equivalently:
$ kptncook-setup
```

The `.env` is written to `$KPTNCOOK_HOME/.env` when `KPTNCOOK_HOME` is set,
otherwise to `~/.kptncook/.env`.

Configuration is validated lazily. If you run a command before configuring the
API key, kptncook will scaffold the `.env` file when that command first needs
settings and then exit; just re-run the command after updating the file. `help`
and other non-config commands do not force `.env` validation up front.

Then set environment variables in the `~/.kptncook/.env` file (or directly in your shell). You'll need to set at least the `KPTNCOOK_API_KEY` variable. If you want to sync the recipes with mealie, set `MEALIE_API_TOKEN` or `MEALIE_USERNAME`/`MEALIE_PASSWORD`.

When kptncook creates or updates `~/.kptncook/.env`, it applies owner-only read/write permissions (`0600`) where the platform supports it, because the file may contain access tokens, passwords, or trusted shell commands. Updates are written to an owner-only temporary file next to `.env` and then moved into place, so a failed write leaves the previous file intact.

Cooperating setup/token writers hold an OS file lock across rereading and atomic
publication, so concurrent updates preserve other keys. Paths are resolved before
locking, including symlink targets. The stable sibling `.<filename>.lock` (for
`.env`, `..env.lock`) is retained after use; do not delete it while writers may
be running. Manual editors must coordinate separately. Setup and runtime use python-dotenv
syntax, including single/double quotes, `export`, spaced assignments and inline
comments. Updates quote literal values (including backslashes, quotes, spaces,
`#` and newlines) and preserve unrelated lines. Normal python-dotenv `${VAR}`
interpolation still applies when reading configuration. Scaffolding uses the same
lock and does not overwrite an existing nonempty configuration. If the filesystem
cannot provide a lock, updates fail without replacing the configuration.

**Important:** The `.env` file must be created in the `~/.kptncook/` directory, NOT in the installation directory or by editing the `kptncook` executable.

If you want to back up your favorite recipes from KptnCook, you have to set the `KPTNCOOK_ACCESS_TOKEN` variable as well. You can obtain the access token by running `kptncook kptncook-access-token`; the command fetches the token and saves `KPTNCOOK_ACCESS_TOKEN` into `~/.kptncook/.env` instead of printing the raw token to stdout. But you need a kptncook account to do that.
Beware: If you don't have a kptncook account, you'll lose all your favorites by creating a new one.

Optional API defaults for discovery/dailies/onboarding requests:

- `KPTNCOOK_LANG` (default `de`)
- `KPTNCOOK_STORE` (default `de`)
- `KPTNCOOK_PREFERENCES` (for example `rt:diet_vegetarian,`)

Optional image hosts:

- `KPTNCOOK_IMAGE_HOSTS`: comma-separated extra host names allowed to receive
  the API key with image downloads (for example `images.example.org`), in
  addition to KptnCook's image hosts. Entries are bare host names: no scheme,
  port, path, user name or IP address.

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

These commands are executed through your local shell. Only configure commands you trust and fully control, and treat the `.env` file as sensitive because it can contain shell commands as well as tokens and passwords.

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

# Optional: Mealie inventory budget (positive, finite, at most 86400 seconds)
MEALIE_INVENTORY_TIMEOUT_SECONDS=120

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

See [CONTRIBUTING.md](CONTRIBUTING.md) for notes on how this repository uses
coding-agent assisted maintenance.

## Install Development Version

- Checkout source repository
- Install uv if not already installed
- Install just if not already installed

Install the development environment:
```shell
$ uv sync
```

uv chooses an interpreter that matches `requires-python` (3.10 or newer,
including 3.15) through its interpreter discovery: it reuses an existing
project `.venv` when that still matches, and otherwise picks a Python based on
its discovery rules (for example a `.python-version` file or the order of
interpreters on `PATH`), so the version can differ between machines. To use a
specific interpreter, pass it explicitly, for example `uv sync -p 3.13`; uv
recreates `.venv` if it was built for a different Python. The locked compiled
dependencies ship prebuilt
wheels for these versions, so no Rust toolchain is needed; a few small packages
(such as `pyyaml` on 3.15 or `watchdog` on macOS) are still built locally.

Install the git hooks:
```shell
$ uv run prek install -f
```

## Quality Gates (Required)

```shell
$ just check
```

This runs `just lint`, `just typecheck`, and `just test` in sequence. You can
also run each step individually:

The same three gates run in GitHub Actions for pushes and pull requests.

Update hook revisions:

```shell
$ just update-hooks
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

CLI browsing and export contracts run without accounts, network services or
export files. Run `uv run pytest tests/cli_browsing_test.py` to exercise the
registered commands with synthetic workflow results: discovery display and
`--no-quick-search`, ingredient/tag normalization, explicit `--save`, export
format and optional recipe ID dispatch, warnings and failure exits. Assertions
use a fixed console width and strip error styling to work on colored CI consoles.

## Disposable Mealie Integration Tests

`tests/mealie_integration_test.py` exercises real HTTP against an externally
provisioned **disposable** Mealie instance. It is skipped by default and requires
all three variables below; never point it at your personal or production server.
The URL is the API base (including `/api`), and the token must permit recipe
creation, inventory reads, media uploads, and deletion.

```shell
$ export MEALIE_TEST_URL=http://127.0.0.1:19001/api
$ read -rs MEALIE_TEST_TOKEN  # paste a disposable server token, then press Enter
$ export MEALIE_TEST_TOKEN
$ export MEALIE_TEST_DISPOSABLE=1
$ uv run pytest -q tests/mealie_integration_test.py
$ unset MEALIE_TEST_TOKEN MEALIE_TEST_URL MEALIE_TEST_DISPOSABLE
```

Run the same tests separately against pinned
`ghcr.io/mealie-recipes/mealie:v1.12.0` and
`ghcr.io/mealie-recipes/mealie:v3.28.0` instances. For example, start an isolated
SQLite container on a localhost-only port (use another name/port for the other
version):

```shell
$ docker run -d --name kptncook-mealie-v1-test \
    -p 127.0.0.1:19001:9000 \
    -e DB_ENGINE=sqlite -e ALLOW_SIGNUP=false \
    ghcr.io/mealie-recipes/mealie:v1.12.0
```

Wait until `GET /api/app/about` succeeds before obtaining a token from the
instance's login/API-token UI. The upstream installation checklists for
[v1.12.0](https://github.com/mealie-recipes/mealie/blob/v1.12.0/docs/docs/documentation/getting-started/installation/installation-checklist.md)
and [v3.28.0](https://github.com/mealie-recipes/mealie/blob/v3.28.0/docs/docs/documentation/getting-started/installation/installation-checklist.md)
list the initial login as `changeme@example.com` / `MyPassword` (unless overridden).
The client discovers the server version via
`GET /app/about`; no test-specific version override is needed. Keep tokens out of
logs, shell history, and tracked files. No KptnCook account or external KptnCook
media is used: repository/conversion fixtures are local, and a tiny step image is
served from an ephemeral localhost HTTP server.

Coverage includes same-title recipes with distinct KptnCook identities, a
second sync with no writes, ingredients/tags/instructions/nutrition roundtrip,
and step-media enrichment via PATCH. A real HTTP transport also injects a read
timeout **after** the server successfully commits an archive import; the next
sync must discover the persisted identity without deleting or retrying it.
Extras assertions allow Mealie to add ownership metadata.

A `finally` inventory cleanup deletes only recipes carrying the exact randomly
generated test identities, even when a committed response is lost. Recipe tags,
foods, and units may remain, so discard the whole instance after testing. Stop
and remove every test container (including after a failed run):

```shell
$ docker rm -f -v kptncook-mealie-v1-test
# If provisioned:
$ docker rm -f -v kptncook-mealie-v3-test
```

## Lines of Code

Show a Rich-formatted summary of lines by language, area (src, tests, scripts,
tooling), and directory:

```shell
$ just loc
```

During the `slopscope` pre-release phase, this uses a sibling `../slopscope`
checkout. It uses `cloc` when available and falls back to Python physical-line
counting when `cloc` is not installed. Compared to the previous project-local
counter, totals no longer include that removed counter implementation itself;
for example, the previous `cloc` total was 51 files and 6867 code lines, while
the migrated `slopscope` report is 50 files and 6437 code lines.

## Release Process

Use the helper commands below from a clean worktree:

1. Prepare the release metadata:
   ```shell
   $ just release-prepare <version>
   ```
   Preparation validates both replacement texts before writing either file.
   Each file is replaced atomically; if the second write fails, the changelog
   is restored. Invalid inputs preserve both original files.

   This moves the current `Unreleased` section into a dated changelog entry and
   bumps `version` in `pyproject.toml`. `kptncook.__version__` is derived from
   package metadata, so no second version file needs updating.
2. Run quality gates:
   ```shell
   $ just check
   ```
3. Review the generated release notes if needed:
   ```shell
   $ just release-notes <version>
   ```
4. Commit the release prep and create a tag:
   ```shell
   $ git commit -am "release: v<version>"
   $ git tag -a v<version> -m "v<version>"
   ```
5. Push commits and tags:
   ```shell
   $ git push
   $ git push --tags
   ```
6. Draft the GitHub release from the matching changelog section:
   ```shell
   $ just release-draft <version>
   ```
7. Publish the GitHub release, then rebuild and publish the package as
   described in [Publish Packages](#publish-packages).

## Publish Packages

After running `just check`, rebuild the distributions for the new version and
publish them to PyPI using uv:

```shell
$ just clean-build
$ uv build
$ uv publish --token your_token
```

`uv publish` uploads whatever is in `dist/`, so skipping the rebuild re-uploads
the previous release's files instead of the new version.

## Development dependency checks

Run `uv sync --frozen`, then `just lint`, `just typecheck`, and `just test`.
The recipe client supports Python 3.10+. Notebook tooling (JupyterLab in the
`dev` group) requires Python 3.11.5+; older interpreters install the other
development tools without JupyterLab. Smoke-check notebooks with
`uv run --frozen python -c "import jupyterlab, tornado, setuptools"` on Python 3.11.5+.

The notebook dependency graph locks SoupSieve 4.1 for CVE-2026-126190, with
Beautiful Soup 4.14.3. SoupSieve requires Python 3.11.5+, so the notebook marker
keeps older interpreters from falling back to an obsolete Beautiful Soup release.
The recipe client continues to support Python 3.10+.

The notebook/build lock uses setuptools 84.2.1 and Tornado 6.6 to address
PYSEC-2026-3447, GHSA-xmj3-9gf7-h52w and CVE-2026-116577/115927/116578.
These findings concern development tools; they do not establish a recipe API exploit.

Run the same frozen dependency audit as CI with
`uv run --frozen python scripts/audit_python.py` (Python 3.13 in CI).
It exports runtime dependencies and all dependency groups, including the default
`dev` group's notebook/build tooling; there are no optional extras. The pinned
pip-audit 2.10.1 checks registry packages applicable to the active interpreter
and platform. This does not audit other interpreter/platform marker branches or
separate build-backend/hook environments. Export errors and advisory findings
both fail the command; it never updates the lock. CI has read-only repository
permissions and a ten-minute audit timeout.
