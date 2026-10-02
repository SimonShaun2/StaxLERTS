# StaxBot development and production

`production/staxbot.pine` is the live TradingView strategy. It is StaxBot 2.5.2. `development/staxbot_dev.pine` is the same strategy with a different name and alert tags, so a test on the chart cannot fire a live trade.

The trading rules in the two files match. `python3 tools/promote.py` checks that. A difference that survives promotion is a mistake: do not publish it.

## What is different in the test twin

| | Live `production/staxbot.pine` | Test `development/staxbot_dev.pine` |
| --- | --- | --- |
| TradingView name | StaxBot | StaxBot DEV |
| Short title | StaxBot | StaxBot DEV |
| HUD | STAXBOT 2.5.2 | STAXBOT DEV |
| Alerts | `alert()` runs | **Send DEV Alerts** is off, so `alert()` does not run |
| Payload `source` | `staxbot` | `staxbot-dev` |
| Payload `env` | absent | `dev` |
| Payload `version` | `2.5.2` | `2.5.2-dev` |

Stops, targets, grades, sessions, and the state machine are the same. The extra input only decides whether the test script calls `alert()`.

## Load the live script

1. Open the chart in TradingView. Open the Pine Editor and create a new blank script. Do not paste into an older saved script: TradingView keeps the old name.
2. Delete the template. Paste the entire contents of `production/staxbot.pine`. Save.
3. Add it to the chart. The legend reads **StaxBot**. The HUD reads **STAXBOT 2.5.2**. If the HUD reads anything else, the wrong file is on the chart.
4. Create the alert on **StaxBot**:
   - Condition: StaxBot → **alert() function calls only**
   - Message box: empty. The script sends the JSON.
   - Webhook URL: the paper desk `/webhook/trade-signal` (the public tunnel in front of `http://127.0.0.1:8791/webhook/trade-signal`)
   - Do not also enable order fills. This script does not place strategy orders.
5. Recreate that alert after any input change. The HUD says **INPUTS CHANGED** when the running alert is stale.

## Load the test script

1. Create a second new Pine script. Paste the entire contents of `development/staxbot_dev.pine`. Save.
2. Add it to the same chart. The legend reads **StaxBot DEV**. The HUD reads **STAXBOT DEV**. Both scripts can stay on the chart together.
3. Leave **Send DEV Alerts** off while you look at drawings and the HUD. No webhook leaves the chart.
4. To test a webhook, turn **Send DEV Alerts** on and create a separate alert on **StaxBot DEV**, alert() function calls only, message empty. Set the webhook to the paper desk `/webhook/dev` (the public tunnel in front of `http://127.0.0.1:8791/webhook/dev`).
5. Do not point the StaxBot DEV alert at `/webhook/trade-signal`, at Sam, or at a Stax strategy webhook.

The desk treats `source` `staxbot-dev`, `env` `dev`, and a `version` ending in `-dev` as a test. On `/webhook/trade-signal` it answers and does not book a position, does not put the alert in the inbox, and does not release it. On `/webhook/dev` it stores the payload in `devLog` on `/api/state` and still does not book or release. A live alert posted to `/webhook/dev` is rejected.

## Change, test, promote

1. Edit `development/staxbot_dev.pine` only. Leave `production/staxbot.pine` alone.
2. Paste the test file into the saved **StaxBot DEV** script in TradingView. Confirm the HUD still reads **STAXBOT DEV**.
3. Check the drawing, the HUD, and, if you turned alerts on, that the desk recorded the payload under `devLog` and the paper position did not change.
4. From the repo root, run `python3 tools/promote.py`. Exit 0 means the live file already matches the test twin with the DEV tags swapped back. Exit 1 prints the production diff and writes nothing.
5. Promote with the next version number. The number is the three-part version in the HUD and in the alert `version` field.

```bash
python3 tools/promote.py --write --version 2.5.3
```

That command:

- writes `production/staxbot.pine` with the title **StaxBot**, HUD **STAXBOT 2.5.3**, source `staxbot`, and version `2.5.3`
- writes the same text to `archive/staxbot_2_5_3.pine`
- updates the baseline line and the `-dev` version inside `development/staxbot_dev.pine` so the twin stays on 2.5.3-dev
- refuses to overwrite an archive file that already has different contents

6. Add a bullet under `## Unreleased` in `CHANGELOG.md`. The script prints a line you can start from: `Promoted StaxBot DEV to production 2.5.3. Live file is production/staxbot.pine.` Say what the trading change was.
7. Paste `production/staxbot.pine` into a new TradingView script named **StaxBot**. Confirm the HUD reads **STAXBOT 2.5.3**. Delete the old live alert and create it again on that script, still aimed at `/webhook/trade-signal`.
8. Commit the production file, the updated twin, the archive snapshot, and the changelog.

`python3 tools/promote.py --write` without `--version` republishes the current baseline number. Use a new `--version` when the trading logic changed. The same-version write warns if the result no longer matches the archive snapshot for that number.

## What not to load

Everything under `archive/` is history, including `archive/staxbot_2_5_2.pine`. `archive/staxbot_2_1.pine` is the untouched rebuild base. None of those files is the live chart or the test chart.
