# Round 83 resume: review required

Work is parked on `fix/r83-store-durations`; nothing is merged into main.
The required Opus review could not run: OAuth session expired and could not
be refreshed. The harness returned PROVIDER_ERROR; there is no review verdict.
Do not treat the absence of findings as a clean review.

## Implemented bundle B34

Baseline: origin/main `005b84a7882fd2ed68344f9a13cfa966974b75db`.
Both issues were reverified present on the freshly fetched default branch.

- `kptncook-store-write-read-budget-parity`: serialize UTF-8 bytes and enforce
  the shared 64 MiB limit before updating primary or recovery backup. Finish
  and sync staging before backup/publication, retaining the mutation lock.
- `kptncook-recipe-duration-domain-across-exporters`: strict nonnegative integer
  preparation/cooking minutes capped at 525600 (365 days); preparation remains
  required, cooking may be absent/null, and Paprika now preserves explicit zero.
- README and CHANGELOG Unreleased updated; no version bump.

Validation on Python 3.13 with synthetic fixtures:

- New regressions against the original implementation: 42 failed, 79 passed
  in the selected numeric/store files, establishing both bugs and the zero
  Paprika projection bug. Some parameter cases already rejected by baseline
  remain preservation coverage.
- Focused store/numeric/repository checks after fixes: 187 passed.
- `just check`: Ruff and mypy pass; 901 passed, 4 skipped. The four skipped
  tests are opt-in real disposable-Mealie integration tests; B34 does not need
  a live server.
- `uv run --frozen python scripts/audit_python.py`: no known vulnerabilities.
- `git diff --check`: pass.

## Finisher procedure

Use the installed claude-review-loop skill with `--model claude-opus-5-5
--effort high`. Review the entire branch delta from origin/main (the changes
are committed for parking, so pass `--baseline-ref origin/main` to the harness).
Use a fresh run directory, `--record-baseline`, and a stable B34 slice ID.
Fix findings and review repair deltas while making progress, stopping at four
rounds or a repeated required finding as requested by the owner.

Before merge, fetch origin. If main moved, rebase onto origin/main and rerun
the local checks. Merge only reviewed-clean/suggestion-only work with `--no-ff`,
run full local checks after merge and before pushing main, then verify the
remote SHA with `git ls-remote`. No PR, release, deploy, tag, host access, work-app
write, Actions invocation/wait or remote-branch deletion.

Caches may need workspace overrides, for example:

```sh
export UV_CACHE_DIR=/Users/jochen/workspaces/ws-cx-r83-kptncook/.cache/uv
export UV_TOOL_DIR=/Users/jochen/workspaces/ws-cx-r83-kptncook/.cache/uv-tools
just check
uv run --frozen python scripts/audit_python.py
```

For the review harness use writable workspace `--lock-dir` and `--ledger-dir`;
their default cache paths were denied by this sandbox. The actual review attempt
then reached Claude and failed specifically on expired OAuth, with zero tool
calls, no omissions/redactions/truncations, and no completed review.
Local artifacts (outside this repository) are in `../reviews/b34-r1-retry/`,
`../b34-check.log`, `../b34-audit.log`, and `../b34-baseline-tests.log`.

## Remaining bundles, in report order

No work or re-verification has been done on B35–B37 because the owner explicitly
requires stopping after OAuth failure. Re-fetch and verify each issue against
current main before implementation; skip and report anything already fixed.
Read the full slice descriptions, cautions and validation requirements in
`~/workspaces/ws-work-planning/reviews/scout-round83.md`.

1. B35: `kptncook-export-complete-no-clobber-publication` then
   `kptncook-paprika-export-one-recipe-memory`.
2. B36: `kptncook-authenticated-image-origin-boundary` then
   `kptncook-inventory-detail-request-identity-binding`.
3. B37: `kptncook-existing-organizer-ambiguity-refusal` then
   `kptncook-sync-organizer-inventory-amortization`.

Keep fixes minimal, synthetic and platform-independent. Each new regression
must fail without its fix. Update README and Unreleased changelog in each
bundle, run full local gates, and use the same Opus review gate. Do not push
feature branches except to park unfinished work when required.
