"""TradingView is the live plan source; this module mirrors its webhook plan.

The legacy scanner helpers remain for offline diagnostics only. They do not run
in the live watcher and must not be treated as synchronized plan settings.
"""
from __future__ import annotations

import json
import threading
import time
import urllib.request
from datetime import datetime, timezone
from typing import Any
from zoneinfo import ZoneInfo

NY = ZoneInfo("America/New_York")
SWING = 5
FVG_WINDOW = 5
EXPIRY = 20
MAX_STOP_DOLLARS = 250.0
CATALOG = {
    "MNQ": {"root": "MNQ", "yahoo": "MNQ=F", "tick": 0.25, "point": 2.0},
    "MGC": {"root": "MGC", "yahoo": "MGC=F", "tick": 0.1, "point": 10.0},
    "MES": {"root": "MES", "yahoo": "MES=F", "tick": 0.25, "point": 5.0},
    "M2K": {"root": "M2K", "yahoo": "M2K=F", "tick": 0.1, "point": 5.0},
    "MYM": {"root": "MYM", "yahoo": "MYM=F", "tick": 1.0, "point": 0.5},
}
DEFAULT_ROOTS = ("MNQ", "MGC", "MES", "M2K", "MYM")
DEFAULT_RULES = {
    "min_grade": "A",
    "tp_r": 1.0,
    "qty_a": 3,
    "qty_aplus": 5,
    "roots": list(DEFAULT_ROOTS),
}


def fetch_bars(yahoo: str) -> list[dict[str, Any]]:
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{yahoo}?interval=5m&range=5d"
    req = urllib.request.Request(url, headers={"User-Agent": "BreakawayDesk/1.2"})
    with urllib.request.urlopen(req, timeout=20) as resp:
        payload = json.loads(resp.read().decode())
    result = payload["chart"]["result"][0]
    stamps = result.get("timestamp") or []
    quote = result["indicators"]["quote"][0]
    now = time.time()
    bars = []
    for i, ts in enumerate(stamps):
        op, hi, lo, cl, vol = (quote[k][i] for k in ("open", "high", "low", "close", "volume"))
        if None in (op, hi, lo, cl) or ts + 300 > now:
            continue
        bars.append({
            "t": datetime.fromtimestamp(ts, timezone.utc).astimezone(NY),
            "o": float(op), "h": float(hi), "l": float(lo), "c": float(cl),
            "v": float(vol or 0),
        })
    return bars


def _ema(values: list[float], length: int) -> list[float | None]:
    out: list[float | None] = [None] * len(values)
    if len(values) < length:
        return out
    seed = sum(values[:length]) / length
    out[length - 1] = seed
    k = 2 / (length + 1)
    prev = seed
    for i in range(length, len(values)):
        prev = values[i] * k + prev * (1 - k)
        out[i] = prev
    return out


def _atr(bars: list[dict[str, Any]], length: int = 14) -> list[float | None]:
    trs = []
    for i, bar in enumerate(bars):
        if i == 0:
            trs.append(bar["h"] - bar["l"])
        else:
            prev = bars[i - 1]["c"]
            trs.append(max(bar["h"] - bar["l"], abs(bar["h"] - prev), abs(bar["l"] - prev)))
    out: list[float | None] = [None] * len(bars)
    if len(trs) < length:
        return out
    window = sum(trs[:length])
    out[length - 1] = window / length
    for i in range(length, len(trs)):
        window += trs[i] - trs[i - length]
        out[i] = window / length
    return out


def _in_session(when: datetime) -> bool:
    minutes = when.hour * 60 + when.minute
    london = 3 * 60 <= minutes < 6 * 60
    ny_gold = 8 * 60 + 20 <= minutes < 11 * 60 + 30
    return london or ny_gold


def _stop_hit(bar: dict[str, Any], direction: int, stop: float, tick: float) -> bool:
    # A print on the tick in front of the stop counts. The feed can sit
    # one tick short of the price that trades on the chart.
    if direction == 1:
        return bar["l"] <= stop + tick + 1e-4
    return bar["h"] >= stop - tick - 1e-4


def grade_setup(flags: dict[str, bool]) -> str:
    score = sum(flags.values())
    if score == 5:
        return "A+"
    if score == 4 and (flags["displacement"] or flags["session"]):
        return "A"
    return "B"


