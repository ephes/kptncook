# Is the `dailies --subscribed/--not-subscribed` option needed? (2026-10-27)

## Context

- Trigger: legal-risk discussion in nextcloud/cookbook#3482 (§ 202c StGB
  concern about using KptnCook's internal API).
- `kptncook dailies --subscribed` sends `isSubscribed=true` to `/dailies`.
  KptnCook sells Premium/PRO subscriptions. If the server trusted this
  client-side claim, the option would let non-paying users claim a paid
  status. That is the most problematic-looking option in the CLI, so the
  question is whether anything needs it.
- No code change is made until this is verified.

## Findings from code and history

1. **Origin.** Issue ephes/kptncook#26 lists endpoints captured from the
   app's traffic, including
   `dailies?recipeFilter=veggie&store=de&zone=%2B02:00&isSubscribed=false&lang=de`.
   The app (non-paying account) sends `false`. Commit `bf2d7fe`
   ("add discovery/dailies/onboarding/ingredient commands (refs #26)")
   turned the captured parameter into a tri-state option. No issue or user
   request asked for it.
2. **Default behavior omits the parameter.** `cli.py` declares
   `is_subscribed: bool | None = None`; `services/workflows.py` passes it
   through; `api.py` `list_dailies()` only adds `isSubscribed` when the value
   is not `None`. So plain `kptncook dailies` never sends `isSubscribed`.
3. **No other dependency.** `git grep -i subscri` shows only: the CLI option,
   the workflow pass-through, the API parameter, one test asserting
   `isSubscribed == "true"` (`tests/discovery_api_test.py`), and the README
   dailies section. Nothing else (sync, save, exporters) reads or sets it.
4. **Server semantics are unknown (hypothesis).** The repository only shows
   that the client forwards a boolean. The help text calls it a filter
   ("Filter daily recipes by subscription status"); the parameter name and the
   captured app URL suggest it may instead describe the user's own
   subscription state. Neither reading is verified: nothing here establishes
   whether the server uses it for content selection, entitlement, analytics,
   or ignores it.

## Live verification (run by the maintainer, 2026-10-27)

Automated calls to the KptnCook API were not made from this session. The
maintainer ran `kptncook dailies` three times on 2026-10-27, within a few
minutes, with default settings (`lang=de`, `store=de`):

- run A: parameter left out
- run B: `--not-subscribed` (`isSubscribed=false`, the value the free app sends)
- run C: parameter left out again (control)

`--subscribed` (`true`) was intentionally not tested. The pretty-printed
outputs were compared by recipe `_id` order and by the set of retailer
product `_id`s (the `retailers`/product entries nested under ingredients).

Results:

- `kptncook dailies` succeeds without `isSubscribed`: **yes**.
- Recipes: all three runs returned the same 6 recipes in the same order.
- A vs C (identical requests): identical set of 511 retailer products. The
  only differences were `updateDate`, `publishDates` and `favoriteCount`
  values, i.e. per-request timestamps and live counters.
- A vs B: same recipes, but the retailer product lists differ: 20 products
  only without the parameter, 37 only with `false` (511 vs 528 products),
  spread over 6 retailer ids (B lists 17, A/C 16). C vs B shows exactly the
  same difference. Since A and C agree, this is evidence that the parameter
  affects retailer selection. With a single B run it does not exclude
  backend variation or a different cache entry for the `false` URL.
- Other fields: after removing retailer product entries and normalizing
  `updateDate`, `publishDates` and `favoriteCount`, A vs C has 0 differing
  lines, and A vs B has 56, all of them fields of about five retailer store
  records (`_id`, `key`, `name`, `status`, `typ`, `country`, `priceUpdate`,
  `mapStatus`, `onlineOrderingState`, `creationDate`, `updateDate`, one
  `rkey`). No recipe field outside retailer data differed in this run.
- No code in `src/` reads retailer data (`git grep -i retailer -- src` is
  empty). The difference only affects the raw payload stored in
  `kptncook.json`; Mealie, Paprika, Tandoor and Markdown exports do not use it.

So, for this configuration, omitting the parameter was not equivalent to
`isSubscribed=false`; the observed difference was confined to retailer
data. Unchecked: other request configurations (`--recipe-filter`, `--zone`,
other `lang`/`store`/`preferences`), repeated `false` runs, other days, and
what `true` does.

## Conclusion

No internal caller or default workflow needs the option: it is off by
default and no other code reads it. It is, however, documented public
surface: README shows `kptncook dailies --subscribed` and
`--not-subscribed --save`, and `KptnCookClient.list_dailies()` accepts
`is_subscribed`. Removing it is therefore a breaking CLI/API change for users
who script those flags, and must be called out as such in the CHANGELOG.

The live check does not say anything about what `--subscribed` (`true`)
returns; that case is deliberately not tested.

The original decision rule assumed one of two outcomes (omitted equals
`false`, or omitted fails). The live check found a third: omitted works but
is not equivalent to `false`. The parameter therefore has an observable
effect, and `--not-subscribed` is not strictly redundant. In the observed
run its effect was limited to retailer data, which no exporter reads; that
it is always limited to retailer data is not established.

Remaining options, decision left to the maintainer:

1. Remove only `--subscribed`, which sends `isSubscribed=true` with an
   untested server effect, and keep `--not-subscribed`. Default
   behavior (parameter left out) is unchanged. Still a breaking CLI change
   for scripts using `--subscribed`.
2. Remove the tri-state option and always send `isSubscribed=false`, the
   value the free app sends. This changes the default raw payload for every
   `dailies` call (observed: retailer data only). Export output is not
   expected to change, since exporters do not read retailer data, but this
   was not verified by running the exporters. Breaking for scripts using
   either flag.
3. Remove the option and keep leaving the parameter out (as originally
   planned). Default behavior unchanged; users lose the ability to request
   the `false` variant. Breaking for scripts using either flag.

Any of these needs the CLI option, workflow pass-through,
`list_dailies(is_subscribed=...)`, the test and README updated, plus a
CHANGELOG entry marked as breaking. `true` should not be exposed in any
option.

## Decision (2026-10-27)

The maintainer chose option 1. `--subscribed` is removed and `isSubscribed=true`
is never sent: `list_dailies()` and the dailies workflow accept only
`is_subscribed=False` or `None` (typed `Literal[False] | None`) and raise
`ValueError` for `True` before any request. `--not-subscribed` and the
default (parameter omitted) are unchanged. CHANGELOG lists this under
breaking changes.
