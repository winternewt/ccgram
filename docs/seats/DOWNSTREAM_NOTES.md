# Downstream seat — notes

State as of 2026-10-17, the first session to hold the seat.

## Upstream

- Last release taken: `v4.16.0`, by replay (the fork's last rebuild, 2026-10-12/13). No release past
  it yet. From the next one on: merges (the priming, "History"); its delta is read from `v4.16.0`.
- `upstream-main` on `origin` moved to `upstream/main` (0b8ae82) on 2026-10-17.

## Patch table

- Written in `docs/FORK.md`: 20 rows (not the 14 the adoption counted; six commits landed on
  2026-10-17). 14 upstream-bound, all unfiled; 6 local; 0 retired. Fates set by the seat from the
  commit messages, for the person to move.

## Seats kit

- `.claude/settings.local.json` (untracked) pins v0.1.74 (adopted 2026-10-19); the seat reloads
  itself (the priming, "Reloading plugins itself"). The adoption pull request is merged, so the kit is trusted and each release
  is adopted on its `announcements` post (`bin/move-to-tag vX` from the plugin's copy, through its
  `bin/py`).
