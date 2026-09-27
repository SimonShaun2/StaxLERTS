# Stax → GrokBot contract v1.3 — draft

Status: **draft, not frozen**. The owner has fixed grade routing, exact thirds, stop management, and volatility-relative stop-distance policy. Grade score and R:R values still must be calibrated on the labeled evaluation library and written into versioned server policy, not invented here. Three-target response schema remains `grok_decision_v2`, with exit fractions removed; consumers of the earlier response shape require migration.

## Authority and lifecycle

The server is the state authority. Pine emits observations through `CANDIDATE`; GrokBot proposes `CONFIRMED`, `REVIEW`, or `REJECTED`; the server validates the proposal and records every transition. A human may resolve `REVIEW` in a private channel before its deadline.

| State | Meaning | Terminal |
| --- | --- | --- |
| `WATCHING`, `ARMED`, `PENDING` | Pine setup formation states | No |
| `CANDIDATE` | Pine emitted a complete candidate | No |
| `REVIEW` | Human review required; expires at the candidate deadline | No |
| `CONFIRMED` | Plan approved and published; entry unfilled | No |
| `TRACKING` | Model entry filled | No |
| `T1_HIT` | First partial exit filled, remainder open | No |
| `T2_HIT` | Second partial exit filled, final portion open | No |
| `GREEN` | Final target filled | Yes |
| `PARTIAL` | At least one target filled, then the remaining portion stopped before final target | Yes |
| `BREAKEVEN` | Full position stopped at entry before T1 | Yes in the enum; unreachable in v1 because pre-T1 breakeven is off |
| `FAILED` | Initial stop hit before T1 | Yes |
| `EXPIRED` | Unfilled plan deadline or directional staleness reached | Yes |
| `REJECTED` | GrokBot, reviewer, or validator rejected the candidate | Yes |
| `INVALIDATED` | Pre-entry setup invalidation rule fired | Yes |

`CANDIDATE → CONFIRMED/REVIEW/REJECTED`; `REVIEW → CONFIRMED/REJECTED/EXPIRED`; `CONFIRMED → TRACKING/EXPIRED/INVALIDATED`; **v1** `TRACKING → T1_HIT/FAILED`; `T1_HIT → T2_HIT/PARTIAL`; `T2_HIT → GREEN/PARTIAL`. A direct `TRACKING → GREEN` is not allowed: all three targets are accounted for in order, even if one market-data event crosses multiple targets. `BREAKEVEN` remains in the enum for future policies but has no reachable v1 transition. Every transition is immutable and timestamped. A model outcome never implies that a Discord member received that fill.

## Transport and timing

TradingView sends `pine_candidate_v1` at confirmed bar close. `bar_open_time_utc` means the bar's open; `bar_close_time_utc` is the event time used for age checks. Both must be explicit because TradingView `time` denotes bar open. The gateway authenticates and persists the request, returns a quick 2xx response, then processes asynchronously. The body carries `auth_token`; the gateway compares it to a server secret and removes it before logging, enrichment, or model inference. HTTPS is required. The token is never included in the rendered chart, Grok request, or Discord post.

`candidate_id` is stable across retries; `event_id` identifies a delivery. A retry of the same `candidate_id` is idempotent. The server assigns a separate `trade_id` only after confirmation and publication.

## Pine candidate (`pine_candidate_v1`)

Pine may provide chart facts, observations, and deterministic level options. It does not provide chart images, merge metadata, a final grade, account policy, contract count, or an AI decision.

