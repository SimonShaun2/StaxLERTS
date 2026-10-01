# Strategy logic, StaxBot 2.5.0

`staxbot_2_5_0.pine` runs the state machine once on the confirmed bar. Realtime ticks can send one provisional `break_forming` alert. They do not arm, fill, or count a trade. The script does not call `strategy.entry` or `strategy.exit`. The drawing is the plan. Confirmed alerts are `alert()` calls, one per bar.

Load the file as a new script. The legend reads **StaxBot**. The HUD reads **STAXBOT 2.5.0**. The version is in the file name, this header, and that HUD header.

## 1. Settings

A strategy preset can change direction, session, which stop is selected, breakeven, trailing stop, and the daily cap.

It cannot change Take Profit (R), the entry, or the scenario. Take Profit (R) is always the input.

Tight, Medium, and Large choose the stop distance. They do not choose the entry, the targets, or whether the plan arms.

- Tight, on a gap plan, is the gap's far edge plus `Tight Buffer` (default 0.05× ATR). On a shelf plan it is the break candle's extreme plus that same buffer. A long uses the candle low. A short uses the candle high. The break candle is the bar that arms the plan.
- Medium is the displacement candle plus `Medium Buffer` (default 0.10× ATR). On a shelf plan that candle is the break bar. On a gap plan it is the middle bar of the gap, which is the 2.0 rule. Targets are R multiples of this distance on every preset. Min stop (default 0.5× ATR) and max stop (default 3× ATR, 0 = off) are checked on this distance.
- Large is the leg extreme plus `Large Buffer` (default 0.25× ATR).

Chart defaults are Swing Length 3, Gap Window 1, Plan Expires 12 bars, Min Gap Height 0.2× ATR, Max Trades Per Day 20, Take Profit 1R, TP1 / TP2 / TP3 on with weights 2 / 1 / 1, and Trailing Stop Standard (BE 1R). Shelf, Gap, and Scenario C are on. Momentum is off. Room check is Reject. The extension filter is on at 3× ATR. Structural targets are off. Higher-timeframe zones are drawn. Range watch and the intrabar break are on. Resolved Plans is Remove. Reclaim, the three buffers, and the min and max stop use ATR from the bar before the arming bar. After the buffers, Tight is pulled back to Medium when it would sit farther out, and Large is pushed out to Medium when it would sit closer. A selected stop closer than the minimum is widened to the minimum. Switching the preset on a fresh calculation moves only the stop line. The geometry check uses the Medium stop.

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
- Tight stop is beyond the break candle. Medium stop is beyond that same candle. Large stop is beyond the leg extreme.
- A missing gap does not block this plan. The Shelf checkbox can turn the shelf plan off. Gap alone still records the move so a gap inside the window can arm.

## 5. Gap plan (A)

A fair-value gap in the move's direction, inside `Gap Window After The Break`, arms a second plan on the same move. The bull gap is `low > high[2]` with a bullish middle bar. The bear gap is `high < low[2]` with a bearish middle bar.

The gap entry is the near edge, the midpoint, or the far edge. The tight stop is beyond the far edge. The medium stop is beyond the middle bar of the gap. The large stop is the leg extreme. A gap plan invalidates on a close beyond that far edge by the reclaim tolerance. The shelf is not the invalidation price.

`Min Gap Height` defaults to 0.2× ATR. `Gap Window After The Break` defaults to 1 bar. The Gap checkbox off does not arm this plan.

## 6. Plans at once

Several plans can be armed, in both directions. `Live Positions At Once` defaults to 1. A live trade blocks new fills. It does not block new plans.

On a bar where more than one armed plan could fill, the script fills the entry closest to the open in the direction the bar traded. A down bar takes the higher entry first. An up bar takes the lower entry first. The other armed plan from that same move is `CANCELLED`.

The daily count increases only when a plan becomes `TRIGGERED`. Plans can still arm after the daily cap. They cannot fill until the count resets. The count resets when a bar opens at or after 5:00 PM America/Chicago, which is the futures session open. It does not reset at midnight New York.

## 7. Fill order

On an armed plan, starting the bar after it was created:

