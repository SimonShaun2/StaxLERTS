# StaxLERTS — Stax Session Alerts

TradingView Pine Script v6 tools for futures day trading across the **Asia, London and NY** sessions (all times ET).

| File | What it is |
|---|---|
| `stax_session_alerts.pine` | Indicator. Detects the LDR, ORB and SEQ setups, grades each one and sends Discord/JSON webhook alerts. |
| `stax_session_strategy.pine` | `strategy()` twin with the same logic and inputs, plus orders and a results table for backtesting. |

Built for **5-minute charts**. It works on any micro or mini future (MNQ, NQ, MES, ES, MGC, GC, …) because targets and risk come from `syminfo.pointvalue`. Use a chart timeframe of 15 minutes or less so the 15-minute opening ranges are built from whole bars.

---

## The three setups

| Setup | Idea | Stages alerted |
|---|---|---|
| **LDR** (level sweep reversal) | Price sweeps a marked level and closes back inside. Then comes a displacement candle that breaks the last swing, a retrace to the displacement origin, and a confirmation close. | WATCH, SWEEP, DISPLACEMENT, CONFIRMED |
| **ORB** (15-minute opening range breakout) | A strong close beyond the session's opening range. You enter on the breakout close or on a retest. | BREAKOUT, CONFIRMED, FAILED, SKIPPED |
| **SEQ** (session sequence) | Asia sets a range, London breaks one side, and NY either continues that break or reverses it. | MAP, REVERSAL ARMED, CONTINUATION CONFIRMED, REVERSAL CONFIRMED |

Marked levels: PDH/PDL, Asia high/low, London high/low, and the most recently completed opening range (`AS-OR`, `LO-OR`, `NY-OR`). Every setup resets at the 18:00 reopen.

Every CONFIRMED signal gets:

- Sizing from **Targets and sizing** (dollar or R targets, max-risk size cut).
- A **quality gate**: room to the next level must be at least *min R* stop distances, and T1 must come before that level.
- A **confluence score** out of 8, graded **A+** (6–8), **A** (4–5), **B** (2–3) or **C** (0–1).

Signals below *Minimum grade to alert* still get a chart label but send no alert.

---

## 1. Add the indicator and create the single alert

1. Open a 5-minute futures chart, e.g. `MNQ1!`.
2. Pine Editor → paste `stax_session_alerts.pine` → **Save** → **Add to chart**.
3. Set your inputs (see §3 for presets).
4. Create **one** alert (Alerts panel → **+**):
   - **Condition:** `Stax Session Alerts` → **Any alert() function call**
   - **Expiration:** open-ended, or as far out as your plan allows
   - **Notifications → Webhook URL:** your Discord webhook URL (`https://discord.com/api/webhooks/...`)
   - **Message:** leave it as is. The script builds the message body itself.
5. Save. The Grok Bot routine reads the Discord channel the webhook posts to.

**Message format** (input group *Alert output*):

- **Discord** (default) sends `{"content":"..."}` with one line of text, for example:
  `[NY] LDR LONG CONFIRMED | MNQ 5m | Level NY-ORL | Entry 21436.00 | Stop 21411.00 | T1 21461.00 | T2 21486.00 | Size 3 | Risk $150 | R to next level 2.24 | Grade A+ (7/8): SEQ, stack, HTF level, VWAP, trend, regime, session`
- **JSON** sends a flat object with these fields: `source, setup, session, stage, dir, symbol, tf, level, price, entry, stop, t1, t2, rr_to_obstacle, contracts, risk_usd, grade, score, factors, note, time`. Empty fields are `null`. `note` carries the extra context text, such as the SEQ map line or the ORB skip reason.

Notes:

- Alerts fire once per bar close (`alert.freq_once_per_bar_close`) and only from confirmed bars.
- TradingView throttles alerts that fire too often. The WATCH/SWEEP/DISPLACEMENT stages are the noisiest. Turn off any you don't need in *Alert output*.
- An alert only runs on the symbol and timeframe it was created on. Make one alert per chart or instrument you want covered.

## 2. Re-sync an alert after changing settings

A TradingView alert keeps a **snapshot** of the script version and inputs from when it was created. Changing inputs on the chart does **not** update a running alert. After any input change or script update:

1. Alerts panel → hover the alert → **Edit** (pencil).
2. In **Condition**, reselect the indicator's **current** listing (`Stax Session Alerts` with your new input values), then pick **Any alert() function call** again.
3. **Save**.