```json
{
  "schema_version": "pine_candidate_v1",
  "auth_token": "<secret supplied in TradingView alert configuration>",
  "event_id": "evt_001",
  "candidate_id": "cand_MNQZ2026_20260928T143500Z_L_001",
  "emitted_at_utc": "2026-09-28T14:35:02Z",
  "bar_open_time_utc": "2026-09-28T14:30:00Z",
  "bar_close_time_utc": "2026-09-28T14:35:00Z",
  "market": {
    "continuous_symbol": "MNQ1!",
    "execution_symbol": "MNQZ2026",
    "exchange": "CME",
    "timeframe_seconds": 300,
    "tick_size": 0.25,
    "point_value_usd": 2.0,
    "timezone": "America/New_York"
  },
  "state": "CANDIDATE",
  "direction": "LONG",
  "setup_tags": ["LDR", "BREAKAWAY_FVG"],
  "invalidation_reference": {
    "LDR": {"basis": "SWEEP_EXTREME", "price": 30882.25, "source_id": "sweep_001"},
    "BREAKAWAY_FVG": {"basis": "SWEEP_EXTREME", "price": 30882.25, "source_id": "sweep_001"}
  },
  "reference_price": 30908.25,
  "atr_14": 20.0,
  "level_catalog": [
    {"id": "TARGET_SESSION_HIGH", "price": 30930.25, "type": "LONDON_HIGH", "classification": "MINOR", "swept": true, "relative_to_entry": "ABOVE"},
    {"id": "TARGET_PDH", "price": 30948.25, "type": "PDH", "classification": "MINOR", "swept": true, "relative_to_entry": "ABOVE"},
    {"id": "TARGET_FIRST_OPPOSING", "price": 30970.25, "type": "ONH", "classification": "MAJOR", "swept": false, "relative_to_entry": "ABOVE"}
  ],
  "entry_options": [
    {"id": "ENTRY_FVG_MID", "price": 30900.25},
    {"id": "ENTRY_FVG_NEAR", "price": 30905.25}
  ],
  "stop_options": [
    {"id": "STOP_SWEEP", "price": 30881.75, "is_invalidation_level": true},
    {"id": "STOP_FVG_FAR", "price": 30894.75, "is_invalidation_level": false},
    {"id": "STOP_SWING", "price": 30885.75, "is_invalidation_level": false}
  ],
  "physical_target_options": [
    {"id": "TARGET_INTERMEDIATE_1", "price": 30922.25},
    {"id": "TARGET_SESSION_HIGH", "price": 30930.25},
    {"id": "TARGET_PDH", "price": 30948.25},
    {"id": "TARGET_FIRST_OPPOSING", "price": 30970.25}
  ],
  "context": {
    "fvg_bottom": 30895.25,
    "fvg_top": 30905.25,
    "sweep_low": 30882.25,
    "displacement_high": 30915.25,
    "first_opposing_level": 30970.25,
    "prior_swing_low": 30886.25,
    "liquidity_sweep": true,
    "structure_break": true,
    "displacement": true,
    "vwap_bias": "BULLISH",
    "volume_state": "NORMAL",
    "session": "NY_AM",
    "news_blackout": false
  }
}
```

The gateway resolves `execution_symbol` against its instrument master and rejects a contract mismatch. A continuous symbol is for chart context only. Each candidate and published trade pins its exact execution contract and point value; no open plan silently migrates on rollover. New candidates after rollover use the new front month under a versioned rollover schedule.

`invalidation_reference` is required and has exactly one named entry per `setup_tags` value. Each entry supplies `basis`, tick-aligned `price`, and a stable `source_id`; it is a structured Pine assertion, not a screenshot-derived level. Allowed bases: LDR/Breakaway with sweep → `SWEEP_EXTREME` (long sweep low, short sweep high); ORB → `OPENING_RANGE_OPPOSITE` or `RETEST_EXTREME` (the actual opposite OR boundary or confirmed retest low/high); SEQ → `SEQ_EXPLICIT` with a versioned sequence-invalidation rule ID as `source_id`. A merged candidate preserves every tag and every reference. A selected stop must invalidate **all** carried setup tags; if references conflict, reject or split into distinct candidates rather than silently dropping a tag. For shorts, `displacement_low` replaces `displacement_high`. `is_invalidation_level` is an option-level Pine assertion; the server independently verifies the stop against every reference and tick buffer.

