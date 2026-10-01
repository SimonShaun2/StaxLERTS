# Changelog

## Unreleased

- Phase 1 removals. Discarded the untracked `staxbot_2_4.pine` draft; it was never committed. Moved `staxbot_2_0.pine`, `staxbot_2_2.pine`, and `staxbot_2_3.pine` to `archive/`. `staxbot_2_1.pine` is unchanged and remains the rebuild base. `bot/execution_bot.py`, `bot/watch.py`, and `docs/alerts-thread.md` were not edited.
- Phase 1 condition. `docs/alerts-thread.md` now names `archive/staxbot_2_3.pine`. No other line in that file changed. A read of every input, function, and statement binding in `staxbot_2_1.pine` found nothing unused and no unreachable branch, so that script was not edited.

## Phase 2 requirements

- Rewrite `docs/strategy-logic.md` to the spec after the script exists.
- Tight, Medium, and Large all anchor at the leg extreme and change only the buffer.
- Replace the `staxbot_2_1.pine` blockers and confirm they are gone from the active script: one-sided fill (line 456), level spent on the first close (lines 271 and 277), stop preset changing the anchor (line 630), gap required to arm (lines 621–622), and grade flag 5 as `bar_index - bosBar <= 3` (line 637).
