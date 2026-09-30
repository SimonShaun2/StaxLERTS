#!/usr/bin/env python3
"""
Breakaway execution bot.

TradingView finds the setup. This process takes the trade.

It listens for the Generic JSON alert from breakaway_bot_stax.pine and
immediately papers the order: one position, the stop and target from the
alert, then the exit when the strategy sends one. Daily trade count, daily
loss, and daily profit target are enforced here, so a signal can still be
refused after it leaves the chart.

    python3 bot/execution_bot.py                 # http://127.0.0.1:8791
    python3 bot/execution_bot.py --selftest

Paper fills only. Nothing is sent to a broker unless you set a forward URL
in the desk. Stax's documented webhook accepts an options ticker, not MNQ.
"""
from __future__ import annotations

import argparse
import json
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

NY = ZoneInfo("America/New_York")
STATIC = Path(__file__).resolve().parent / "static" / "index.html"
POINT_VALUES = {
    "MNQ": 2.0, "NQ": 20.0, "MES": 5.0, "ES": 50.0, "MYM": 0.5, "YM": 5.0, "M2K": 5.0, "RTY": 50.0,
    "MGC": 10.0, "GC": 100.0,
}

def now_ny() -> datetime:
    return datetime.now(NY)


def parse_time(value: Any) -> datetime:
    if not isinstance(value, str) or not value.strip():
        return now_ny()
    text = value.strip()
    if text.endswith(("Z", "z")):
        text = text[:-1] + "+00:00"
    if len(text) >= 5 and text[-5] in "+-" and text[-3] != ":":
        text = text[:-2] + ":" + text[-2:]
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return now_ny()
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=NY)
    return parsed.astimezone(NY)


def point_value(root: str, override: float) -> float:
    if override > 0:
        return override
    return POINT_VALUES.get((root or "").upper(), 2.0)


