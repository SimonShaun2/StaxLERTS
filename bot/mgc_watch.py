"""Watch Micro Gold 5-minute bars and take only A and A+ Breakaway setups.

A setup is a close through the last confirmed swing, then a fair value gap in
that direction. Five checks grade it:

  displacement   the break candle's body is at least 1 ATR
  bias           9/21 EMA cloud agrees with the trade
  volume         the gap's middle candle is above its 20-bar average
  session        London 03:00-06:00 or NY gold 08:20-11:30 New York
  timing         the gap prints within 3 bars of the break

A+ means all five. A means four, and one of them is displacement or the session.
Anything less is logged and skipped.

Size is fixed: A takes 3 MGC, A+ takes 5. MGC is $10 per 1.00 point.
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
CHART = "https://query1.finance.yahoo.com/v8/finance/chart/MGC=F?interval=5m&range=5d"
SWING = 5
FVG_WINDOW = 5
EXPIRY = 20
TICK = 0.1
BUFFER = 2 * TICK
MIN_STOP = 8 * TICK
POINT = 10.0
QTY = {"A": 3, "A+": 5}
# One full stop on 5 contracts has to fit under the desk's daily loss cap.
MAX_STOP_DOLLARS = 750.0


def fetch_bars() -> list[dict[str, Any]]:
    req = urllib.request.Request(CHART, headers={"User-Agent": "BreakawayDesk/1.2"})
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


def grade_setup(flags: dict[str, bool]) -> str | None:
    score = sum(flags.values())
    if score == 5:
        return "A+"
    if score == 4 and (flags["displacement"] or flags["session"]):
        return "A"
    return None


class Market:
    def __init__(self) -> None:
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

    def replay(self, bars: list[dict[str, Any]]) -> None:
        self.__init__()
        for i in range(len(bars)):
            self._step(bars, i, act=False)

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

        if self.pending and i > self.pending["i"]:
            actions.extend(self._manage(bars, i, act))

        if i >= 2 and self.pending is None:
            setup = self._maybe_setup(bars, i)
            if setup and setup.get("grade"):
                self.pending = setup
                if act:
                    actions.append({"kind": "rest", **setup})
            elif setup and act:
                why = "stop wider than the $750 daily cap" if setup.get("wide") else "below A"
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
        raw = min(leg, mid["l"]) if direction == 1 else max(leg, mid["h"])
        stop = raw - BUFFER if direction == 1 else raw + BUFFER
        risk = (entry - stop) if direction == 1 else (stop - entry)
        if risk < MIN_STOP:
            return None
        flags = {
            "displacement": bool(self.bull_displaced if direction == 1 else self.bear_displaced),
            "bias": _bias(bars, i, direction),
            "volume": mid["v"] > (bar.get("vol_avg") or 0),
            "session": _in_session(bar["t"]),
            "timing": i - bos_i <= 3,
        }
        setup_grade = grade_setup(flags)
        if setup_grade is None:
            return {"i": i, "grade": None, "flags": flags, "side": "long" if direction == 1 else "short"}
        qty = QTY[setup_grade]
        risk_dollars = risk * qty * POINT
        if risk_dollars > MAX_STOP_DOLLARS:
            return {"i": i, "grade": None, "flags": flags, "wide": True, "side": "long" if direction == 1 else "short"}
        target = entry + direction * risk
        setup = {
            "i": i,
            "grade": setup_grade,
            "flags": flags,
            "side": "long" if direction == 1 else "short",
            "direction": direction,
            "entry": round(entry, 2),
            "stop": round(stop, 2),
            "target": round(target, 2),
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
        if not act:
            return []
        stop_hit = bar["l"] <= pending["stop"] if direction == 1 else bar["h"] >= pending["stop"]
        target_hit = bar["h"] >= pending["target"] if direction == 1 else bar["l"] <= pending["target"]
        actions = [{"kind": "fill", **pending, "when": bar["t"]}]
        if stop_hit:
            actions.append({"kind": "exit", "price": pending["stop"], "reason": "SL", "when": bar["t"]})
        elif target_hit:
            actions.append({"kind": "exit", "price": pending["target"], "reason": "TP", "when": bar["t"]})
        return actions


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


def prepare(bars: list[dict[str, Any]]) -> Market:
    enrich(bars)
    market = Market()
    market.replay(bars)
    return market


def payload_from(setup: dict[str, Any], event: str, price: float, reason: str, when: datetime) -> dict[str, Any]:
    side = setup["side"]
    return {
        "source": "breakaway-mgc",
        "event": event,
        "action": "buy" if (event == "entry" and side == "long") or (event == "exit" and side == "short") else "sell",
        "side": side,
        "ticker": "MGC",
        "root": "MGC",
        "qty": setup["qty"],
        "price": price,
        "stop": setup["stop"],
        "target": setup["target"],
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
            desk.handle({
                "event": "exit", "price": action["price"], "reason": action["reason"],
                "timestamp": action["when"].strftime("%Y-%m-%dT%H:%M:%S%z"),
            })


def describe(market: Market, bars: list[dict[str, Any]]) -> str:
    last = bars[-1]
    pending = market.pending
    if not pending:
        return f"MGC {last['c']:.1f} flat, no A or A+ setup on the last closed 5m bar"
    if pending.get("grade") is None:
        return f"MGC {last['c']:.1f} saw a break, grade below A, skipped"
    return (
        f"Resting {pending['grade']} {pending['side']} {pending['qty']} MGC "
        f"@ {pending['entry']:.2f} stop {pending['stop']:.2f}"
    )


def watch_once(desk=None) -> str:
    bars = fetch_bars()
    if len(bars) < 30:
        note = "MGC feed returned too few closed bars"
        if desk:
            desk.set_watch({"symbol": "MGC", "note": note})
        return note
    market = prepare(bars)
    note = describe(market, bars)
    if desk:
        desk.set_watch({"symbol": "MGC", "price": bars[-1]["c"], "grade": (market.pending or {}).get("grade"), "note": note})
    return note


def start_watcher(desk) -> None:
    def loop() -> None:
        market = None
        seen = 0
        while True:
            try:
                bars = fetch_bars()
                if len(bars) < 30:
                    desk.set_watch({"symbol": "MGC", "note": "Waiting for enough MGC bars"})
                    time.sleep(30)
                    continue
                enrich(bars)
                if market is None or seen > len(bars):
                    market = Market()
                    market.replay(bars)
                    seen = len(bars)
                    desk.set_watch({
                        "symbol": "MGC", "price": bars[-1]["c"],
                        "grade": (market.pending or {}).get("grade"),
                        "note": describe(market, bars),
                    })
                elif len(bars) > seen:
                    for i in range(seen, len(bars)):
                        actions = market._step(bars, i, act=True)
                        apply_actions(desk, actions)
                    seen = len(bars)
                    desk.set_watch({
                        "symbol": "MGC", "price": bars[-1]["c"],
                        "grade": (market.pending or {}).get("grade"),
                        "note": describe(market, bars),
                    })
                else:
                    desk.set_watch({"symbol": "MGC", "price": bars[-1]["c"], "note": describe(market, bars)})
            except Exception as exc:  # keep the watch alive across a bad poll
                desk.set_watch({"symbol": "MGC", "note": f"MGC feed error: {exc}"})
            time.sleep(30)

    threading.Thread(target=loop, name="mgc-watch", daemon=True).start()


if __name__ == "__main__":
    print(watch_once())
