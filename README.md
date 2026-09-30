# Breakaway Bot — Stax Edition

An open Pine Script v6 re-implementation of the Breakaway model (range → break of
structure → fair value gap → retrace entry) with alerts formatted for the
[StaxInvesting](https://staxinvesting.com) webhook, so Stax can execute what the
strategy signals.

The original *Breakaway Bot* on TradingView is a closed, invite-only script. Nothing
here is copied from it; the logic below is rebuilt from its published description,
release notes, and the replay screenshots of its settings and info table. Where the
original's exact rules are not public, this README says what this version does instead.

> Educational software. Trading futures and options carries substantial risk. This
> strategy does not guarantee profit. Backtest it, paper trade it through Stax's paper
> mode, and keep a broker-level daily loss limit regardless of any setting here.

## What's in the repo

| Path | Purpose |
| --- | --- |
| `breakaway_bot_stax.pine` | The signal. One script, titled StaxBot. Minimum Grade and Take Profit are settings on the chart. The table reads **StaxBot** and **v1.5**. |
| `bot/execution_bot.py` | The bot that takes the trade. Paper desk at `http://127.0.0.1:8791`. |
| `docs/strategy-logic.md` | Bar-by-bar description of every rule and setting. |
| `tools/mock_stax_webhook.py` | Payload checker for the Stax options webhook format. |

## What takes the trades

The Pine script does not send an order to a broker. On each closed bar it decides whether a Breakaway setup exists. The execution bot is the process that takes it.

```bash
python3 bot/execution_bot.py --selftest
python3 bot/execution_bot.py --port 8791
```

Open the desk, then in TradingView set **Alert Payload** to **Generic JSON** and create an alert:

- Condition: this strategy, **Order fills only**
- Message: `{{strategy.order.alert_message}}`
- Webhook URL: `http://127.0.0.1:8791/webhook/trade-signal` while you are on this machine. TradingView's servers cannot see localhost, so a public HTTPS tunnel is required before a real alert will arrive.

An entry alert opens the paper position at the signal price with the stop and target from the strategy. An exit alert closes it and books P/L using the contract point value (MNQ = $2). The desk refuses a new trade when the daily loss cap, the profit target, or the max-trades count is hit, or when a position is already open.

**Take sample trades** on the desk runs two MNQ round-trips so you can see a fill without waiting for the chart. Leave the forward URL blank for futures. Stax's webhook expects an options ticker (`SPY260930C660.0`), not a futures root. Paste that URL only when the chart is the underlying and the payload is **Stax Options Webhook**.

StaxBot is one script. Another watch is a change to Minimum Grade and Take Profit on the chart, not a new file. The paper desk is a Tradeify Select 25K evaluation: $25,000 start, $1,500 profit target, $1,000 end-of-day trailing drawdown enforced in real time, no daily loss limit, and a 40% consistency rule. The day stops at $600 so the best day can still be 40% of the $1,500 target. Max size is 1 mini or 10 micros. One trade is capped at $250 of stop risk so a single stop cannot spend the trail. An alert from the chart is taken with the grade and target in that alert.

## How the strategy trades

1. **Range** — the last confirmed swing high and swing low (`Swing Length` bars each side).
2. **Break of structure** — a bar *closes* through the range high (bullish) or range low
   (bearish). Optional displacement filter (`Min Break Candle Body`).
3. **Fair value gap** — within `FVG Must Form Within N Bars Of The Break`, a three-candle
   imbalance prints in the direction of the break (bull: `low > high[2]`, bear: `high < low[2]`).
4. **Entry** — one resting **limit order** at the FVG near edge (default), midpoint (CE), or far edge.
5. **Stop** — `Tight` (far edge of the FVG), `Medium` (displacement candle low/high), or
   `Large` (origin of the leg: last swing that started the move), plus a tick buffer.
6. **Target** — `Take Profit (R)` × the stop distance (0.1R–5R).
7. **Size** — `floor(Risk Per Trade / (stop distance × point value))` contracts; point value
   is taken from the exchange (MNQ = $2) unless overridden.
8. **Management** — optional breakeven or a trailing ladder (Aggressive / Standard / Wide,
   same steps as the original's v1.9 release notes). The stop never moves backwards.
9. **Coach filters** — direction, session preset (NY AM / NY PM / London / Asia / Overnight /
   Custom / 24-5), volume on the displacement candle, max trades per day, max daily loss,
   daily profit target.

The setup is cancelled if it expires, if the FVG fails the `FVG Line Check` (Strict: a close
through the far edge of the gap; Relaxed: a close through the stop), if price reaches the
target without filling, if structure flips the other way, or if the session/daily caps close.

Everything is evaluated **once per bar on closed data**, structure only uses confirmed
swings, and the bot holds at most **one resting order and one open position** — the same
anti-repaint posture the original moved to in its v1.9 update.

## Replay settings from your screenshots

These are the values shown in the Breakaway Bot v2.01 dialog and info table (MNQZ2026, 5m),
and the input in this script that carries each one:

| Original setting | Value in replay | This script |
| --- | --- | --- |
| Strategy Preset | Manual | `Strategy Preset = Manual` |
| Allowed Direction | Both | `Allowed Direction = Both` |
| Custom Session | 24/5 (No Filter) | `Session Preset = 24/5 (No Filter)` |
| Custom Start / End | 8:00 PM – 9:30 PM (unused) | `Custom Session = 2000-2130` |
| Timezone | America/New_York | `Timezone = America/New_York` |
| Max Trades Per Day | 5 | `Max Trades Per Day = 5` |
| Max Daily Loss | 0 (off) | `Max Daily Loss = 0` |
| Daily Profit Target | 0 (off) | `Daily Profit Target = 0` |
| Point Value Override | 0 (auto → $2 on MNQ) | `Point Value Override = 0` |
| Risk Per Trade | $100 | `Risk Per Trade = 100` |
| Take Profit (R) | 1 | `Take Profit (R) = 1.0` |
| FVG Line Check | Strict | `FVG Line Check = Strict` |
| SL Type | Large | `SL Type = Large` |
| Breakeven / +1 Tick | Off / unchecked | `Breakeven = Off`, `BE +1 Tick Buffer = off` |
| Trailing Stop | Off | `Trailing Stop = Off` |
| Volume Filter | Off | `Volume Filter = Off` |
| Show Info Table / Drawings | on / on | same |
| EMA Cloud Color | cyan | `EMA Cloud Color` |

These are also the script's defaults, so a fresh add-to-chart matches the replay
configuration. The chart labels follow the original's convention: `L_10509 +2` = long,
setup id 10509 (the FVG bar index), 2 contracts; `X_L_10509 -2` = its exit, tagged with the
result (`TP +1.0R`, `SL -1.0R`, `BE +0.0R`, `TRAILED +1.5R`).

## Wiring it to Stax

Stax's webhook accepts one entry payload per trade and then manages the exit with the rules
of the Stax strategy you attach it to
([webhook docs](https://staxinvesting.com/docs/webhook)):

```json
{ "timestamp": "2026-09-30T09:47:00-0400", "unmodifiedTicker": "SPY260930C660.0" }
```

`unmodifiedTicker` is `SYMBOL + YYMMDD + C/P + STRIKE`. This strategy sends a **call** for a
long setup and a **put** for a short setup, on the underlying of the chart it runs on. Extra
fields the script includes (`source`, `side`, `underlyingEntry`, `underlyingStop`,
`underlyingTarget`, `contracts`) are ignored by Stax but useful in its alert feed.

### Steps

1. In Stax, create (or pick) a strategy, set its alert source to **Bring Your Own (Webhook)**,
   and copy your personal webhook URL. Leave the strategy **disabled** until you have seen test
   alerts arrive.
2. In TradingView, open the chart you want Stax to trade from (for options that is the
   underlying — SPY, QQQ, SPX…), add `breakaway_bot_stax.pine` from the Pine Editor, and set
   your inputs. In the **Stax Alerts** group:
   * `Alert Payload = Stax Options Webhook`
   * `Days To Expiration`, `Strikes OTM`, `Strike Step` to taste (0DTE, ATM, step 1 by default)
   * `Underlying Symbol Override` only if the chart root differs from the option root (e.g. `SPX` on an `ES` chart)
3. Create an alert on the strategy:
   * **Condition**: the strategy → **Order fills only**
   * **Message**: `{{strategy.order.alert_message}}`
   * **Webhook URL**: your Stax URL
   * Expiration: as long as your TradingView plan allows; alerts that expire fail silently
   * If you prefer `Deliver Alerts Via = alert() calls at bar close`, use condition
     **alert() function calls only** instead. Don't select "Order fills and alert()" — that
     doubles the alerts.
4. Fire a couple of paper trades (or run the mock receiver below) and confirm they land in
   Stax's alert feed before enabling the strategy.

The strategy's own stop / target / trailing logic still runs on the chart so you can see what
the model would have done; in **Stax Options Webhook** mode only entries are sent, because
the documented webhook has no exit action — Stax's exit rules take over after the fill.

### Futures and other receivers

Stax's public webhook contract is options-only. For futures (MNQ, ES…), a Tradovate bridge,
or your own receiver, set `Alert Payload = Generic JSON (Futures / Any Webhook)`. Entries,
exits, and (optionally) stop updates are then sent as:

```json
{"source":"breakaway-bot","event":"entry","action":"buy","side":"long","ticker":"MNQZ2026",
 "root":"MNQ","exchange":"CME","qty":2,"orderType":"limit","price":30677.75,"stop":30652.75,
 "target":30702.75,"reason":"fvg_retrace","realizedR":null,"timestamp":"2026-09-30T04:15:00-0400"}
```

If Stax publishes a futures payload format, the `f_genMsg` / `f_staxMsg` helpers at the top of
the script are the only place that needs to change.

## Testing alerts locally

```bash
python3 tools/mock_stax_webhook.py --selftest     # validates sample payloads
python3 tools/mock_stax_webhook.py --port 8787    # local receiver
```

Expose the port with any HTTPS tunnel (TradingView only posts to public 80/443 endpoints) and
paste the tunnel URL into the alert's webhook field. Accepted and rejected payloads are
printed with the decoded contract, and the response mirrors Stax's success / error shape.

## Where this differs from the original

* **Range engine** — the original hinted at a higher-timeframe range that re-arms on a price
  event. This version uses confirmed swing highs/lows on the chart timeframe, which achieves
  the same goal (no repaint, identical on every chart history length) with fewer moving parts.
* **Presets** — the original ships preset names we do not know. Four sensible presets are
  included (NY AM Scalper, NY AM Standard, London Breakout, Overnight Conservative); Manual
  behaves exactly like the replay.
* **Exit alerts to Stax** — not sent, because the public webhook has no exit action.
  Generic JSON mode sends them.
* **Daily reset** — at midnight in the selected timezone (original behaviour unknown).
* **Extra inputs** — entry level (near edge / CE / far edge), min/max stop distance, EMA bias
  filter (off by default), flatten at session end. They default to values that reproduce the
  replay behaviour.

## Running it

There is nothing to build: paste `breakaway_bot_stax.pine` into TradingView's Pine Editor and
click *Add to chart*. The strategy tester shows the backtest; the info table in the top right
mirrors the original's SETTINGS / LIVE panel.
