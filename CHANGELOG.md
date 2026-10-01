# Changelog

## Unreleased

- Phase 1 removals. Discarded the untracked `staxbot_2_4.pine` draft; it was never committed. Moved `staxbot_2_0.pine`, `staxbot_2_2.pine`, and `staxbot_2_3.pine` to `archive/`. `staxbot_2_1.pine` is unchanged and remains the rebuild base. `bot/execution_bot.py`, `bot/watch.py`, and `docs/alerts-thread.md` were not edited.
- Phase 1 condition. `docs/alerts-thread.md` now names `archive/staxbot_2_3.pine`. No other line in that file changed. A read of every input, function, and statement binding in `staxbot_2_1.pine` found nothing unused and no unreachable branch, so that script was not edited.

- StaxBot 2.4.0 in `staxbot_2_4_0.pine`. Shelf and gap plans arm independently. Tight, Medium, and Large share the leg extreme and change only the buffer. A fill is an overlap on a later bar. The daily count moves only on that fill. `staxbot_2_1.pine` was not edited.
- `docs/strategy-logic.md` and the README now describe StaxBot 2.4.0.
- 2.4.1. `planSeq` and `hudReject` are fields on `Engine`. Functions update those fields instead of the globals. `moveSeq` and `moveIdNow` stay globals because only the main script writes them.
- 2.4.1 is its own file, `staxbot_2_4_1.pine`. `staxbot_2_4_0.pine` is the 2.4.0 script again.
- 2.4.2 in `staxbot_2_4_2.pine`. Exits and scenario D use one-sided tests. A close through the shelf opens a break window; a displaced close inside it arms. Grade flag 1 is strong displacement. Reclaim needs the tolerance. The daily count resets at 5:00 PM America/Chicago. An invalidated plan keeps scenario A or B. One `alert()` call per bar, and a JSON array when that bar has more than one event.
- 2.4.3 in `staxbot_2_4_3.pine`. A bar that opens beyond the live stop exits at the open, so realized R uses that price. A target exit stays at the target. The alerts build must accept a JSON array and process each event in order. The desk was not changed.
- 2.4.4 in `staxbot_2_4_4.pine`. The plan-cleanup loop counts from the last plan down to the first without `by -1`. Pine v6 takes a positive step and gets the direction from the bounds.
- 2.4.5 in `staxbot_2_4_5.pine`. Stop presets pick the distance: Tight beyond the gap far edge or the break candle, Medium beyond the nearest swing past entry, Large beyond the leg extreme. Targets stay on the Medium distance. Drawings and the HUD follow the 2.3 layout. Resolved plans default to Remove; Grey keeps a faint entry line, a state tag, and the last three. The legend reads StaxBot. The alert payload is unchanged.
- 2.4.6 in `staxbot_2_4_6.pine`. Reclaim, min stop, max stop, and the Tight and Medium buffers are ATR fractions. A shelf fills on the zone from the shelf to the reclaim tolerance. Min and max stop are checked on the Medium distance, and Tight stays at or inside Medium while Large stays at or beyond it, so the preset does not change whether a plan arms. Review keeps the last five resolved plans in grey. The alert payload is unchanged.
