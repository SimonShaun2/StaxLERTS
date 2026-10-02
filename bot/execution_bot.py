#!/usr/bin/env python3
"""
Breakaway paper execution desk.

TradingView sends price levels and R targets. This process sizes and papers the trade.

It takes entry, stop, and target prices from the Pine alert, applies the desk's
independent per-trade dollar-risk setting to choose contract quantity, and
scales target allocation weights to that quantity. Desk-side limits can still
refuse a signal after it leaves the chart.

    python3 bot/execution_bot.py                 # http://127.0.0.1:8791
    python3 bot/execution_bot.py --selftest

Paper fills only. Nothing is sent to a broker unless you set a forward URL
in the desk. Stax's documented webhook accepts an options ticker, not a futures root.

A DEV alert (source staxbot-dev, env dev, or a version ending in -dev) is not a
live trade. /webhook/trade-signal ignores it. POST it to /webhook/dev to record
it without booking a position or releasing it.
"""
from __future__ import annotations

import argparse
import json
import math
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

NY = ZoneInfo("America/New_York")
STATIC = Path(__file__).resolve().parent / "static" / "index.html"
# Boot book until a session profile is set. The trail rolls at 5:00 PM New York
# and is enforced the moment equity touches it. One day cannot be more
# than 40% of total profit, so the day stops at 40% of the $1,500 target.
SELECT_START = 25_000.0
SELECT_TARGET = 1_500.0
SELECT_TRAIL = 1_000.0
SELECT_LOCK = 100.0
SELECT_CONSISTENCY = 0.40
SELECT_DAY_CAP = SELECT_TARGET * SELECT_CONSISTENCY
SELECT_MAX_RISK = 250.0
WATCH_ROOTS = ("MNQ", "MGC", "MES", "M2K", "MYM")
CONTRACT_MONTH = "Z2026"
MICROS = {"MNQ", "MES", "MYM", "MGC", "M2K"}
MINIS = {"NQ", "ES", "YM", "GC", "RTY"}
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
    return POINT_VALUES.get(contract_key(root), 2.0)


def contract_key(root: str) -> str:
    root = (root or "").upper()
    for name in ("MNQ", "MES", "MYM", "MGC", "M2K", "RTY", "NQ", "ES", "YM", "GC"):
        if root.startswith(name):
            return name
    return root


def bearer_token(value: str) -> str:
    text = (value or "").strip()
    if text.lower().startswith("authorization:"):
        text = text.split(":", 1)[1].strip()
    if text.lower().startswith("bearer "):
        text = text[7:].strip()
    return text


def current_contract(root: str) -> str:
    return f"{contract_key(root)}{CONTRACT_MONTH}"


def month_matches(ticker: str, root: str) -> bool:
    key = contract_key(root)
    text = (ticker or "").upper().split(":")[-1].strip()
    if text in {"", key, f"{key}Z2026", f"{key}Z26"}:
        return True
    return text.startswith(key) and ("Z2026" in text or text.endswith("Z26"))