class Desk:
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.starting_equity = 50_000.0
        self.equity = 50_000.0
        self.max_trades = 5
        self.max_daily_loss = 750.0
        self.daily_target = 0.0
        self.point_override = 0.0
        self.forward_url = ""
        self.watch = {"symbol": "MNQ MES MGC MYM", "price": None, "grade": None, "note": "Watching MNQ, MES, MGC, and MYM for A+ setups. Target is 2R."}
        self.day = now_ny().date()
        self.day_start_equity = self.equity
        self.trades_today = 0
        self.position: dict[str, Any] | None = None
        self.fills: list[dict[str, Any]] = []
        self.activity: list[dict[str, Any]] = []
        self.killed = False

    def snapshot(self) -> dict[str, Any]:
        with self.lock:
            self._roll_day(now_ny())
            daily = self.equity - self.day_start_equity
            pos = dict(self.position) if self.position else None
            if pos:
                pos["openRisk"] = round(pos["riskDollars"], 2)
            return {
                "mode": "paper",
                "listening": not self.killed,
                "equity": round(self.equity, 2),
                "dailyPnl": round(daily, 2),
                "tradesToday": self.trades_today,
                "maxTrades": self.max_trades,
                "maxDailyLoss": self.max_daily_loss,
                "dailyTarget": self.daily_target,
                "pointOverride": self.point_override,
                "forwardUrl": self.forward_url,
                "killed": self.killed,
                "watch": dict(self.watch),
                "position": pos,
                "fills": list(reversed(self.fills[-30:])),
                "activity": list(reversed(self.activity[-40:])),
            }

    def update_settings(self, body: dict[str, Any]) -> None:
        with self.lock:
            if "maxTrades" in body:
                self.max_trades = max(1, int(body["maxTrades"]))
            if "maxDailyLoss" in body:
                self.max_daily_loss = max(0.0, float(body["maxDailyLoss"]))
            if "dailyTarget" in body:
                self.daily_target = max(0.0, float(body["dailyTarget"]))
            if "pointOverride" in body:
                self.point_override = max(0.0, float(body["pointOverride"]))
            if "forwardUrl" in body:
                self.forward_url = str(body["forwardUrl"] or "").strip()
            if "killed" in body:
                self.killed = bool(body["killed"])
            self._note("SETTINGS", "Risk limits updated")

    def flatten(self) -> dict[str, Any]:
        with self.lock:
            if not self.position:
                return self._result(False, "No open trade to flatten")
            pos = self.position
            return self._close(pos["entry"], "FLAT", now_ny())

    def reset_book(self) -> None:
        with self.lock:
            self.equity = self.starting_equity
            self.day = now_ny().date()
            self.day_start_equity = self.equity
            self.trades_today = 0
            self.position = None
            self.fills.clear()
            self.activity.clear()
            self.killed = False
            self._note("RESET", "Paper book reset to $50,000")

    def set_watch(self, info: dict[str, Any]) -> None:
        with self.lock:
            self.watch.update(info)

    def handle(self, payload: dict[str, Any]) -> dict[str, Any]:
        with self.lock:
            if not isinstance(payload, dict):
                return self._result(False, "Body must be a JSON object")
            when = parse_time(payload.get("timestamp"))
            self._roll_day(when)
            if "unmodifiedTicker" in payload and "event" not in payload:
                return self._take_option(payload, when)
            event = payload.get("event") or "entry"
            if event == "entry":
                return self._take_future(payload, when)
            if event == "exit":
                return self._on_exit(payload, when)
            if event == "stop_update":
                return self._on_stop(payload)
            return self._result(False, f"Unknown event {event!r}")

    def _roll_day(self, when: datetime) -> None:
        if when.date() != self.day:
            self.day = when.date()
            self.day_start_equity = self.equity
            self.trades_today = 0

    def _blocked(self) -> str | None:
        if self.killed:
            return "Kill switch is on"
        daily = self.equity - self.day_start_equity
        if self.max_daily_loss > 0 and daily <= -self.max_daily_loss:
            return f"Daily loss cap hit ({daily:.0f})"
        if self.daily_target > 0 and daily >= self.daily_target:
            return f"Daily profit target hit ({daily:.0f})"
        if self.trades_today >= self.max_trades:
            return f"Max trades reached ({self.trades_today}/{self.max_trades})"
        if self.position:
            return f"Already in {self.position['side']} {self.position['ticker']}"
        return None

    def _take_future(self, payload: dict[str, Any], when: datetime) -> dict[str, Any]:
        reason = self._blocked()
        if reason:
            self._note("REFUSED", reason)
            return self._result(False, reason)
        try:
            qty = int(payload["qty"])
            price = float(payload["price"])
            stop = float(payload["stop"])
            target = float(payload["target"])
        except (KeyError, TypeError, ValueError):
            self._note("REFUSED", "Entry needs qty, price, stop, and target")
            return self._result(False, "Entry needs qty, price, stop, and target")
        if qty <= 0 or price <= 0:
            self._note("REFUSED", "Qty and price must be positive")
            return self._result(False, "Qty and price must be positive")
        side = payload.get("side") or ("long" if payload.get("action") == "buy" else "short")
        if side not in ("long", "short"):
            self._note("REFUSED", "Side must be long or short")
            return self._result(False, "Side must be long or short")
        grade = payload.get("grade")
        root = str(payload.get("root") or payload.get("ticker") or "MNQ")
        pv = point_value(root[:3] if root[:3] in POINT_VALUES else root, self.point_override)
        risk_pts = abs(price - stop)
        if risk_pts <= 0:
            self._note("REFUSED", "Stop is on top of the entry")
            return self._result(False, "Stop is on top of the entry")
        ticker = str(payload.get("ticker") or root)
        self.position = {
            "ticker": ticker,
            "root": root,
            "side": side,
            "qty": qty,
            "entry": price,
            "stop": stop,
            "target": target,
            "pointValue": pv,
            "riskDollars": risk_pts * qty * pv,
            "openedAt": when.isoformat(),
            "reason": payload.get("reason") or "fvg_retrace",
            "grade": grade or "",
        }
        self.trades_today += 1
        tag = f"{grade} " if grade else ""
        self._note(
            "TAKEN",
            f"{tag}{side.upper()} {qty} {ticker} @ {price:.2f}  stop {stop:.2f}  target {target:.2f}",
        )
        self._forward(payload)
        return self._result(True, "Trade taken", self.position)

    def _take_option(self, payload: dict[str, Any], when: datetime) -> dict[str, Any]:
        reason = self._blocked()
        if reason:
            self._note("REFUSED", reason)
            return self._result(False, reason)
        ticker = str(payload["unmodifiedTicker"])
        qty = int(payload.get("contracts") or 1)
        side = payload.get("side") or ("long" if "C" in ticker[6:] else "short")
        price = float(payload["close"]) if isinstance(payload.get("close"), (int, float)) else None
        self.position = {
            "ticker": ticker,
            "root": "OPT",
            "side": side,
            "qty": qty,
            "entry": price,
            "stop": payload.get("underlyingStop"),
            "target": payload.get("underlyingTarget"),
            "pointValue": 100.0,
            "riskDollars": 0.0,
            "openedAt": when.isoformat(),
            "reason": "stax_option",
        }
        self.trades_today += 1
        px = f" @ {price}" if price is not None else ""
        self._note("TAKEN", f"OPTION {side.upper()} {qty} {ticker}{px}")
        self._forward(payload)
        return self._result(True, "Option trade taken", self.position)

    def _on_exit(self, payload: dict[str, Any], when: datetime) -> dict[str, Any]:
        if not self.position:
            self._note("IGNORED", "Exit arrived with no open trade")
            return self._result(False, "No open trade")
        try:
            price = float(payload["price"])
        except (KeyError, TypeError, ValueError):
            self._note("IGNORED", "Exit is missing a price")
            return self._result(False, "Exit is missing a price")
        return self._close(price, str(payload.get("reason") or "EXIT"), when)

    def _on_stop(self, payload: dict[str, Any]) -> dict[str, Any]:
        if not self.position:
            return self._result(False, "No open trade")
        try:
            new_stop = float(payload.get("stop") if payload.get("stop") is not None else payload["price"])
        except (KeyError, TypeError, ValueError):
            return self._result(False, "Stop update is missing a price")
        pos = self.position
        old = pos["stop"]
        if pos["side"] == "long":
            pos["stop"] = max(old, new_stop) if isinstance(old, (int, float)) else new_stop
        else:
            pos["stop"] = min(old, new_stop) if isinstance(old, (int, float)) else new_stop
        if isinstance(pos["entry"], (int, float)) and isinstance(pos["stop"], (int, float)):
            pos["riskDollars"] = abs(pos["entry"] - pos["stop"]) * pos["qty"] * pos["pointValue"]
        self._note("STOP", f"Stop moved to {pos['stop']}")
        return self._result(True, "Stop updated", pos)

    def _close(self, price: float, reason: str, when: datetime) -> dict[str, Any]:
        pos = self.position
        assert pos is not None
        entry = pos["entry"] if isinstance(pos["entry"], (int, float)) else price
        sign = 1 if pos["side"] == "long" else -1
        pnl = sign * (price - entry) * pos["qty"] * pos["pointValue"]
        risk = pos["riskDollars"] or 0
        realized_r = pnl / risk if risk else None
        self.equity += pnl
        fill = {
            "ticker": pos["ticker"],
            "side": pos["side"],
            "qty": pos["qty"],
            "entry": entry,
            "exit": price,
            "pnl": round(pnl, 2),
            "r": None if realized_r is None else round(realized_r, 2),
            "reason": reason,
            "closedAt": when.isoformat(),
        }
        self.fills.append(fill)
        self.position = None
        self._note("CLOSED", f"{reason} {pos['side']} {pos['qty']} {pos['ticker']} pnl {pnl:+.2f}")
        return self._result(True, "Trade closed", fill)

    def _forward(self, payload: dict[str, Any]) -> None:
        url = self.forward_url
        if not url:
            return

        def send() -> None:
            data = json.dumps(payload).encode()
            req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
            try:
                with urllib.request.urlopen(req, timeout=5) as resp:
                    note = f"Stax accepted ({resp.status})"
            except (urllib.error.URLError, TimeoutError) as exc:
                note = f"Stax forward failed: {exc}"
            with self.lock:
                self._note("FORWARD", note)

        threading.Thread(target=send, daemon=True).start()

    def _note(self, kind: str, text: str) -> None:
        self.activity.append({"at": now_ny().strftime("%H:%M:%S"), "kind": kind, "text": text})

    def _result(self, ok: bool, message: str, data: dict[str, Any] | None = None) -> dict[str, Any]:
        body: dict[str, Any] = {"success": ok, "message": message}
        if data is not None:
            body["data"] = data
        return body


