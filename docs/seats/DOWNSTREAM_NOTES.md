# Downstream seat — notes

State as of the adoption; no session has held the seat yet.

## Upstream

- Last release taken: `v4.16.0`, by replay (the fork's last rebuild, 2026-10-12/13). From the next
  release on: merges (the priming, "History").
- `upstream-main` on `origin` still names `v4.6.0` (2026-08-16); the first session moves it to
  `upstream/main` by fast-forward (`v4.6.0` is an ancestor).

## Patch table

- Not yet written: `docs/FORK.md` starts from the fork's 14 commits past `v4.16.0` that touch
  `src/` or `tests/` (10 fixes, 4 features; the other 8 are records and plans and get no row), each
  given a row by the first session, with the fates the
  existing records already state (`docs/plans/`, and the commit "record what upstream v4.6.8 took over
  from this fork").

## Seats kit

- Pinned at v0.1.50; `.claude/settings.local.json` (untracked) enables the plugin and names the
  marketplace and the tag. `.claude/settings.json` is the upstream's and is left alone.
- Once the person sets the kit trusted in `docs/TRUST.md` (by merging the adoption pull request), each new release is
  adopted on its `announcements` post: the kit's
  `bin/move-to-tag vX` from the plugin's copy, through its `bin/py`, run in this clone; then the
  person sends `/reload-plugins`.