`atr_14` is required: a positive, finite ATR(14) in price points on the **setup timeframe**, calculated from confirmed bars by Pine and sent with the candidate. The server verifies the timeframe, age and numeric validity against the market feed; a missing, zero, stale, or inconsistent ATR cannot authorize publication. GrokBot cannot choose or alter ATR or its bounds.

`level_catalog` is required and complete at the confirmed cut; the example above is an excerpt, not permission to omit levels. Include every computed level with stable ID, price, type, effective major/minor classification, swept status, and above/below/at-entry relation. Preserve swept entries as evidence, but do not treat them as unswept major obstacles. Also identify the first unswept major obstacle in the trade direction for each entry option. The server verifies completeness and ordering before asking GrokBot to select supplied levels. GrokBot never reconstructs this catalog from a screenshot.

## Server enrichment (`enriched_candidate_v1`)

The server merges later events with the same execution contract, direction, bar neighborhood, and compatible price zone. It processes the first event immediately; the merge window never delays inference. Later events either attach their tags and distinct options to the in-flight candidate, or are logged as a suppressed duplicate if a decision is already frozen. All originating event IDs and tags are retained. Different prices with the same semantic option ID must be namespaced by source rather than overwritten. An update after the Grok request starts requires a new immutable candidate revision and a new evaluation if it materially changes level options.

The server fetches market bars, verifies the feed timestamp, and renders a chart with its own data and the Pine levels. `enriched_candidate_v1` adds `source_event_ids`, `candidate_revision`, `render_id`, `render_sha256`, `data_feed_id`, `bars_from_utc`, `bars_to_utc`, and merge metadata. It also adds deterministic feature flags and render-cut metadata. It excludes `auth_token`, `stax_grade`, and example images. The live Grok request uses the canonical [v1.9 playbook](../eval/playbook/stax-grokbot-playbook-v1.9.md), one enriched candidate revision, and its rendered chart. Load Part 1 and the matching Part 2 instrument module as instructions; Part 4 is the owner's teaching workflow, not additional live examples. Preserve the canonical file unchanged; proposed changes require owner approval and a new version.

## Grok response (`grok_decision_v2`)

```json
{
  "schema_version": "grok_decision_v2",
  "candidate_id": "cand_MNQZ2026_20260928T143500Z_L_001",
  "candidate_revision": 1,
  "decision": "CONFIRMED",
  "entry_option_id": "ENTRY_FVG_MID",
  "stop_option_id": "STOP_SWEEP",
  "target_1_option_id": "TARGET_INTERMEDIATE_1",
  "target_2_option_id": "TARGET_SESSION_HIGH",
  "target_final_option_id": "TARGET_PDH",
  "visual_adjustment": 1,
  "visual_checks": {
    "displacement_quality": "STRONG",
    "retrace_quality": "CLEAN",
    "chart_cleanliness": "CLEAN"
  },
  "checklist": {
    "0_cut": {"cut_time_et": "2026-09-28T10:35:00-04:00", "visible_high": 30970.25, "visible_low": 30882.25, "pane": "render_001"},
    "1_context": {"result": "PASS", "htf_bias": "UP", "day_type": "TREND", "session_active": true, "news_clear": true, "note": "Context supplied at the cut"},
    "2_level": {"result": "PASS", "level": "sweep_001", "location": "DISCOUNT", "freshness": "FIRST_TEST", "note": "Candidate level provenance"},
    "3_displacement": {"result": "PASS", "quality": "STRONG", "structure_broken": true, "note": "Directional expansion"},
    "4_retracement": {"result": "PASS", "quality": "CLEAN", "zone_pre_selected": true, "note": "Rejection in preselected zone"},
    "5_confirmation": {"result": "PASS", "signal": "held_retest", "note": "Confirmed-bar evidence"},
    "6_invalidation": {"result": "PASS", "stop_option_id": "STOP_SWEEP", "invalidation_reference": "sweep_001", "invalidation_price": 30882.25, "note": "Verbatim reference, not a newly selected stop price"},
    "7_room": {"result": "PASS", "session_checklist": [{"level": "London H", "price": 30930.25, "status": "SWEPT"}], "majors_between_entry_and_final": [], "first_obstacle": "TARGET_FIRST_OPPOSING", "first_obstacle_price": 30970.25, "targets_before_obstacle": true, "note": "Illustrative excerpt; live list must cover every required session item"},
    "8_instrument": {"result": "PASS", "module": "MNQ", "rules_applied": ["equity_index_opening_range"], "cross_market": "Provided separately in enriched context", "note": "ET session rules"}
  },
  "reasons": ["Clean retrace into the FVG", "Strong displacement"],
  "reject_codes": [],
  "warnings": []
}
```

