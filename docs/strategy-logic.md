# Strategy logic, StaxBot 2.4.5

`staxbot_2_4_5.pine` runs once per bar, on the bar's close. It does not call `strategy.entry` or `strategy.exit`. The drawing is the plan. Alerts are `alert()` calls.

`staxbot_2_1.pine` is the untouched base this version was built from. Load the file as a new script. The legend reads **StaxBot**. The HUD reads **STAXBOT 2.4.5**. The version is in the file name, this header, and that HUD header.

## 1. Settings

A strategy preset can change direction, session, which stop is selected, breakeven, trailing stop, and the daily cap.

It cannot change Take Profit (R), the entry, or the scenario. Take Profit (R) is always the input.

Tight, Medium, and Large choose the stop distance. They do not choose the entry or the targets.

- Tight, on a gap plan, is the gap's far edge plus the tight tick buffer. On a shelf plan it is the break candle's extreme plus that same tick buffer. A long uses the candle low. A short uses the candle high. The break candle is the bar that arms the plan.
- Medium is the nearest stored swing beyond the entry, plus the medium tick buffer. That swing is the other side of the range the shelf formed on. Targets are R multiples of this distance on every preset.
- Large is the leg extreme plus the large ATR buffer. That is the previous large stop.

Switching the preset on a fresh calculation moves only the stop line. The entry and the target lines stay on the Medium distance. The geometry check uses the selected stop.

## 2. Latest swing

`ta.pivothigh` and `ta.pivotlow` confirm a swing `Swing Length` bars after it prints. Every new pivot replaces that side of the range. A later lower high replaces the high. A later higher low replaces the low. The script does not keep an older extreme, and the first close through a level does not spend it.

The leg extreme is the lowest low, or the highest high, from that swing to the break, capped by `Leg Lookback`. The price is stored on the plan. Later bars do not move it.

## 3. One displaced close

`Displaced Close` is the only definition of the bar that arms a plan inside the break window.

- `Body >= ATR x` (default 1.0): the candle body is at least that multiple of ATR(14).
- `Close beyond zone by zone width`: the close is past the broken swing by at least the swing-range height.

Grade flag 1 is separate. It is a strong displacement: the arming bar's body is at least `Strong Displacement` times ATR (default 1.5). A displaced close smaller than that can still arm, so flag 1 can be false.

A level can arm again after its plan has resolved, when a new close goes through it from the other side and a displaced close follows inside the window.

## 4. Shelf plan (B)

A close through the latest swing opens a break window of `Break Window` bars (default 3), including the crossing bar. A displaced close that is still beyond that shelf inside the window opens a new move, even when the crossing bar itself was not displaced.

Older armed plans in that same direction, from an older move, become `REPLACED`.

The shelf plan then arms when the session is open, the pause is not active, a target is enabled, the volume filter passes, the direction is allowed, the optional shelf-height filter passes, the grade passes, and the geometry check passes.

- Entry is the broken swing.
- Tight stop is beyond the break candle. Medium stop is beyond the nearest swing past that entry. Large stop is beyond the leg extreme.
- A missing gap does not block this plan.

## 5. Gap plan (A)

A fair-value gap in the move's direction, inside `Gap Window After The Break`, arms a second plan on the same move. The bull gap is `low > high[2]` with a bullish middle bar. The bear gap is `high < low[2]` with a bearish middle bar.

The gap entry is the near edge, the midpoint, or the far edge. The tight stop is beyond the far edge. The medium stop is the nearest swing beyond that entry, the same shelf structure as the shelf plan. The large stop is the leg extreme.

`Min Gap Height` defaults to off, so a tight gap is not rejected.

## 6. Plans at once

Several plans can be armed, in both directions. `Live Positions At Once` defaults to 1. A live trade blocks new fills. It does not block new plans.

On a bar where more than one armed plan could fill, the script fills the entry closest to the open in the direction the bar traded. A down bar takes the higher entry first. An up bar takes the lower entry first. The other armed plan from that same move is `CANCELLED`.

The daily count increases only when a plan becomes `TRIGGERED`. Plans can still arm after the daily cap. They cannot fill until the count resets. The count resets when a bar opens at or after 5:00 PM America/Chicago, which is the futures session open. It does not reset at midnight New York.

## 7. Fill order

On an armed plan, starting the bar after it was created:

1. A close beyond the shelf by at least `Reclaim Tolerance` (default 2 ticks) sets `INVALIDATED`. The scenario stays A or B. The reason is `reclaim close`. No fill.
2. A bar that trades both the entry and the stop (`low <= price <= high` on each) does not fill.
3. A fill requires the bar to trade the entry (`low <= entry <= high`) and the close to stay within the reclaim tolerance of the shelf. The state is `TRIGGERED` on that bar and `LIVE` from the next bar. Exit checks start on the next bar.

The 3:00–5:00 PM Chicago pause, while enabled, blocks step 3 and blocks new plans. The window and the timezone are inputs.

