# StaxBot 2.6.0 webhook additions

StaxBot keeps its existing event names, base fields, event IDs, and alert timing. The version value changes to `2.6.0`. New properties are appended to existing JSON objects; consumers should ignore fields they do not use.

## Added fields

| Field | Events | Type | Meaning |
|---|---|---|---|
| `order_certainty` | `entry`, `exit` | string: `proven` or `unproven` | Whether lower-timeframe data proves the order of same-bar touches. Missing lower-timeframe data is `unproven`. |
| `r_net` | `exit` | number | Realized R after modeled commission and applicable slippage. With both cost inputs at zero, this equals `realizedR`. |
| `qty` | `plan`, `entry`, `exit`, `stop_update` | integer | Planned futures quantity. It is always 1 in options mode. Risk sizing can produce 0; the plan and its alerts remain present. |
| `legs` | `plan`, `entry`, `exit`, `stop_update` | integer array of length 3 | Quantity allocated to TP1, TP2, and TP3. When sizing is off, the value is `[1,0,0]`. |
| `disp_quality` | `plan` | object | `{ "outer_close": bool, "run_bars": int }`, where `run_bars` is 0, 2, or 3. |

The existing `allocation` values inside `targets` remain unchanged. They continue to express the configured target weights.

## Existing fields and event values

The existing `version` field is now `2.6.0` on all events, including `watch`, `break_forming`, and `break_cancelled`. All other pre-existing properties keep their names and meanings. The exit `reason` may now be `session_end_missing_bar` when a session-end bar is absent and the existing flatten option is enabled.

For options mode, `qty` remains 1 and `legs` is `[1,0,0]`; contract sizing applies to futures only. A zero-size futures plan still emits the normal plan and entry events with `qty: 0` and is marked `NO SIZE` in the HUD.

## Fingerprint

At default values, the fingerprint retains the exact 2.5.2 base string. New input values are appended only when they differ from their defaults. The webhook additions do not change the confirmed-bar alert cadence or the existing event vocabulary.
