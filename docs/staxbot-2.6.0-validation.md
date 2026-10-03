# StaxBot 2.6.0 validation status

## Completed source review

- The C1–C6 answers were resolved against the unchanged `staxbot_2_5_2.pine` before implementation and recorded in [staxbot-2.6.0-source-resolutions.md](staxbot-2.6.0-source-resolutions.md).
- The original input declarations remain unchanged. New inputs are appended in the `Engine imports` group.
- The original fingerprint expression remains the base expression. The new suffix is empty when all new inputs have their defaults, so its default value is byte-for-byte the 2.5.2 fingerprint.
- Batch B behavior is guarded by default-off toggles. A2 only annotates certainty, A3 costs default to zero, A4 sizing defaults to off, and A5 adds plan annotations. A6 only runs when the existing session-flatten input is enabled and a session filter is configured.
- The original plan state machine remains the source for arming, fills, exits, and alerts. Strategy orders have alerts disabled and are not read back into the state machine.

## Not run

This environment has no TradingView Pine Editor/compiler or chart replay session. Therefore none of these acceptance checks is claimed as passed:

| Check | Status |
|---|---|
| Pine v6 compile | Not run |
| 60-day default parity: MNQ, MES, MGC at 1m and 5m | Not run |
| B5 replay at 15m | Not run |
| Strategy Tester versus HUD trade list and fills | Not run |
| Individual Batch B reports and B6a+B6b+B6c / everything-on combinations | Not run |
| A4 hand-calculated size, cap, and remainder samples | Not run |
| A5 hand-checked displacement samples | Not run |
| A6 missing-session-bar sample | Not run |
| B1 catalog display-only | Not run |
| B1 catalog feeds room/grade | Not run |
| B2 session windows | Not run |
| B3 news block | Not run |
| B4 tick floors | Not run |
| B5 scaled contexts | Not run |
| B6a structure filter | Not run |
| B6b structural targets | Not run |
| B6c confirmation before fill | Not run |
| B6a + B6b + B6c together | Not run |
| Everything-on combination | Not run |

These runs need to be completed in TradingView before treating 2.6.0 as release-ready. No market data, trade counts, win rates, or changed-plan examples are fabricated here.
The requested per-toggle plans/grades/win-rate/net-R deltas and first 10 changed plans are not generated because there was no replay data or TradingView session.

## Implementation notes for review

- For retest scenarios, the strategy limit order is staged while the plan is armed so the broker emulator can observe the later retest. The plan state machine still emits the fill event and remains authoritative. Scenario M uses a market order on the signal close.
- The strategy declaration now uses `pyramiding = 5` and `process_orders_on_close = true` for the tester mirror and scenario M. The prior `calc_on_every_tick`, `calc_on_order_fills`, margins, commission, and slippage values were reviewed in the source-resolution note. Alert calls remain in their original confirmed/provisional branches.
- `commissionRT` and `slippageTicks` affect the script's net-R and sizing calculations. The strategy declaration remains at zero commission and slippage, so TradingView's tester cost columns do not follow those runtime input values in this version. This needs an owner decision before relying on cost-adjusted tester performance.
- Staging pending limits while a plan is armed and processing orders on the close can allow broker-emulator fills that the close-based state machine later cancels, or can resolve same-bar orders differently. Tester parity and sibling-cancel edge cases require replay review.

## Questions for Shaun about the 5-minute Engine lock

1. For A2, is a lower-timeframe request of `min(1 minute, chart timeframe)` correct on charts below one minute and on all supported symbols?
2. For A5, should `outer_close` and the aligned `run_bars` count use chart bars on all chart timeframes, or should they always use 5m bars?
3. For A6, is `time_tradingday` the intended missing-session boundary on all chart timeframes, including multi-hour and daily bars?
4. For B1, should equal-high/low pairing use chart bars on every timeframe? Should NY range and the 15-minute Opening Range aggregate correctly on 1m, 15m, and higher charts, or should these levels always be built from 5m data?
5. For B2, should enabled session windows be evaluated against each chart bar on all timeframes, including bars that straddle a window boundary?
6. For B3, should news overlap use chart-bar open/close on every timeframe, including sub-minute and multi-hour bars?
7. For B4, should tick floors compose with StaxBot's existing chart-bar ATR, or should the imported buffer use a fixed 5m ATR on other charts?
8. For B5, are the setup/position/runner/slow multipliers and the `>= 15m` gate correct when the resulting contexts are nonstandard durations (for example, 45m, 3h, or 12h)?
9. For B6a, should its 100/200 EMA fallback, pullback position check, and chart-level confirmation use the current chart timeframe, while structure direction stays on D/4H and position context stays on 1H?
10. For B6b, when B5 is on, should the scaled setup/position/runner swings replace the fixed 15m/1h/4h swing ladder as implemented?
11. For B6c, should the pivot length of 2 be measured in current chart bars on all timeframes, or should confirmation pivots always use 5m bars?

