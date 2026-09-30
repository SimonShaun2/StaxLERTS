"""Watch MES, MGC, and MYM 5-minute bars and take only A+ Breakaway setups.

MNQ is left off the watch. It is too expensive for the Select 25K account.

A setup is a close through the last confirmed swing, then a fair value gap in
that direction. Five checks grade it:

  displacement   the break candle's body is at least 1 ATR
  bias           9/21 EMA cloud agrees with the trade
  volume         the gap's middle candle is above its 20-bar average
  session        London 03:00-06:00 or New York 08:20-11:30
  timing         the gap prints within 3 bars of the break

A+ means all five. Anything less is skipped, including a four-flag A.
Size is 5 contracts. The target is 2R.
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
QTY_APLUS = 5
REWARD = 2.0
# One full stop has to leave the Select 25K $1,000 trail intact.
# The desk caps a single trade at $250.
MAX_STOP_DOLLARS = 250.0
MARKETS = (
    {"root": "MES", "yahoo": "MES=F", "tick": 0.25, "point": 5.0},
    {"root": "MGC", "yahoo": "MGC=F", "tick": 0.1, "point": 10.0},
    {"root": "MYM", "yahoo": "MYM=F", "tick": 1.0, "point": 0.5},
)


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


def grade_setup(flags: dict[str, bool]) -> str | None:
    score = sum(flags.values())
    if score == 5:
        return "A+"
    if score == 4 and (flags["displacement"] or flags["session"]):
        return "A"
    return None


class Market:
    def __init__(self, spec: dict[str, Any]) -> None:
        self.spec = spec
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

    def replay(self, bars: list[dict[str, Any]], live_from: datetime | None = None) -> None:
        self._reset()
        for i in range(len(bars)):
            self._step(bars, i, act=False)
            # A trade that filled before this watch started is not ours.
            # Drop it so a later A+ on the same market can still be taken.
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
                why = "stop risks more than $250" if setup.get("wide") else "below A+"
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
        if setup_grade != "A+":
            return {"i": i, "grade": None, "flags": flags, "side": side}
        qty = QTY_APLUS
        risk_dollars = risk * qty * self.spec["point"]
        if risk_dollars > MAX_STOP_DOLLARS:
            return {"i": i, "grade": None, "flags": flags, "wide": True, "side": side}
        target = entry + direction * risk * REWARD
        setup = {
            "i": i,
            "root": self.spec["root"],
            "grade": "A+",
            "flags": flags,
            "side": side,
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
        stop_hit = _stop_hit(bar, direction, pending["stop"], self.spec["tick"])
        target_hit = bar["h"] >= pending["target"] if direction == 1 else bar["l"] <= pending["target"]
        self.open_trade = {**pending, "fill_i": i, "when": bar["t"]}
        if stop_hit or target_hit:
            self._finish(pending["stop"] if stop_hit else pending["target"], "SL" if stop_hit else "TP", bar["t"])
        if not act:
            return []
        actions = [{"kind": "fill", **pending, "when": bar["t"]}]
        if stop_hit:
            actions.append({"kind": "exit", "price": pending["stop"], "reason": "SL", "when": bar["t"]})
        elif target_hit:
            actions.append({"kind": "exit", "price": pending["target"], "reason": "TP", "when": bar["t"]})
        return actions

    def _guard(self, bars, i, act: bool) -> list[dict[str, Any]]:
        trade = self.open_trade
        bar = bars[i]
        direction = trade["direction"]
        stop_hit = _stop_hit(bar, direction, trade["stop"], self.spec["tick"])
        target_hit = bar["h"] >= trade["target"] if direction == 1 else bar["l"] <= trade["target"]
        if not stop_hit and not target_hit:
            return []
        if stop_hit:
            self._finish(trade["stop"], "SL", bar["t"])
            return [{"kind": "exit", "price": trade["stop"], "reason": "SL", "when": bar["t"]}] if act else []
        self._finish(trade["target"], "TP", bar["t"])
        return [{"kind": "exit", "price": trade["target"], "reason": "TP", "when": bar["t"]}] if act else []

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


def prepare(bars: list[dict[str, Any]], spec: dict[str, Any]) -> Market:
    enrich(bars)
    market = Market(spec)
    market.replay(bars)
    return market


def payload_from(setup: dict[str, Any], event: str, price: float, reason: str, when: datetime) -> dict[str, Any]:
    side = setup["side"]
    root = setup.get("root") or "MNQ"
    return {
        "source": "breakaway-bot",
        "event": event,
        "action": "buy" if (event == "entry" and side == "long") or (event == "exit" and side == "short") else "sell",
        "side": side,
        "ticker": root,
        "root": root,
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
    root = market.spec["root"]
    last = bars[-1]["c"]
    trade = market.open_trade
    if trade:
        return (
            f"{root} A+ {trade['side']} {trade['qty']} @ {trade['entry']:.2f} "
            f"stop {trade['stop']:.2f} target {trade['target']:.2f}"
        )
    pending = market.pending
    if pending and pending.get("grade") == "A+":
        return (
            f"{root} resting A+ {pending['side']} {pending['qty']} "
            f"@ {pending['entry']:.2f} target {pending['target']:.2f}"
        )
    return f"{root} {last:.1f} flat"


def watch_once(desk=None) -> str:
    lines = []
    grade = None
    for spec in MARKETS:
        try:
            bars = fetch_bars(spec["yahoo"])
            if len(bars) < 30:
                lines.append(f"{spec['root']} waiting")
                continue
            market = prepare(bars, spec)
            lines.append(describe(market, bars))
            if (market.open_trade or market.pending or {}).get("grade") == "A+":
                grade = "A+"
        except Exception as exc:
            lines.append(f"{spec['root']} feed error")
            print(spec["root"], exc)
    note = " · ".join(lines)
    if desk:
        desk.set_watch({"symbol": "MES MGC MYM", "grade": grade, "note": note})
    return note


def _root_of(row: dict[str, Any]) -> str:
    return str(row.get("root") or row.get("ticker") or "")


def _same_trade(pos: dict[str, Any] | None, trade: dict[str, Any] | None) -> bool:
    if not pos or not trade:
        return False
    try:
        same_root = _root_of(pos) == _root_of(trade)
        return same_root and pos.get("side") == trade.get("side") and int(pos.get("qty")) == int(trade.get("qty")) and abs(float(pos.get("entry")) - float(trade.get("entry"))) < 0.05
    except (TypeError, ValueError):
        return False


def _already_closed(desk, closed: dict[str, Any]) -> bool:
    for fill in desk.snapshot().get("fills") or []:
        if _same_trade(fill, closed) and abs(float(fill.get("exit")) - float(closed["exit_price"])) < 0.05:
            return True
    return False


def sync_open(desk, market: Market, live_from: datetime) -> None:
    """Take an A+ that fills after the watch starts, and exit it at 2R or the stop."""
    pos = desk.snapshot().get("position")
    trade = market.open_trade if market.open_trade and market.open_trade["when"] >= live_from else None
    closed = market.last_close if market.last_close and market.last_close["when"] >= live_from else None
    if pos and not _same_trade(pos, trade) and closed and _same_trade(pos, closed):
        apply_actions(desk, [{
            "kind": "exit",
            "price": closed["exit_price"],
            "reason": closed["exit_reason"],
            "when": closed["exit_when"],
        }])
        pos = None
    if trade and pos is None:
        apply_actions(desk, [{"kind": "fill", **trade}])
    elif closed and pos is None and trade is None and not _already_closed(desk, closed):
        apply_actions(desk, [
            {"kind": "fill", **closed},
            {"kind": "exit", "price": closed["exit_price"], "reason": closed["exit_reason"], "when": closed["exit_when"]},
        ])


def start_watcher(desk) -> None:
    def loop() -> None:
        live_from = datetime.now(NY)
        while True:
            lines = []
            grade = None
            try:
                for spec in MARKETS:
                    try:
                        bars = fetch_bars(spec["yahoo"])
                        if len(bars) < 30:
                            lines.append(f"{spec['root']} waiting")
                            continue
                        enrich(bars)
                        market = Market(spec)
                        market.replay(bars, live_from)
                        sync_open(desk, market, live_from)
                        lines.append(describe(market, bars))
                        if (market.open_trade and market.open_trade["when"] >= live_from) or (market.pending or {}).get("grade") == "A+":
                            grade = "A+"
                    except Exception as exc:
                        lines.append(f"{spec['root']} feed error")
                        print(spec["root"], exc)
                desk.set_watch({"symbol": "MES MGC MYM", "grade": grade, "note": " · ".join(lines)})
            except Exception as exc:
                desk.set_watch({"symbol": "MES MGC MYM", "note": f"Watch error: {exc}"})
            time.sleep(30)

    threading.Thread(target=loop, name="mgc-watch", daemon=True).start()


if __name__ == "__main__":
    print(watch_once())
