# A1+A2 owner-review checkpoint

Canonical source: [playbook v2.1](../eval/playbook/stax-grokbot-playbook-v2.1.md). Indicator: [Stax_A1_A2.pine](../pine/Stax_A1_A2.pine). This remains a v1.9-based A1+A2 review build, with the owner-approved 0.25 ATR equal-tolerance default adopted; full v2.1 migration is not claimed. New trend-day and flipped-major retest requirements are scoped in [A3+A4](A3_A4_V2_1_REQUIREMENTS.md).

This is a review draft, not approval of the trading method or a completed production engine. It has no alerts, orders, candidate emission, server code, or A3–A6 logic. Owner accepted the current MNQ PDH 30,998.50, PDL 30,680 and NY H/L 30,952.50 / 30,684; historical drill verification remains pending.

## Delivered

- Pine v6, standard 5-minute MGC/MNQ/MES candles.
- Globex prior high/low (18:00–17:00 ET = 17:00–16:00 CT), overnight, Asia, London, NY, equity-only opening range, and confirmed equal-pivot liquidity.
- Internal level catalog with effective major/minor classification, developing/partial/swept/flipped status. Owner requested removal of the on-chart table; chart levels, structure and FVG indications remain. Discord transport is deferred to the integration checkpoint.
- Confirmed ATR(14), directional structure breaks, displacement classifications, bullish/bearish FVGs with bounded drawings.
- Swept levels become minor. The v2.1 helper revision distinguishes raw consumption from buffered reversal confirmation and preserves original major provenance separately for trend/retest evidence.
- Chart display: full-name, normal-size high-contrast annotations; session-specific colors; two-pixel level lines from availability to annotation; solid established and dotted developing lines. Swept levels are hidden by default (Display input restores them) without removing catalog facts. Nearby labels share a multiline block with each exact price retained and separate horizontal price lines.
- Equal H/L tolerance defaults to 0.25 × confirmed ATR(14), OWNER TUNE (superseding the prior 0.1 default). Matching and deduplication use ATR on the detection/confirmation bar, with no tick-based fallback or future-bar ATR. Equality is inclusive at the boundary; missing/zero ATR cannot establish an equal level. Equal level price remains the outer extreme of the two pivots; already-cleared liquidity is still excluded.
- Optional **Debug - Replay only** inputs: full catalog table and per-confirmed-bar STRONG/ADEQUATE/WEAK annotations. Both default off. Table includes every retained nonempty catalog entry (including swept/hidden entries), with name, exact price, effective class, sweep status, equal-record flag and coverage. Equal liquidity is represented by separate EQH/EQL entries; a matching session boundary does not automatically change the session row's equal-record flag. Catalog remains bounded to 12 base slots plus the configured 2–12 equal entries; this is not an unlimited historical archive.

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

## Revised owner Replay checklist

Friday MNQ **08:00 CT / 09:00 ET**, using the drill's exact date and contract: PDH ~30,828; PDL ~30,375; ONH ~31,000; ONL ~30,680 with unswept equal liquidity; London high/low ~30,965 / ~30,838; Asia equal highs ~30,905. These are owner-supplied answer-key expectations, not verified values or code constants. Compare catalog cut time, coverage, equal records and sweep status, not just drawings. The precise Friday date is not inferred from these prices.

Also inspect MNQ ~30,680/30,683 and MGC ~4,331 repeated highs, ~4,313/4,314 lows. At 0.25 ATR, a 3-point separation needs ATR ≥12 and a 1-point separation needs ATR ≥4. If the detection bar has smaller ATR, these pairs legitimately fail the requested default; tune the multiplier based on the drills rather than hardcoding their prices. Triple tops deduplicate to a retained equal-high level.

After owner Replay acceptance, the next full build checkpoint is **A3+A4**. The owner has since authorized its v2.1 trend/buffer/retest helpers; full merged setup states and candidate/target options remain pending. See [current requirements and verification](A3_A4_V2_1_REQUIREMENTS.md).

Revision verification: the ATR/debug revision compiled and was saved in TradingView as version 7. Both optional debug inputs were enabled for a MNQ 5m runtime smoke check: table showed retained swept/unswept entries and equal flags, and confirmed-bar grade annotations rendered. Current-cut equal lows at 30,680 were visible; this is not acceptance of the Friday answer key. Six arithmetic checks covered MNQ/MGC boundary/outside-tolerance cases, exact equality and invalid ATR (policy checks, not a Pine interpreter test). Debug inputs were returned to off. The former version-5 chart instance was hidden, not deleted, when the latest saved revision was loaded.