def accepts(grade: str, minimum: str) -> bool:
    if minimum == "Off":
        return True
    if grade == "A+":
        return True
    return minimum == "A" and grade == "A"


def _make_targets(entry: float, risk: float, direction: int, qty: int, reward: float, plan: Any) -> list[dict[str, Any]]:
    """Resolve enabled 1x/2x/3x TP steps into exact whole-contract allocations."""
    if not isinstance(plan, list) or not plan:
        return [{"id": "TP1", "price": round(entry + direction * risk * reward, 2), "qty": qty, "r": reward}]
    enabled = [
        (index, item) for index, item in enumerate(plan[:3], 1)
        if isinstance(item, dict) and bool(item.get("enabled"))
    ]
    if not enabled:
        return []
    if len(enabled) == 1:
        index, _ = enabled[0]
        return [{"id": f"TP{index}", "price": round(entry + direction * risk * reward * index, 2), "qty": qty, "r": reward * index}]

    remaining = qty
    result = []
    for index, item in enabled:
        try:
            requested = int(item.get("qty") or 1)
        except (TypeError, ValueError):
            return []
        # Once an enabled level cannot fit, skip it and all farther levels.
        if requested > remaining:
            break
        result.append({
            "id": f"TP{index}",
            "price": round(entry + direction * risk * reward * index, 2),
            "qty": requested,
            "r": reward * index,
        })
        remaining -= requested
    if not result:
        return []
    # Keep every contract accounted for. Any unallocated remainder stays with
    # the nearest selected target when farther allocations do not fit.
    result[0]["qty"] += remaining
    return result


