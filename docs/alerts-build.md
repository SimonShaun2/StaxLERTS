# Stax alerts build

Release-agnostic paper desk. The inspected baseline is StaxBot 2.4.5.
This file is the build record. It does not change Pine.

## Source

`staxbot_2_4_5.pine` is on `main` at commit `3d8a61223daee6029b8720c9194300afb3a6906c`, blob `7a86263cc48b6dec1356f47af5514148e4c982d5`.

That file is the initial `legacy_stax` baseline. The running desk does not read it, and a later compatible release does not need another Python deploy. The contract is `docs/event-contract.md`. The machine-readable notes are `docs/stax-event-v1.schema.json`.

`schemaVersion` 1 is supported. The 2.4.5 script does not send it. Missing `pineVersion`, `2.4.5`, and synthetic `2.4.6` / `2.5.0` labels are accepted when the event shape is valid. Those future labels are fixtures, not a review of unreleased scripts.

## What this build does

- Both `POST /webhook/trade-signal` and `POST /api/alert` use one adapter, one book, and one outbox.
- Plans notify and do not book. Entries book at the received price or record the exact rejection. Exits close frozen target quantity or the remainder.
- Fixed quantity and dollar risk are separate modes. Setup alerts still fire when sizing is not configured.
- Leftover contracts all go to the earliest enabled target. Allocation is frozen at entry.
- Guards use the stored session. They do not block an exit.
- The session rolls at 17:00 America/Chicago.
- A notice is delivered only after acceptance. A timeout stays uncertain. A retry does not add a fill.
- Sample `POST /api/demo` returns 410 in this source. The page no longer offers that button.
- Sam forwarding is not used. The old in-memory path POSTed the alert body to the saved URL with a bearer token and `X-Automation-Key`. That URL is an automation webhook, not a broker. This build does not forward to it.

## Paper configuration

Nothing is confirmed. Proposals are not active. No starting balance is stored. Sizing mode is unset, so a valid entry is rejected with `Paper sizing is not configured` and the setup alert still goes out.

## Rollout

The process already listening on port 8791 was not restarted. It still has the code and Sam URL it loaded at startup. The committed processor is not loaded there. The page HTML is read from disk, so the sample-trade button is gone on the next refresh. The old `/api/demo` route stays in that process until a planned restart.

Do not point an old scanner and the chart at the same book. Do not send a sample webhook. Production connectivity stays pending until a real TradingView event is in the alert log and the book. Cutover still needs an isolated validation inbox, the old entry source disabled first, and open positions reconciled. That restart is not part of this change.

## Checked offline

`python3 -m pytest bot/test_paper_desk.py` — 26 passed.
`python3 bot/execution_bot.py --selftest` — pass, equity 12.0. The selftest uses its own in-memory desk and does not bind 8791.

No webhook was sent to the listening process. No order was sent.

## Changelog

- Add the legacy and schema-1 adapters and remove the missing-source gate.
- Book paper entries, partials, and final exits in one transaction with the outbox.
- Point both webhook routes at that processor and disable the sample route in source.
