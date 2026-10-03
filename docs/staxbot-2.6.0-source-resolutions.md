# StaxBot 2.6.0 source resolutions

Resolved against `staxbot_2_5_2.pine` on the `dev` branch before editing the strategy.

## C1. Fill price by scenario

- **B (shelf):** `f_entry_hit()` qualifies the shelf touch using the shelf price and reclaim tolerance: for longs, `low <= shelf` and `high >= shelf - tolerance`; shorts mirror it. The triggered plan retains its stored `entry` as the modeled fill price. The fill path does not substitute the open when a bar opens through that zone.
- **A (gap):** `f_entry_hit()` requires the bar range to cross the stored `entry`; the modeled fill remains that stored price. There is no gap-through-open adjustment.
- **C (sweep):** Like A, the sweep plan uses the generic stored-entry range test and retains the stored entry as the modeled fill. There is no gap-through-open adjustment.
- **M (momentum):** The plan is triggered on the signal bar and its entry is the break close. The stored close is the modeled fill.

The state machine records `fillBar`, not a separate realized fill price. The stop gap rule is distinct from the entry rules: a stop uses the bar open when the open is beyond the stop; otherwise it uses the stop price.

## C2. Exit decisions and prices

Live management checks the stop first. A stop exit uses `min(open, liveStop)` for a long or `max(open, liveStop)` for a short, which takes the open on a gap through the stop. If not stopped, enabled targets are checked in TP1, TP2, TP3 order and each target uses its limit price. A reclaim exit uses `close`; the configured session-end flatten also uses `close`. Breakeven and trailing stop changes are calculated after those exit checks and therefore apply to later bars. Session-end handling is skipped when an earlier exit has already closed the plan.

## C3. Target weights and partial state

The default leg weights are TP1 2, TP2 1, TP3 1. They are copied into the plan and serialized as each target's `allocation`; they do not currently size executable orders. The state machine has separate `hit1`, `hit2`, and `hit3` flags and keeps the plan live until every enabled leg is hit. This is per-target progress state, not quantity-based partial position accounting. The base script has no strategy orders.

## C4. Options ticker and quantity

For an entry in Stax Options Webhook mode, `f_payload()` adds `unmodifiedTicker` as the chosen underlying (`tickerOverrideIn`, or `syminfo.root`), the calculated expiry, `C` for a long or `P` for a short, and a strike rounded using `strikeStepIn` plus `strikesOtmIn`. The strike is based on the chart close. There is no quantity field or quantity-sizing path in the existing options payload; the strategy declaration's default quantity is 1. Per the spec, 2.6.0 keeps options quantity at 1 and applies contract sizing only to futures.

## C5. Lower-timeframe requests

`staxbot_2_5_2.pine` has no `request.security_lower_tf()` call. Its higher-timeframe data uses `request.security()` for D, W, 4H, 1H, 15m, and 5m context.

## C6. Strategy declaration and alert timing

The declaration sets fixed quantity 1, pyramiding 0, `calc_on_every_tick = true`, `calc_on_order_fills = false`, `process_orders_on_close = false`, margin long/short 1, cash-per-contract commission 0, and slippage 0. The base script has no `strategy.entry()` or `strategy.exit()` calls. Confirmed-bar alerts are emitted once per bar close (`alert.freq_once_per_bar_close`); the provisional realtime break alert uses once per bar. The imported strategy orders must not be allowed to gate the existing plan or alert state machine.