class Market:
    def __init__(self, spec: dict[str, Any], rules: dict[str, Any]) -> None:
        self.spec = spec
        self.rules = rules
        self._reset()

    def _reset(self) -> None:
        self.range_high = None
        self.range_low = None
        self.range_high_i = None
        self.range_low_i = None
        self.high_broken = False
        self.low_broken = False
        self.bull_bos_i = None
        self.bear_bos_i = None
        self.bull_leg = None
        self.bear_leg = None
        self.bull_displaced = False
        self.bear_displaced = False
        self.pending = None
        self.open_trade = None
        self.last_close = None
        self.exit_events: list[dict[str, Any]] = []
        self.trade_events: list[dict[str, Any]] = []

    def replay(self, bars: list[dict[str, Any]], live_from: datetime | None = None) -> None:
        self._reset()
        for i in range(len(bars)):
            self._step(bars, i, act=False)
            # A fill from before this process started is history, not a live trade.
            if live_from and self.open_trade and self.open_trade["when"] < live_from:
                self.open_trade = None
                self.last_close = None

    def _pivot(self, bars, i, key):
        p = i - SWING
        if p < SWING:
            return None
        vals = [bars[j][key] for j in range(p - SWING, p + SWING + 1)]
        center = bars[p][key]
        others = vals[:SWING] + vals[SWING + 1:]
        if center > max(others):
            return p
        return None

    def _step(self, bars, i, act: bool) -> list[dict[str, Any]]:
        actions: list[dict[str, Any]] = []
        ph = self._pivot(bars, i, "h")
        pl = self._pivot(bars, i, "l")
        if ph is not None:
            self.range_high = bars[ph]["h"]
            self.range_high_i = ph
            self.high_broken = False
        if pl is not None:
            self.range_low = bars[pl]["l"]
            self.range_low_i = pl
            self.low_broken = False

        bar = bars[i]
        atr = bar.get("atr")
        displaced = atr is not None and abs(bar["c"] - bar["o"]) >= atr
        if self.range_high is not None and not self.high_broken and bar["c"] > self.range_high:
            self.high_broken = True
            self.bull_bos_i = i
            start = 0 if self.range_low_i is None else max(0, i - 30, self.range_low_i)
            self.bull_leg = min(b["l"] for b in bars[start:i + 1])
            self.bull_displaced = displaced
        if self.range_low is not None and not self.low_broken and bar["c"] < self.range_low:
            self.low_broken = True
            self.bear_bos_i = i
            start = 0 if self.range_high_i is None else max(0, i - 30, self.range_high_i)
            self.bear_leg = max(b["h"] for b in bars[start:i + 1])
            self.bear_displaced = displaced

        if self.open_trade and i > self.open_trade["fill_i"]:
            actions.extend(self._guard(bars, i, act))
        if self.open_trade:
            return actions

        if self.pending and i > self.pending["i"]:
            actions.extend(self._manage(bars, i, act))

        if i >= 2 and self.pending is None:
            setup = self._maybe_setup(bars, i)
            if setup and setup.get("grade"):
                self.pending = setup
                if act:
                    actions.append({"kind": "rest", **setup})
            elif setup and act:
                why = "stop risks more than $250" if setup.get("wide") else f"below {self.rules['min_grade']}"
                actions.append({"kind": "skip", "side": setup.get("side", ""), "why": why})
        return actions

    def _maybe_setup(self, bars, i) -> dict[str, Any] | None:
        bar = bars[i]
        mid = bars[i - 1]
        bull_fvg = bar["l"] > bars[i - 2]["h"] and mid["c"] > mid["o"]
        bear_fvg = bar["h"] < bars[i - 2]["l"] and mid["c"] < mid["o"]
        if bull_fvg and self.bull_bos_i is not None and 0 <= i - self.bull_bos_i <= FVG_WINDOW:
            direction = 1
        elif bear_fvg and self.bear_bos_i is not None and 0 <= i - self.bear_bos_i <= FVG_WINDOW:
            direction = -1
        else:
            return None
        bos_i = self.bull_bos_i if direction == 1 else self.bear_bos_i
        fvg_top = bar["l"] if direction == 1 else bars[i - 2]["l"]
        fvg_bot = bars[i - 2]["h"] if direction == 1 else bar["h"]
        entry = fvg_top if direction == 1 else fvg_bot
        far = fvg_bot if direction == 1 else fvg_top
        leg = self.bull_leg if direction == 1 else self.bear_leg
        tick = self.spec["tick"]
        buffer = 2 * tick
        raw = min(leg, mid["l"]) if direction == 1 else max(leg, mid["h"])
        stop = raw - buffer if direction == 1 else raw + buffer
        risk = (entry - stop) if direction == 1 else (stop - entry)
        if risk < 8 * tick:
            return None
        flags = {
            "displacement": bool(self.bull_displaced if direction == 1 else self.bear_displaced),
            "bias": _bias(bars, i, direction),
            "volume": mid["v"] > (bar.get("vol_avg") or 0),
            "session": _in_session(bar["t"]),
            "timing": i - bos_i <= 3,
        }
        setup_grade = grade_setup(flags)
        side = "long" if direction == 1 else "short"
        minimum = str(self.rules.get("min_grade") or "A")
        if not accepts(setup_grade, minimum):
            return {"i": i, "grade": None, "flags": flags, "side": side}
        qty = int(self.rules["qty_aplus"] if setup_grade == "A+" else self.rules["qty_a"])
        risk_dollars = risk * qty * self.spec["point"]
        if risk_dollars > MAX_STOP_DOLLARS:
            return {"i": i, "grade": None, "flags": flags, "wide": True, "side": side}
        reward = float(self.rules["tp_r"])
        target = entry + direction * risk * reward
        targets = [{"id": "TP1", "price": round(target, 2), "qty": qty, "r": reward}]
        if not targets:
            return {"i": i, "grade": None, "flags": flags, "wide": False, "side": side}
        setup_id = f"{self.spec['root']}:{bar['t'].isoformat()}:{side}"
        setup = {
            "i": i,
            "setup_id": setup_id,
            "root": self.spec["root"],
            "grade": setup_grade,
            "flags": flags,
            "side": side,
            "direction": direction,
            "entry": round(entry, 2),
            "stop": round(stop, 2),
            "target": round(target, 2),
            "targets": targets,
            "far": far,
            "qty": qty,
            "risk": round(risk_dollars, 2),
        }
        if direction == 1:
            self.bull_bos_i = None
        else:
            self.bear_bos_i = None
        return setup

    def _manage(self, bars, i, act: bool) -> list[dict[str, Any]]:
        pending = self.pending
        bar = bars[i]
        if pending.get("grade") is None:
            self.pending = None
            return []
        if i - pending["i"] > EXPIRY:
            self.pending = None
            return [{"kind": "cancel", "why": "expired"}] if act else []
        direction = pending["direction"]
        invalid = bar["c"] < pending["far"] if direction == 1 else bar["c"] > pending["far"]
        if invalid:
            self.pending = None
            return [{"kind": "cancel", "why": "gap failed"}] if act else []
        touched = bar["l"] <= pending["entry"] if direction == 1 else bar["h"] >= pending["entry"]
        if not touched:
            return []
        self.pending = None
        stop_hit = _stop_hit(bar, direction, pending["stop"], self.spec["tick"])
        self.open_trade = {**pending, "fill_i": i, "when": bar["t"], "remaining_qty": pending["qty"]}
        self.trade_events.append({
            "kind": "fill", "event_id": f"{pending['setup_id']}:ENTRY", "setup": dict(pending), "when": bar["t"],
        })
        if stop_hit:
            exits = [self._exit_event(self.open_trade, pending["stop"], "SL", bar["t"], pending["qty"], "STOP")]
            self._finish(pending["stop"], "SL", bar["t"])
        else:
            exits = self._targets_hit(self.open_trade, bar, bar["t"])
        if not act:
            return []
        actions = [{"kind": "fill", **pending, "when": bar["t"]}]
        actions.extend({"kind": "exit", **event} for event in exits)
        return actions

    def _guard(self, bars, i, act: bool) -> list[dict[str, Any]]:
        trade = self.open_trade
        bar = bars[i]
        direction = trade["direction"]
        stop_hit = _stop_hit(bar, direction, trade["stop"], self.spec["tick"])
        if not stop_hit:
            exits = self._targets_hit(trade, bar, bar["t"])
            return [{"kind": "exit", **event} for event in exits] if act else []
        remaining = trade["remaining_qty"]
        event = self._exit_event(trade, trade["stop"], "SL", bar["t"], remaining, "STOP")
        self._finish(trade["stop"], "SL", bar["t"])
        return [{"kind": "exit", **event}] if act else []

    def _targets_hit(self, trade: dict[str, Any], bar: dict[str, Any], when: datetime) -> list[dict[str, Any]]:
        hits = []
        remaining_targets = trade.setdefault("remaining_targets", [dict(item) for item in trade["targets"]])
        for target in list(remaining_targets):
            hit = bar["h"] >= target["price"] if trade["direction"] == 1 else bar["l"] <= target["price"]
            if not hit:
                continue
            qty = int(target["qty"])
            event = self._exit_event(trade, target["price"], "TP", when, qty, target["id"])
            hits.append(event)
            remaining_targets.remove(target)
            trade["remaining_qty"] -= qty
            if trade["remaining_qty"] <= 0:
                self._finish(target["price"], "TP", when)
                break
        return hits

    def _exit_event(self, trade: dict[str, Any], price: float, reason: str, when: datetime, qty: int, target_id: str) -> dict[str, Any]:
        event_id = f"{trade['setup_id']}:EXIT:{target_id}"
        if target_id == "STOP":
            event_id = f"{trade['setup_id']}:EXIT:STOP:{when.isoformat()}"
        event = {
            "setup": trade,
            "event_id": event_id,
            "price": price,
            "qty": qty,
            "target_id": target_id,
            "reason": reason,
            "when": when,
        }
        self.exit_events.append(event)
        self.trade_events.append({"kind": "exit", **event})
        return event

    def _finish(self, price: float, reason: str, when) -> None:
        trade = self.open_trade
        if trade:
            self.last_close = {**trade, "exit_price": price, "exit_reason": reason, "exit_when": when}
        self.open_trade = None


