"""Durable paper desk for StaxBot setup events.

Pipeline: raw body -> schema or legacy adapter -> canonical event ->
setup and paper position -> notification outbox.

The receiver does not read a Pine file, a release tag, or GitHub.
``schemaVersion`` selects the adapter. ``pineVersion`` is diagnostic only
and is never part of a dedupe key.

The 2.4.5 inspection that defined ``legacy_stax`` is recorded in
``docs/event-contract.md``. A later look at 2.5.0 is recorded there too.
Neither file is a startup gate. ``version`` is diagnostic, the same way
``pineVersion`` is.

Point values are exchange contract multipliers, matched exactly on the
payload ``root`` (CME micro equity index FAQ and COMEX micro gold specs):
MNQ $2, MES $5, MYM $0.50, M2K $5, MGC $10 per point. Minis are not inferred
from a ticker substring.

Batch policy: one SQLite transaction per request. Malformed JSON audits and
books nothing. In a valid array, invalid items are audited and do not roll
back later siblings. A later valid item whose setup was touched by an invalid
item in that same batch is quarantined. Valid items run in order. A delivery
retry never inserts another fill.
"""
from __future__ import annotations

import json
import math
import os
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

CHICAGO = ZoneInfo("America/Chicago")

# Exact roots only. CME micro E-mini multipliers and COMEX micro gold ($10/oz).
POINT_VALUES = {
    "MNQ": 2.0,
    "MES": 5.0,
    "MYM": 0.5,
    "M2K": 5.0,
    "MGC": 10.0,
}
SUPPORTED_ROOTS = tuple(POINT_VALUES)
KNOWN_EVENTS = {"plan", "entry", "exit", "plan_cancel", "stop_update", "watch", "break_forming", "break_cancelled"}
HEADS_UP = {"watch", "break_forming", "break_cancelled"}
EXIT_REASONS = {"TP", "SL", "BE", "TRAILED", "RECLAIM", "FLAT"}
FINAL_EXITS = {"SL", "BE", "TRAILED", "RECLAIM", "FLAT"}
# Inspected 2.4.5 and 2.5.0: f_close_trade uses these targetIds. TP exits use TP1, TP2, or TP3.
EXIT_TARGET_IDS = {
    "SL": "STOP",
    "BE": "STOP",
    "TRAILED": "STOP",
    "RECLAIM": "RECLAIM",
    "FLAT": "FLAT",
}
CANCEL_REASONS = {
    "replaced",
    "reclaim close",
    "ran without retest",
    "expired",
    "session",
    "sibling filled",
    "failed breakout",
}
CANCEL_TEXT = {
    "replaced": "it was replaced",
    "reclaim close": "price reclaimed the shelf",
    "ran without retest": "price ran to the target without a retest",
    "expired": "it expired",
    "session": "the session ended",
    "sibling filled": "the other plan on this move filled",
    "failed breakout": "the breakout failed",
}
EXIT_TEXT = {
    "TP": "target",
    "SL": "stop",
    "BE": "breakeven",
    "TRAILED": "trailed stop",
    "RECLAIM": "reclaim",
    "FLAT": "session flatten",
}
TARGET_ORDER = {"TP1": 0, "TP2": 1, "TP3": 2}
CONTRACT_STATUS = "supported:legacy_stax,schema-1"


def session_date(when: datetime):
    """Trading day rolls at 17:00 America/Chicago, including DST."""
    local = when.astimezone(CHICAGO) if when.tzinfo else when.replace(tzinfo=CHICAGO)
    if (local.hour, local.minute) >= (17, 0):
        return local.date() + timedelta(days=1)
    return local.date()


def paper_r(pnl: float, initial_stop_risk: float) -> float | None:
    """Cumulative paper R. The denominator is the original whole-position risk."""
    if initial_stop_risk <= 0:
        return None
    return pnl / initial_stop_risk


def derived_ref_risk(entry: float, target_price: float, chart_r: float) -> float | None:
    """Estimate of Pine refRisk from rounded prices. Not an exact internal value."""
    if chart_r <= 0:
        return None
    return abs(target_price - entry) / chart_r


def contract_spec(root: str) -> float | None:
    """Return the point value for an exact root. Never match by substring."""
    key = (root or "").strip().upper()
    if key in POINT_VALUES:
        return POINT_VALUES[key]
    return None


