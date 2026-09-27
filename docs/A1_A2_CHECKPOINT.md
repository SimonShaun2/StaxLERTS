# A1+A2 owner-review checkpoint

Canonical definitions: [playbook v1.9](../eval/playbook/stax-grokbot-playbook-v1.9.md). Indicator: [Stax_A1_A2.pine](../pine/Stax_A1_A2.pine).

This is a review draft, not approval of the trading method or a completed production engine. It has no alerts, orders, candidate emission, server code, or A3–A6 logic.

## Delivered

- Pine v6, standard 5-minute MGC/MNQ/MES candles.
- Globex prior high/low (18:00–17:00 ET = 17:00–16:00 CT), overnight, Asia, London, NY, equity-only opening range, and confirmed equal-pivot liquidity.
- Internal level catalog with effective major/minor classification, developing/partial/swept/flipped status. Owner requested removal of the on-chart table; chart levels, structure and FVG indications remain. Discord transport is deferred to the integration checkpoint.
- Confirmed ATR(14), directional structure breaks, displacement classifications, bullish/bearish FVGs with bounded drawings.
- Swept levels become minor; closes through otherwise unswept levels retain flipped major status.

## Verification completed on 2026-09-27

TradingView Pine Editor compiled the indicator and it was saved privately as **Stax A1+A2**. The panel rendered on MESZ2026, MNQZ2026 and COMEX MGCZ2026 at 5 minutes without a visible runtime error. Existing indicators were preserved.

All price-state mutations are under `barstate.isconfirmed`. Pivots become available on their confirmation bar, not the historical pivot candle. Structure checks precede newly available pivots. No `request.*` calls or negative plotting offsets are used. Object budgets: 80 lines, 80 labels, 30 boxes; actual FVG catalog capped at 20, equal catalog at 12.

These checks are compilation/runtime smoke tests, **not** a replay proof or verification against the Friday drill answer key.

## Owner review required

1. Verify configurable session boundaries: Asia 19:00–02:00, London 03:00–07:00, index NY 09:30–16:00, gold NY 08:20–11:30; overnight starts at 18:00 and ends at the instrument's NY open. The playbook does not fully specify every range boundary; defaults remain explicitly provisional.
2. Verify opening range classification. It is currently minor for obstacle purposes because §1.9 does not list it as a major blocker.
3. Tune equal-pivot confirmation/tolerance and minimum FVG size. Default minimum gap is 0.1 ATR, a provisional input, not an owner-approved playbook constant.
4. Review displacement: STRONG requires the size test (single body or aligned 2–3-bar run), outer-quarter close, structure break and FVG; ADEQUATE requires directional structure break. Meaningful versus minor structure and little-overlap/grind criteria need owner examples before claiming exact drill agreement.
5. Run Replay at the **same contract, date and cut** as the drill. The catalog is at the latest confirmed cut, not whatever historical candles happen to be in the viewport. Check PD/ON/session extremes, unswept equal liquidity, first availability, sweeps, and live-bar stability. The quoted Friday levels are review targets, not hardcoded constants.
6. Check ETH coverage. PARTIAL only detects a missing range start; arbitrary mid-session feed gaps are not certified by this checkpoint.

Stop here. A3 starts only after the owner accepts this checkpoint.