DESK = Desk()


def demo_script() -> list[dict[str, Any]]:
    stamp = now_ny().strftime("%Y-%m-%dT%H:%M:%S%z")
    long_entry = {
        "source": "breakaway-bot", "event": "entry", "action": "buy", "side": "long",
        "ticker": "MNQZ2026", "root": "MNQ", "exchange": "CME", "qty": 2,
        "orderType": "limit", "price": 30441.0, "stop": 30405.0, "target": 30477.0,
        "reason": "fvg_retrace", "timestamp": stamp,
    }
    stop_move = {
        "source": "breakaway-bot", "event": "stop_update", "action": "sell", "side": "long",
        "ticker": "MNQZ2026", "root": "MNQ", "qty": 2, "price": 30441.0, "stop": 30441.0,
        "target": 30477.0, "reason": "trail", "timestamp": stamp,
    }
    long_exit = {
        "source": "breakaway-bot", "event": "exit", "action": "sell", "side": "long",
        "ticker": "MNQZ2026", "root": "MNQ", "qty": 2, "price": 30477.0, "stop": 30441.0,
        "target": 30477.0, "reason": "TP", "timestamp": stamp,
    }
    short_entry = {
        "source": "breakaway-bot", "event": "entry", "action": "sell", "side": "short",
        "ticker": "MNQZ2026", "root": "MNQ", "exchange": "CME", "qty": 1,
        "orderType": "limit", "price": 30390.0, "stop": 30420.0, "target": 30360.0,
        "reason": "fvg_retrace", "timestamp": stamp,
    }
    short_exit = {
        "source": "breakaway-bot", "event": "exit", "action": "buy", "side": "short",
        "ticker": "MNQZ2026", "root": "MNQ", "qty": 1, "price": 30420.0, "stop": 30420.0,
        "target": 30360.0, "reason": "SL", "timestamp": stamp,
    }
    return [long_entry, stop_move, long_exit, short_entry, short_exit]