def allocate_contracts(total_qty: int, targets: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Floor weighted shares. All leftover contracts go to the earliest enabled target.

    Targets are ordered TP1, TP2, TP3. Disabled targets stay at zero.
    One enabled target receives the full quantity. Missing targets are omitted
    by the sender; an explicit ``enabled: false`` is also skipped.
    """
    decorated = []
    for index, item in enumerate(targets):
        enabled = bool(item.get("enabled", True))
        weight = float(item.get("allocation", item.get("weight", 0)) or 0)
        name = str(item.get("id") or "")
        decorated.append((TARGET_ORDER.get(name, 10), index, {**item, "enabled": enabled, "allocation": weight}))
    decorated.sort(key=lambda row: (row[0], row[1]))
    ordered = [row[2] for row in decorated]
    enabled = [item for item in ordered if item["enabled"] and item["allocation"] > 0 and str(item.get("id")) in TARGET_ORDER]
    quantities = {id(item): 0 for item in ordered}
    if total_qty > 0 and enabled:
        weight_sum = sum(item["allocation"] for item in enabled)
        floors = [int(total_qty * item["allocation"] / weight_sum) for item in enabled]
        left = total_qty - sum(floors)
        for offset, item in enumerate(enabled):
            quantities[id(item)] = floors[offset]
        quantities[id(enabled[0])] += left
    result = []
    for item in ordered:
        result.append({key: value for key, value in item.items()} | {"qty": quantities[id(item)]})
    return result


def _num(value: float | None) -> str:
    if value is None:
        return ""
    text = f"{float(value):.8f}".rstrip("0").rstrip(".")
    return text or "0"


def _finite(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(float(value))


def _source_instance(source: str, exchange: str, ticker: str, timeframe: str) -> str:
    return json.dumps([source, exchange, ticker, timeframe], separators=(",", ":"))


def _diagnostic_version(clean: dict[str, Any]) -> str | None:
    """pineVersion and version are labels. They do not select an adapter."""
    if clean.get("pineVersion") not in (None, ""):
        return str(clean["pineVersion"])
    if clean.get("version") not in (None, ""):
        return str(clean["version"])
    return None


def _headsup_dedupe(event: dict[str, Any]) -> str:
    """Heads-up payloads omit the market, so prices keep two charts on one bar apart."""
    parts = [
        event["source_instance"],
        event["eventId"],
        event.get("fp") or "",
        event.get("level") or "",
        event.get("side") or "",
        _num(event.get("entry")),
        _num(event.get("stop")),
        _num(event.get("target")),
        _num(event.get("range_high")),
        _num(event.get("range_low")),
    ]
    return json.dumps(parts, separators=(",", ":"))


def _headsup_sentence(event: dict[str, Any]) -> str:
    name = event["event"]
    if name == "watch":
        sentence = "A compressed range is on watch"
        if event.get("level"):
            sentence += f" at {event['level']}"
        if event.get("range_low") is not None and event.get("range_high") is not None:
            sentence += f", {_num(event['range_low'])} to {_num(event['range_high'])}"
        return sentence + "."
    if name == "break_forming":
        sentence = "A provisional"
        if event.get("side"):
            sentence += f" {event['side']}"
        sentence += " break is forming"
        details = []
        if event.get("entry") is not None:
            details.append(f"entry {_num(event['entry'])}")
        if event.get("stop") is not None:
            details.append(f"stop {_num(event['stop'])}")
        if event.get("target") is not None:
            details.append(f"target {_num(event['target'])}")
        if event.get("level"):
            details.append(event["level"])
        if details:
            sentence += ": " + ", ".join(details)
        return sentence + "."
    sentence = "The provisional"
    if event.get("side"):
        sentence += f" {event['side']}"
    sentence += " break"
    if event.get("entry") is not None:
        sentence += f" at {_num(event['entry'])}"
    return sentence + " was cancelled."


def _dedupe_key(source_instance: str, event_id: str, event: str, target_id: str, reason: str) -> str:
    if event == "exit":
        parts = [source_instance, event_id, target_id, reason]
    else:
        parts = [source_instance, event_id]
    return json.dumps(parts, separators=(",", ":"))


def _loads(text: str) -> Any:
    def hook(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        conflicts: list[str] = []
        for key, value in pairs:
            if key in result and result[key] != value:
                conflicts.append(key)
            result[key] = value
        if conflicts:
            result["__conflicts__"] = conflicts
        return result

    return json.loads(text, object_pairs_hook=hook)


def _conflicts(value: Any, path: str = "") -> list[tuple[str, list[str]]]:
    found: list[tuple[str, list[str]]] = []
    if isinstance(value, dict):
        if value.get("__conflicts__"):
            found.append((path or "$", list(value["__conflicts__"])))
        for key, item in value.items():
            if key == "__conflicts__":
                continue
            found.extend(_conflicts(item, f"{path}.{key}" if path else key))
    elif isinstance(value, list):
        for index, item in enumerate(value):
            found.extend(_conflicts(item, f"{path}[{index}]"))
    return found


def _strip(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: _strip(item) for key, item in value.items() if key != "__conflicts__"}
    if isinstance(value, list):
        return [_strip(item) for item in value]
    return value


def _adapt(item: dict[str, Any]) -> tuple[dict[str, Any] | None, str, str]:
    """Return canonical event, audit status, detail. Status '' means usable."""
    conflicts = _conflicts(item)
    schema = item.get("schemaVersion", None)
    if schema is not None and "schemaVersion" in item:
        if schema != 1:
            return None, "unsupported-schema", f"schemaVersion {schema} has no adapter"
        adapter = "schema-1"
        if conflicts:
            return None, "invalid-event", "schema 1 requires one unambiguous value per field"
    else:
        if item.get("source") != "staxbot":
            return None, "unknown-source", "No schemaVersion and source is not a registered StaxBot source"
        adapter = "legacy_stax"
        top = item.get("__conflicts__") or []
        nested = [row for row in conflicts if row[0] != "$"]
        if nested or (top and top != ["price"]):
            return None, "invalid-event", "Conflicting duplicate fields are not part of the legacy exit price exception"
        if top == ["price"] and item.get("event") != "exit":
            return None, "invalid-event", "Duplicate price is accepted only on a legacy exit"
    clean = _strip(item)
    event = clean.get("event")
    if event not in KNOWN_EVENTS:
        return None, "unknown-event", f"Unsupported event {event!r}"
    if event in HEADS_UP:
        return _adapt_headsup(clean, adapter)
    required = ("source", "event", "eventId", "setupId", "side", "entry", "stop", "price", "ticker", "root", "exchange", "timeframe")
    missing = [name for name in required if clean.get(name) in (None, "")]
    if missing:
        return None, "invalid-event", "Missing " + ", ".join(missing)
    if clean.get("source") != "staxbot":
        return None, "unknown-source", "source is not staxbot"
    side = str(clean["side"]).lower()
    if side not in {"long", "short"}:
        return None, "invalid-event", "side must be long or short"
    for name in ("entry", "stop", "price"):
        if not _finite(clean[name]):
            return None, "invalid-event", f"{name} is not a finite price"
    entry = float(clean["entry"])
    stop = float(clean["stop"])
    price = float(clean["price"])
    # 2.4.x repeats price, and the last value is the exit. 2.5.0 keeps price as the
    # entry and sends the fill in exit_price. The booked price is the fill.
    if event == "exit" and clean.get("exit_price") is not None:
        if not _finite(clean.get("exit_price")):
            return None, "invalid-event", "exit_price is not a finite price"
        price = float(clean["exit_price"])
    # Plans and entries still have the original stop on the risk side of entry.
    # Exits and stop updates carry the live stop, which may sit at or through entry.
    if event in {"plan", "entry"}:
        if side == "long" and not (stop < entry and stop < price):
            return None, "invalid-event", "Long stop must sit below entry and price"
        if side == "short" and not (stop > entry and stop > price):
            return None, "invalid-event", "Short stop must sit above entry and price"
    targets, target_error = _targets(clean.get("targets"), side, entry)
    if target_error:
        return None, "invalid-event", target_error
    reason = clean.get("reason")
    target_id = str(clean.get("targetId") or "")
    if event == "exit":
        if reason not in EXIT_REASONS:
            return None, "unknown-event", f"Unsupported exit reason {reason!r}"
        if reason == "TP":
            if target_id not in TARGET_ORDER:
                return None, "unknown-event", f"Unsupported target {target_id!r}"
        elif reason in FINAL_EXITS and target_id != EXIT_TARGET_IDS[reason]:
            expected = EXIT_TARGET_IDS[reason]
            return None, "invalid-event", f"{reason} exit targetId must be {expected}"
    if event == "plan_cancel":
        if reason not in CANCEL_REASONS:
            return None, "unknown-event", f"Unsupported cancel reason {reason!r}"
    if event == "stop_update" and not _finite(clean.get("stop")):
        return None, "invalid-event", "stop_update needs a finite stop"
    pine_version = _diagnostic_version(clean)
    canonical = {
        "adapter": adapter,
        "schemaVersion": 1 if adapter == "schema-1" else None,
        "pineVersion": pine_version,
        "source": "staxbot",
        "event": event,
        "eventId": str(clean["eventId"]),
        "setupId": str(clean["setupId"]),
        "plan_id": clean.get("plan_id"),
        "move_id": None if clean.get("move_id") is None else str(clean["move_id"]),
        "scenario": None if clean.get("scenario") is None else str(clean["scenario"]),
        "state": None if clean.get("state") is None else str(clean["state"]),
        "origin": None if clean.get("origin") is None else str(clean["origin"]),
        "side": side,
        "entry": entry,
        "stop": stop,
        "stop_preset": None if clean.get("stop_preset") is None else str(clean["stop_preset"]),
        "price": price,
        "target": float(clean["target"]) if _finite(clean.get("target")) else None,
        "targets": targets,
        "grade": None if clean.get("grade") is None else str(clean["grade"]),
        "timeframe": str(clean["timeframe"]),
        "ticker": str(clean["ticker"]),
        "root": str(clean["root"]).strip().upper(),
        "exchange": str(clean["exchange"]),
        "timestamp": None if clean.get("timestamp") is None else str(clean["timestamp"]),
        "reason": None if reason is None else str(reason),
        "targetId": target_id,
        "realizedR": float(clean["realizedR"]) if _finite(clean.get("realizedR")) else None,
        "extra": {
            key: value
            for key, value in clean.items()
            if key
            not in {
                "source", "event", "eventId", "setupId", "plan_id", "move_id", "scenario", "state",
                "origin", "side", "entry", "stop", "stop_preset", "price", "target", "targets",
                "grade", "timeframe", "ticker", "root", "exchange", "timestamp", "reason",
                "targetId", "realizedR", "schemaVersion", "pineVersion", "version",
            }
        },
    }
    canonical["source_instance"] = _source_instance(
        canonical["source"], canonical["exchange"], canonical["ticker"], canonical["timeframe"]
    )
    canonical["dedupe_key"] = _dedupe_key(
        canonical["source_instance"], canonical["eventId"], event, target_id, str(reason or "")
    )
    return canonical, "", ""


def _adapt_headsup(clean: dict[str, Any], adapter: str) -> tuple[dict[str, Any] | None, str, str]:
    """watch, break_forming, and break_cancelled notify. They do not book."""
    if clean.get("source") != "staxbot":
        return None, "unknown-source", "source is not staxbot"
    event_id = clean.get("eventId")
    if not isinstance(event_id, str) or not event_id:
        return None, "invalid-event", "Missing eventId"
    side = clean.get("side")
    if side not in (None, ""):
        side = str(side).lower()
        if side not in {"long", "short"}:
            return None, "invalid-event", "side must be long or short"
    else:
        side = None
    for name in ("entry", "stop", "target", "range_high", "range_low"):
        if clean.get(name) not in (None, "") and not _finite(clean.get(name)):
            return None, "invalid-event", f"{name} is not a finite price"
    level = None if clean.get("level") in (None, "") else str(clean["level"])
    entry = float(clean["entry"]) if _finite(clean.get("entry")) else None
    stop = float(clean["stop"]) if _finite(clean.get("stop")) else None
    target = float(clean["target"]) if _finite(clean.get("target")) else None
    canonical = {
        "adapter": adapter,
        "schemaVersion": 1 if adapter == "schema-1" else None,
        "pineVersion": _diagnostic_version(clean),
        "source": "staxbot",
        "event": str(clean["event"]),
        "eventId": event_id,
        "setupId": "",
        "plan_id": None,
        "move_id": None,
        "scenario": None,
        "state": None,
        "origin": None,
        "side": side,
        "entry": entry,
        "stop": stop,
        "stop_preset": None,
        "price": entry,
        "target": target,
        "targets": [],
        "grade": None,
        "level": level,
        "fp": None if clean.get("fp") in (None, "") else str(clean["fp"]),
        "range_high": float(clean["range_high"]) if _finite(clean.get("range_high")) else None,
        "range_low": float(clean["range_low"]) if _finite(clean.get("range_low")) else None,
        "timeframe": str(clean.get("timeframe") or ""),
        "ticker": str(clean.get("ticker") or ""),
        "root": str(clean.get("root") or "").strip().upper(),
        "exchange": str(clean.get("exchange") or ""),
        "timestamp": None if clean.get("timestamp") is None else str(clean["timestamp"]),
        "reason": None,
        "targetId": "",
        "realizedR": None,
        "extra": {},
    }
    canonical["source_instance"] = _source_instance(
        canonical["source"], canonical["exchange"], canonical["ticker"], canonical["timeframe"]
    )
    canonical["dedupe_key"] = _headsup_dedupe(canonical)
    return canonical, "", ""


def _targets(raw: Any, side: str, entry: float) -> tuple[list[dict[str, Any]], str]:
    if raw is None:
        return [], ""
    if not isinstance(raw, list):
        return [], "targets must be an array"
    result = []
    for item in raw:
        if not isinstance(item, dict):
            return [], "target must be an object"
        name = str(item.get("id") or "")
        if name not in TARGET_ORDER:
            return [], f"Unsupported target {name!r}"
        if not _finite(item.get("price")):
            return [], f"{name} price is not finite"
        weight = item.get("allocation", item.get("weight", 0))
        if not _finite(weight) or float(weight) < 0:
            return [], f"{name} allocation must be a positive weight"
        price = float(item["price"])
        if side == "long" and price <= entry:
            return [], f"{name} is not beyond entry"
        if side == "short" and price >= entry:
            return [], f"{name} is not beyond entry"
        chart_r = float(item["r"]) if _finite(item.get("r")) else None
        enabled = bool(item.get("enabled", True)) and float(weight) > 0
        result.append({
            "id": name,
            "price": price,
            "allocation": float(weight),
            "enabled": enabled,
            "chart_r": chart_r,
            "r": chart_r,
        })
    return result, ""


def _planned_rr(entry: float, stop: float, target_price: float) -> float | None:
    risk = abs(entry - stop)
    if risk <= 0:
        return None
    return abs(target_price - entry) / risk


def _market(event: dict[str, Any]) -> str:
    return event["ticker"] or event["root"]


def _target_phrase(targets: list[dict[str, Any]]) -> str:
    parts = []
    for item in targets:
        if not item.get("enabled", True):
            continue
        bit = f"{item['id']} {_num(item['price'])}"
        if item.get("chart_r") is not None:
            bit += f" ({_num(item['chart_r'])} reference R)"
        parts.append(bit)
    return ", ".join(parts)


def _alloc_phrase(allocations: list[dict[str, Any]]) -> str:
    parts = []
    for item in allocations:
        if int(item.get("qty") or 0) <= 0:
            continue
        qty = int(item["qty"])
        noun = "contract" if qty == 1 else "contracts"
        bit = f"{item['id']} {qty} {noun} at {_num(item['price'])}"
        if item.get("chart_r") is not None:
            bit += f" ({_num(item['chart_r'])} reference R)"
        parts.append(bit)
    return "; ".join(parts)


def _grade_phrase(grade: str | None, side: str) -> str:
    if grade:
        return f"{grade} {side}"
    return side


class PaperDesk:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(self.path)
        self.db.row_factory = sqlite3.Row
        self.db.isolation_level = None
        self.db.execute("PRAGMA journal_mode=WAL")
        self._migrate()
        os.chmod(self.path, 0o600)

    def close(self) -> None:
        self.db.close()

    def _migrate(self) -> None:
        self.db.executescript(
            """
            CREATE TABLE IF NOT EXISTS meta (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS settings (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                confirmed INTEGER NOT NULL,
                sizing_mode TEXT,
                fixed_qty INTEGER,
                starting_balance REAL,
                risk_per_trade REAL,
                max_trades INTEGER,
                max_concurrent INTEGER,
                max_daily_loss REAL,
                daily_target REAL,
                kill_switch INTEGER NOT NULL,
                allowed_roots TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS proposals (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS audits (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                received_at TEXT NOT NULL,
                status TEXT NOT NULL,
                detail TEXT NOT NULL,
                body TEXT
            );
            CREATE TABLE IF NOT EXISTS sources (
                id TEXT PRIMARY KEY,
                entries_enabled INTEGER NOT NULL
            );
            CREATE TABLE IF NOT EXISTS events (
                dedupe_key TEXT PRIMARY KEY,
                source_instance TEXT NOT NULL,
                event_name TEXT NOT NULL,
                event_id TEXT NOT NULL,
                setup_id TEXT NOT NULL,
                adapter TEXT NOT NULL,
                pine_version TEXT,
                raw TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS setups (
                id TEXT PRIMARY KEY,
                source_instance TEXT NOT NULL,
                setup_id TEXT NOT NULL,
                move_id TEXT,
                chart_state TEXT,
                chart_terminal INTEGER NOT NULL,
                paper_state TEXT NOT NULL,
                side TEXT,
                market TEXT,
                grade TEXT,
                scenario TEXT,
                origin TEXT,
                stop_preset TEXT,
                entry REAL,
                stop REAL,
                reconciliation INTEGER NOT NULL,
                payload TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS positions (
                id TEXT PRIMARY KEY,
                state TEXT NOT NULL,
                payload TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS ledger (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                setup_id TEXT NOT NULL,
                kind TEXT NOT NULL,
                qty INTEGER NOT NULL,
                price REAL,
                pnl REAL,
                detail TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS sessions (
                day TEXT PRIMARY KEY,
                trades INTEGER NOT NULL,
                pnl REAL NOT NULL
            );
            CREATE TABLE IF NOT EXISTS outbox (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                identity TEXT NOT NULL UNIQUE,
                body TEXT NOT NULL,
                status TEXT NOT NULL,
                attempts INTEGER NOT NULL,
                next_attempt TEXT
            );
            CREATE TABLE IF NOT EXISTS credentials (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );
            """
        )
        row = self.db.execute("SELECT id FROM settings WHERE id = 1").fetchone()
        if row is None:
            self.db.execute(
                """
                INSERT INTO settings (
                    id, confirmed, sizing_mode, fixed_qty, starting_balance, risk_per_trade,
                    max_trades, max_concurrent, max_daily_loss, daily_target, kill_switch, allowed_roots
                ) VALUES (1, 0, NULL, NULL, NULL, NULL, NULL, NULL, 0, 0, 0, ?)
                """,
                (json.dumps(list(SUPPORTED_ROOTS)),),
            )
        self.db.execute(
            "INSERT INTO meta (key, value) VALUES ('contract_status', ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (CONTRACT_STATUS,),
        )
        self.db.commit()

    def contract_status(self) -> str:
        row = self.db.execute("SELECT value FROM meta WHERE key = 'contract_status'").fetchone()
        return str(row["value"])

    def settings(self) -> dict[str, Any]:
        row = self.db.execute("SELECT * FROM settings WHERE id = 1").fetchone()
        data = dict(row)
        data["allowed_roots"] = json.loads(data["allowed_roots"])
        data["confirmed"] = bool(data["confirmed"])
        data["kill_switch"] = bool(data["kill_switch"])
        return data

    def store_proposals(self, proposals: dict[str, Any]) -> None:
        """Remember suggested settings. They do not become the active profile."""
        for key, value in proposals.items():
            self.db.execute(
                "INSERT INTO proposals (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (key, json.dumps(value)),
            )
        self.db.commit()

    def proposals(self) -> dict[str, Any]:
        return {row["key"]: json.loads(row["value"]) for row in self.db.execute("SELECT key, value FROM proposals")}

    def confirm_settings(
        self,
        *,
        sizing_mode: str,
        allowed_roots: list[str],
        fixed_qty: int | None = None,
        risk_per_trade: float | None = None,
        starting_balance: float | None = None,
        max_trades: int | None = None,
        max_concurrent: int | None = None,
        max_daily_loss: float = 0.0,
        daily_target: float = 0.0,
        kill_switch: bool = False,
    ) -> None:
        unknown = [root for root in allowed_roots if contract_spec(root) is None]
        if unknown:
            raise ValueError(f"Unsupported roots: {', '.join(unknown)}")
        if sizing_mode not in {"fixed", "risk"}:
            raise ValueError("sizing_mode must be fixed or risk")
        if sizing_mode == "fixed" and (fixed_qty is None or fixed_qty < 1):
            raise ValueError("Fixed quantity is not confirmed")
        if sizing_mode == "risk" and (risk_per_trade is None or risk_per_trade <= 0):
            raise ValueError("Dollar risk per trade is not confirmed")
        if starting_balance is not None and starting_balance <= 0:
            raise ValueError("Starting balance must be positive when it is set")
        self.db.execute(
            """
            UPDATE settings SET confirmed = 1, sizing_mode = ?, fixed_qty = ?, starting_balance = ?,
                risk_per_trade = ?, max_trades = ?, max_concurrent = ?, max_daily_loss = ?,
                daily_target = ?, kill_switch = ?, allowed_roots = ?
            WHERE id = 1
            """,
            (
                sizing_mode,
                fixed_qty,
                starting_balance,
                risk_per_trade,
                max_trades,
                max_concurrent,
                max_daily_loss,
                daily_target,
                int(kill_switch),
                json.dumps(allowed_roots),
            ),
        )
        self.db.commit()

    def disable_entries(self, source_instance: str) -> None:
        """Keep exits on this source. Refuse new paper entries. A Pine release does not call this."""
        self.db.execute(
            "INSERT INTO sources (id, entries_enabled) VALUES (?, 0) "
            "ON CONFLICT(id) DO UPDATE SET entries_enabled = 0",
            (source_instance,),
        )
        self.db.commit()

    def health(self) -> dict[str, Any]:
        current = self.settings()
        pending = self.db.execute(
            "SELECT COUNT(*) AS n FROM outbox WHERE status != 'delivered'"
        ).fetchone()["n"]
        flagged = [
            row["setup_id"]
            for row in self.db.execute("SELECT setup_id FROM setups WHERE reconciliation = 1 ORDER BY setup_id")
        ]
        last = self.db.execute("SELECT received_at, status FROM audits ORDER BY id DESC LIMIT 1").fetchone()
        ready = bool(current["confirmed"] and current["sizing_mode"] in {"fixed", "risk"})
        return {
            "contract": CONTRACT_STATUS,
            "adapterGate": "schemaVersion",
            "pineFileRequired": False,
            "paperReady": ready,
            "sizingMode": current["sizing_mode"],
            "startingBalanceSet": current["starting_balance"] is not None,
            "pendingNotifications": int(pending),
            "reconciliation": flagged,
            "lastAudit": None if last is None else {"at": last["received_at"], "status": last["status"]},
        }

    def apply_dashboard_settings(self, body: dict[str, Any]) -> None:
        """Persist owner settings in SQLite. Sizing stays unconfirmed until sizingMode is sent."""
        current = self.settings()
        allowed = list(current["allowed_roots"])
        if "watchMarkets" in body:
            raw = body["watchMarkets"]
            if isinstance(raw, str):
                names = [part.strip().upper() for part in raw.replace(";", ",").split(",") if part.strip()]
            else:
                names = [str(part).strip().upper() for part in raw]
            chosen = [name for name in SUPPORTED_ROOTS if name in names]
            if chosen:
                allowed = chosen
        max_trades = current["max_trades"]
        if "maxTrades" in body and body["maxTrades"] not in (None, ""):
            max_trades = max(1, int(body["maxTrades"]))
        max_daily_loss = current["max_daily_loss"] if current["max_daily_loss"] is not None else 0.0
        if "maxDailyLoss" in body and body["maxDailyLoss"] not in (None, ""):
            max_daily_loss = max(0.0, float(body["maxDailyLoss"]))
        daily_target = current["daily_target"] if current["daily_target"] is not None else 0.0
        if "dailyTarget" in body and body["dailyTarget"] not in (None, ""):
            daily_target = max(0.0, float(body["dailyTarget"]))
        risk = current["risk_per_trade"]
        if "riskPerTrade" in body and body["riskPerTrade"] not in (None, ""):
            risk = max(0.0, float(body["riskPerTrade"]))
        fixed_qty = current["fixed_qty"]
        if "fixedQty" in body and body["fixedQty"] not in (None, ""):
            fixed_qty = max(0, int(body["fixedQty"]))
        kill = current["kill_switch"]
        if "killed" in body:
            kill = bool(body["killed"])
        mode = body.get("sizingMode")
        confirmed = 1 if current["confirmed"] else 0
        sizing_mode = current["sizing_mode"]
        if mode in {"fixed", "risk"}:
            if mode == "fixed" and (not fixed_qty or int(fixed_qty) < 1):
                raise ValueError("Fixed quantity is not confirmed")
            if mode == "risk" and (risk is None or float(risk) <= 0):
                raise ValueError("Dollar risk per trade is not confirmed")
            sizing_mode = mode
            confirmed = 1
        if "pointOverride" in body and body["pointOverride"] not in (None, ""):
            self.db.execute(
                "INSERT INTO meta (key, value) VALUES ('point_override', ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (str(max(0.0, float(body["pointOverride"]))),),
            )
        if "forwardUrl" in body:
            self._credential("forward_url", str(body.get("forwardUrl") or "").strip())
        if body.get("forwardToken"):
            self._credential("forward_token", str(body["forwardToken"]).strip())
        self.db.execute(
            """
            UPDATE settings SET confirmed = ?, sizing_mode = ?, fixed_qty = ?, risk_per_trade = ?,
                max_trades = ?, max_daily_loss = ?, daily_target = ?, kill_switch = ?, allowed_roots = ?
            WHERE id = 1
            """,
            (
                confirmed,
                sizing_mode,
                fixed_qty,
                risk,
                max_trades,
                max_daily_loss,
                daily_target,
                int(kill),
                json.dumps(allowed),
            ),
        )
        self.db.commit()

    def import_legacy_snapshot(self, snapshot: dict[str, Any]) -> dict[str, Any]:
        """Keep credentials and unprocessed inbox events. Do not activate legacy limits or rebook a position."""
        url = str(snapshot.get("forwardUrl") or "").strip()
        token = str(snapshot.get("forwardToken") or "").strip()
        if url:
            self._credential("forward_url", url)
        if token:
            self._credential("forward_token", token)
        redacted = dict(snapshot)
        if redacted.get("forwardToken"):
            redacted["forwardToken"] = "[redacted]"
        if redacted.get("forwardUrl"):
            redacted["forwardUrl"] = "[redacted]"
        self._audit("legacy-import", "Memory snapshot stored. Sizing was not confirmed.", json.dumps(redacted)[:4000])
        self.db.commit()
        ingested = 0
        for item in snapshot.get("inbox") or []:
            payload = item.get("payload") if isinstance(item, dict) else None
            if isinstance(payload, dict):
                self.ingest(json.dumps(payload))
                ingested += 1
        noted = 0
        if snapshot.get("position"):
            self._audit("reconciliation", "Legacy open position was not rebooked", json.dumps({"hasPosition": True}))
            self.db.commit()
            noted = 1
        return {
            "ingested": ingested,
            "openPositionsNoted": noted,
            "sizingConfirmed": self.settings()["confirmed"],
            "startingBalance": self.settings()["starting_balance"],
        }

    def dashboard(self, now: datetime | None = None) -> dict[str, Any]:
        """The page and the five-minute check read this. It comes from SQLite only."""
        current = self.settings()
        when = now or datetime.now(CHICAGO)
        day = session_date(when).isoformat()
        session = self.db.execute("SELECT trades, pnl FROM sessions WHERE day = ?", (day,)).fetchone()
        trades_today = int(session["trades"]) if session else 0
        daily_pnl = float(session["pnl"]) if session else 0.0
        total_pnl = float(self.db.execute("SELECT COALESCE(SUM(pnl), 0) AS n FROM ledger").fetchone()["n"])
        balance = current["starting_balance"]
        equity = (float(balance) + total_pnl) if balance is not None else total_pnl
        days = self.db.execute("SELECT COUNT(*) AS n FROM sessions WHERE trades > 0").fetchone()["n"]
        opens = [row for row in self.positions() if row["state"] == "OPEN"]
        position = self._position_view(opens[0]) if opens else None
        plan = self._armed_plan()
        watch_note = "Waiting for a TradingView plan."
        if plan:
            watch_note = (
                f"TradingView {str(plan.get('side') or '').upper()} {plan.get('ticker') or plan.get('root') or ''} "
                f"@ {_num(plan.get('entry'))} stop {_num(plan.get('stop'))}"
            ).strip()
        override = self.db.execute("SELECT value FROM meta WHERE key = 'point_override'").fetchone()
        pending = []
        for row in self.db.execute(
            "SELECT identity, body, status FROM outbox WHERE status != 'delivered' ORDER BY id"
        ):
            body = json.loads(row["body"])
            pending.append({"identity": row["identity"], "text": body.get("text", ""), "status": row["status"]})
        return {
            "mode": "paper",
            "account": "Paper",
            "listening": True,
            "delivery": "outbox",
            "equity": round(equity, 2),
            "dailyPnl": round(daily_pnl, 2),
            "profit": round(total_pnl, 2),
            "passTarget": None,
            "floor": None,
            "floorLocked": False,
            "failed": False,
            "passed": False,
            "bestDay": 0.0,
            "consistency": None,
            "daysTraded": int(days),
            "tradesToday": trades_today,
            "maxTrades": current["max_trades"],
            "maxDailyLoss": current["max_daily_loss"],
            "dailyTarget": current["daily_target"],
            "pointOverride": 0.0 if override is None else float(override["value"]),
            "riskPerTrade": current["risk_per_trade"],
            "sizingMode": current["sizing_mode"],
            "paperReady": bool(current["confirmed"] and current["sizing_mode"] in {"fixed", "risk"}),
            "forwardUrlSet": self._credential_set("forward_url"),
            "samKeySet": self._credential_set("forward_token"),
            "inbox": pending,
            "planSettingsSource": "TradingView prices and R levels; paper desk sizing settings",
            "contractMonth": "Z2026",
            "contracts": [f"{root}Z2026" for root in current["allowed_roots"]],
            "watchMarkets": list(current["allowed_roots"]),
            "killed": current["kill_switch"],
            "watch": {
                "symbol": plan.get("ticker") if plan else " ".join(f"{root}Z2026" for root in current["allowed_roots"]),
                "price": None if plan is None else plan.get("entry"),
                "grade": None if plan is None else plan.get("grade"),
                "note": watch_note,
            },
            "position": position,
            "positions": [self._position_view(row) for row in opens],
            "plan": plan,
            "fills": self._fills(),
            "activity": self._activity(),
            "contract": CONTRACT_STATUS,
            "pineFileRequired": False,
            "reconciliation": self.health()["reconciliation"],
        }

    def _credential(self, key: str, value: str) -> None:
        self.db.execute(
            "INSERT INTO credentials (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, value),
        )

    def _credential_set(self, key: str) -> bool:
        row = self.db.execute("SELECT value FROM credentials WHERE key = ?", (key,)).fetchone()
        return bool(row and str(row["value"]).strip())

    def _position_view(self, row: dict[str, Any]) -> dict[str, Any]:
        payload = row["payload"]
        remaining = int(payload.get("remaining") or 0)
        distance = abs(float(payload.get("fill") or 0) - float(payload.get("stop") or 0))
        open_risk = distance * remaining * float(payload.get("point_value") or 0)
        targets = payload.get("allocations") or []
        return {
            "setupId": row["setup_id"],
            "side": payload.get("side"),
            "qty": payload.get("qty"),
            "remaining": remaining,
            "ticker": payload.get("ticker"),
            "root": payload.get("root"),
            "entry": payload.get("fill"),
            "stop": payload.get("stop"),
            "target": targets[0]["price"] if targets else None,
            "targets": [{"id": item["id"], "price": item["price"], "qty": item.get("qty", 0)} for item in targets],
            "grade": None,
            "openRisk": round(open_risk, 2),
            "state": row["state"],
        }

    def _armed_plan(self) -> dict[str, Any] | None:
        row = self.db.execute(
            """
            SELECT * FROM setups
            WHERE chart_terminal = 0 AND paper_state = 'NONE'
            ORDER BY rowid DESC LIMIT 1
            """
        ).fetchone()
        if row is None:
            return None
        payload = json.loads(row["payload"])
        return {
            "status": row["chart_state"] or "ARMED",
            "setupId": row["setup_id"],
            "side": row["side"],
            "root": payload.get("root"),
            "ticker": row["market"],
            "entry": row["entry"],
            "stop": row["stop"],
            "grade": row["grade"],
            "targets": payload.get("targets") or [],
        }

    def _fills(self) -> list[dict[str, Any]]:
        rows = []
        query = """
            SELECT l.qty, l.price, l.pnl, l.detail, s.market, s.side, s.entry
            FROM ledger l
            LEFT JOIN setups s ON s.id = l.setup_id
            WHERE l.kind IN ('exit', 'partial')
            ORDER BY l.id DESC LIMIT 30
        """
        for row in self.db.execute(query):
            rows.append({
                "ticker": row["market"],
                "side": row["side"],
                "qty": row["qty"],
                "entry": row["entry"],
                "exit": row["price"],
                "pnl": row["pnl"],
                "reason": row["detail"],
            })
        return rows

    def _activity(self) -> list[dict[str, Any]]:
        rows = []
        for row in self.db.execute(
            "SELECT received_at, status, detail FROM audits ORDER BY id DESC LIMIT 40"
        ):
            stamp = str(row["received_at"])
            clock = stamp[11:19] if len(stamp) >= 19 else stamp
            rows.append({"at": clock, "kind": str(row["status"]).upper(), "text": row["detail"]})
        return rows

    def ingest(self, raw: str | bytes, now: datetime | None = None) -> dict[str, Any]:
        """Adapt and apply one webhook body. Bookkeeping happens here, not on the chat poll."""
        text = raw.decode() if isinstance(raw, bytes) else raw
        when = now or datetime.now(CHICAGO)
        try:
            parsed = _loads(text)
        except json.JSONDecodeError as exc:
            self.db.execute("BEGIN")
            self._audit("invalid-json", str(exc), text, when)
            self.db.commit()
            return {"ok": False, "success": False, "booked": 0, "notifications": [], "audited": 1, "detail": "invalid json"}
        if not isinstance(parsed, (dict, list)):
            self.db.execute("BEGIN")
            self._audit("invalid-json", "Body must be an object or array", text, when)
            self.db.commit()
            return {"ok": False, "success": False, "booked": 0, "notifications": [], "audited": 1, "detail": "invalid json"}
        items = parsed if isinstance(parsed, list) else [parsed]
        notes: dict[str, dict[str, list[str]]] = {}
        booked = 0
        audited = 0
        try:
            self.db.execute("BEGIN")
            ambiguous: set[tuple[str, str]] = set()
            usable: list[dict[str, Any]] = []
            for index, item in enumerate(items):
                if not isinstance(item, dict):
                    self._audit("invalid-item", f"Item {index} is not an event object", json.dumps(item), when)
                    audited += 1
                    continue
                canonical, status, detail = _adapt(item)
                if canonical is None:
                    self._audit(status, f"Item {index}: {detail}", json.dumps(_strip(item), sort_keys=True), when)
                    audited += 1
                    setup_id = item.get("setupId")
                    if isinstance(setup_id, str) and setup_id:
                        source = item.get("source") if isinstance(item.get("source"), str) else ""
                        exchange = item.get("exchange") if isinstance(item.get("exchange"), str) else ""
                        ticker = item.get("ticker") if isinstance(item.get("ticker"), str) else ""
                        timeframe = item.get("timeframe") if isinstance(item.get("timeframe"), str) else ""
                        instance = _source_instance(source, exchange, ticker, timeframe) if source and exchange and ticker and timeframe else ""
                        ambiguous.add((instance, setup_id))
                        self._flag_setup(instance, setup_id, detail, when)
                    continue
                usable.append(canonical)
            for canonical in usable:
                key = (canonical["source_instance"], canonical["setupId"])
                bare = ("", canonical["setupId"])
                if key in ambiguous or bare in ambiguous:
                    self._audit(
                        "quarantine",
                        f"Setup {canonical['setupId']} is ambiguous after an invalid sibling",
                        canonical["eventId"],
                        when,
                    )
                    audited += 1
                    self._flag_setup(canonical["source_instance"], canonical["setupId"], "invalid sibling in the same batch", when)
                    continue
                if self.db.execute("SELECT 1 FROM events WHERE dedupe_key = ?", (canonical["dedupe_key"],)).fetchone():
                    continue
                booked += self._apply(canonical, text, when, notes)
            notifications = []
            for bucket in notes.values():
                identity = "notice:" + json.dumps(bucket["keys"], separators=(",", ":"))
                text_out = " ".join(bucket["texts"])
                self.db.execute(
                    "INSERT INTO outbox (identity, body, status, attempts, next_attempt) VALUES (?, ?, 'pending', 0, NULL)",
                    (identity, json.dumps({"text": text_out})),
                )
                notifications.append(text_out)
            self.db.commit()
        except Exception:
            self.db.rollback()
            raise
        return {
            "ok": True,
            "success": True,
            "booked": booked,
            "notifications": notifications,
            "audited": audited,
            "detail": "ok" if audited == 0 else "audited",
        }

    def _apply(self, event: dict[str, Any], raw: str, when: datetime, notes: dict[str, dict[str, list[str]]]) -> int:
        self._ensure_source(event["source_instance"])
        self.db.execute(
            """
            INSERT INTO events (
                dedupe_key, source_instance, event_name, event_id, setup_id, adapter, pine_version, raw
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                event["dedupe_key"],
                event["source_instance"],
                event["event"],
                event["eventId"],
                event["setupId"],
                event["adapter"],
                event["pineVersion"],
                raw,
            ),
        )
        name = event["event"]
        if name == "plan":
            self._on_plan(event, when, notes)
            return 0
        if name == "entry":
            return self._on_entry(event, when, notes)
        if name == "exit":
            return self._on_exit(event, when, notes)
        if name == "plan_cancel":
            self._on_cancel(event, notes)
            return 0
        if name in HEADS_UP:
            self._on_headsup(event, notes)
            return 0
        self._on_stop(event, notes)
        return 0

    def _on_headsup(self, event: dict[str, Any], notes: dict[str, dict[str, list[str]]]) -> None:
        self._note(notes, "headsup:" + event["dedupe_key"], _headsup_sentence(event), event["dedupe_key"])

    def _setup_id(self, event: dict[str, Any]) -> str:
        return event["source_instance"] + "\x1f" + event["setupId"]

    def _load_setup(self, event: dict[str, Any]) -> sqlite3.Row | None:
        return self.db.execute("SELECT * FROM setups WHERE id = ?", (self._setup_id(event),)).fetchone()

    def _save_setup(self, event: dict[str, Any], **fields: Any) -> None:
        current = self._load_setup(event)
        payload = json.dumps(event, sort_keys=True)
        if current is None:
            self.db.execute(
                """
                INSERT INTO setups (
                    id, source_instance, setup_id, move_id, chart_state, chart_terminal, paper_state,
                    side, market, grade, scenario, origin, stop_preset, entry, stop, reconciliation, payload
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?)
                """,
                (
                    self._setup_id(event),
                    event["source_instance"],
                    event["setupId"],
                    fields.get("move_id", event["move_id"]),
                    fields.get("chart_state", event["state"]),
                    int(fields.get("chart_terminal", 0)),
                    fields.get("paper_state", "NONE"),
                    fields.get("side", event["side"]),
                    fields.get("market", _market(event)),
                    fields.get("grade", event["grade"]),
                    fields.get("scenario", event["scenario"]),
                    fields.get("origin", event["origin"]),
                    fields.get("stop_preset", event["stop_preset"]),
                    fields.get("entry", event["entry"]),
                    fields.get("stop", event["stop"]),
                    payload,
                ),
            )
            return
        merged = {
            "move_id": current["move_id"],
            "chart_state": current["chart_state"],
            "chart_terminal": current["chart_terminal"],
            "paper_state": current["paper_state"],
            "side": current["side"],
            "market": current["market"],
            "grade": current["grade"],
            "scenario": current["scenario"],
            "origin": current["origin"],
            "stop_preset": current["stop_preset"],
            "entry": current["entry"],
            "stop": current["stop"],
            "reconciliation": current["reconciliation"],
        }
        for key, value in fields.items():
            if key in merged and value is not None:
                merged[key] = value
        self.db.execute(
            """
            UPDATE setups SET move_id = ?, chart_state = ?, chart_terminal = ?, paper_state = ?,
                side = ?, market = ?, grade = ?, scenario = ?, origin = ?, stop_preset = ?,
                entry = ?, stop = ?, reconciliation = ?, payload = ?
            WHERE id = ?
            """,
            (
                merged["move_id"], merged["chart_state"], int(merged["chart_terminal"]), merged["paper_state"],
                merged["side"], merged["market"], merged["grade"], merged["scenario"], merged["origin"],
                merged["stop_preset"], merged["entry"], merged["stop"], int(merged["reconciliation"]),
                payload, self._setup_id(event),
            ),
        )

    def _note(self, notes: dict[str, dict[str, list[str]]], key: str, text: str, dedupe: str) -> None:
        bucket = notes.setdefault(key, {"texts": [], "keys": []})
        bucket["texts"].append(text)
        bucket["keys"].append(dedupe)

    def _on_plan(self, event: dict[str, Any], when: datetime, notes: dict[str, dict[str, list[str]]]) -> None:
        del when
        current = self._load_setup(event)
        if current is not None and (current["chart_terminal"] or current["paper_state"] in {"OPEN", "REJECTED", "CLOSED"}):
            self._save_setup(
                event,
                grade=event["grade"] or current["grade"],
                scenario=event["scenario"] or current["scenario"],
                origin=event["origin"] or current["origin"],
                stop_preset=event["stop_preset"] or current["stop_preset"],
            )
            return
        self._save_setup(event, chart_state=event["state"] or "ARMED", chart_terminal=0, paper_state="NONE")
        market = _market(event)
        siblings = []
        if event["move_id"] is not None:
            siblings = list(self.db.execute(
                """
                SELECT * FROM setups
                WHERE source_instance = ? AND move_id = ? AND chart_terminal = 0 AND paper_state = 'NONE'
                """,
                (event["source_instance"], event["move_id"]),
            ))
        if len(siblings) >= 2:
            for row in siblings:
                notes.pop(row["id"], None)
            self._note(notes, f"move:{event['source_instance']}:{event['move_id']}", self._two_plans(siblings), event["dedupe_key"])
            return
        targets = _target_phrase(event["targets"])
        sentence = (
            f"The desk is watching {market}. "
            f"A {_grade_phrase(event['grade'], event['side'])} setup is armed: "
            f"entry {_num(event['entry'])}, stop {_num(event['stop'])}"
        )
        if targets:
            sentence += f", {targets}"
        sentence += ". Waiting for the retest."
        self._note(notes, self._setup_id(event), sentence, event["dedupe_key"])

    def _two_plans(self, rows: list[sqlite3.Row]) -> str:
        def describe(row: sqlite3.Row) -> dict[str, Any]:
            payload = json.loads(row["payload"])
            return {
                "origin": (row["origin"] or "").lower(),
                "scenario": (row["scenario"] or "").upper(),
                "entry": row["entry"],
                "stop": row["stop"],
                "targets": _target_phrase(payload.get("targets") or []),
                "market": row["market"],
            }

        described = [describe(row) for row in rows]
        market = described[0]["market"]
        shelf = next((item for item in described if item["origin"] == "shelf" or item["scenario"] == "B"), None)
        gap = next((item for item in described if item["origin"] == "gap" or item["scenario"] == "A"), None)
        if shelf and gap and shelf is not gap:
            return (
                f"The {market} move has a shelf plan at {_num(shelf['entry'])} and a gap plan at {_num(gap['entry'])}. "
                f"Shelf stop {_num(shelf['stop'])}, {shelf['targets']}. "
                f"Gap stop {_num(gap['stop'])}, {gap['targets']}. "
                "Pine cancels the other currently armed plan when one triggers."
            )
        first, second = described[0], described[1]
        return (
            f"The {market} move has two armed plans, at {_num(first['entry'])} and {_num(second['entry'])}. "
            "Each keeps its own stop and targets. Pine cancels the other currently armed plan when one triggers."
        )

    def _on_entry(self, event: dict[str, Any], when: datetime, notes: dict[str, dict[str, list[str]]]) -> int:
        current = self._load_setup(event)
        market = _market(event)
        if current is not None and int(current["chart_terminal"]):
            self._audit("late-entry", "Entry arrived after a terminal chart event", event["eventId"], when)
            self._note(
                notes,
                self._setup_id(event),
                f"StaxBot triggered the {market} {event['side']} at {_num(event['price'])}, "
                "but the paper desk did not enter: the setup is already closed.",
                event["dedupe_key"],
            )
            return 0
        if current is not None and current["paper_state"] in {"OPEN", "REJECTED", "CLOSED"}:
            return 0
        reason = self._entry_block(event, when)
        self._save_setup(
            event,
            chart_state=event["state"] or "TRIGGERED",
            chart_terminal=0,
            paper_state="REJECTED" if reason else "OPEN",
        )
        if reason:
            self._note(
                notes,
                self._setup_id(event),
                f"StaxBot triggered the {market} {event['side']} at {_num(event['price'])}, "
                f"but the paper desk did not enter: {reason}.",
                event["dedupe_key"],
            )
            return 0
        qty = self._quantity(event)
        if isinstance(qty, str):
            self._save_setup(event, paper_state="REJECTED")
            self._note(
                notes,
                self._setup_id(event),
                f"StaxBot triggered the {market} {event['side']} at {_num(event['price'])}, "
                f"but the paper desk did not enter: {qty}.",
                event["dedupe_key"],
            )
            return 0
        allocations = allocate_contracts(qty, event["targets"])
        for item in allocations:
            item["chart_r"] = item.get("chart_r", item.get("r"))
            item["planned_rr"] = _planned_rr(event["entry"], event["stop"], float(item["price"]))
            item["filled"] = 0
        point = contract_spec(event["root"])
        assert point is not None
        risk = abs(event["entry"] - event["stop"]) * qty * point
        position = {
            "qty": qty,
            "remaining": qty,
            "entry": event["entry"],
            "fill": event["price"],
            "initial_stop": event["stop"],
            "stop": event["stop"],
            "side": event["side"],
            "root": event["root"],
            "ticker": event["ticker"],
            "point_value": point,
            "initial_risk": risk,
            "realized_pnl": 0.0,
            "allocations": allocations,
            "final_exit": False,
        }
        self._write_position(event, "OPEN", position)
        self._ledger(event, "entry", qty, event["price"], 0.0, "entry")
        self._add_session(when, trades=1, pnl=0.0)
        allocs = _alloc_phrase(allocations)
        sentence = (
            f"StaxBot triggered the {market} {event['side']} at {_num(event['price'])}. "
            f"The paper desk opened {qty} {'contract' if qty == 1 else 'contracts'} at the signal price. "
            f"Stop {_num(event['stop'])}"
        )
        if allocs:
            sentence += f"; {allocs}"
        sentence += "."
        self._note(notes, self._setup_id(event), sentence, event["dedupe_key"])
        return 1

    def _entry_block(self, event: dict[str, Any], when: datetime) -> str | None:
        current = self.settings()
        if not current["confirmed"] or current["sizing_mode"] not in {"fixed", "risk"}:
            return "Paper sizing is not configured"
        if current["sizing_mode"] == "fixed" and not current["fixed_qty"]:
            return "Fixed quantity is not confirmed"
        if current["sizing_mode"] == "risk" and not current["risk_per_trade"]:
            return "Dollar risk per trade is not confirmed"
        source = self.db.execute(
            "SELECT entries_enabled FROM sources WHERE id = ?", (event["source_instance"],)
        ).fetchone()
        if source is not None and not source["entries_enabled"]:
            return "New entries are disabled for this alert generation"
        if current["kill_switch"]:
            return "Kill switch is on"
        if contract_spec(event["root"]) is None:
            return f"No contract spec for {event['root']}"
        if event["root"] not in current["allowed_roots"]:
            return f"{event['root']} is not in the allowed markets"
        day = session_date(when).isoformat()
        session = self.db.execute("SELECT trades, pnl FROM sessions WHERE day = ?", (day,)).fetchone()
        trades = int(session["trades"]) if session else 0
        pnl = float(session["pnl"]) if session else 0.0
        if current["max_trades"] is not None and trades >= int(current["max_trades"]):
            return f"Max trades reached ({trades}/{current['max_trades']})"
        if current["max_daily_loss"] and pnl <= -float(current["max_daily_loss"]):
            return "Daily loss limit is hit"
        if current["daily_target"] and pnl >= float(current["daily_target"]):
            return "Daily profit limit is hit"
        open_positions = self.db.execute("SELECT COUNT(*) AS n FROM positions WHERE state = 'OPEN'").fetchone()["n"]
        if current["max_concurrent"] is not None and int(open_positions) >= int(current["max_concurrent"]):
            return f"Max concurrent paper positions reached ({open_positions}/{current['max_concurrent']})"
        return None

    def _quantity(self, event: dict[str, Any]) -> int | str:
        current = self.settings()
        if current["sizing_mode"] == "fixed":
            return int(current["fixed_qty"])
        point = contract_spec(event["root"])
        if point is None:
            return f"No contract spec for {event['root']}"
        distance = abs(event["entry"] - event["stop"])
        per = distance * point
        if per <= 0:
            return "Dollar risk does not cover one contract"
        qty = int(float(current["risk_per_trade"]) / per)
        if qty < 1:
            return "Dollar risk does not cover one contract"
        return qty

    def _on_exit(self, event: dict[str, Any], when: datetime, notes: dict[str, dict[str, list[str]]]) -> int:
        setup = self._load_setup(event)
        position = self._position(event)
        market = _market(event)
        if position is None:
            if setup is None:
                self._audit("unknown-exit", "Exit did not match a setup", event["eventId"], when)
                self._save_setup(event, chart_state="UNKNOWN", chart_terminal=0, paper_state="NONE")
                self._flag_setup(event["source_instance"], event["setupId"], "exit without a setup", when)
                return 0
            if event["reason"] == "TP":
                self._chart_only_target(event, notes, market)
                return 0
            self._chart_close(event, notes, market, had_position=False)
            self._save_setup(event, chart_state=event["state"] or "CLOSED", chart_terminal=1)
            return 0
        if position["state"] != "OPEN":
            self._audit("reconciliation", "Exit arrived after the paper position was already closed", event["eventId"], when)
            self._flag_setup(event["source_instance"], event["setupId"], "exit after final close", when)
            if event["reason"] == "TP":
                self._note(
                    notes,
                    self._setup_id(event),
                    f"{event['targetId']} reached at {_num(event['price'])} on {market}.",
                    event["dedupe_key"],
                )
            return 0
        payload = json.loads(position["payload"])
        if event["reason"] == "TP":
            return self._take_target(event, when, notes, payload, market)
        return self._close_remainder(event, when, notes, payload, market)

    def _chart_only_target(self, event: dict[str, Any], notes: dict[str, dict[str, list[str]]], market: str) -> None:
        self._save_setup(event, chart_state=event["state"] or "TRIGGERED", chart_terminal=0)
        label = event["targetId"] or "Target"
        self._note(
            notes,
            self._setup_id(event),
            f"{label} reached at {_num(event['price'])} on {market}.",
            event["dedupe_key"],
        )

    def _chart_close(self, event: dict[str, Any], notes: dict[str, dict[str, list[str]]], market: str, had_position: bool) -> None:
        del had_position
        reason = EXIT_TEXT.get(event["reason"] or "", event["reason"] or "exit")
        sentence = (
            f"The {market} {event['side']} closed at {_num(event['price'])}: {reason}. "
            f"Entry {_num(event['entry'])}."
        )
        if self._book_is_flat():
            sentence += " The book is flat."
        self._note(notes, self._setup_id(event), sentence, event["dedupe_key"])

    def _take_target(
        self,
        event: dict[str, Any],
        when: datetime,
        notes: dict[str, dict[str, list[str]]],
        payload: dict[str, Any],
        market: str,
    ) -> int:
        match = next((item for item in payload["allocations"] if item["id"] == event["targetId"]), None)
        label = event["targetId"]
        if match is None:
            self._audit("unknown-exit", f"No frozen allocation for {label}", event["eventId"], when)
            self._flag_setup(event["source_instance"], event["setupId"], f"unknown target {label}", when)
            return 0
        room = int(match.get("qty") or 0) - int(match.get("filled") or 0)
        if room <= 0:
            self._note(
                notes,
                self._setup_id(event),
                f"{label} reached at {_num(event['price'])} on {market}.",
                event["dedupe_key"],
            )
            return 0
        qty = min(int(payload["remaining"]), room)
        if qty <= 0:
            self._note(
                notes,
                self._setup_id(event),
                f"{label} reached at {_num(event['price'])} on {market}.",
                event["dedupe_key"],
            )
            return 0
        pnl = self._pnl(payload["side"], payload["fill"], event["price"], qty, payload["point_value"])
        match["filled"] = int(match["filled"]) + qty
        payload["remaining"] = int(payload["remaining"]) - qty
        payload["realized_pnl"] = float(payload["realized_pnl"]) + pnl
        closed = payload["remaining"] <= 0
        state = "CLOSED" if closed else "OPEN"
        self._write_position(event, state, payload)
        self._ledger(event, "exit" if closed else "partial", qty, event["price"], pnl, event["reason"] or "TP")
        self._add_session(when, trades=0, pnl=pnl)
        self._save_setup(
            event,
            chart_state="CLOSED" if closed and (event["state"] or "") == "CLOSED" else (event["state"] or "TRIGGERED"),
            chart_terminal=1 if (event["state"] or "") == "CLOSED" or closed else 0,
            paper_state=state,
            stop=payload["stop"],
        )
        ratio = paper_r(float(payload["realized_pnl"]), float(payload["initial_risk"]))
        ratio_text = "" if ratio is None else f" ({_num(ratio)} paper R)"
        if closed:
            original = int(payload["qty"])
            sentence = (
                f"The {market} {payload['side']} closed at {_num(event['price'])}: target. "
                f"Entry {_num(payload['entry'])}. "
                f"The paper desk closed {original} {'contract' if original == 1 else 'contracts'}, "
                f"P&L {_num(payload['realized_pnl'])}, paper R {_num(ratio) if ratio is not None else 'n/a'}."
            )
            if self._book_is_flat():
                sentence += " The book is flat."
        else:
            sentence = (
                f"{label} reached at {_num(event['price'])} on {market}. "
                f"The paper desk closed {qty}; {payload['remaining']} remain. "
                f"Stop {_num(payload['stop'])}. Paper result {_num(payload['realized_pnl'])}{ratio_text}."
            )
        self._note(notes, self._setup_id(event), sentence, event["dedupe_key"])
        return 1

    def _close_remainder(
        self,
        event: dict[str, Any],
        when: datetime,
        notes: dict[str, dict[str, list[str]]],
        payload: dict[str, Any],
        market: str,
    ) -> int:
        qty = int(payload["remaining"])
        pnl = 0.0
        if qty > 0:
            pnl = self._pnl(payload["side"], payload["fill"], event["price"], qty, payload["point_value"])
            self._ledger(event, "exit", qty, event["price"], pnl, event["reason"] or "exit")
            self._add_session(when, trades=0, pnl=pnl)
        payload["remaining"] = 0
        payload["realized_pnl"] = float(payload["realized_pnl"]) + pnl
        payload["final_exit"] = True
        self._write_position(event, "CLOSED", payload)
        self._save_setup(event, chart_state=event["state"] or "CLOSED", chart_terminal=1, paper_state="CLOSED", stop=payload["stop"])
        ratio = paper_r(float(payload["realized_pnl"]), float(payload["initial_risk"]))
        reason = EXIT_TEXT.get(event["reason"] or "", event["reason"] or "exit")
        original = int(payload["qty"])
        sentence = (
            f"The {market} {payload['side']} closed at {_num(event['price'])}: {reason}. "
            f"Entry {_num(payload['entry'])}. "
            f"The paper desk closed {original} {'contract' if original == 1 else 'contracts'}, "
            f"P&L {_num(payload['realized_pnl'])}, paper R {_num(ratio) if ratio is not None else 'n/a'}."
        )
        if pnl < 0 and event["reason"] in {"SL", "BE", "TRAILED"}:
            sentence += f" The {payload['side']} failed."
        if self._book_is_flat():
            sentence += " The book is flat."
        self._note(notes, self._setup_id(event), sentence, event["dedupe_key"])
        return 1 if qty else 0

    def _on_cancel(self, event: dict[str, Any], notes: dict[str, dict[str, list[str]]]) -> None:
        current = self._load_setup(event)
        if current is not None and current["paper_state"] == "OPEN":
            self._flag_setup(event["source_instance"], event["setupId"], "cancel arrived for an open paper position", datetime.now(CHICAGO))
            return
        self._save_setup(event, chart_state=event["state"] or "CANCELLED", chart_terminal=1, paper_state=current["paper_state"] if current else "NONE")
        if current is not None and int(current["chart_terminal"]):
            return
        plain = CANCEL_TEXT.get(event["reason"] or "", event["reason"] or "cancelled")
        self._note(
            notes,
            self._setup_id(event),
            f"The {_market(event)} {event['side']} setup at {_num(event['entry'])} is off: {plain}.",
            event["dedupe_key"],
        )

    def _on_stop(self, event: dict[str, Any], notes: dict[str, dict[str, list[str]]]) -> None:
        current = self._load_setup(event)
        old = None if current is None or current["stop"] is None else float(current["stop"])
        self._save_setup(event, chart_state=event["state"] or (current["chart_state"] if current else "LIVE"), stop=event["stop"])
        position = self._position(event)
        paper = False
        if position is not None and position["state"] == "OPEN":
            payload = json.loads(position["payload"])
            old = float(payload["stop"])
            if float(event["stop"]) != old:
                payload["stop"] = event["stop"]
                self._write_position(event, "OPEN", payload)
                paper = True
        if old is None or float(event["stop"]) == old:
            if old is None:
                self._note(
                    notes,
                    self._setup_id(event),
                    f"{_market(event)} {event['side']}: the stop moved to {_num(event['stop'])}.",
                    event["dedupe_key"],
                )
            return
        label = "the paper stop" if paper else "the stop"
        sentence = f"{_market(event)} {event['side']}: {label} moved from {_num(old)} to {_num(event['stop'])}."
        locked = (event.get("extra") or {}).get("locked_r")
        if _finite(locked):
            sentence += f" Locked R {_num(float(locked))}."
        self._note(
            notes,
            self._setup_id(event),
            sentence,
            event["dedupe_key"],
        )

    def _pnl(self, side: str, entry: float, price: float, qty: int, point: float) -> float:
        points = (price - entry) if side == "long" else (entry - price)
        return points * qty * point

    def _write_position(self, event: dict[str, Any], state: str, payload: dict[str, Any]) -> None:
        self.db.execute(
            "INSERT INTO positions (id, state, payload) VALUES (?, ?, ?) "
            "ON CONFLICT(id) DO UPDATE SET state = excluded.state, payload = excluded.payload",
            (self._setup_id(event), state, json.dumps(payload)),
        )

    def _position(self, event: dict[str, Any]) -> sqlite3.Row | None:
        return self.db.execute("SELECT * FROM positions WHERE id = ?", (self._setup_id(event),)).fetchone()

    def _ledger(self, event: dict[str, Any], kind: str, qty: int, price: float, pnl: float, detail: str) -> None:
        self.db.execute(
            "INSERT INTO ledger (setup_id, kind, qty, price, pnl, detail) VALUES (?, ?, ?, ?, ?, ?)",
            (self._setup_id(event), kind, qty, price, pnl, detail),
        )

    def _add_session(self, when: datetime, *, trades: int, pnl: float) -> None:
        day = session_date(when).isoformat()
        self.db.execute(
            """
            INSERT INTO sessions (day, trades, pnl) VALUES (?, ?, ?)
            ON CONFLICT(day) DO UPDATE SET trades = trades + excluded.trades, pnl = pnl + excluded.pnl
            """,
            (day, trades, pnl),
        )

    def _book_is_flat(self) -> bool:
        row = self.db.execute("SELECT COUNT(*) AS n FROM positions WHERE state = 'OPEN'").fetchone()
        return int(row["n"]) == 0

    def _ensure_source(self, source_instance: str) -> None:
        self.db.execute(
            "INSERT INTO sources (id, entries_enabled) VALUES (?, 1) ON CONFLICT(id) DO NOTHING",
            (source_instance,),
        )

    def _flag_setup(self, source_instance: str, setup_id: str, detail: str, when: datetime) -> None:
        del detail, when
        if not setup_id:
            return
        if source_instance:
            self.db.execute(
                "UPDATE setups SET reconciliation = 1 WHERE source_instance = ? AND setup_id = ?",
                (source_instance, setup_id),
            )
        else:
            self.db.execute("UPDATE setups SET reconciliation = 1 WHERE setup_id = ?", (setup_id,))

    def _audit(self, status: str, detail: str, body: str | None, when: datetime | None = None) -> None:
        stamp = (when or datetime.now(CHICAGO)).isoformat()
        self.db.execute(
            "INSERT INTO audits (received_at, status, detail, body) VALUES (?, ?, ?, ?)",
            (stamp, status, detail, body),
        )

    def audits(self) -> list[dict[str, Any]]:
        return [dict(row) for row in self.db.execute("SELECT * FROM audits ORDER BY id")]

    def positions(self) -> list[dict[str, Any]]:
        rows = []
        for row in self.db.execute("SELECT id, state, payload FROM positions"):
            rows.append({"setup_id": row["id"], "state": row["state"], "payload": json.loads(row["payload"])})
        return rows

    def outbox(self) -> list[dict[str, Any]]:
        rows = []
        for row in self.db.execute("SELECT * FROM outbox ORDER BY id"):
            body = json.loads(row["body"])
            rows.append(dict(row) | {"body": body, "text": body.get("text", "")})
        return rows

    def notifications(self) -> list[dict[str, Any]]:
        return [item for item in self.outbox() if item["status"] != "delivered"]

    def ledger_count(self) -> int:
        return int(self.db.execute("SELECT COUNT(*) AS n FROM ledger").fetchone()["n"])

    def confirm_delivery(self, identity: str) -> None:
        updated = self.db.execute(
            "UPDATE outbox SET status = 'delivered' WHERE identity = ? AND status != 'delivered'",
            (identity,),
        )
        self.db.commit()
        if updated.rowcount != 1:
            raise KeyError(identity)

    def mark_uncertain(self, identity: str) -> None:
        self.db.execute(
            "UPDATE outbox SET status = 'uncertain', attempts = attempts + 1 WHERE identity = ?",
            (identity,),
        )
        self.db.commit()

    def retry_pending(self, now: datetime | None = None) -> list[dict[str, Any]]:
        """Return notices still awaiting acceptance. Does not insert a fill."""
        moment = now or datetime.now(CHICAGO)
        rows = []
        pending = list(self.db.execute(
            "SELECT * FROM outbox WHERE status IN ('pending', 'uncertain') ORDER BY id"
        ))
        for row in pending:
            if row["next_attempt"]:
                due = datetime.fromisoformat(row["next_attempt"])
                if due > moment:
                    continue
            attempts = int(row["attempts"]) + 1
            delay = min(300, 2 ** attempts)
            nxt = (moment + timedelta(seconds=delay)).isoformat()
            self.db.execute(
                "UPDATE outbox SET attempts = ?, next_attempt = ? WHERE id = ?",
                (attempts, nxt, row["id"]),
            )
            body = json.loads(row["body"])
            rows.append(dict(row) | {"body": body, "text": body.get("text", ""), "attempts": attempts})
        self.db.commit()
        return rows
