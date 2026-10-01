# Breakaway Bot — Stax Edition

An open Pine Script v6 re-implementation of the Breakaway model. The chart
script is `staxbot_2_4_0.pine`, saved in TradingView as **StaxBot 2.4.0**. A
shelf break arms without a gap. A gap inside the window is a second plan. Tight,
Medium, and Large all use the leg extreme and change only the buffer. Alerts
are formatted for the
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
| `staxbot_2_4_0.pine` | The chart. Saved in TradingView as **StaxBot 2.4.0**. The legend reads **Stax 2.4.0**. The HUD reads **STAXBOT 2.4.0**. Shelf plan and gap plan, leg-extreme stop, fill on a later bar. |
| `staxbot_2_1.pine` | Untouched rebuild base. Do not load it over 2.4.0. |
| `tools/engine_rules.py` | The fill, stop, and grade checks that match the 2.4.0 rules. |
| `archive/staxbot_2_0.pine` | Archived. Gap detection and the alert-only HUD. The bar-1001 history lookup is why it left the active path. |
| `archive/staxbot_2_2.pine` | Archived. Same-bar fill-and-exit counting. Its Medium stop is the displacement candle, and that is the script that filled the day at 5/5. |
| `archive/staxbot_2_3.pine` | Archived. Plan/fill split, Take Profit (R) input, and RANGE drawing stay available to port. Oldest-extreme range memory is why it is not the base. |
| `bot/execution_bot.py` | The paper desk at `http://127.0.0.1:8791`. It sizes contracts from its own risk setting and uses the prices in the alert. |
| `bot/watch.py` | Mirrors the TradingView plan on the desk. It does not invent entry, stop, or target prices. |
| `docs/strategy-logic.md` | Bar-by-bar description of every rule and setting. |
| `docs/alerts-thread.md` | Standing split: this repo's chart is accepted, and the alerts chat does not edit Pine. |
| `tools/mock_stax_webhook.py` | Payload checker for the Stax options webhook format. |

## What takes the trades

The Pine script does not send an order to a broker. On each closed bar it decides whether a Breakaway setup exists. The execution bot is the process that takes it.

```bash
python3 bot/execution_bot.py --selftest
python3 bot/execution_bot.py --port 8791
```

Open the desk, then in TradingView set **Alert Payload** to **Generic JSON** and create an alert:

- Condition: this strategy, **alert() function calls only**
- The message box can stay empty. The script sends the JSON itself.
- Webhook URL: `http://127.0.0.1:8791/webhook/trade-signal` while you are on this machine. TradingView's servers cannot see localhost, so a public HTTPS tunnel is required before a real alert will arrive.

A `plan` alert stores the chart's entry, stop, and targets. An `entry` alert opens the paper position at those prices. The desk chooses the contract count from **Risk per trade**. It does not recompute the target from its own R. An `exit` alert closes the contracts it allocated.

**Take sample trades** on the desk runs two MES round-trips so you can see a fill without waiting for the chart. Leave the forward URL blank for futures. Stax's webhook expects an options ticker (`SPY260930C660.0`), not a futures root. Paste that URL only when the chart is the underlying and the payload is **Stax Options Webhook**.

StaxBot is one script. Another watch is a change to the chart inputs, not a new file. The chart plan is price and R only: TP1 is `Take Profit (R)` times the stop distance, TP2 is twice that, and TP3 is three times that. Turn each target on or off and set its weight in the inputs. Weights are shares, not contracts. Defaults match the original replay's single 1R target: TP1 on, TP2 off, TP3 off, minimum grade Off. Dollar loss, dollar profit, and contract size are not chart inputs. The account is not part of the chart or the alerts chat. Say what you are trading before the session, and say it again when that changes. The desk's **Risk per trade** setting sizes the position from the alert's stop distance. The charts it watches are the December 2026 contracts: MNQZ2026, MGCZ2026, MESZ2026, M2KZ2026, and MYMZ2026.

TradingView copies the script inputs into an alert when the alert is created. Editing the chart later does not edit that alert, and the drawn plan does not move. The HUD says **INPUTS CHANGED** when the live inputs no longer match the plan. Delete the old alert and create it again. The script also puts a `settingsId` on the payload so the desk can see that an entry was built from a different snapshot than the armed plan. It still uses the armed plan's prices.

## How the strategy trades

1. **Range** — the last confirmed swing high and swing low (`Swing Length` bars each side).
   A minor lower high does not replace the rally high. A minor higher low does not replace the shelf.
2. **Break** — a bar *closes* through the range high (bullish) or range low (bearish).
   Optional displacement filter (`Min Break Candle Body`). A range under Min Range Height (default 2 ATR) does not arm.
