# A3+A4 additions — canonical playbook v2.1

Source: [unchanged owner playbook v2.1](../eval/playbook/stax-grokbot-playbook-v2.1.md), §1.9. Trend/buffer and flipped-major review helpers are implemented in the existing indicator; the merged A3 setup engine and A4 candidate/target options remain pending. A1+A2 Replay acceptance remains pending. No separate indicator, webhook, strategy twin, server work or order execution is included.

## A3: deterministic trend-day context (Rule B)

Pine computes context from confirmed bars only, not screenshot interpretation. Preserve original major-level provenance separately from current swept/minor obstacle status: a major does not stop being a qualifying historical major merely because the drive swept it.

- Count at least **two** qualifying major levels broken in the same direction during the defined session. Keep event IDs, level IDs/prices, break direction and confirmation time. Never count a level not yet available at that bar, a developing/partial range as a completed extreme, or repeated breaks of the same level as new levels.
- Use two **close-source EMAs on the chart timeframe (5m)**: owner-confirmed defaults **100 fast (blue)** and **200 slow (yellow)**, both **OWNER TUNE**. Holding above/below means the confirmed 5m close is strictly above both for long / below both for short. An unrelated chart overlay is not an input. Optional internal EMA plots default off to avoid duplicating the owner's overlays.
- Reclaim means a confirmed 5m close back across any broken level **beyond the instrument buffer**. Wicks do not count. For an upward break at L, reclaim is close < L - buffer; for a downward break, close > L + buffer. A reclaim disables that direction and resets its qualifying break sequence. It may reactivate only after fresh confirmed closes beyond two major levels in that direction after the reclaim. Preserve prior events as evidence, but never reuse pre-reclaim breaks to re-arm the flag. The reclaim bar cannot also re-arm the reclaimed direction.
- Emit direction and evidence alongside the boolean. For a candidate, `trend_day` can enable continuation only when candidate direction matches the verified trend. It must be false for counter-trend candidates, incomplete session coverage, unavailable MA values, or missing required evidence.
- Reset counters and reclaim/arming state at the **full Globex day open, 17:00 CT / 18:00 ET**, covering 17:00–16:00 CT. Asia/London/NY boundaries do not reset this flag. Retain level provenance; count only breaks confirmed from the Globex open. Require session-start coverage. Use IANA timezone handling and confirmed bars, regardless of chart timezone. No future bars, retroactive pivots or repainting.

Owner-confirmed buffer policy: **max(instrument tick floor × minimum tick, 0.1 × confirmed ATR(14))**. Floors: MNQ **8 ticks / 2.0 points**, MES **4 ticks / 1.0 point**, MGC **10 ticks / 1.0 point**. Floors and ATR multiplier are OWNER TUNE. The same active buffer applies to confirmed-close reclaims, Rule A stop selection and sweep confirmation; missing ATR fails closed. Debug shows the active instrument, buffer in points and configured floor in ticks. Rule A stops round outward to an executable tick; reclaim comparisons use the unrounded buffer.

Implemented break-count convention for owner review: confirmed close crosses a previously available completed major; aliases at the same tick-aligned price count once per active direction sequence. Mixed active directions block trend_day. Ledger retains up to 64 events per Globex day; overflow disables trend_day rather than silently discarding evidence. Debug pages expose all retained events, including reclaimed and prior-sequence events.

Expose `trend_day`, `trend_direction` and the broken-level evidence list in Pine's internal catalog/context and optional debug table. Preserve the distinction between historical broken levels and the current post-reclaim qualifying sequence. Each evidence item includes exact price, identity/type, break direction/time and session key. Neither a chart label nor GrokBot can set these facts. A fresh same-bar drive through two distinct major levels may count both after a prior reclaim, but cannot reuse the reclaim bar. Payload transport remains A5; no webhook is introduced in this checkpoint.

## A3: flipped-major retest detection (Rule A)

Track an immutable broken-major reference, its original classification, price, break direction/time, then a **later** retracement into that preselected level and a confirmed hold/rejection on the flipped side. A same-bar break/retest, a wick touch without confirmation, repeated churn, a moving reference or a closed-through failed zone cannot produce a successful Rule A event. Exact retest-zone tolerance, confirmation signal and expiry policy must be explicit in the A3 state machine and checked against owner examples.

The event needs a stable reference ID, exact level price, direction, break and retest confirmation times and retest extreme. It feeds the existing LDR/Breakaway/ORB merged setup engine; it is not an independent duplicate suggestion. SEQ remains context attached to an entry model.

## A4: invalidation and target selection

- A confirmed Rule A entry gets an invalidation reference at the retested flipped major. Supply a stop option beyond that level plus the instrument buffer. Preserve the entry model's provenance; never silently substitute a tighter stop just to pass R:R.
- For other entries, retain the playbook's displacement-origin/setup-specific requirements. Resolve the pre-existing §1.6/§1.9 conflict explicitly rather than extending Rule A to all FVG entries.
- Provide every selected option and the full level catalog. Pine supplies the trend-day evidence and which levels qualify as in-path continuation targets. GrokBot selects supplied options only; the server validates facts and assigns grades.
- A successful flipped-major retest is **A-eligible**, not automatically A. Add this eligibility path to the future server grade-cap policy beside major sweep and failed major reclaim.
- Verified with-trend Rule B can treat qualifying in-path majors as T1/T2 and move the first obstacle to the next major beyond them. Missing obstacle data is not proof there is no obstacle. If there is genuinely none, v2.1 specifies a deterministic 2R final target. That option must be calculated from a fixed valid entry and stop by Pine/server, not invented by GrokBot. Three ordered targets, valid stop, ATR checks, freshness and no-chase requirements still apply. Final must remain beyond current price and displacement extreme.

## Acceptance fixtures

Cover both long/short: one major versus two; repeated break versus distinct breaks; alias levels at one price; mixed-direction breaks; above/below one MA versus both; reclaim after confirmation; session reset and partial history; same-bar break/retest rejection; successful later flipped-major hold; retest failed close/churn; Rule A stop plus buffer versus tighter invalid stop; trend-aligned versus counter-trend candidate; normal first obstacle versus continuation target path; real missing obstacle data versus valid 2R fallback; invalid 2R final already passed by price. Verify event/option stability under Replay and an open realtime bar.

Deliver A3+A4 as one owner-review checkpoint, updating the existing TradingView instance in place. Do not add an additional indicator instance.

## Helper verification and remaining limits

TradingView compiled and saved revision 9 of the existing Stax A1+A2 instance on MGC 5m. The optional debug tables rendered: active MGC buffer 1.0 point at ATR about 7.2, EMA 100/200, DOWN trend direction and retained broken-major evidence. This is a current-chart runtime smoke test, not Friday Replay acceptance or complete A3+A4 verification.

Raw liquidity consumption (any trade beyond a level) is separate from buffered sweep confirmation (excursion at least buffer plus close back inside). Consumed levels become minor for obstacle purposes while original major provenance remains available to the break ledger.

Rule A helper conventions still require owner review: a later bar intersects level ± active buffer and closes directionally away beyond the buffer; the break must have aligned displacement; at most two distinct touch episodes within 20 bars (expiry input marked OWNER VERIFY). It is not yet a complete setup lifecycle, chop filter, entry option or payload. Retest extremes/timestamps, merged candidate provenance, direction gating, complete target catalog selection and Rule B target/obstacle handling remain A3+A4 work. No automatic A grade is assigned.

`scripts/Test-PineBufferPolicy.ps1` checks arithmetic policy boundaries only; it does not execute Pine. Replay is still required for event ordering, open-bar stability and owner example agreement.
