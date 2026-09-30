#!/usr/bin/env python3
"""
Local stand-in for the StaxInvesting trade-signal webhook.

Point a TradingView alert (or curl) at http://<host>:<port>/webhook/trade-signal
and this server validates the body the way Stax documents it:

  * body must be JSON (TradingView only sends application/json when the
    alert message parses as JSON)
  * `timestamp`        ISO-8601 with a timezone offset
  * `unmodifiedTicker` SYMBOL + YYMMDD + P/C + STRIKE   e.g. SPY250630P616.0
  * `close`            optional number
  * `action`           optional, "entry" (default) or "reverse"

Generic Breakaway payloads (the ones with an `event` field) are validated too,
so the same server can be used to eyeball futures / generic JSON alerts.

Usage:
    python tools/mock_stax_webhook.py                # listens on 127.0.0.1:8787
    python tools/mock_stax_webhook.py --port 9000
    python tools/mock_stax_webhook.py --selftest     # run the built-in checks

Only the Python standard library is used.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

TICKER_RE = re.compile(r"^(?P<root>[A-Z]{1,6})(?P<exp>\d{6})(?P<side>[PC])(?P<strike>\d+(?:\.\d+)?)$")
GENERIC_EVENTS = {"entry", "exit", "stop_update"}
GENERIC_ACTIONS = {"buy", "sell"}


class PayloadError(ValueError):
    pass


def parse_timestamp(value: Any) -> datetime:
    if not isinstance(value, str) or not value.strip():
        raise PayloadError("Missing required field: timestamp")
    text = value.strip()
    if text.endswith("Z") or text.endswith("z"):
        text = text[:-1] + "+00:00"
    # Accept "-0500" as well as "-05:00" (Pine's str.format_time 'Z' emits the former).
    match = re.search(r"([+-])(\d{2})(\d{2})$", text)
    if match and text[-6] != ":":
        text = text[:-5] + f"{match.group(1)}{match.group(2)}:{match.group(3)}"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError as exc:
        raise PayloadError(f"timestamp is not ISO-8601: {value!r}") from exc
    if parsed.tzinfo is None:
        raise PayloadError("timestamp must include a timezone offset (e.g. 2025-01-13T11:00:00-05:00)")
    return parsed


def decode_ticker(value: Any) -> dict[str, Any]:
    if not isinstance(value, str) or not value:
        raise PayloadError("Missing required field: unmodifiedTicker")
    match = TICKER_RE.match(value)
    if not match:
        raise PayloadError(f"unmodifiedTicker must match XXXYYMMDD[PC]STRIKE, got {value!r}")
    exp = match.group("exp")
    try:
        expiry = datetime.strptime(exp, "%y%m%d").date()
    except ValueError as exc:
        raise PayloadError(f"unmodifiedTicker expiration {exp!r} is not a valid YYMMDD date") from exc
    return {
        "root": match.group("root"),
        "expiration": expiry.isoformat(),
        "type": "put" if match.group("side") == "P" else "call",
        "strike": float(match.group("strike")),
    }


def validate_stax(payload: dict[str, Any]) -> dict[str, Any]:
    ts = parse_timestamp(payload.get("timestamp"))
    contract = decode_ticker(payload.get("unmodifiedTicker"))
    close = payload.get("close")
    if close is not None and not isinstance(close, (int, float)):
        raise PayloadError("close must be a number when provided")
    action = payload.get("action", "entry")
    if action not in ("entry", "reverse"):
        raise PayloadError(f"action must be 'entry' or 'reverse', got {action!r}")
    for key in ("strike", "limit"):
        if key in payload and not isinstance(payload[key], (int, float)):
            raise PayloadError(f"{key} must be a number when provided")
    return {
        "kind": "stax",
        "timestamp": ts.isoformat(),
        "action": action,
        "contract": contract,
        "close": close,
    }


def validate_generic(payload: dict[str, Any]) -> dict[str, Any]:
    ts = parse_timestamp(payload.get("timestamp"))
    event = payload.get("event")
    if event not in GENERIC_EVENTS:
        raise PayloadError(f"event must be one of {sorted(GENERIC_EVENTS)}, got {event!r}")
    action = payload.get("action")
    if action not in GENERIC_ACTIONS:
        raise PayloadError(f"action must be one of {sorted(GENERIC_ACTIONS)}, got {action!r}")
    ticker = payload.get("ticker")
    if not isinstance(ticker, str) or not ticker:
        raise PayloadError("Missing required field: ticker")
    qty = payload.get("qty")
    if not isinstance(qty, int) or qty <= 0:
        raise PayloadError("qty must be a positive integer")
    for key in ("price", "stop", "target"):
        val = payload.get(key)
        if val is not None and not isinstance(val, (int, float)):
            raise PayloadError(f"{key} must be a number or null")
    return {
        "kind": "generic",
        "timestamp": ts.isoformat(),
        "event": event,
        "action": action,
        "ticker": ticker,
        "qty": qty,
        "price": payload.get("price"),
        "stop": payload.get("stop"),
        "target": payload.get("target"),
        "reason": payload.get("reason"),
    }


def validate(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise PayloadError("Body must be a JSON object")
    if "unmodifiedTicker" in payload:
        return validate_stax(payload)
    if "event" in payload:
        return validate_generic(payload)
    raise PayloadError("Missing required field: unmodifiedTicker")


class Handler(BaseHTTPRequestHandler):
    server_version = "MockStaxWebhook/1.0"
    counter = 0

    def _send(self, status: int, body: dict[str, Any]) -> None:
        raw = json.dumps(body, indent=2).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self) -> None:  # noqa: N802 (http.server naming)
        self._send(200, {"ok": True, "hint": "POST JSON to /webhook/trade-signal"})

    def do_POST(self) -> None:  # noqa: N802
        Handler.counter += 1
        request_id = f"WHK-MOCK-{Handler.counter:05d}"
        now = datetime.now(timezone.utc).isoformat()
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b""
        content_type = self.headers.get("Content-Type", "")

        try:
            if "application/json" not in content_type:
                raise PayloadError(
                    f"Content-Type is {content_type or 'missing'}; TradingView only sends application/json "
                    "when the alert message is valid JSON"
                )
            try:
                payload = json.loads(raw.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise PayloadError(f"Body is not valid JSON: {exc}") from exc
            decoded = validate(payload)
        except PayloadError as exc:
            print(f"[{request_id}] REJECTED  {exc}\n    body={raw[:400]!r}", flush=True)
            self._send(400, {"success": False, "error": str(exc), "timestamp": now, "requestId": request_id})
            return

        print(f"[{request_id}] ACCEPTED  {json.dumps(decoded)}", flush=True)
        self._send(
            200,
            {
                "success": True,
                "message": "Trade accepted by mock (nothing was executed)",
                "timestamp": now,
                "requestId": request_id,
                "data": {"decoded": decoded, "tradeCounter": {"current": Handler.counter, "limit": None}},
            },
        )

    def log_message(self, fmt: str, *args: Any) -> None:  # quieter default logging
        return


def selftest() -> int:
    good_stax = {"timestamp": "2026-09-30T11:00:00-0500", "unmodifiedTicker": "SPY260930C660.0"}
    good_stax_colon = {"timestamp": "2025-01-13T11:00:00-05:00", "unmodifiedTicker": "IWM250215P200.5", "close": 1.25}
    good_generic = {
        "source": "breakaway-bot", "event": "entry", "action": "buy", "side": "long", "ticker": "MNQZ2026",
        "root": "MNQ", "qty": 2, "orderType": "limit", "price": 30677.75, "stop": 30652.75, "target": 30702.75,
        "reason": "fvg_retrace", "realizedR": None, "timestamp": "2026-09-30T04:15:00-0400",
    }
    bad_cases = [
        ({"unmodifiedTicker": "SPY260930C660.0"}, "timestamp"),
        ({"timestamp": "2026-09-30T11:00:00", "unmodifiedTicker": "SPY260930C660.0"}, "timezone"),
        ({"timestamp": "2026-09-30T11:00:00-05:00", "unmodifiedTicker": "SPY2609C660"}, "XXXYYMMDD"),
        ({"timestamp": "2026-09-30T11:00:00-05:00", "unmodifiedTicker": "SPY261340C660.0"}, "valid YYMMDD"),
        ({"timestamp": "2026-09-30T11:00:00-05:00", "event": "entry", "action": "buy", "ticker": "MNQ", "qty": 0}, "qty"),
        ("not an object", "JSON object"),
    ]

    failures = 0
    for payload in (good_stax, good_stax_colon, good_generic):
        try:
            print("OK  ", json.dumps(validate(payload)))
        except PayloadError as exc:
            failures += 1
            print("FAIL expected accept, got:", exc)
    for payload, needle in bad_cases:
        try:
            validate(payload)
            failures += 1
            print("FAIL expected reject:", payload)
        except PayloadError as exc:
            if needle.lower() in str(exc).lower():
                print("OK   rejected:", exc)
            else:
                failures += 1
                print(f"FAIL wrong error (wanted {needle!r}):", exc)

    decoded = decode_ticker("SPY250630P616.0")
    assert decoded == {"root": "SPY", "expiration": "2025-06-30", "type": "put", "strike": 616.0}, decoded
    print("selftest:", "PASS" if failures == 0 else f"{failures} failure(s)")
    return 0 if failures == 0 else 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8787)
    parser.add_argument("--selftest", action="store_true", help="validate sample payloads and exit")
    args = parser.parse_args()

    if args.selftest:
        return selftest()

    server = ThreadingHTTPServer((args.host, args.port), Handler)
    print(f"Mock Stax webhook listening on http://{args.host}:{args.port}/webhook/trade-signal  (Ctrl+C to stop)")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