3. **Plan** — that close arms a resting limit. A short rests on the broken shelf with the stop above the rally high. A long rests on the broken high with the stop below the shelf. The HUD says ARMED. The alert is `plan` only.
4. **Entry** — a later bar that trades back to that level, and does not trade the stop. The HUD then says LIVE, and the alert is `entry`. The break bar is not a fill.
5. **Stop** — the far side of the range, plus the tick buffer. SL Type is Range.
6. **Targets** — TP1, TP2, and TP3 are 1×, 2×, and 3× `Take Profit (R)` times the stop
   distance. Only enabled targets are drawn and alerted. Weights are allocation shares.
   A preset does not change Take Profit (R).
7. **Size** — not calculated on the chart. The paper desk sizes from its own risk setting
   after the alert arrives.
8. **Management** — optional breakeven or a trailing ladder (Aggressive / Standard / Wide,
   same steps as the original's v1.9 release notes). The stop never moves backwards.
   Stop and target checks start the bar after the fill.
9. **Coach filters** — direction, session preset (NY AM / NY PM / London / Asia / Overnight /
   Custom / 24-5), volume on the break bar, max trades per day, minimum grade.
   Dollar loss and dollar profit are desk limits, not plan filters.

The resting plan stays while price runs to a target without tagging the entry. It is cancelled if it expires, if a bar closes beyond the stop, if a bar trades both the entry and the stop, if structure flips, or if the session ends.

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
| Max Daily Loss | 0 (off) | Desk limit, not a chart input |
| Daily Profit Target | 0 (off) | Desk limit. Select paper uses a $600 day cap |
| Point Value Override | 0 (auto) | Desk input. The chart does not price from it |
| Risk Per Trade | $100 | Desk **Risk per trade**. It does not move chart prices |
| Take Profit (R) | 1 | `Take Profit (R) = 1.0`, TP1 on, TP2 off, TP3 off |
| FVG Line Check | Strict | Not in StaxBot 2.3. A close beyond the stop cancels a resting plan |
| SL Type | Large | `SL Type = Range`. The stop is the far side of the range |
| Breakeven / +1 Tick | Off / unchecked | `Breakeven = Off`, `BE +1 Tick Buffer = off` |
| Trailing Stop | Off | `Trailing Stop = Off` |
| Volume Filter | Off | `Volume Filter = Off` |
| Show Info Table / Drawings | on / on | same |
| EMA Cloud Color | cyan | `EMA Cloud Color` |

A fresh add-to-chart uses those structure defaults. The chart does not draw strategy
order arrows. A filled long is a `LONG` mark, and an exit is `TP`, `SL`, `BE`, `TRAILED`,
or `FLAT` with the R result. Price tags sit on the plan, to the left of the last plan bar.

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
   underlying — SPY, QQQ, SPX…), add `staxbot_2_4_0.pine` from the Pine Editor, and set
   your inputs. In the **Stax Alerts** group:
   * `Alert Payload = Stax Options Webhook`
   * `Days To Expiration`, `Strikes OTM`, `Strike Step` to taste (0DTE, ATM, step 1 by default)
   * `Underlying Symbol Override` only if the chart root differs from the option root (e.g. `SPX` on an `ES` chart)
3. Create an alert on the strategy:
   * **Condition**: the strategy → **alert() function calls only**
   * **Webhook URL**: your Stax URL for options, or the paper desk for futures
   * Expiration: as long as your TradingView plan allows; alerts that expire fail silently
   * Do not also select order fills. This script does not place strategy orders, so an
     order-fill alert would never fire. Recreate the alert after you change an input.
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
{"source":"staxbot","event":"plan","side":"long","ticker":"MESZ2026","root":"MES",
 "setupId":"MESZ2026:1759234500000:long","settingsId":"1.00|1:1|0:1|0:1|Large|Off|...",
 "entry":5800.00,"stop":5785.60,"target":5814.40,
 "targets":[{"id":"TP1","price":5814.40,"allocation":1,"r":1.00}],
 "timestamp":"2026-09-30T09:35:00-0400"}
```

The payload has no contract quantity and no dollar risk. `allocation` is the weight from
the chart. The desk turns weights into whole contracts. There is no Pine compiler in this
repo, so paste `staxbot_2_4_0.pine` into TradingView and confirm the HUD reads **STAXBOT 2.4.0**
before treating the script as compiled.

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
* **Stop buffer** — Tight, Medium, and Large share the leg extreme. Only the buffer changes. Targets stay on the Medium distance.

## Running it

Paste `staxbot_2_4_0.pine` into a new Pine Editor tab and save it. The script name is
**StaxBot 2.4.0**. Pasting into an older tab keeps the old saved name, so TradingView
will not store this update. The legend reads **Stax 2.4.0** and the HUD reads
**STAXBOT 2.4.0**. The strategy tester will not show order arrows: the plan is the
drawing, not a broker fill. `staxbot_2_1.pine` and `archive/staxbot_2_3.pine` are not the chart to load.