If price is at or beyond the nearest enabled target, and the entry has not traded, the plan becomes `EXPIRED`, scenario D, reason `ran without retest`. A short target is hit when `low <= target`. A long target is hit when `high >= target`. The alert is `plan_cancel`. There is no entry alert and the count does not move.

A plan also expires after `Plan Expires After N Bars`, or when a filtered session ends.

## 8. Geometry

Before a plan arms, a short must satisfy `selected stop > entry > TP1 > TP2 > TP3`. A long is the mirror. The selected stop is the one from the active preset. Each target's distance divided by the Medium reference risk must equal its R multiple. A Medium pivot that is not beyond the entry rejects the plan, because the targets have no distance.

If that fails, the plan is not armed. The Pine log records the plan id, and the HUD reads `PLAN REJECTED: geometry.`

A stop that passes that order but breaks the min or max stop distance is not armed either. The HUD then reads `PLAN REJECTED: stop distance.`

## 9. Live trade

Stop, then targets, then the reclaim exit, then breakeven or trail. Trail steps use reference R, so 1R is the TP1 distance. The stop never moves backward.

Stops and targets are one-sided. A short stop is hit when `high >= live stop`, and a short target when `low <= target`. A long is the mirror. The entry fill and the entry-and-stop same-bar check still require the price to trade inside the bar.

When the bar opens beyond the live stop, the exit price is the open: `max(open, stop)` for a short and `min(open, stop)` for a long. A stop that is only traded inside the bar still exits at the stop. Realized R uses that exit price. A target exit keeps the target price.

`Reclaim Exit On Live Trades` defaults to on. A live close beyond the shelf by at least the reclaim tolerance exits at that close. Off leaves that rule for the pre-fill check only.

## 10. Grade

| Flag | Meaning |
| --- | --- |
| 1 | Strong displacement on the arming bar (body >= 1.5× ATR by default) |
| 2 | EMA bias agrees with the plan |
| 3 | Volume is above its average |
| 4 | London 03:00–06:00 or New York 08:20–11:30 in the chart timezone |
| 5 | Gap present on scenario A, or swing age within the flag-5 input on scenario B |

Five flags is A+. Four flags, including displacement or the session flag, is A. Anything else is B. `Minimum Grade` defaults to off.

The D / 4H / 1H / 15m / 5m cloud is display only unless `Only Trade With EMA Bias` is on. That filter uses the chart timeframe.

## 11. Drawings and HUD

A shelf plan draws a `RANGE` box. A gap plan draws a `GAP` box. The box border is 2px, solid, with a 70% fill. Lines start 24 bars before the arm bar. Entry is a 2px `#E6EAF2` line. The stop is 3px `#FF5C6C`. TP1 is 2px `#3DDC97`. TP2 is 3px `#3DDC97`. TP3 is 3px `#147A4E`. Price tags sit on the right and the target tags include the R multiple. A `BREAK` note sits on the arm bar at the shelf. A trailed stop turns `#9AA3B5`.

`TRIGGERED`, `LIVE`, and `ARMED` stay in color. `Resolved Plans` defaults to Remove, which deletes the drawing. Grey keeps a faint grey entry line and a short state tag (`CLOSED`, `EXPIRED`, `INVALIDATED`, `REPLACED`, or `CANCELLED`), with no price tags, and only the last three resolved plans.

The HUD follows that same row layout. Its header reads `STAXBOT 2.4.5`. The state word is `WATCH`, `ARMED`, `TRIGGERED`, `LIVE`, `PAUSE`, or the resolve reason on the bar it happens. Full size lists TP3, then TP2, then TP1, then entry, then stop, and shows or hides rows when a target is toggled. An armed move reads like `Move 3: shelf plan 7750 and gap plan 7758`. Changing an input after a plan exists shows `INPUTS CHANGED`. Recreate the alert.

Scenario C is an input and it is not built. Turning it on does not arm a sweep-and-reclaim plan. The HUD says so.

## 12. Alert payload

Every alert JSON object includes `plan_id`, `move_id`, `scenario`, `state`, `entry`, `stop`, `stop_preset`, `targets`, `grade`, and `timeframe`, plus the existing `event`, `setupId`, `side`, `ticker`, and `root` fields.

`alert.freq_once_per_bar_close` does not promise that every `alert()` call on that bar is delivered. The script therefore makes one `alert()` call per bar. One event sends that JSON object. Two or more events on the same bar send one JSON array of those objects.

| Event | When |
| --- | --- |
| `plan` | The plan arms |
| `entry` | The plan fills |
| `plan_cancel` | Expired, invalidated, replaced, or a sibling is cancelled. Scenario D uses reason `ran without retest` |
| `exit` | Stop, target, reclaim, or session flatten, when exit alerts are on |
| `stop_update` | The live stop moves, when that alert is on |

The paper desk is a separate project and is not changed by this script. It still stores one plan. A second `plan` alert replaces that slot. A `plan_cancel` clears it only when the setup id matches. The alerts build has to accept a JSON array and process each event in order.
