# Strategy logic, bar by bar

Everything below runs once per bar, on the bar's close, in the order listed. This mirrors the
original bot's v1.9 change to "run once per bar on closed data" so that the chart copy and
TradingView's alert-server copy agree.

## 0. Settings resolution

* A **Strategy Preset** other than `Manual` overrides Direction, Session, TP (R), SL Type,
  Breakeven, Trailing Stop and Max Trades Per Day. All other inputs are always honoured.
* If a **Trailing Stop** preset is on, **Breakeven** is ignored — the ladder's first step is
  breakeven. (The original's rule: "Trail ON? Breakeven OFF.")
* **Point value** = `Point Value Override` if > 0, else `syminfo.pointvalue`.
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
| Max Daily Loss | realized P/L today `>` −limit |
| Daily Profit Target | realized P/L today `<` target |
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
| Target | entry + TP(R) × risk | entry − TP(R) × risk |
| Invalidation (`FVG Line Check`) | Strict: far edge, Relaxed: stop | same |

`buffer = SL Buffer (ticks) × mintick`. The setup is skipped if the stop distance is below
`Min Stop Distance (ticks)` or above `Max Stop Distance (points)`.

Size = `floor(Risk Per Trade / (risk points × point value))`. If that is below 1 contract,
either 1 contract is taken (`Take 1 Contract When Risk Budget Is Too Small` on) or the setup
is skipped.

The bot then places one limit order (`L` or `S`) and a matching stop/limit exit (`XL`/`XS`).
The setup id shown in labels is the bar index of the FVG bar.

## 7. While the order rests

Cancel the order when any of these happen:

* `Setup Expires After N Bars` elapsed
* a bar **closes** beyond the invalidation level (Strict = through the gap, Relaxed = through the stop)
* price reaches the target without filling (missed trade)
* an opposite BOS prints (structure flipped)
* the session ended, or the daily loss / profit cap was hit

The FVG box turns grey when a setup is cancelled; filled setups keep their colour.

## 8. While in a trade

* Filled trades increment the daily counter and draw the entry label (`L_<id> +qty`).
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

* A yellow line marks the live stop once it differs from the original red stop.
* Optional `Flatten Open Trade At Session End`.

## 9. On exit

The exit label `X_L_<id> -qty` carries the result tag:

| Tag | Meaning |
| --- | --- |
| `TP +xR` | target reached |
| `SL -xR` | original stop hit |
| `BE ±0.0R` | moved stop hit near entry |
| `TRAILED +xR` | moved stop hit in profit |
| `FLAT` | session-end flatten |

## 10. Alerts

| Mode | Entry | Exit | Stop update |
| --- | --- | --- | --- |
| Stax Options Webhook | `{timestamp, unmodifiedTicker, …}` — call for longs, put for shorts | not sent (Stax manages exits) | not sent |
| Generic JSON | `event: "entry"` with qty / price / stop / target | `event: "exit"` with reason and realized R | `event: "stop_update"` via `alert()` when enabled |

Delivery:

* **Order fills (strategy alert_message)** — payload rides on the order and fires the instant
  the limit fills. Alert condition: *Order fills only*; message `{{strategy.order.alert_message}}`.
  The resting order is re-issued every bar so the timestamp in the payload is fresh.
* **alert() calls at bar close** — payload is emitted by `alert()` when the fill is detected at
  the close of the fill bar. Alert condition: *alert() function calls only*.

Option contract encoding (Stax mode): `root + YYMMDD + C/P + strike`. Expiration is today +
`Days To Expiration`, rolled forward off weekends. Strike is `close` rounded up (calls) or down
(puts) to `Strike Step`, then pushed `Strikes OTM` steps further out.
