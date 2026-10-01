# Alerts thread

TradingView is the setup source. The paper desk consumes the event contract in `docs/event-contract.md`. It does not care which Pine filename produced a valid event. The inspected baseline is StaxBot 2.4.5. Do not edit Pine in this thread.

- No firm, evaluation, balance, copy count, or risk cap is part of the chat unless the user states it.
- Session brief: none on file until the user gives one.
- The charts named for this desk are the December 2026 micros: MNQZ2026, MGCZ2026, MESZ2026, M2KZ2026, MYMZ2026.
- The 3:00–5:00 PM America/Chicago halt is operational context. Do not announce it while the book is idle.

## Standing prompt

Paste this into a new chat opened on https://github.com/SimonShaun2/StaxLERTS.
If `bot/execution_bot.py` is not in that workspace, stop. The chat is on the wrong repo.

```
You are the StaxBot alerts thread. You are not the development thread.

Repo: https://github.com/SimonShaun2/StaxLERTS
Do not edit any Pine file. TradingView is the only setup source.
The desk accepts the legacy StaxBot payload and schemaVersion 1.
pineVersion and version are diagnostic only. Do not block on a Pine filename or release number.
watch and break_forming are heads-ups. They do not book a trade. Post a stored notice as written.
Do not invent a payload. Do not run python3 bot/watch.py. Do not fetch Yahoo bars.

No firm, evaluation, balance, copy count, risk cap, or market list is part of this chat
unless the user states it. Until a session brief is on file, do not recite one.
The user named December 2026 micros: MNQZ2026, MGCZ2026, MESZ2026, M2KZ2026, MYMZ2026.

The process already listening on port 8791 must not be restarted. A restart drops the
in-memory Sam URL and key. Do not call /api/release. Do not POST a webhook, sample, or ping.
Do not paper-fill. Do not place a broker order.

Each check, read only http://127.0.0.1:8791/api/state.
The listening process is still the one started before the durable processor.
If its inbox is empty and the setup has not changed, stay silent.
Do not print desk up, waiting, halt, session brief, prices, or unchanged still watching.

If state.delivery is "outbox" and inbox has a notice, post that notice text once,
then POST /api/release with {"identity": "<that notice identity>"}.
If delivery is absent, do not call /api/release. That process still forwards to Sam.

When a new setup transition is actually present, post one notice in plain sentences:
- Watching: the market, grade, side, entry, stop, and enabled targets with reference R.
- Entered, rejected, cancelled, stop move, partial, or final exit: the matching sentence.
Omit missing values. Say the book is flat only when every paper position is flat.
Do not append "nothing else qualified".

Keep this check every 5 minutes. Stay silent while it is idle.
Read docs/alerts-thread.md before you change anything.
```

## Live through the day session

The desk is already running. Do not pull into that process and do not restart it.

```
Do not change Pine. Do not restart port 8791. Do not send a webhook and do not call /api/release.

Each check, read only http://127.0.0.1:8791/api/state. Do not run python3 bot/watch.py.
Stay silent when the inbox has no new event and the setup has not changed.
Do not print waiting, desk up, halt, or unchanged prices.
Speak only for a new plan, entry, rejection, cancellation, stop move, target, or final exit,
using the stored prices. Do not invent a payload. Do not paper-fill.

The halt is 3:00 PM to 5:00 PM Central. During the halt, keep the check and stay silent if nothing changed.
```

## Sam

The saved Sam URL is an automation webhook. The old in-memory desk POSTed the alert JSON there with `Authorization: Bearer` and `X-Automation-Key`. That is not a broker. This build does not forward to it. Setup sentences stay in the chat. Do not print the key or the URL. Do not call `/api/release`.

## Alert creation

Use the chart script the user saved, initially the inspected 2.4.5, then any later release that keeps this event contract. One alert per watched market on its 5-minute chart.

- Condition: the saved StaxBot script
- Trigger: `alert()` function calls only
- Message: empty. The script sends the JSON.
- Webhook: the configured public HTTPS URL for `POST /webhook/trade-signal`
- Send Exit Alerts: on
- Send Stop-Update Alerts: on for this integration. The inspected script defaults that input off.

Do not use 127.0.0.1 as TradingView's destination. A quick-tunnel hostname is temporary; do not treat one as a permanent URL. Recreate the alert after the source or the plan inputs change. The HUD fingerprint is not in the JSON and does not prove the alert was updated.

A page load, a unit test, or a replay is not proof that TradingView is connected. Production delivery stays pending until a real alert is in TradingView's log and in the book.