Allowed target IDs are `R_1`, `R_2`, `R_3`, or one of the physical target option IDs. Grok returns three target IDs, assessment, bounded score adjustment in `[-3, +2]`, checklist, reject codes and reasons; **it does not choose exit fractions**. The server assigns exact fractions `1/3`, `1/3`, `1/3` from versioned policy. It returns no newly invented trade prices, R arithmetic, dollar risk, contract count, final grade, or Discord routing flag. Numeric checklist evidence is an explicit exception: `0_cut.visible_high/low`, `6_invalidation.invalidation_price`, `7_room.session_checklist[].price`, and `first_obstacle_price` must echo verified input/render values verbatim, not prices generated from the image. The server cross-checks them and rejects mismatches. `REVIEW` and `REJECTED` responses use null selections. Unknown IDs, unexpected fraction fields, and stale candidate revisions fail validation.

The checklist keys `0_cut` through `8_instrument` are required. `0_cut` records the current render ID, ET cut time and visible range without later bars or previous-pane values. `6_invalidation` records the exact source reference and its price. `7_room.session_checklist` accounts for Asia H/L, London H/L, NY H/L so far, PDH/PDL, ONH/ONL, OR H/L and each equal-high/low catalog item, with status ABOVE/BELOW/SWEPT/NOT_VISIBLE. For MGC, OR entries are NOT_VISIBLE with null prices and an explicit not-applicable note, not zero-price levels. Missing/unreadable data uses null evidence and REVIEW; after a first FAIL, later checklist objects may be null rather than fabricated PASS values. CONFIRMED requires all steps 1–8 PASS. A first FAIL requires REJECTED with a reason code. Price echoes, list completeness and first-obstacle ordering require server semantic validation in addition to JSON Schema.

## Server plan calculation and validation

1. Resolve entry and stop IDs to prices. Require positive risk distance, tick alignment, correct stop side, and a valid instrument mapping.
2. Compute `risk_points = abs(entry - stop)`. Resolve `R_n` targets as `entry + direction_sign × n × risk_points`, rounded to the instrument tick using a specified conservative rounding rule. The server calculates targets only after entry and stop are chosen. Require `entry < T1 < T2 < final` for longs and reverse for shorts. Require the final target to lie beyond **both** the candidate reference price and displacement extreme in the trade direction. Reject duplicate/crossed targets or a final target already reached before publication.
3. Identify the first opposing level from the market feed and Pine physical options. The final target may not lie beyond it. `room_rr = abs(first_opposing - entry) / risk_points` is the single obstacle factor; there is no separate "opposing level too close" bonus or penalty. Missing or inconsistent opposing-level data routes to `REVIEW`.
4. Evaluate code-owned, **setup-specific** eligibility gates. Mandatory conditions for each carried tag establish eligibility and do not earn points; ORB and SEQ do not acquire a synthetic sweep/FVG requirement. Score only nonmandatory objective factors (for example, session alignment, VWAP alignment, volume quality, and regime). Add Grok's bounded visual adjustment. Thresholds come from held-out labeled offline evaluation and are versioned in server configuration.
5. Server policy has required `min_score` and `min_rr` values for **each** tier A++, A+, A, and B. Calibrate and record all eight numbers before grade routing is activated; require ordered score thresholds `A++ > A+ > A > B` and tier-specific minimum R:R values, including B. Apply the v1.9 **server-owned level-quality cap**: an intraday/non-major sweep caps at B; A/A+/A++ requires a verified major sweep or failed reclaim of a major level. Pine level/event provenance supplies the facts; GrokBot's visual adjustment cannot lift this cap. Missing provenance routes to REVIEW, not a guessed major classification. For a physically valid plan, test A++ → A+ → A → B subject to that cap; select the first tier meeting both score and R:R requirements. An R:R miss may legitimately downgrade a high-score candidate. If B fails either requirement, reject and log privately. Grades B and above alone route to the main channel with the grade at the top. Record policy version and downgrade/cap reasons.
6. Read current price and feed timestamp before publication. Apply the pre-entry rules below. Require the selected stop's `is_invalidation_level` flag and independently validate its price against **every setup-specific `invalidation_reference`**: for a long, stop ≤ reference price minus the configured tick buffer; for a short, stop ≥ reference price plus the buffer. Independently require `0.5 × atr_14 ≤ abs(entry − stop) ≤ 4.0 × atr_14` (inclusive), with ATR in the same instrument price points; the multipliers are versioned server policy and owner-tunable in Phase 3. No dollar cap, account size, or model contract count may gate publication. Separately compute optional model-account size/P&L for internal analysis; it must not alter the suggestion decision or appear in a member post.
7. Store the resolved plan, its input revision, policy version, grading version, and chosen option IDs atomically before publication. Freeze those values for outcome tracking.

