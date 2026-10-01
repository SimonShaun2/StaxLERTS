# Strategy logic, StaxBot 2.4.1

`staxbot_2_4_0.pine` runs once per bar, on the bar's close. It does not call `strategy.entry` or `strategy.exit`. The drawing is the plan. Alerts are `alert()` calls.

`staxbot_2_1.pine` is the untouched base this version was built from. Load **StaxBot 2.4.1**, saved as a new script. The legend reads **Stax 2.4.1**. The HUD reads **STAXBOT 2.4.1**.

## 1. Settings

A strategy preset can change direction, session, which stop buffer is selected, breakeven, trailing stop, and the daily cap.

It cannot change Take Profit (R), the entry, or the scenario. Take Profit (R) is always the input.

Tight, Medium, and Large all use the same stop anchor: the extreme of the leg that broke the shelf. The preset changes only the buffer.

- Tight adds its tick buffer.
- Medium adds its tick buffer. Targets are measured from this distance.
- Large adds its ATR buffer.

The HUD shows the reference R of the targets and the actual R of the selected stop. Switching the preset moves the stop and the actual R. It does not move the entry or the targets.

## 2. Latest swing

`ta.pivothigh` and `ta.pivotlow` confirm a swing `Swing Length` bars after it prints. Every new pivot replaces that side of the range. A later lower high replaces the high. A later higher low replaces the low. The script does not keep an older extreme, and the first close through a level does not spend it.

The leg extreme is the lowest low, or the highest high, from that swing to the break, capped by `Leg Lookback`. The price is stored on the plan. Later bars do not move it.

## 3. One displaced close

`Displaced Close` is the only definition, used for the break, a later re-arm, and grade flag 1.

- `Body >= ATR x` (default 1.0): the candle body is at least that multiple of ATR(14).
- `Close beyond zone by zone width`: the close is past the broken swing by at least the swing-range height.

A level can arm again after its plan has resolved, when a new displaced close goes through it from the other side.

## 4. Shelf plan (B)

A displaced close through the latest swing, the first such close of this departure, opens a new move.

Older armed plans in that same direction, from an older move, become `REPLACED`.

The shelf plan then arms when the session is open, the pause is not active, a target is enabled, the volume filter passes, the direction is allowed, the optional shelf-height filter passes, the grade passes, and the geometry check passes.

- Entry is the broken swing.
- Stop anchor is the leg extreme.
- A missing gap does not block this plan.

## 5. Gap plan (A)

A fair-value gap in the move's direction, inside `Gap Window After The Break`, arms a second plan on the same move. The bull gap is `low > high[2]` with a bullish middle bar. The bear gap is `high < low[2]` with a bearish middle bar.

The gap entry is the near edge, the midpoint, or the far edge. The stop anchor is the same leg extreme as the shelf plan. It is not the gap edge.

`Min Gap Height` defaults to off, so a tight gap is not rejected.

## 6. Plans at once

Several plans can be armed, in both directions. `Live Positions At Once` defaults to 1. A live trade blocks new fills. It does not block new plans.

On a bar where more than one armed plan could fill, the script fills the entry closest to the open in the direction the bar traded. A down bar takes the higher entry first. An up bar takes the lower entry first. The other armed plan from that same move is `CANCELLED`.

The daily count increases only when a plan becomes `TRIGGERED`. Plans can still arm after the daily cap. They cannot fill until the next day.

## 7. Fill order

On an armed plan, starting the bar after it was created:

1. A close back through the shelf sets `INVALIDATED`, scenario E, reason `reclaim close`. No fill.
2. A bar that trades both the entry and the stop does not fill.
3. A fill requires the bar to trade the entry (`low <= entry <= high`) and the close to stay on the trade side of the shelf. The state is `TRIGGERED` on that bar and `LIVE` from the next bar. Exit checks start on the next bar.

The 3:00–5:00 PM Chicago pause, while enabled, blocks step 3 and blocks new plans. The window and the timezone are inputs.

If price trades the nearest target and does not trade the entry, the plan becomes `EXPIRED`, scenario D, reason `ran without retest`. The alert is `plan_cancel`. There is no entry alert and the count does not move.

A plan also expires after `Plan Expires After N Bars`, or when a filtered session ends.

## 8. Geometry

Before a plan arms, a short must satisfy `stop > entry > TP1 > TP2 > TP3`. A long is the mirror. Each target's distance divided by the reference risk must equal its R multiple.

If that fails, the plan is not armed. The Pine log records the plan id, and the HUD reads `PLAN REJECTED: geometry.`

A stop that passes that order but breaks the min or max stop distance is not armed either. The HUD then reads `PLAN REJECTED: stop distance.`

## 9. Live trade

Stop, then targets, then the reclaim exit, then breakeven or trail. Trail steps use reference R, so 1R is the TP1 distance. The stop never moves backward.

`Reclaim Exit On Live Trades` defaults to on. A live close back through the shelf exits at that close. Off leaves that rule for the pre-fill check only.

## 10. Grade

| Flag | Meaning |
| --- | --- |
| 1 | The move's displaced close |
| 2 | EMA bias agrees with the plan |
| 3 | Volume is above its average |
| 4 | London 03:00–06:00 or New York 08:20–11:30 in the chart timezone |
| 5 | Gap present on scenario A, or swing age within the flag-5 input on scenario B |

Five flags is A+. Four flags, including displacement or the session flag, is A. Anything else is B. `Minimum Grade` defaults to off.

The D / 4H / 1H / 15m / 5m cloud is display only unless `Only Trade With EMA Bias` is on. That filter uses the chart timeframe.

## 11. Drawings and HUD

A shelf plan draws a `RANGE` box. A gap plan draws a `GAP` box. Entry, stop, and enabled targets are lines.

`TRIGGERED` and `LIVE` and `ARMED` stay in color. `EXPIRED`, `INVALIDATED`, `REPLACED`, `CANCELLED`, and `CLOSED` stop extending. They turn grey and keep a state label, or they are removed, from the `Resolved Plans` input.

The HUD state is `WATCH`, `ARMED`, `TRIGGERED`, `LIVE`, `PAUSE`, or the resolve reason on the bar it happens. An armed move reads like `Move 3: shelf plan 7750 and gap plan 7758`. Changing an input after a plan exists shows `INPUTS CHANGED`. Recreate the alert.

Scenario C is an input and it is not built. Turning it on does not arm a sweep-and-reclaim plan. The HUD says so.

## 12. Alert payload

Every `alert()` JSON includes `plan_id`, `move_id`, `scenario`, `state`, `entry`, `stop`, `stop_preset`, `targets`, `grade`, and `timeframe`, plus the existing `event`, `setupId`, `side`, `ticker`, and `root` fields.

| Event | When |
| --- | --- |
| `plan` | The plan arms |
| `entry` | The plan fills |
| `plan_cancel` | Expired, invalidated, replaced, or a sibling is cancelled. Scenario D uses reason `ran without retest` |
| `exit` | Stop, target, reclaim, or session flatten, when exit alerts are on |
| `stop_update` | The live stop moves, when that alert is on |

The paper desk is a separate project and is not changed by this script. It still stores one plan. A second `plan` alert replaces that slot. A `plan_cancel` clears it only when the setup id matches.