1. A shelf plan invalidates on a close beyond the shelf by at least `Reclaim Tolerance` (default 0.25× the previous bar's ATR). A gap plan invalidates on a close beyond the gap's far edge by that same tolerance. The scenario stays A or B. The reason is `reclaim close`. No fill.
2. A bar that trades both the entry and the stop (`low <= price <= high` on each) does not fill.
3. A shelf fill requires the bar to trade the zone from the shelf to the reclaim tolerance, and the close to stay within that tolerance. A gap fill still requires the bar to trade the gap entry (`low <= entry <= high`). The state is `TRIGGERED` on that bar and `LIVE` from the next bar. Exit checks start on the next bar.

`Reclaim Tolerance` defaults to 0.25× the previous bar's ATR. The live reclaim exit uses the same level as invalidation: the shelf for a shelf plan, the far edge for a gap plan. That distance is also the far side of the shelf entry zone.

The 3:00–5:00 PM Chicago pause, while enabled, blocks step 3 and blocks new plans. The window and the timezone are inputs.

If price is at or beyond the nearest enabled target, and the entry has not traded, the plan becomes `EXPIRED`, scenario D, reason `ran without retest`. A short target is hit when `low <= target`. A long target is hit when `high >= target`. The alert is `plan_cancel`. There is no entry alert and the count does not move.

A plan also expires after `Plan Expires After N Bars`, or when a filtered session ends.

## 8. Geometry

Before a plan arms, a short must satisfy `Medium stop > entry > TP1 > TP2 > TP3`. A long is the mirror. Each target's distance divided by the Medium reference risk must equal its R multiple. A displacement candle that is not beyond the entry rejects the plan, because the targets have no distance.

If that fails, the plan is not armed. The Pine log records the plan id, and the HUD reads `PLAN REJECTED: geometry.`

A Medium stop that passes that order but breaks the min or max stop distance is not armed either. Both limits are ATR multiples of the Medium distance, using the previous bar's ATR, so the preset does not change the result. The HUD then reads `PLAN REJECTED: stop distance.` If the Medium stop passes and the selected stop is closer than the minimum, the selected stop is widened to the minimum and the plan still arms.

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
| 5 | Entry within 0.5 ATR of a higher-timeframe zone |

Five flags is A+. Four flags, including displacement or the session flag, is A. Anything else is B. `Minimum Grade` defaults to off.

The D / 4H / 1H / 15m / 5m cloud is display only unless `Only Trade With EMA Bias` is on. That filter uses the chart timeframe.

## 11. Drawings and HUD

A shelf plan draws a `RANGE` box. A gap plan draws a `GAP` box. The box border is 2px, solid, with a 70% fill. Lines start 24 bars before the arm bar. Entry is a 2px `#E6EAF2` line. The stop is 3px `#FF5C6C`. TP1 is 2px `#3DDC97`. TP2 is 3px `#3DDC97`. TP3 is 3px `#147A4E`. Price tags sit on the right and the target tags include the R multiple. A `BREAK` note sits on the arm bar at the shelf. A trailed stop turns `#9AA3B5`.

`TRIGGERED`, `LIVE`, and `ARMED` stay in color. `Resolved Plans` defaults to Remove, which deletes the drawing. Grey keeps a faint grey entry line and a short state tag (`CLOSED`, `EXPIRED`, `INVALIDATED`, `REPLACED`, or `CANCELLED`), with no price tags, and only the last three resolved plans. Review keeps the last five resolved plans: entry, stop, and target lines in grey, plus that state tag and the reason.

The HUD follows that same row layout. Its header reads `STAXBOT 2.5.0`. The state word is `WATCH`, `RANGE WATCH`, `BREAK FORMING`, `ARMED`, `TRIGGERED`, `LIVE`, `PAUSE`, or the resolve reason on the bar it happens. While a trade is live, the move line describes that trade, and other armed plans collapse to one `Also armed` line. Those other plans draw as a dashed entry only. A trailed stop tag reads `STOP (trail)` and the HUD shows the locked R. Full size lists TP3, then TP2, then TP1, then entry, then stop, and shows or hides rows when a target is toggled. Changing an input after a plan exists shows `INPUTS CHANGED`. Recreate the alert.

Scenario C is `Scenario C: Sweep & Reclaim`, default on. A close beyond a range, a higher-timeframe zone, or an armed shelf, then a later close back inside within the sweep window, arms the reversal at the reclaimed level. A higher-timeframe sweep is A+. A chart-range sweep is capped at B. Arming C cancels armed plans in the failed direction with reason `failed breakout`. Momentum entry is off.

Higher-timeframe zones are prior day, prior week, the last completed Asia, London, and overnight sessions on the Chicago clock, and the last three confirmed swings on the daily, 4H, and 1H. Each zone is the level plus or minus 0.1 ATR. Overlapping zones merge. A zone between entry and TP1 rejects the plan with `PLAN REJECTED: no room (level)`, unless Room Check is Downgrade. A continuation whose entry is more than 3× ATR from the slow EMA drops one grade and is tagged extended. Scenario C is not extended.

## 12. Alert payload

Every alert JSON object includes `plan_id`, `move_id`, `scenario`, `state`, `entry`, `stop`, `stop_preset`, `targets`, `grade`, `level`, `version`, `fp`, and `timeframe`, plus the existing `event`, `setupId`, `side`, `ticker`, and `root` fields. `price` stays the plan entry. An exit puts the fill in `exit_price`. An exit event id includes the target id and the reason.

`alert.freq_once_per_bar_close` does not promise that every `alert()` call on that bar is delivered. The script therefore makes one confirmed `alert()` call per bar. One event sends that JSON object. Two or more events on the same bar send one JSON array of those objects. `break_forming` is a separate once-per-bar alert on the realtime tick, with `provisional` true.

| Event | When |
| --- | --- |
| `plan` | The plan arms |
| `entry` | The plan fills |
| `watch` | One compressed range with a shelf. Not a trade |
| `break_forming` | One provisional realtime shelf break. Not a trade |
| `break_cancelled` | That forming break did not arm at the close |
| `plan_cancel` | Expired, invalidated, replaced, failed breakout, or a sibling is cancelled. Scenario D uses reason `ran without retest` |
| `exit` | Stop, target, reclaim, or session flatten, when exit alerts are on |
| `stop_update` | The live stop moves, when that alert is on. `locked_r` is the R locked by the new stop |

The paper desk is a separate project and is not changed by this script. Attach 2.5.0 alerts only after that desk accepts these fields. `watch` and `break_forming` book nothing.