The grade publication cutoff is fixed at **B and above**. Stop-distance policy keys are `min_stop_atr_multiple=0.5` and `max_stop_atr_multiple=4.0`, identical for MNQ and MGC and versioned in server configuration; there are no fixed-point stop-distance bounds. The server must refuse publication when ATR is invalid or calibrated tier thresholds are missing. The optional internal model-account cap is separate from these publication rules.

| Publishable tier | Required calibrated `min_score` | Required calibrated `min_rr` | Routing |
| --- | --- | --- | --- |
| A++ | Required numeric value from labeled evaluation | Required numeric value from labeled evaluation | Main, grade at top |
| A+ | Required numeric value from labeled evaluation | Required numeric value from labeled evaluation | Main, grade at top |
| A | Required numeric value from labeled evaluation | Required numeric value from labeled evaluation | Main, grade at top |
| B | Required numeric value from labeled evaluation | Required numeric value from labeled evaluation | Main, grade at top |

No numeric score or R:R values are claimed calibrated yet. Do not fabricate them; until the evaluation library and owner-approved policy values exist, routing remains disabled even though the B cutoff is decided.

## Pre-entry staleness and fills

The server uses three independent checks: candidate age, entry deadline, and directional market movement. Long limit entries are not expired solely because current price is above entry; the reverse applies to shorts. Before entry, expire a long if its post-candidate high reaches the configured runaway price toward the target without an entry fill, or invalidate it if a bar trades through the proposed stop side first. Reverse the comparisons for shorts. **Only the final target already reached before entry is an explicit target-hit expiry rule**; T1/T2 contact alone is handled by the runaway-price rule, not automatic target-hit expiry. The runaway price is derived from the candidate's reference price and a configured tick threshold; it is not `abs(current_price - entry)`. The initial candidate may be rejected as already stale if feed evidence shows the move happened before publication.

The model fill rule is one tick through the limit entry, with price assumed at the limit; mere touch is not enough. This is a conservative proxy, not a broker fill guarantee. A filled order transitions `CONFIRMED → TRACKING`. If no fill occurs before the entry deadline, transition to `EXPIRED`.

For a bar containing both entry and stop, or stop and target, reconstruct the order with lower timeframe or tick data if available. If sequence cannot be proven, choose the adverse event and record `AMBIGUOUS_ADVERSE` as the resolution reason. `GREEN` cannot result from an ambiguous bar.

## Partial exits and outcomes