def _bias(bars, i, direction) -> bool:
    fast = bars[i].get("ema_fast")
    slow = bars[i].get("ema_slow")
    if fast is None or slow is None:
        return False
    close = bars[i]["c"]
    if direction == 1:
        return close > fast > slow
    return close < fast < slow


def enrich(bars: list[dict[str, Any]]) -> None:
    closes = [b["c"] for b in bars]
    fast = _ema(closes, 9)
    slow = _ema(closes, 21)
    atr = _atr(bars, 14)
    vols = [b["v"] for b in bars]
    for i, bar in enumerate(bars):
        bar["ema_fast"] = fast[i]
        bar["ema_slow"] = slow[i]
        bar["atr"] = atr[i]
        window = vols[max(0, i - 20):i]
        bar["vol_avg"] = sum(window) / len(window) if window else 0.0


def prepare(bars: list[dict[str, Any]], spec: dict[str, Any], rules: dict[str, Any] | None = None) -> Market:
    enrich(bars)
    market = Market(spec, rules or DEFAULT_RULES)
    market.replay(bars)
    return market


def payload_from(setup: dict[str, Any], event: str, price: float, reason: str, when: datetime) -> dict[str, Any]:
    side = setup["side"]
    root = setup.get("root") or "MES"
    return {
        "source": "breakaway-bot",
        "event": event,
        "eventId": f"{setup.get('setup_id', root + ':' + when.isoformat())}:ENTRY" if event == "entry" else None,
        "setupId": setup.get("setup_id"),
        "action": "buy" if (event == "entry" and side == "long") or (event == "exit" and side == "short") else "sell",
        "side": side,
        "ticker": root,
        "root": root,
        "qty": setup["qty"],
        "price": price,
        "stop": setup["stop"],
        "target": setup["target"],
        "targets": setup.get("targets"),
        "grade": setup["grade"],
        "reason": reason,
        "timestamp": when.strftime("%Y-%m-%dT%H:%M:%S%z"),
    }