def run_demo(delay: float = 0.0) -> None:
    DESK.reset_book()
    for payload in demo_script():
        if delay:
            time.sleep(delay)
        DESK.handle(payload)


def selftest() -> int:
    desk = Desk()
    global DESK
    saved = DESK
    DESK = desk
    try:
        run_demo()
        snap = desk.snapshot()
        assert snap["position"] is None, snap["position"]
        assert snap["tradesToday"] == 2, snap["tradesToday"]
        # Long 2 MNQ, 36 points * $2 * 2 = +144. Short 1 MNQ, -30 points * $2 = -60. Net +84.
        assert abs(snap["equity"] - 50084.0) < 0.01, snap["equity"]
        assert abs(snap["dailyPnl"] - 84.0) < 0.01, snap["dailyPnl"]
        refused = desk.handle(demo_script()[0])
        # max trades default 5, so a third entry is allowed. Hit the cap instead.
        desk.max_trades = 2
        refused = desk.handle(demo_script()[0])
        assert refused["success"] is False, refused
        print("selftest: PASS", snap["equity"], snap["dailyPnl"])
        return 0
    except AssertionError as exc:
        print("selftest: FAIL", exc)
        return 1
    finally:
        DESK = saved


class Handler(BaseHTTPRequestHandler):
    server_version = "BreakawayDesk/1.2"

    def _send(self, status: int, body: dict[str, Any] | None = None, raw: bytes | None = None, content_type: str = "application/json") -> None:
        data = raw if raw is not None else json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self) -> None:  # noqa: N802
        path = self.path.split("?", 1)[0]
        if path in ("/", "/index.html"):
            self._send(200, raw=STATIC.read_bytes(), content_type="text/html; charset=utf-8")
            return
        if path == "/api/state":
            self._send(200, DESK.snapshot())
            return
        self._send(404, {"success": False, "message": "Not found"})

    def do_POST(self) -> None:  # noqa: N802
        path = self.path.split("?", 1)[0]
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b"{}"
        try:
            payload = json.loads(raw.decode() or "{}")
        except json.JSONDecodeError:
            self._send(400, {"success": False, "message": "Body is not valid JSON"})
            return
        if path in ("/webhook/trade-signal", "/api/alert"):
            result = DESK.handle(payload)
            self._send(200 if result["success"] else 400, result)
            return
        if path == "/api/settings":
            DESK.update_settings(payload)
            self._send(200, DESK.snapshot())
            return
        if path == "/api/flatten":
            self._send(200, DESK.flatten())
            return
        if path == "/api/demo":
            if getattr(self.server, "demo_running", False):
                self._send(409, {"success": False, "message": "Sample session already running"})
                return
            self.server.demo_running = True

            def job() -> None:
                try:
                    run_demo(delay=1.1)
                finally:
                    self.server.demo_running = False

            threading.Thread(target=job, daemon=True).start()
            self._send(200, {"success": True, "message": "Sample session started"})
            return
        if path == "/api/reset":
            DESK.reset_book()
            self._send(200, DESK.snapshot())
            return
        self._send(404, {"success": False, "message": "Not found"})

    def log_message(self, fmt: str, *args: Any) -> None:
        return


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8791)
    parser.add_argument("--selftest", action="store_true")
    args = parser.parse_args()
    if args.selftest:
        return selftest()
    server = ThreadingHTTPServer((args.host, args.port), Handler)
    server.demo_running = False
    from mgc_watch import start_watcher
    start_watcher(DESK)
    print(f"Breakaway execution bot listening on http://127.0.0.1:{args.port}  (MNQ MES MGC MYM, A+ only, 2R)")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
