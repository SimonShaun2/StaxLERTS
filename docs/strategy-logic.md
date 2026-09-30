# Strategy logic, bar by bar

Everything below runs once per bar, on the bar's close, in the order listed. This mirrors the
original bot's v1.9 change to "run once per bar on closed data" so that the chart copy and
TradingView's alert-server copy agree.

## 0. Settings resolution

* A **Strategy Preset** other than `Manual` overrides Direction, Session, TP (R), SL Type,
  Breakeven, Trailing Stop and Max Trades Per Day. All other inputs are always honoured.
* If a **Trailing Stop** preset is on, **Breakeven** is ignored — the ladder's first step is
  breakeven. (The original's rule: "Trail ON? Breakeven OFF.")
* **Point value and dollar risk** are not chart inputs. The paper desk owns contract size.
* **Session** presets (times in the `Timezone` input):

  | Preset | Session string |
  | --- | --- |
  | 24/5 (No Filter) | none |
  | NY AM | 09:30–12:00 |
  | NY PM | 13:00–16:00 |
  | London | 03:00–06:00 |
  | Asia | 20:00–00:00 |
  | Overnight | 18:00–03:00 |
  | Custom | the `Custom Session` input |

## 1. Indicators

* EMA cloud: `EMA Cloud Fast` (9) and `EMA Cloud Slow` (21). Bias = Bull when
  `close > fast > slow`, Bear when `close < fast < slow`, otherwise Flat. Bias is shown in
  the table; it only gates trades when `Only Trade With EMA Bias` is on.
* ATR(14) for the optional range-height and displacement filters.
* SMA(volume, `Volume Average Length`) for the volume filter.

## 2. Range structure

`ta.pivothigh/low(Swing Length, Swing Length)` gives confirmed swings `Swing Length` bars after
they print. The most recent swing high and swing low are the **range**. Each new swing
re-arms its side (a broken high can be broken again only after a new swing high forms).

Optional: `Min Range Height (ATR x)` rejects ranges that are too tight.

## 3. Break of structure

* Bullish BOS: `close > rangeHigh` and the high side is not yet broken.
* Bearish BOS: `close < rangeLow` and the low side is not yet broken.
* Optional displacement filter: `|close - open| >= Min Break Candle Body × ATR`.

On a BOS the bot records the bar and the **leg origin**: the lowest low (bullish) / highest
high (bearish) since the swing that started the move, capped at `Large SL: Max Leg Lookback`.

## 4. Fair value gap

* Bullish FVG on this bar: `low > high[2]` with a bullish middle candle (`close[1] > open[1]`).
  Gap = `high[2]` (bottom) … `low` (top).
* Bearish FVG: `high < low[2]` with a bearish middle candle. Gap = `high` (bottom) … `low[2]` (top).

The FVG counts only if a same-direction BOS happened within
`FVG Must Form Within N Bars Of The Break` bars, and only the first FVG after a BOS is used.

## 5. Coach filters (checked before a setup is created)

| Filter | Rule |
| --- | --- |
| Direction | Longs Only / Shorts Only / Both |
| Session | bar must be inside the session (24/5 = always) |
| Max Trades Per Day | filled trades today `<` the limit |
| Minimum Grade | Off, A (A and A+), or A+ only |
| Enabled targets | at least one of TP1, TP2, TP3 is on |
| Volume Filter | displacement candle volume `>` SMA × 1.0 (Above Average) or × 1.5 (Strong) |
| EMA bias | optional, see §1 |
| One at a time | no open position and no resting order |

## 6. Setup construction

| Element | Long | Short |
| --- | --- | --- |
| Near edge | FVG top (`low`) | FVG bottom (`high`) |
| Far edge | FVG bottom (`high[2]`) | FVG top (`low[2]`) |
| Entry (`Entry Level`) | near edge / midpoint / far edge | same |
| SL `Tight` | far edge − buffer | far edge + buffer |
| SL `Medium` | `low[1]` − buffer | `high[1]` + buffer |
| SL `Large` | min(leg low, `low[1]`) − buffer | max(leg high, `high[1]`) + buffer |
| TP1 / TP2 / TP3 | entry + n × TP(R) × risk, n = 1, 2, 3, only if that target is on | entry − the same distance |
| Invalidation (`FVG Line Check`) | Strict: far edge, Relaxed: stop | same |

`buffer = SL Buffer (ticks) × mintick`. The setup is skipped if the stop distance is below
`Min Stop Distance (ticks)` or above `Max Stop Distance (points)`, or if every target toggle
is off. Weights do not change the prices. A disabled target is omitted from the drawing,
the HUD, and the alert.

The plan is drawn when the setup qualifies. No `strategy.entry` or `strategy.exit` is sent,
so TradingView does not add order arrows or per-target fill tags. A limit fill is simulated
on a later bar. The same bar cannot be both the signal and the fill. If that later bar
trades through the entry and the stop, the stop wins. If it trades through the stop and a
target, the stop wins. The setup id is `ticker + bar time + side`, and the prices plus a
`settingsId` are frozen at that bar.

## 7. While the order rests

Cancel the order when any of these happen:

* `Setup Expires After N Bars` elapsed
* a bar **closes** beyond the invalidation level (Strict = through the gap, Relaxed = through the stop)
* price reaches the target without filling (missed trade)
* an opposite BOS prints (structure flipped)
* the session ended

The plan drawing fades when it is cancelled or closed. Dollar loss and dollar profit caps
are enforced by the paper desk, not by this plan.

## 8. While in a trade

* Filled trades increment the daily counter and draw a `LONG` or `SHORT` mark. The mark is
  not a strategy order.
* Favourable excursion in R is measured from the fill price using the bar's high (long) /
  low (short). The **fill bar itself is skipped** so pre-entry price action never arms
  breakeven (a bug the original fixed in v1.9).
* Breakeven: at `beR` the stop moves to entry (+1 tick if `BE +1 Tick Buffer`).
* Trailing ladder (stop only ever moves in the trade's favour):

  | Preset | BE at | Step 1 | Step 2 | Step 3 |
  | --- | --- | --- | --- | --- |
  | Aggressive | 0.75R | 1.0R → +0.25R | 1.25R → +0.5R | 1.5R → +0.75R |
  | Standard | 1.0R | 1.5R → +0.5R | 2.0R → +1.0R | 2.5R → +1.5R |
  | Wide | 1.5R | 2.0R → +0.5R | 2.5R → +1.0R | 3.0R → +1.5R |

* Once the stop moves, the stop line and STOP tag move to the new price and turn gray. Entry, stop, and targets stay lines.
* Those lines start 24 bars before the signal and run through the current bar. They are solid: entry is 2px, stop and the higher targets are 3px. The price tag sits on the current bar and grows left, off the price scale. The HUD uses small type: title and LIVE, bias and session, the side and grade, then only the enabled targets, entry, and stop. Disabled targets do not leave a blank row.
* Optional `Flatten Open Trade At Session End`.

## 9. On exit

The exit mark carries the result tag:

| Tag | Meaning |
| --- | --- |
| `TP +xR` | target reached |
| `SL -xR` | original stop hit |
| `BE ±0.0R` | moved stop hit near entry |
| `TRAILED +xR` | moved stop hit in profit |
| `FLAT` | session-end flatten |

## 10. Alerts

| Mode | Plan | Entry | Exit | Stop update |
| --- | --- | --- | --- | --- |
| Stax Options Webhook | drawn, not sent | `{timestamp, unmodifiedTicker}` — call for longs, put for shorts | not sent | not sent |
| Generic JSON | `event: "plan"` with entry, stop, enabled targets, weights, R, `settingsId` | `event: "entry"` with the same frozen prices | `event: "exit"` with reason, target id, and realized R | `event: "stop_update"` when enabled |

Delivery is `alert()` at bar close. Create the alert with **alert() function calls only**.
There is no order-fill message. Changing an input after the alert exists does not change
that alert; the HUD shows `INPUTS CHANGED` and the already drawn prices stay put.
`plan_cancel` is sent if the resting plan expires, the gap fails, price runs to the nearest
target without filling, structure flips, or the session ends.

Option contract encoding (Stax mode): `root + YYMMDD + C/P + strike`. Expiration is today +
`Days To Expiration`, rolled forward off weekends. Strike is `close` rounded up (calls) or down
(puts) to `Strike Step`, then pushed `Strikes OTM` steps further out.