## A1/A3 owner questions

- Is it acceptable to change the strategy declaration from pyramiding 0 to 5 and from `process_orders_on_close = false` to `true` for the Tester mirror and same-close scenario M? TradingView documents that processing on close changes order fill timing for the strategy.
- Is staging retest limit orders while plans are armed acceptable? A close-based strategy cannot submit a limit after seeing a historical bar's touch and then retroactively fill it at that earlier price. Staging makes retests measurable, but a broker-emulator fill can precede a later close-based invalidation or sibling cancellation.
- Should A3 costs remain in the webhook/HUD and sizing model while the Tester properties stay at zero, or should traders enter the desired commission/slippage in Strategy Properties? The runtime inputs do not currently change the strategy declaration's Tester cost settings.
- Please confirm in TradingView whether the 2/1/1 `qty_percent` exits work as intended at quantity 1 on MNQ, MES, and MGC, or require a modeling quantity equal to the sum of weights.
- For B6a's strong-drive exception, should any existing StaxBot higher-timeframe zone qualify as a major-level sweep, or only a catalog level marked major and swept?
- The current B6a implementation uses the existing StaxBot `atLevel` zone-proximity result as its available strong-level-sweep proxy. Confirm whether that is an acceptable interpretation before enabling B6a; it has not been replay-validated against the Engine's major-level state.

## Relay-owner release question

Can the GrokBot / Discord relay owner confirm that additive fields, `qty: 0`, the `session_end_missing_bar` reason, and the `2.6.0` version value are accepted before release?

## Review follow-up

Static fixes in response to the source review:

- B2 publish windows now gate new plan arming only. The original session filter remains responsible for existing plan expiry and session flattening.
- A2 only asks lower-timeframe data on bars where entry/stop/target ordering conflicts. Missing data in that case stays `unproven`; target-before-stop on a conflicting exit is also `unproven` because it contradicts StaxBot's stop-first decision.
- A6's missing-session-bar fallback now requires a configured session filter and runs before that bar's stop/target checks. It does not flatten the 24/5 no-filter setup at each daily rollover.
- B1 now sizes the `high` and `low` history buffers for its 500-bar scan, retains one combined equal-level cap, clears New York range levels at the Globex rollover, builds the Opening Range only for MNQ/MES, allows its duration to be configured, and uses the preceding Globex last print for PDC.
- B6a allows both directions to reach the filter decision, treats neutral direction as no block, and uses the Engine's close-versus-both-EMAs fallback. Its counter-bias exception remains based on the current strong-drive/HTF-sweep or StaxBot level proxy pending the owner's definition of a major sweep.
- B6b no longer requires structural TP1 to equal the fixed ladder R, does not let the older structural mode overwrite B6b, searches and prunes the retained unswept swing lists, uses pivot length 2, measures the runner beyond the raw setup swing, rounds targets to tick, and checks that the final target is at least 1.5R.
- B6c records the deepest retest touch for the position check and compares each newly confirmed pullback pivot with the preceding confirmed pivot.
- The A5 outer-close flag now also requires a candle in the trade direction.
- The compact HUD preserves the old gross-R value for armed plans and shows gross and net R for live plans. The full HUD retains its grade text while adding net-R and displacement details.
- A1 now submits brackets while a retest entry is pending, posts Momentum orders only after the FSM enters TRIGGERED, and cancels/closes Tester orders when plans resolve or are pruned. This does not remove the broker-emulator timing gap between an armed limit fill and the close-based FSM checks; A1 still needs replay validation.

These edits are source-only. No Pine compiler, TradingView chart, Strategy Tester, or replay was available; none of these changes is claimed to compile or pass runtime acceptance. A1 order mirroring and the declaration/HUD owner decisions remain unresolved pending the owner's answer. The PR stays draft until those decisions and a TradingView compile are complete.

Additional timeframe questions identified by review:

12. For B1, are the equal-pivot length and lookback measured in chart bars on each timeframe, or should they be measured in 5m bars?
13. For B6b, should the fixed 15m setup-swing context remain fixed on charts below 5m when B5 is off?
14. B6b now uses Engine pivot length 2 for its ladder. Is that the intended choice over StaxBot's existing swing length of 3?

Additional owner choices:

- If the declaration must remain at the 2.5.2 values, may A1's tester-order mirror be gated behind a new default-OFF input while it is replay-validated?
- Should the compact HUD display gross and net R together, preserving the original armed-plan gross-R cell?
- Does production enable `Flatten Open Trade At Session End` with no session filter? The revised guard leaves the 24/5 no-filter setup unchanged.
- Should `r_net` remain the weighted share of position costs used by the current implementation, or should each leg absorb the full per-contract cost?
- Where should the strategy file land when the proposed `production/` and `development/` folders are introduced?
