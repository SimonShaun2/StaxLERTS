# StaxBot event contract

The paper desk accepts a stable event contract. A new Pine release does not
need a Python rebuild, a filename upload, or a release allowlist.

`schemaVersion` selects the adapter. `pineVersion` is optional diagnostics.
It never rejects a valid event and it is not part of a dedupe key.

## Baseline inspection

This inspection defined the `legacy_stax` adapter. It is not a runtime gate.
The receiver does not open a Pine file.

| Item | Value |
| --- | --- |
| File | `staxbot_2_4_5.pine` |
| Commit | `3d8a61223daee6029b8720c9194300afb3a6906c` |
| Blob | `7a86263cc48b6dec1356f47af5514148e4c982d5` |

Checked against that file:

- `strategy()` is declared. The script does not call `strategy.entry` or `strategy.exit`. Events come from `alert()`.
- `calc_on_every_tick` is false. Alerts use `alert.freq_once_per_bar_close`.
- One event is one JSON object. Several events on a bar are one ordered JSON array. An empty outbox sends nothing.
- `source` is `staxbot`. `side` is `long` or `short`.
- `eventId` is `setupId:event:bar_index`. Two exits on one bar can share it.
- The builder writes `price` once, as the entry. Exit builders append a second `price`. On that known exit shape the last `price` is the exit and `entry` is the entry.
- Enabled targets send `id`, `price`, `allocation`, and lowercase `r`. Disabled targets are omitted. `allocation` is a weight, not a contract count.
- No `schemaVersion`, `pineVersion`, settings fingerprint, tick size, or point value is sent.
- Stop-update alerts default off. Exit alerts default on.
- The wire cancel reason for a newer break is `replaced`. The plan object's internal text `newer break` is not sent.

No payload mismatch with that baseline turned up. Later compatible scripts do not need this audit again.

## Adapters

| Body | Adapter |
| --- | --- |
| No `schemaVersion`, `source` is `staxbot` | `legacy_stax` |
| `schemaVersion` 1 | `schema-1` |
| Any other `schemaVersion` | Audited. Not parsed as legacy. |

`schema-1` uses the same event names and core fields as the legacy shape, requires one unambiguous value per field, and still dedupes exits by target and reason. `pineVersion` is optional. Pine does not have to emit schema 1 for the legacy adapter to run.

Unknown event names, exit reasons, and cancel reasons are audited. They do not open or close a paper position. If they name a setup the desk already has, that setup is flagged for reconciliation.

Unrecognized grades, scenarios, origins, and stop-preset labels are kept as metadata.

## Identity

Source instance, stable across restarts:

`["staxbot", exchange, ticker, timeframe]`

A Pine release number is not part of it. The same numeric `move_id` on two tickers is two moves.

Dedupe, one check only:

- Other events: `[source_instance, eventId]`
- Exits: `[source_instance, eventId, targetId, reason]`

A retry that adds metadata or changes `pineVersion` does not book again.

Alert replacement that regenerates setup ids is explicit. `disable_entries` on that source instance refuses new entries and still accepts exits for positions already open. Ordinary compatible upgrades do not call it.

## Lifecycle

Chart setup state and the paper position are stored separately.

| Event | Desk |
| --- | --- |
| `plan` | Track the setup and notify. Do not book. |
| `entry` | Run entry guards once. Book at the received price, or record the rejection. |
| `exit` `TP` | Close that target's frozen quantity. Zero quantity is a chart milestone. |
| `exit` `SL`, `BE`, `TRAILED`, `RECLAIM`, `FLAT` | Close the paper remainder at the received price. |
| `plan_cancel` | Update that setup only. |
| `stop_update` | Move the matching stop. `realizedR` is a stop offset, not P&L. |

An entry that arrives before its plan creates the setup from the entry. A later plan may fill in metadata. It does not reset the setup to armed, overwrite the fill, or repeat the first alert. A closed setup does not reopen. A rejected entry can still take later chart events, with no paper P&L. An exit that matches nothing is audited and does not invent a position.

## Batch policy

One SQLite transaction per request. Malformed JSON is audited and books nothing. In a valid array, invalid items are audited and do not roll back their siblings. A later valid item whose setup was named by an invalid item in that same batch is quarantined. Valid items run in array order. If a final exit is already applied and a target exit for that position arrives afterward, the target is not used to reopen the position; the setup is flagged.

## Sizing and allocation

Sizing mode is explicit. Fixed quantity needs a confirmed quantity. Risk sizing needs a confirmed dollar risk. Neither mode requires the other setting. Setup alerts still go out when sizing is not configured. The entry alert then states the rejection.

Suggested quantity, trade count, and daily limits stay proposals until they are confirmed. No starting balance is set.

Enabled targets are ordered TP1, TP2, TP3. Each gets the floor of its weighted share. Every leftover contract goes to the earliest enabled target. That allocation is frozen at entry. A disabled target and a zero-quantity target create no paper fill.

## R

1. Chart reference R is the payload `r` or exit `realizedR`. In the inspected 2.4.5 script, targets are built from the entry-to-Medium-stop distance. The desk does not rebuild target prices.
2. Planned reward/risk against the selected stop is `abs(target - entry) / abs(entry - initial_stop)`.
3. Paper R is realized paper P&L divided by the original whole-position dollar risk. Later stop moves do not change that denominator.

## Session and point values

The desk session rolls at 17:00 America/Chicago using the zone database. It does not copy Pine's clock-minute crossing. The desk trade count is not the chart's local count.

Point values are exact `root` matches, checked against CME micro equity index multipliers and the COMEX micro gold contract (10 troy ounces, $10 per $1 move):

| Root | Dollars per point |
| --- | --- |
| MNQ | 2 |
| MES | 5 |
| MYM | 0.5 |
| M2K | 5 |
| MGC | 10 |

`MNQZ2026` is not looked up by stripping characters, and `NQ` does not inherit MNQ's multiplier. The supported list is not an account authorization.

## Delivery

Event identity, the setup, the position, the ledger, and the pending notice commit together. A notice is delivered only after the chat accepts it. A timeout is uncertain. A retry uses the same notice identity and does not book again. Remote delivery is not exactly-once unless the receiver dedupes.

The five-minute check reads stored notices. It does not scan prices. Idle checks stay silent.

## What this build does not authorize

The previous Sam URL is an automation webhook. The in-memory desk POSTs the alert JSON to that URL with `Authorization: Bearer` and `X-Automation-Key`. That is not a broker and this build does not forward there. Paper bookkeeping and setup sentences are the authorized outputs.
