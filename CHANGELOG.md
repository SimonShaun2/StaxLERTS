# Changelog

## Unreleased

- Phase 1 removals. Discarded the untracked `staxbot_2_4.pine` draft; it was never committed. Moved `staxbot_2_0.pine`, `staxbot_2_2.pine`, and `staxbot_2_3.pine` to `archive/`. `staxbot_2_1.pine` is unchanged and remains the rebuild base. `bot/execution_bot.py`, `bot/watch.py`, and `docs/alerts-thread.md` were not edited.
- Phase 1 condition. `docs/alerts-thread.md` now names `archive/staxbot_2_3.pine`. No other line in that file changed. A read of every input, function, and statement binding in `staxbot_2_1.pine` found nothing unused and no unreachable branch, so that script was not edited.

- StaxBot 2.4.0 in `staxbot_2_4_0.pine`. Shelf and gap plans arm independently. Tight, Medium, and Large share the leg extreme and change only the buffer. A fill is an overlap on a later bar. The daily count moves only on that fill. `staxbot_2_1.pine` was not edited.
- `docs/strategy-logic.md` and the README now describe StaxBot 2.4.0.
- 2.4.1. `planSeq` and `hudReject` are fields on `Engine`. Functions update those fields instead of the globals. `moveSeq` and `moveIdNow` stay globals because only the main script writes them.
- 2.4.1 is its own file, `staxbot_2_4_1.pine`. `staxbot_2_4_0.pine` is the 2.4.0 script again.