The frozen plan includes `entry`, `initial_stop`, `target_1`, `target_2`, `target_final`, server-owned fractions `1/3` each, `stop_after_t1=ENTRY`, and `stop_after_t2=ENTRY`. **Pre-T1 breakeven is off for v1**, so `BREAKEVEN` is unreachable. Trailing the post-T2 stop to T1 requires a later versioned policy decision and new evaluation. After either partial target, a remaining-position stop results in terminal `PARTIAL`; record the exact exit path and realized R, even when the remainder stop is at breakeven.

Every published suggestion receives a posted resolution: GREEN, PARTIAL, BREAKEVEN, FAILED, or an unfilled EXPIRED/INVALIDATED closure. `DATA_UNAVAILABLE` is a visible pending status, not a fabricated terminal outcome. The new-suggestion kill switch cannot block these resolution posts; delivery failures remain in a durable outcome outbox until reconciled.

`realized_r = f1 × exit_R_at_T1 + f2 × exit_R_at_T2 + f_final × exit_R_for_final_portion`, where each term is included only when that fraction has exited; `exit_R = direction_sign × (exit_price − entry) / initial_risk_points`. For a complete GREEN, all three fractions exit at their respective targets. For a T1-then-stop `PARTIAL`, the unfilled T2 and final fractions exit at the active stop. For a T2-then-stop `PARTIAL`, only the final fraction exits at that stop. Model dollar P&L uses separately calculated internal model size; if a model cap would allow zero contracts, dollar P&L is unavailable but realized R and publication are unaffected. Discord labels all levels as **suggestions** and shows risk in points and per-contract dollars, planned R, realized R, and the exact target/stop path, with no member contract count or model-account dollar cap. The outcome tracker uses the frozen plan and authoritative market-data sequence, not later recalculated levels.

## Numerical fixture

Date: Monday, 2026-09-28, 14:35 UTC. MNQ tick size 0.25 index points ($0.50 per tick); point value $2 per index point per contract. Setup-timeframe `atr_14=20.0`, so valid stop distance is `10.0–80.0` points. Selected entry `30900.25`; selected sweep stop `30881.75` (two ticks below sweep low `30882.25`). The FVG stop option `30894.75` is **inside** the sweep and must fail stop-integrity validation if selected. The first opposing level is `30970.25`.

- Risk distance: `30900.25 − 30881.75 = 18.50` points = 74 ticks; per-contract initial risk is `18.50 × $2 = $37` before costs.
- Three physical targets: T1 `30922.25`, T2 `30930.25`, final `30948.25`, all beyond reference price `30908.25` and before first opposing `30970.25`.
- Target R values: `22/18.5 ≈ 1.1892R`, `30/18.5 ≈ 1.6216R`, `48/18.5 ≈ 2.5946R`. With exact server-owned thirds, GREEN is approximately `1.8018R` gross before costs.
- T1 then breakeven stop: `PARTIAL ≈ 0.3964R`; T1 and T2 then breakeven stop: `PARTIAL ≈ 0.9369R`, both before costs. A post-T2 T1-trailing policy would produce a different result and cannot be silently substituted.
- If final target is instead selected at or behind reference price in the trade direction, or price has already reached it before publication, reject/expire under the pre-entry rules; do not publish a self-expired plan.

Fixture R arithmetic excludes commission and slippage only for unit testing. Validation and reported performance must include configured costs and use the actual feed and rollover mapping. Internal model sizing, including a possible zero-contract result under a chosen model cap, never decides publication.

MNQ/MGC, ORB, and SEQ stop-integrity fixtures and the continuous phase plan remain companion owner documents; this checkpoint does not claim to implement the server test suite.

## Shadow record

Store the sanitized Pine payload, all source event IDs, merged revisions and tags, feed bars or immutable feed reference, render version and image hash, **playbook prompt version/hash**, exact Grok request, raw Grok response, parsed option IDs, validation result and failure codes, policy and grading versions, market staleness checks, every state transition, any manual review, publication attempt, and final model outcome. Owner labels, preferred stop/target IDs and disagreement notes belong in the evaluation library and correction loop, keyed to the same candidate/revision. Example charts and labels are used only for offline evaluation; no example images are sent in live requests.