If you added a new version of the script to the chart, delete the old copy from the chart first, so you can't pick the stale listing.

## 3. Input presets per instrument and size

Dollar targets and max risk are **whole-position** dollar amounts, so one preset per instrument and size keeps them sensible.

1. Set the inputs for, say, MNQ with 3 contracts, $150 T1, $300 T2 and $200 max risk.
2. Settings dialog → bottom-left **Defaults** → **Save as…** → name it `MNQ x3`.
3. Repeat for each combination, e.g. `NQ x1`, `MES x5`, `MGC x2`.
4. To switch, open the settings, **Defaults** → pick the preset, then **re-sync the alert** (§2).

The point value comes from `syminfo.pointvalue` automatically. Use *Point value override* only if a symbol reports the wrong value. Reference point values: MNQ 2, NQ 20, MES 5, ES 50, MGC 10, GC 100.

## 4. Backtesting with the strategy

1. Pine Editor → paste `stax_session_strategy.pine` → **Add to chart** → open **Strategy Tester**.
2. Use the same inputs or preset as the live indicator.
3. **Strategy** group:
   - *Exit mode*: `Half at T1, BE, rest at T2` (default), `All at T1`, or `All at T2`.
   - *Minimum grade to trade*: `C` (default) trades every filtered signal, so you can compare grades.
   - *Flatten before the daily close*: on by default.
4. **Commission and slippage** are set in **Settings → Properties**. The defaults are $0.62 per contract per side and 1 tick of slippage. Pine can't bind these to regular inputs, so edit them there to match your broker.
5. Each trade's entry ID and comment record its setup, session, grade, side and trade number, e.g. `LDR|NY|A+|L|12` or `SEQ-R|NY|A|S|31`. You can see these in the *List of Trades* tab.
6. The on-chart results table breaks trades down by setup (LDR, ORB, SEQ-C, SEQ-R), session (ASIA, LONDON, NY), grade (A+, A, B, C) and ALL. For each row it shows trade count, win %, average R, profit factor and max drawdown in dollars. A split exit (T1 plus runner) counts as one trade.

The strategy holds one position at a time. A signal that fires while a position is open is skipped. Orders fill at the next bar's open.

## 5. Testing checklist

Collect **at least 30–50 samples per bucket** before trusting a number. Log each bucket separately.

**Bar replay (indicator)**

- [ ] Replay at least 30 sessions per instrument you plan to trade.
- [ ] For every CONFIRMED label, log: date, setup, session, grade, entry, stop, T1, T2, outcome (T1 / T2 / stop / neither), and whether you would have taken it.
- [ ] Log stage alerts too. Did SWEEP → DISPLACEMENT → CONFIRMED match what you saw? Did ORB FAILED lead into an LDR?
- [ ] Log **SEQ continuation and SEQ reversal separately**, including days with no clean sequence (MAP says "SEQ off today").
- [ ] Check that levels, OR tags and VWAP resets line up with the session times on the chart.

**Strategy Tester**

- [ ] Run each setup alone (turn the other two off in *Setups*) and together.
- [ ] Record the results table rows for **each setup, each session and each grade**, plus **SEQ-C vs SEQ-R**. Only trust a row once it has 30–50+ trades.
- [ ] Compare *Minimum grade to trade* A+ / A / B / C to confirm the grades actually separate expectancy.
- [ ] Compare the exit modes.
- [ ] Re-run with realistic commission and slippage (Properties) for each instrument.
- [ ] Test Dollar vs R-multiple targets, and with *Max stop risk $* on.
- [ ] Forward-test live alerts in a private Discord channel for at least 2 weeks before acting on them.

---

## Input groups

Setups · Sessions · Alert windows · News blackouts · Model · ORB · SEQ · Filters · Confluence · Targets and sizing · Alert output (+ Strategy in the strategy file). Anything non-obvious has a tooltip.

## Repainting

- State changes only on confirmed bars (`barstate.isconfirmed`), and alerts fire once per bar close.
- The prior-day high/low uses `request.security(..., "D", [high[1], low[1]], lookahead_on)`, i.e. only the completed prior day.
- The HTF trend uses `close[1]` and `ema[1]` of the HTF with `lookahead_on`, i.e. only the last completed HTF bar.
- Swings come from `ta.pivothigh` / `ta.pivotlow`, which confirm `pivLen` bars after the pivot and never move afterwards.
