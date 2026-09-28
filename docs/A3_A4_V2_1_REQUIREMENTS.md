# A3+A4 additions — canonical playbook v2.1

Source: [unchanged owner playbook v2.1](../eval/playbook/stax-grokbot-playbook-v2.1.md), §1.9. These are implementation requirements, not completed detector code. A1+A2 Replay acceptance remains pending. No separate indicator, webhook, strategy twin, server work or order execution is included.

## A3: deterministic trend-day context (Rule B)

Pine computes context from confirmed bars only, not screenshot interpretation. Preserve original major-level provenance separately from current swept/minor obstacle status: a major does not stop being a qualifying historical major merely because the drive swept it.

- Count at least **two** qualifying major levels broken in the same direction during the defined session. Keep event IDs, level IDs/prices, break direction and confirmation time. Never count a level not yet available at that bar, a developing/partial range as a completed extreme, or repeated breaks of the same level as new levels.
- Verify the confirmed price is above both configured MAs for an up trend, below both for a down trend. MAs must be computed in Pine with explicit type/period/timeframe configuration; an unrelated chart overlay is not an input.
- Track whether a broken level was reclaimed. Reclaim blocks the continuation exception under the agreed definition; do not erase the earlier event to make the flag pass.
- Emit direction and evidence alongside the boolean. For a candidate, `trend_day` can enable continuation only when candidate direction matches the verified trend. It must be false for counter-trend candidates, incomplete session coverage, unavailable MA values, or missing required evidence.
- Reset session-local break/reclaim state at the agreed boundary, while retaining level provenance. Use ET regardless of chart timezone. No future bars, retroactive pivots or repainting.

Required owner definitions before coding: both MA types/periods/timeframes; whether the trend counter resets by Asia/London/NY or Globex trading day; confirmed-close versus wick reclaim; break confirmation semantics and treatment of overlapping major-level aliases. Do not invent these as locked defaults. Proposed conservative break/reclaim convention for owner approval: confirmed close across the level; unique tick-aligned prices prevent aliases at the same price counting twice.

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