def allocate_target_contracts(total_qty: int, targets: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Scale plan weights to the desk-sized position, assigning remainders nearest-first."""
    if total_qty <= 0 or not targets:
        return []
    weights = [max(0.0, float(item.get("allocation", item.get("qty", 1)))) for item in targets]
    weight_sum = sum(weights)
    if weight_sum <= 0:
        weights = [1.0] * len(targets)
        weight_sum = float(len(targets))
    exact = [total_qty * weight / weight_sum for weight in weights]
    quantities = [math.floor(value) for value in exact]
    remainder = total_qty - sum(quantities)
    priority = sorted(range(len(targets)), key=lambda index: (-(exact[index] - quantities[index]), index))
    for index in priority[:remainder]:
        quantities[index] += 1
    return [
        {**item, "allocation": weights[index], "qty": quantities[index]}
        for index, item in enumerate(targets)
        if quantities[index] > 0
    ]


DEV_SOURCES = {"staxbot-dev", "staxbot_dev"}


def is_dev_alert(payload: Any) -> bool:
    """True when the body is a StaxBot DEV test alert and must not be booked."""
    if isinstance(payload, list):
        return bool(payload) and all(is_dev_alert(item) for item in payload)
    if not isinstance(payload, dict):
        return False
    source = str(payload.get("source") or "").strip().lower()
    if source in DEV_SOURCES:
        return True
    if str(payload.get("env") or "").strip().lower() == "dev":
        return True
    version = str(payload.get("version") or "").strip().lower()
    return version.endswith("-dev")


def session_date(when: datetime) -> datetime.date:
    """The session ends at 5:00 PM New York. After that, it is the next day."""
    if when.tzinfo is None:
        when = when.replace(tzinfo=NY)
    else:
        when = when.astimezone(NY)
    if (when.hour, when.minute) >= (17, 0):
        return when.date() + timedelta(days=1)
    return when.date()


class Desk:
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.starting_equity = SELECT_START
        self.equity = SELECT_START
        self.max_trades = 5
        self.max_daily_loss = 0.0
        self.daily_target = SELECT_DAY_CAP
        self.point_override = 0.0
        self.risk_per_trade = SELECT_MAX_RISK
        self.forward_url = ""
        self.forward_token = ""
        self.inbox: list[dict[str, Any]] = []
        self.dev_log: list[dict[str, Any]] = []
        self.watch_markets = list(WATCH_ROOTS)
        self.contract_month = CONTRACT_MONTH
        self.watch = {
            "symbol": " ".join(current_contract(name) for name in WATCH_ROOTS),
            "price": None,
            "grade": None,
            "note": "Waiting for a TradingView plan.",
        }
        self.day = session_date(now_ny())
        self.day_start_equity = self.equity
        self.trades_today = 0
        self.days_traded = 0
        self.day_pnls: dict[Any, float] = {}
        self.peak_eod = SELECT_START
        self.floor = SELECT_START - SELECT_TRAIL
        self.floor_locked = False
        self.failed = False
        self.position: dict[str, Any] | None = None
        self.plan: dict[str, Any] | None = None
        self.fills: list[dict[str, Any]] = []
        self.activity: list[dict[str, Any]] = []
        self.processed_event_ids: set[str] = set()
        self.killed = False

    def snapshot(self) -> dict[str, Any]:
        with self.lock:
            self._roll_day(now_ny())
            daily = self.equity - self.day_start_equity
            pos = dict(self.position) if self.position else None
            if pos:
                pos["openRisk"] = round(pos["riskDollars"], 2)
            profit = self.equity - self.starting_equity
            best = max(self.day_pnls.values(), default=0.0)
            share = (best / profit) if profit > 0 else 0.0
            passed = (not self.failed) and profit >= SELECT_TARGET and self.days_traded >= 3 and best <= profit * SELECT_CONSISTENCY + 0.01
            return {
                "mode": "paper",
                "account": "Paper",
                "listening": not self.killed,
                "equity": round(self.equity, 2),
                "dailyPnl": round(daily, 2),
                "profit": round(profit, 2),
                "passTarget": SELECT_TARGET,
                "floor": round(self.floor, 2),
                "floorLocked": self.floor_locked,
                "failed": self.failed,
                "passed": passed,
                "bestDay": round(best, 2),
                "consistency": round(share, 4),
                "daysTraded": self.days_traded,
                "tradesToday": self.trades_today,
                "maxTrades": self.max_trades,
                "maxDailyLoss": self.max_daily_loss,
                "dailyTarget": self.daily_target,
                "pointOverride": self.point_override,
                "riskPerTrade": self.risk_per_trade,
                "forwardUrl": self.forward_url,
                "samKeySet": bool(self.forward_token),
                "inbox": [dict(item) for item in self.inbox],
                "devLog": [dict(item) for item in reversed(self.dev_log[-20:])],
                "planSettingsSource": "TradingView prices and R levels; paper desk sizing settings",
                "contractMonth": self.contract_month,
                "contracts": [current_contract(name) for name in self.watch_markets],
                "watchMarkets": list(self.watch_markets),
                "killed": self.killed,
                "watch": dict(self.watch),
                "position": pos,
                "plan": dict(self.plan) if self.plan else None,
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
            if "riskPerTrade" in body:
                self.risk_per_trade = min(SELECT_MAX_RISK, max(1.0, float(body["riskPerTrade"])))
            if "forwardUrl" in body:
                self.forward_url = str(body["forwardUrl"] or "").strip()
            if body.get("forwardToken"):
                self.forward_token = bearer_token(str(body["forwardToken"]))
            if "watchMarkets" in body:
                raw = body["watchMarkets"]
                if isinstance(raw, str):
                    names = [part.strip().upper() for part in raw.replace(";", ",").split(",") if part.strip()]
                else:
                    names = [str(part).strip().upper() for part in raw]
                chosen = [name for name in WATCH_ROOTS if name in names]
                if chosen:
                    self.watch_markets = chosen
                    self.watch["symbol"] = " ".join(current_contract(name) for name in chosen)
            if "killed" in body:
                self.killed = bool(body["killed"])
            self._note("SETTINGS", "Desk settings updated")

    def flatten(self) -> dict[str, Any]:
        with self.lock:
            if not self.position:
                return self._result(False, "No open trade to flatten")
            pos = self.position
            return self._close(pos["entry"], "FLAT", now_ny())

    def reset_book(self) -> None:
        with self.lock:
            self.equity = self.starting_equity
            self.day = session_date(now_ny())
            self.day_start_equity = self.equity
            self.trades_today = 0
            self.position = None
            self.plan = None
            self.fills.clear()
            self.activity.clear()
            self.processed_event_ids.clear()
            self.inbox.clear()
            self.dev_log.clear()
            self.killed = False
            self.days_traded = 0
            self.day_pnls = {}
            self.peak_eod = SELECT_START
            self.floor = SELECT_START - SELECT_TRAIL
            self.floor_locked = False
            self.failed = False
            self._note("RESET", "Paper book reset to the $25,000 Select evaluation")

    def set_watch(self, info: dict[str, Any]) -> None:
        with self.lock:
            self.watch.update(info)

    def _remember_dev(self, payload: Any, note: str) -> None:
        self.dev_log.append({"receivedAt": now_ny().isoformat(), "note": note, "payload": payload})
        if len(self.dev_log) > 50:
            self.dev_log = self.dev_log[-50:]
        self._note("DEV", note)

    def _ignore_dev_locked(self, payload: Any) -> dict[str, Any]:
        self._remember_dev(payload, "DEV alert ignored on the live webhook. Not booked and not released.")
        return self._result(
            False,
            "DEV alert ignored. It cannot open, change, or close a paper trade.",
            {"dev": True, "booked": False},
        )

    def ignore_dev(self, payload: Any) -> dict[str, Any]:
        with self.lock:
            return self._ignore_dev_locked(payload)

    def acknowledge_dev(self, payload: Any) -> dict[str, Any]:
        """Record a DEV alert from /webhook/dev. The paper book and Sam inbox stay put."""
        with self.lock:
            self._remember_dev(payload, "DEV alert recorded on /webhook/dev. Not booked and not released.")
            return self._result(True, "DEV alert recorded. Not a live trade.", {"dev": True, "booked": False})

    def handle(self, payload: Any) -> dict[str, Any]:
        with self.lock:
            if is_dev_alert(payload):
                return self._ignore_dev_locked(payload)
            if not isinstance(payload, dict):
                return self._result(False, "Body must be a JSON object")
            event_id = str(payload.get("eventId") or "")
            if event_id and event_id in self.processed_event_ids:
                return self._result(False, "Duplicate event ignored")
            when = parse_time(payload.get("timestamp"))
            self._roll_day(when)
            if "unmodifiedTicker" in payload and "event" not in payload:
                result = self._take_option(payload, when)
            else:
                event = payload.get("event") or "entry"
                if event == "ping":
                    result = self._result(True, "Connection test. Not a trade.")
                elif event == "entry":
                    result = self._take_future(payload, when)
                elif event == "exit":
                    result = self._on_exit(payload, when)
                elif event == "stop_update":
                    result = self._on_stop(payload)
                elif event == "plan":
                    result = self._on_plan(payload, when)
                elif event == "plan_cancel":
                    result = self._on_plan_cancel(payload)
                else:
                    result = self._result(False, f"Unknown event {event!r}")
            if event_id and result.get("success"):
                self.processed_event_ids.add(event_id)
            return result

    def _on_plan(self, payload: dict[str, Any], when: datetime) -> dict[str, Any]:
        setup_id = str(payload.get("setupId") or "")
        side = str(payload.get("side") or "")
        try:
            entry = float(payload.get("entry", payload.get("price")))
            stop = float(payload["stop"])
        except (KeyError, TypeError, ValueError):
            return self._result(False, "Plan needs entry and stop prices")
        if not setup_id or side not in ("long", "short") or entry <= 0 or stop <= 0:
            return self._result(False, "Plan needs setupId, valid side, and positive prices")
        if (side == "long" and stop >= entry) or (side == "short" and stop <= entry):
            return self._result(False, "Stop must be beyond entry in the risk direction")
        root = str(payload.get("root") or payload.get("ticker") or "").rsplit(":", 1)[-1]
        key = contract_key(root)
        if key not in self.watch_markets:
            return self._result(False, f"{key or root} is not on the Z2026 watch")
        ticker = str(payload.get("ticker") or "")
        if not month_matches(ticker, key):
            return self._result(False, f"Chart contract must be {current_contract(key)}")
        targets = payload.get("targets")
        if not isinstance(targets, list) or not targets:
            try:
                target_price = float(payload["target"])
            except (KeyError, TypeError, ValueError):
                return self._result(False, "Plan needs target or targets")
            targets = [{"id": "TP1", "price": target_price, "allocation": 1, "r": None}]
        if len(targets) > 3:
            return self._result(False, "Plan supports at most three targets")
        normalized = []
        previous = None
        for index, item in enumerate(targets, 1):
            if not isinstance(item, dict):
                return self._result(False, f"Target {index} must be an object")
            try:
                target_price = float(item["price"])
                allocation = float(item.get("allocation", item.get("qty", 1)))
            except (KeyError, TypeError, ValueError):
                return self._result(False, f"Target {index} needs a price and allocation weight")
            if target_price <= 0 or allocation <= 0:
                return self._result(False, f"Target {index} price and allocation weight must be positive")
            if (side == "long" and target_price <= entry) or (side == "short" and target_price >= entry):
                return self._result(False, f"Target {index} must be beyond entry in the trade direction")
            if previous is not None and ((side == "long" and target_price <= previous) or (side == "short" and target_price >= previous)):
                return self._result(False, "Targets must be ordered from nearest to farthest")
            normalized.append({"id": str(item.get("id") or f"TP{index}"), "price": target_price, "allocation": allocation, "r": item.get("r")})
            previous = target_price
        plan = {
            "setupId": setup_id,
            "settingsId": str(payload.get("settingsId") or ""),
            "ticker": ticker or current_contract(key),
            "root": key,
            "side": side,
            "grade": str(payload.get("grade") or ""),
            "entry": entry,
            "stop": stop,
            "targets": normalized,
            "status": "PLAN",
            "receivedAt": when.isoformat(),
        }
        self.plan = plan
        target_text = ", ".join(f"{item['id']} {item['price']:.2f} ({item['allocation']:g}w)" for item in normalized)
        self.watch.update({
            "symbol": plan["ticker"], "price": entry, "grade": plan["grade"],
            "note": f"TradingView plan: {side.upper()} {plan['ticker']} @ {entry:.2f} stop {stop:.2f}; {target_text}",
        })
        self._note("PLAN", self.watch["note"])
        return self._result(True, "Plan received", plan)

    def _on_plan_cancel(self, payload: dict[str, Any]) -> dict[str, Any]:
        setup_id = str(payload.get("setupId") or "")
        if not self.plan or (setup_id and self.plan.get("setupId") != setup_id):
            return self._result(False, "No matching active plan")
        self._note("PLAN", f"TradingView plan cancelled ({self.plan['setupId']})")
        self.plan = None
        self.watch.update({"grade": None, "note": "Waiting for a TradingView plan"})
        return self._result(True, "Plan cleared")

    def _roll_day(self, when: datetime) -> None:
        session = session_date(when)
        if session != self.day:
            self._apply_eod()
            self.day = session
            self.day_start_equity = self.equity
            self.trades_today = 0

    def _apply_eod(self) -> None:
        if self.equity > self.peak_eod:
            self.peak_eod = self.equity
        if self.peak_eod >= self.starting_equity + SELECT_TRAIL + SELECT_LOCK:
            self.floor = self.starting_equity + SELECT_LOCK
            self.floor_locked = True
        else:
            self.floor = max(self.floor, self.peak_eod - SELECT_TRAIL)

    def _check_bust(self) -> None:
        if self.equity <= self.floor:
            self.failed = True
            self.killed = True
            self._note("FAILED", f"Trailing drawdown hit at {self.floor:.0f}. The evaluation is over.")

    def _blocked(self) -> str | None:
        if self.failed:
            return "Select evaluation failed. Trailing drawdown was hit."
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

    def _normalize_targets(self, targets: Any, side: str, price: float) -> list[dict[str, Any]] | str:
        if not isinstance(targets, list) or not targets:
            return "Targets must be a non-empty array"
        normalized_targets = []
        previous = None
        for index, item in enumerate(targets, 1):
            if not isinstance(item, dict):
                return f"Target {index} must be an object"
            try:
                target_price = float(item["price"])
                allocation = float(item.get("allocation", item.get("qty", 1)))
            except (KeyError, TypeError, ValueError):
                return f"Target {index} needs price and allocation weight"
            if target_price <= 0 or allocation <= 0:
                return f"Target {index} price and allocation weight must be positive"
            if side == "long" and target_price <= price or side == "short" and target_price >= price:
                return f"Target {index} must be beyond entry in the trade direction"
            if previous is not None and (target_price <= previous if side == "long" else target_price >= previous):
                return "Targets must be ordered from nearest to farthest"
            normalized_targets.append({
                "id": str(item.get("id") or f"TP{index}"),
                "price": target_price,
                "allocation": allocation,
                "r": item.get("r"),
            })
            previous = target_price
        return normalized_targets

    def _take_future(self, payload: dict[str, Any], when: datetime) -> dict[str, Any]:
        reason = self._blocked()
        if reason:
            self._note("REFUSED", reason)
            return self._result(False, reason)
        try:
            price = float(payload["price"])
            stop = float(payload["stop"])
            target = float(payload["target"])
        except (KeyError, TypeError, ValueError):
            self._note("REFUSED", "Entry needs price, stop, and target")
            return self._result(False, "Entry needs price, stop, and target")
        if price <= 0:
            self._note("REFUSED", "Price must be positive")
            return self._result(False, "Price must be positive")
        side = payload.get("side") or ("long" if payload.get("action") == "buy" else "short")
        if side not in ("long", "short"):
            self._note("REFUSED", "Side must be long or short")
            return self._result(False, "Side must be long or short")
        grade = payload.get("grade")
        root = str(payload.get("root") or payload.get("ticker") or "MES").rsplit(":", 1)[-1]
        setup_id = str(payload.get("setupId") or "")
        if self.plan and setup_id and self.plan.get("setupId") != setup_id:
            self._note("REFUSED", "Entry setup does not match the TradingView plan")
            return self._result(False, "Entry setup does not match the TradingView plan")
        targets = payload.get("targets")
        use_plan = bool(self.plan and setup_id and self.plan.get("setupId") == setup_id)
        if use_plan:
            assert self.plan is not None
            price = float(self.plan["entry"])
            stop = float(self.plan["stop"])
            side = str(self.plan["side"])
            grade = self.plan.get("grade") or grade
            root = str(self.plan.get("root") or root)
            targets = list(self.plan["targets"])
            target = float(targets[0]["price"])
            entry_fp = str(payload.get("settingsId") or "")
            plan_fp = str(self.plan.get("settingsId") or "")
            if entry_fp and plan_fp and entry_fp != plan_fp:
                self._note("MISMATCH", "Alert settings differ from the armed plan. Prices stay on the plan.")
        key = contract_key(root)
        if key not in self.watch_markets:
            self._note("REFUSED", f"{key or root} is not on the Z2026 watch")
            return self._result(False, "Market is not on the Z2026 watch")
        if not month_matches(str(payload.get("ticker") or ""), key):
            self._note("REFUSED", f"Chart contract must be {current_contract(key)}")
            return self._result(False, f"Chart contract must be {current_contract(key)}")
        if (side == "long" and stop >= price) or (side == "short" and stop <= price):
            self._note("REFUSED", "Stop must be beyond entry in the risk direction")
            return self._result(False, "Stop must be beyond entry in the risk direction")
        pv = point_value(root[:3] if root[:3] in POINT_VALUES else root, self.point_override)
        risk_pts = abs(price - stop)
        if risk_pts <= 0:
            self._note("REFUSED", "Stop is on top of the entry")
            return self._result(False, "Stop is on top of the entry")
        key = contract_key(root)
        room = self.equity - self.floor
        risk_per_contract = risk_pts * pv
        budget = min(self.risk_per_trade, SELECT_MAX_RISK, max(0.0, room))
        qty = math.floor(budget / risk_per_contract) if risk_per_contract > 0 else 0
        qty_cap = 10 if key in MICROS else 1 if key in MINIS else 0
        if qty_cap:
            qty = min(qty, qty_cap)
        if qty < 1:
            self._note("REFUSED", f"One contract risks ${risk_per_contract:.0f}; watcher budget is ${budget:.0f}.")
            return self._result(False, "One contract exceeds the watcher's per-trade risk budget")
        risk_dollars = risk_per_contract * qty
        if risk_dollars > SELECT_MAX_RISK or risk_dollars >= room:
            self._note("REFUSED", f"Stop risks ${risk_dollars:.0f}. The trail has ${room:.0f} left, and one trade is capped at ${SELECT_MAX_RISK:.0f}.")
            return self._result(False, "Stop risks more than the trailing drawdown allows")
        if targets is not None:
            # Pine supplies target prices and relative weights; the desk sizes
            # the position and translates those weights into whole contracts.
            normalized = self._normalize_targets(targets, side, price)
            if isinstance(normalized, str):
                self._note("REFUSED", normalized)
                return self._result(False, normalized)
            normalized_targets = normalized
        else:
            normalized_targets = [{"id": "TP1", "price": target, "allocation": 1.0, "r": None}]
        normalized_targets = allocate_target_contracts(qty, normalized_targets)
        if not normalized_targets or sum(item["qty"] for item in normalized_targets) != qty:
            return self._result(False, "Watcher could not allocate the sized position across the plan targets")
        ticker = str((self.plan or {}).get("ticker") or payload.get("ticker") or root) if use_plan else str(payload.get("ticker") or root)
        self.position = {
            "ticker": ticker,
            "root": root,
            "setupId": setup_id,
            "side": side,
            "qty": qty,
            "entry": price,
            "stop": stop,
            "target": target,
            "targets": normalized_targets,
            "targetQtyMap": {item["id"]: item["qty"] for item in normalized_targets},
            "initialQty": qty,
            "exitedTargetIds": [],
            "pointValue": pv,
            "riskDollars": risk_pts * qty * pv,
            "initialRiskDollars": risk_pts * qty * pv,
            "openedAt": when.isoformat(),
            "reason": payload.get("reason") or "fvg_retrace",
            "grade": grade or "",
        }
        if self.plan and self.plan.get("setupId") == self.position["setupId"]:
            self.plan["status"] = "PAPER POSITION"
        if self.trades_today == 0:
            self.days_traded += 1
        self.trades_today += 1
        tag = f"{grade} " if grade else ""
        self._note(
            "TAKEN",
            f"{tag}{side.upper()} {qty} {ticker} @ {price:.2f}  stop {stop:.2f}  target {target:.2f}",
        )
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
        return self._result(True, "Option trade taken", self.position)

    def _on_exit(self, payload: dict[str, Any], when: datetime) -> dict[str, Any]:
        if not self.position:
            self._note("IGNORED", "Exit arrived with no open trade")
            return self._result(False, "No open trade")
        exit_setup_id = str(payload.get("setupId") or "")
        open_setup_id = str(self.position.get("setupId") or "")
        if exit_setup_id and open_setup_id and exit_setup_id != open_setup_id:
            self._note("IGNORED", "Exit setup ID does not match the open trade")
            return self._result(False, "Exit setup ID does not match the open trade")
        try:
            price = float(payload["price"])
        except (KeyError, TypeError, ValueError):
            self._note("IGNORED", "Exit is missing a price")
            return self._result(False, "Exit is missing a price")
        target_id = str(payload.get("targetId") or "")
        if target_id and target_id in self.position.get("exitedTargetIds", []):
            self._note("IGNORED", f"Duplicate {target_id} exit ignored")
            return self._result(False, f"Duplicate {target_id} exit")
        reason = str(payload.get("reason") or "EXIT")
        if target_id.startswith("TP") and reason == "TP":
            qty = int(self.position.get("targetQtyMap", {}).get(target_id, 0))
            if qty <= 0:
                self._note("IGNORED", f"No watcher contracts allocated to {target_id}")
                return self._result(False, f"No watcher contracts allocated to {target_id}")
            qty = min(qty, int(self.position["qty"]))
        else:
            # Pine may emit one stop-fill alert for each simulated target leg;
            # a shared stop closes the watcher's entire remaining position once.
            qty = int(self.position["qty"])
        return self._close(price, reason, when, qty, target_id, str(payload.get("eventId") or ""))

    def _on_stop(self, payload: dict[str, Any]) -> dict[str, Any]:
        if not self.position:
            return self._result(False, "No open trade")
        stop_setup = str(payload.get("setupId") or "")
        open_setup = str(self.position.get("setupId") or "")
        if stop_setup and open_setup and stop_setup != open_setup:
            return self._result(False, "Stop update setup ID does not match the open trade")
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

    def _close(self, price: float, reason: str, when: datetime, qty: int | None = None, target_id: str = "", event_id: str = "") -> dict[str, Any]:
        pos = self.position
        assert pos is not None
        close_qty = pos["qty"] if qty is None else qty
        if close_qty <= 0 or close_qty > pos["qty"]:
            return self._result(False, f"Exit qty {close_qty} exceeds remaining position {pos['qty']}")
        entry = pos["entry"] if isinstance(pos["entry"], (int, float)) else price
        sign = 1 if pos["side"] == "long" else -1
        pnl = sign * (price - entry) * close_qty * pos["pointValue"]
        risk = pos.get("initialRiskDollars", pos["riskDollars"]) or 0
        realized_r = pnl / risk if risk else None
        self.equity += pnl
        self.day_pnls[self.day] = self.day_pnls.get(self.day, 0.0) + pnl
        self._check_bust()
        fill = {
            "ticker": pos["ticker"],
            "side": pos["side"],
            "qty": close_qty,
            "entry": entry,
            "exit": price,
            "pnl": round(pnl, 2),
            "r": None if realized_r is None else round(realized_r, 2),
            "reason": reason,
            "eventId": event_id or None,
            "targetId": target_id or None,
            "remainingQty": pos["qty"] - close_qty,
            "closedAt": when.isoformat(),
        }
        self.fills.append(fill)
        pos["qty"] -= close_qty
        pos["riskDollars"] = abs(pos["entry"] - pos["stop"]) * pos["qty"] * pos["pointValue"]
        if target_id:
            pos.setdefault("exitedTargetIds", []).append(target_id)
        if pos["qty"] == 0:
            self.position = None
            if self.plan and self.plan.get("setupId") == pos.get("setupId"):
                self.plan["status"] = "COMPLETE"
        kind = "CLOSED" if self.position is None else "PARTIAL"
        self._note(kind, f"{reason} {pos['side']} {close_qty}/{pos.get('initialQty', close_qty)} {pos['ticker']} pnl {pnl:+.2f}")
        return self._result(True, "Trade closed" if self.position is None else "Partial exit booked", fill)

    def _chat_line(self, payload: dict[str, Any]) -> str:
        event = str(payload.get("event") or "entry").upper()
        side = str(payload.get("side") or "").upper()
        ticker = str(payload.get("ticker") or payload.get("root") or "")
        parts = [f"Held for chat: {event}"]
        if side:
            parts.append(side)
        if ticker:
            parts.append(ticker)
        entry = payload.get("entry", payload.get("price"))
        stop = payload.get("stop")
        if entry is not None:
            parts.append(f"entry {entry}")
        if stop is not None:
            parts.append(f"stop {stop}")
        return " ".join(parts) + ". Sam waits."

    def hold_for_chat(self, payload: dict[str, Any]) -> None:
        """Keep the alert until the alerts chat has presented it. Sam is later."""
        with self.lock:
            event_id = str(payload.get("eventId") or "")
            if event_id and any(item["eventId"] == event_id for item in self.inbox):
                return
            self.inbox.append({
                "eventId": event_id,
                "event": str(payload.get("event") or "entry"),
                "ticker": str(payload.get("ticker") or ""),
                "side": str(payload.get("side") or ""),
                "receivedAt": now_ny().isoformat(),
                "payload": payload,
            })
            if len(self.inbox) > 50:
                self.inbox = self.inbox[-50:]
            self._note("CHAT", self._chat_line(payload))

    def release_to_sam(self, event_id: str | None = None) -> dict[str, Any]:
        """The chat has presented this alert. Now send that same body to Sam."""
        with self.lock:
            if not self.forward_url or not self.forward_token:
                self._note("CHAT", "Release held. Sam URL or sender key is not saved.")
                return self._result(False, "Sam URL or sender key is not saved", {"released": 0})
            if event_id:
                held = [item for item in self.inbox if item["eventId"] == event_id]
                self.inbox = [item for item in self.inbox if item["eventId"] != event_id]
            else:
                held = list(self.inbox)
                self.inbox.clear()
            for item in held:
                label = f"{str(item['event']).upper()} {item['ticker']}".strip()
                self._note("CHAT", f"Chat released {label}. Forwarding to Sam.")
        for item in held:
            self._forward(item["payload"])
        return self._result(True, f"Released {len(held)}", {"released": len(held)})

    def forward_alert(self, payload: dict[str, Any]) -> None:
        """Send the TradingView body to Sam. Discord is a later leg."""
        self._forward(payload)

    def _forward(self, payload: dict[str, Any]) -> None:
        url = self.forward_url
        if not url:
            return

        token = self.forward_token
        headers = {"Content-Type": "application/json"}
        if token:
            headers["Authorization"] = f"Bearer {token}"
            headers["X-Automation-Key"] = token

        def send() -> None:
            data = json.dumps(payload).encode()
            req = urllib.request.Request(url, data=data, headers=headers)
            try:
                with urllib.request.urlopen(req, timeout=5) as resp:
                    note = f"Sam accepted ({resp.status})"
            except urllib.error.HTTPError as exc:
                detail = exc.read().decode("utf-8", "replace")[:160].replace("\n", " ")
                note = f"Sam forward failed: HTTP {exc.code} {detail}"
            except (urllib.error.URLError, TimeoutError) as exc:
                note = f"Sam forward failed: {exc}"
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
        "ticker": "MESZ2026", "root": "MES", "exchange": "CME",
        "orderType": "limit", "price": 5800.0, "stop": 5785.6, "target": 5814.4,
        "targets": [{"id": "TP1", "price": 5814.4, "allocation": 1, "r": 1.0}],
        "reason": "fvg_retrace", "timestamp": stamp,
    }
    stop_move = {
        "source": "breakaway-bot", "event": "stop_update", "action": "sell", "side": "long",
        "ticker": "MESZ2026", "root": "MES", "price": 5800.0, "stop": 5800.0,
        "target": 5814.4, "reason": "trail", "timestamp": stamp,
    }
    long_exit = {
        "source": "breakaway-bot", "event": "exit", "action": "sell", "side": "long",
        "ticker": "MESZ2026", "root": "MES", "price": 5814.4, "stop": 5800.0,
        "target": 5814.4, "reason": "TP", "targetId": "TP1", "timestamp": stamp,
    }
    short_entry = {
        "source": "breakaway-bot", "event": "entry", "action": "sell", "side": "short",
        "ticker": "MESZ2026", "root": "MES", "exchange": "CME",
        "orderType": "limit", "price": 5800.0, "stop": 5812.0, "target": 5776.0,
        "targets": [{"id": "TP1", "price": 5776.0, "allocation": 1, "r": 2.0}],
        "reason": "fvg_retrace", "timestamp": stamp,
    }
    short_exit = {
        "source": "breakaway-bot", "event": "exit", "action": "buy", "side": "short",
        "ticker": "MESZ2026", "root": "MES", "price": 5812.0, "stop": 5812.0,
        "target": 5776.0, "reason": "SL", "timestamp": stamp,
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
        desk.risk_per_trade = 100.0
        run_demo()
        snap = desk.snapshot()
        assert snap["position"] is None, snap["position"]
        assert snap["tradesToday"] == 2, snap["tradesToday"]
        # Desk risk setting sizes each setup to one MES; Pine quantities are absent.
        assert abs(snap["equity"] - 25012.0) < 0.01, snap["equity"]
        assert abs(snap["dailyPnl"] - 12.0) < 0.01, snap["dailyPnl"]
        assert snap["watchMarkets"] == ["MNQ", "MGC", "MES", "M2K", "MYM"], snap["watchMarkets"]
        assert snap["contracts"] == ["MNQZ2026", "MGCZ2026", "MESZ2026", "M2KZ2026", "MYMZ2026"], snap["contracts"]
        refused = desk.handle(demo_script()[0])
        # max trades default 5, so a third entry is allowed. Hit the cap instead.
        desk.max_trades = 2
        refused = desk.handle(demo_script()[0])
        assert refused["success"] is False, refused
        desk.handle({"event": "exit", "price": 5800.0, "reason": "FLAT", "timestamp": now_ny().strftime("%Y-%m-%dT%H:%M:%S%z")})
        desk.max_trades = 5
        stamp = now_ny().strftime("%Y-%m-%dT%H:%M:%S%z")
        blocked_root = desk.handle({
            "event": "plan", "setupId": "nq-plan", "side": "long", "root": "NQ", "ticker": "NQZ2026",
            "entry": 20000.0, "stop": 19900.0, "timestamp": stamp,
            "targets": [{"id": "TP1", "price": 20100.0, "allocation": 1, "r": 1}],
        })
        assert blocked_root["success"] is False, blocked_root
        wrong_month = desk.handle({
            "event": "plan", "setupId": "mnq-h", "side": "long", "root": "MNQ", "ticker": "MNQH2027",
            "entry": 20000.0, "stop": 19900.0, "timestamp": stamp,
            "targets": [{"id": "TP1", "price": 20100.0, "allocation": 1, "r": 1}],
        })
        assert wrong_month["success"] is False, wrong_month
        mnq = desk.handle({
            "event": "plan", "setupId": "mnq-plan", "side": "long", "root": "MNQ", "ticker": "MNQZ2026",
            "entry": 20000.0, "stop": 19900.0, "timestamp": stamp,
            "targets": [{"id": "TP1", "price": 20100.0, "allocation": 1, "r": 1}],
        })
        assert mnq["success"] is True, mnq
        desk.handle({"event": "plan_cancel", "setupId": "mnq-plan", "timestamp": stamp})
        armed = desk.handle({
            "event": "plan", "setupId": "mes-plan", "settingsId": "fp-a", "side": "long", "root": "MES",
            "ticker": "MESZ2026", "entry": 5800.0, "stop": 5785.6, "grade": "A", "timestamp": stamp,
            "targets": [{"id": "TP1", "price": 5814.4, "allocation": 1, "r": 1}],
        })
        assert armed["success"] is True, armed
        mismatched = desk.handle({
            "event": "entry", "setupId": "other-plan", "side": "long", "root": "MES",
            "price": 5800.0, "stop": 5785.6, "target": 5814.4, "timestamp": stamp,
        })
        assert mismatched["success"] is False, mismatched
        taken = desk.handle({
            "event": "entry", "setupId": "mes-plan", "settingsId": "fp-b", "side": "short", "root": "MES",
            "price": 1.0, "stop": 2.0, "target": 9.0, "timestamp": stamp,
            "targets": [{"id": "TP1", "price": 9.0, "allocation": 1, "r": 1}],
        })
        assert taken["success"] is True, taken
        assert desk.position is not None
        assert desk.position["entry"] == 5800.0, desk.position
        assert desk.position["stop"] == 5785.6, desk.position
        assert desk.position["target"] == 5814.4, desk.position
        assert desk.position["side"] == "long", desk.position
        assert any(item["kind"] == "MISMATCH" for item in desk.activity), desk.activity
        held_payload = {
            "source": "staxbot", "event": "plan", "eventId": "hold-1",
            "setupId": "MESZ2026:hold:long", "side": "long", "ticker": "MESZ2026", "root": "MES",
            "entry": 5800.0, "stop": 5785.6, "target": 5814.4,
            "targets": [{"id": "TP1", "price": 5814.4, "allocation": 1, "r": 1}],
        }
        desk.hold_for_chat(held_payload)
        assert [item["eventId"] for item in desk.snapshot()["inbox"]] == ["hold-1"]
        assert desk.forward_url == ""
        blocked = desk.release_to_sam("hold-1")
        assert blocked["success"] is False, blocked
        assert blocked["data"]["released"] == 0, blocked
        assert [item["eventId"] for item in desk.inbox] == ["hold-1"]
        desk.forward_url = "https://example.invalid/webhook"
        desk.forward_token = "test-key"
        released = desk.release_to_sam("hold-1")
        assert released["data"]["released"] == 1, released
        assert desk.inbox == []
        missing = desk.release_to_sam("missing")
        assert missing["data"]["released"] == 0, missing
        plan_before = desk.plan
        position_before = desk.position
        inbox_before = list(desk.inbox)
        equity_before = desk.equity
        ping = desk.handle({"source": "staxbot", "event": "ping", "eventId": "ping-1", "note": "Connection test. Not a trade."})
        assert ping["success"] is True, ping
        assert desk.plan is plan_before
        assert desk.position is position_before
        dev_entry = {
            "source": "staxbot-dev", "env": "dev", "event": "entry", "eventId": "dev-1",
            "version": "2.5.2-dev", "setupId": "dev-plan", "side": "long", "root": "MES",
            "ticker": "MESZ2026", "entry": 1.0, "stop": 0.5, "target": 2.0,
        }
        ignored = desk.handle(dev_entry)
        assert ignored["success"] is False, ignored
        assert ignored["data"]["booked"] is False, ignored
        assert desk.plan is plan_before
        assert desk.position is position_before
        assert desk.inbox == inbox_before
        assert desk.equity == equity_before
        version_only = desk.handle({"event": "plan", "version": "2.5.2-dev", "setupId": "dev-ver", "side": "long", "entry": 10, "stop": 9})
        assert version_only["success"] is False, version_only
        env_only = desk.handle({"source": "staxbot", "env": "dev", "event": "entry", "side": "short", "entry": 10, "stop": 11})
        assert env_only["success"] is False, env_only
        listed = desk.handle([{"source": "staxbot", "event": "ping"}])
        assert listed["success"] is False and "JSON object" in listed["message"], listed
        listed_dev = desk.handle([{"source": "staxbot-dev", "event": "entry", "version": "2.5.2-dev"}])
        assert listed_dev["success"] is False and listed_dev["data"]["dev"] is True, listed_dev
        assert desk.position is position_before and desk.plan is plan_before and desk.equity == equity_before
        recorded = desk.acknowledge_dev(dev_entry)
        assert recorded["success"] is True and recorded["data"]["booked"] is False, recorded
        assert desk.position is position_before and desk.plan is plan_before and desk.inbox == inbox_before
        assert desk.snapshot()["devLog"], desk.snapshot()["devLog"]
        assert any(item["kind"] == "DEV" for item in desk.activity), desk.activity
        prod_ping = desk.handle({"source": "staxbot", "event": "ping", "eventId": "ping-prod", "version": "2.5.2"})
        assert prod_ping["success"] is True, prod_ping
        assert desk.position is position_before
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
        if path == "/webhook/dev":
            if not is_dev_alert(payload):
                self._send(400, {"success": False, "message": "This endpoint accepts DEV alerts only. Live alerts stay on /webhook/trade-signal."})
                return
            self._send(200, DESK.acknowledge_dev(payload))
            return
        if path in ("/webhook/trade-signal", "/api/alert"):
            if is_dev_alert(payload):
                self._send(200, DESK.ignore_dev(payload))
                return
            result = DESK.handle(payload)
            if result.get("success"):
                DESK.hold_for_chat(payload)
            self._send(200 if result["success"] else 400, result)
            return
        if path == "/api/release":
            event_id = str(payload.get("eventId") or "").strip() or None
            self._send(200, DESK.release_to_sam(event_id))
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
    from watch import start_watcher
    start_watcher(DESK)
    print(f"StaxBot paper desk listening on http://127.0.0.1:{args.port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