def apply_actions(desk, actions: list[dict[str, Any]]) -> None:
    for action in actions:
        if action["kind"] == "rest":
            desk.set_watch({
                "grade": action["grade"],
                "note": f"Resting {action['grade']} {action['side']} {action['qty']} @ {action['entry']:.2f}",
            })
        elif action["kind"] == "cancel":
            desk.set_watch({"grade": None, "note": f"Setup cancelled ({action['why']})"})
        elif action["kind"] == "skip":
            desk.set_watch({"grade": None, "note": f"Skipped {action['side']} ({action['why']})"})
        elif action["kind"] == "fill":
            desk.handle(payload_from(action, "entry", action["entry"], "fvg_retrace", action["when"]))
        elif action["kind"] == "exit":
            setup = action.get("setup") or action
            desk.handle({
                "event": "exit", "eventId": action.get("event_id"), "setupId": setup.get("setup_id"),
                "targetId": action.get("target_id"), "qty": action.get("qty"),
                "price": action["price"], "reason": action["reason"],
                "timestamp": action["when"].strftime("%Y-%m-%dT%H:%M:%S%z"),
            })


def describe(market: Market, bars: list[dict[str, Any]]) -> str:
    root = market.spec["root"]
    last = bars[-1]["c"]
    trade = market.open_trade
    if trade:
        return (
            f"{root} {trade['grade']} {trade['side']} {trade.get('remaining_qty', trade['qty'])}/{trade['qty']} @ {trade['entry']:.2f} "
            f"stop {trade['stop']:.2f} targets " + ", ".join(f"{item['id']} {item['price']:.2f} x{item['qty']}" for item in trade.get("remaining_targets", trade["targets"]))
        )
    pending = market.pending
    if pending and pending.get("grade"):
        return (
            f"{root} resting {pending['grade']} {pending['side']} {pending['qty']} "
            f"@ {pending['entry']:.2f} target {pending['target']:.2f}"
        )
    return f"{root} {last:.1f} flat"


def rules_from(desk) -> dict[str, Any]:
    if desk is None:
        return {
            "min_grade": DEFAULT_RULES["min_grade"],
            "tp_r": DEFAULT_RULES["tp_r"],
            "qty_a": DEFAULT_RULES["qty_a"],
            "qty_aplus": DEFAULT_RULES["qty_aplus"],
            "roots": list(DEFAULT_ROOTS),
        }
    snap = desk.snapshot()
    roots = [root for root in (snap.get("watchMarkets") or DEFAULT_ROOTS) if root in CATALOG]
    return {
        "min_grade": DEFAULT_RULES["min_grade"],
        "tp_r": DEFAULT_RULES["tp_r"],
        "qty_a": DEFAULT_RULES["qty_a"],
        "qty_aplus": DEFAULT_RULES["qty_aplus"],
        "roots": roots or list(DEFAULT_ROOTS),
    }


