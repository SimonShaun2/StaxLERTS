# Stax alerts build

Corrected handoff for the paper desk. Chart version requested: StaxBot 2.4.5.
This file is the build record. It does not change Pine.

## Source

`staxbot_2_4_5.pine` is not in the repository, and there is no `v2.4.5` tag.
The latest frozen chart on `origin/main` is `staxbot_2_4_4.pine`, commit `c142ed80866723e2a6e8c6afc6024794f31a3a4f`, tag `v2.4.4` (`fa95f5f6d06d57563e4cebf8eb27ae8fe4083fde`).

2.4.4 is not the contract for this build. Its payload quirks were not copied forward.
Until 2.4.5 source is in the tree, `bot/paper_desk.py` audits webhook JSON and does not book or notify from it.

Unverified against 2.4.5, so not implemented as parser behavior:

- `alert()` only, and whether the script still declares `strategy()`
- one JSON object versus one ordered JSON array
- duplicate `price` keys
- exit `eventId` sharing
- target field names and the R field's casing
- `move_id`, `scenario`, `state`, `origin`, `plan_id`
- a version or settings fingerprint
- cancel and exit reason strings
- gap-stop exit price

The owner still recreates the TradingView alert when the HUD says INPUTS CHANGED. The desk cannot see that from a payload field that has not been verified.

## What this build does

- One 300-second check. It stays silent when no setup changes.
- No live Yahoo scan and no Python-generated plan in the alerts check.
- SQLite desk for settings, audits, ledger, positions, and the notification outbox.
- Suggested profile values stay proposals until the owner confirms them.
- A paper entry is refused when starting balance or risk per trade is missing.
- Entry guards do not block an exit recorded through the desk API.
- Delivery is complete only after acceptance. A timeout stays uncertain. A retry does not add another fill.
- Day roll for the new desk is 17:00 America/Chicago.
- The hard-coded evaluation start balance and day-cap text are removed from the committed desk.

## Rollout

The process already listening on port 8791 was not restarted. It still has the code and Sam URL it loaded at startup.
The committed files are not loaded into that process.
Do not point both an old and a new chart at the same production book.
Do not send a sample webhook to prove the path.
Cutover waits until 2.4.5 source is verified, an isolated inbox has seen a real alert, and the old source is disabled first.

## Changelog

- Record that 2.4.5 source is missing and refuse to book from a guessed payload.
- Add the durable paper desk and offline checks.
- Remove the hard-coded evaluation balance, trail, and day-cap wording from the committed desk.