def _shown_grade(market: Market, live_from: datetime | None) -> str | None:
    trade = market.open_trade
    if trade and (live_from is None or trade["when"] >= live_from) and trade.get("grade"):
        return trade["grade"]
    pending = market.pending or {}
    if pending.get("grade"):
        return pending["grade"]
    return None


def scan(desk, rules: dict[str, Any], live_from: datetime | None) -> tuple[list[str], str | None]:
    lines = []
    grade = None
    replay_events = []
    for root in rules["roots"]:
        spec = CATALOG[root]
        try:
            bars = fetch_bars(spec["yahoo"])
            if len(bars) < 30:
                lines.append(f"{root} waiting")
                continue
            enrich(bars)
            market = Market(spec, rules)
            market.replay(bars, live_from)
            if desk is not None and live_from is not None:
                replay_events.extend(event for event in market.trade_events if event["when"] >= live_from)
            lines.append(describe(market, bars))
            shown = _shown_grade(market, live_from)
            if shown:
                grade = shown
        except Exception as exc:
            lines.append(f"{root} feed error")
            print(root, exc)
    if desk is not None and live_from is not None:
        sync_events(desk, replay_events)
    return lines, grade


def watch_once(desk=None) -> str:
    rules = rules_from(desk)
    lines, grade = scan(desk, rules, None)
    note = " · ".join(lines)
    if desk:
        desk.set_watch({"symbol": " ".join(rules["roots"]), "grade": grade, "note": note})
    return note


def _root_of(row: dict[str, Any]) -> str:
    return str(row.get("root") or row.get("ticker") or "")


def _same_trade(pos: dict[str, Any] | None, trade: dict[str, Any] | None) -> bool:
    if not pos or not trade:
        return False
    try:
        same_root = _root_of(pos) == _root_of(trade)
        pos_initial = int(pos.get("initialQty", pos.get("qty")))
        return same_root and pos.get("side") == trade.get("side") and pos_initial == int(trade.get("qty")) and abs(float(pos.get("entry")) - float(trade.get("entry"))) < 0.05
    except (TypeError, ValueError):
        return False


def _already_closed(desk, closed: dict[str, Any]) -> bool:
    for fill in desk.snapshot().get("fills") or []:
        if _same_trade(fill, closed) and abs(float(fill.get("exit")) - float(closed["exit_price"])) < 0.05:
            return True
    return False


def sync_events(desk, events: list[dict[str, Any]]) -> None:
    """Replay stable events chronologically; desk eventId tracking prevents rebooking."""
    events.sort(key=lambda event: (event["when"], 0 if event["kind"] == "fill" else 1, event["event_id"]))
    for event in events:
        if event["kind"] == "fill":
            setup = event["setup"]
            payload = payload_from(setup, "entry", setup["entry"], "fvg_retrace", event["when"])
            payload["eventId"] = event["event_id"]
            desk.handle(payload)
            continue
        setup = event["setup"]
        desk.handle({
            "event": "exit", "eventId": event["event_id"], "setupId": setup["setup_id"],
            "targetId": event["target_id"], "qty": event["qty"], "price": event["price"],
            "reason": event["reason"],
            "timestamp": event["when"].strftime("%Y-%m-%dT%H:%M:%S%z"),
        })


def start_watcher(desk) -> None:
    """Mirror the plan received at the webhook; do not make an independent plan."""
    def loop() -> None:
        while True:
            try:
                plan = desk.snapshot().get("plan")
                if plan:
                    targets = ", ".join(
                        f"{item['id']} {item['price']:.2f} (w{item.get('allocation', 1):g})" for item in plan["targets"]
                    )
                    status = str(plan.get("status") or "PLAN")
                    desk.set_watch({
                        "symbol": plan["root"], "price": plan["entry"], "grade": plan["grade"],
                        "note": f"{status}: {plan['side'].upper()} {plan['root']} @ {plan['entry']:.2f} stop {plan['stop']:.2f}; {targets}",
                    })
                else:
                    desk.set_watch({"grade": None, "note": "Waiting for a TradingView plan"})
            except Exception as exc:
                desk.set_watch({"note": f"Watch error: {exc}"})
            time.sleep(1)

    threading.Thread(target=loop, name="stax-watch", daemon=True).start()


if __name__ == "__main__":
    print(watch_once())
