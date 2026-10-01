"""Durable paper-desk infrastructure.

StaxBot 2.4.5 is the requested chart contract, and its Pine file is not in this
repository. This module does not guess that contract. It stores raw webhook
audits, owner settings, paper state, and the notification outbox. A webhook
does not book a fill or create a chat notice until that source is present.

Changelog: add a SQLite paper desk that keeps settings unconfirmed, blocks
unconfigured entries, and stays silent when nothing changes.
"""
from __future__ import annotations

import json
import os
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

CHICAGO = ZoneInfo("America/Chicago")
CONTRACT_VERSION = "2.4.5"
MISSING_SOURCE = "staxbot_2_4_5.pine is not in the repository. Latest frozen chart is staxbot_2_4_4.pine at tag v2.4.4."

# Exchange point values for the micro roots the desk can size. Not a session profile.
POINT_VALUES = {
    "MNQ": 2.0,
    "MES": 5.0,
    "MYM": 0.5,
    "M2K": 5.0,
    "MGC": 10.0,
}
SUPPORTED_ROOTS = tuple(POINT_VALUES)


def session_date(when: datetime) -> datetime.date:
    """Session rolls at 17:00 America/Chicago. DST follows the zone database."""
    local = when.astimezone(CHICAGO) if when.tzinfo else when.replace(tzinfo=CHICAGO)
    if (local.hour, local.minute) >= (17, 0):
        return local.date() + timedelta(days=1)
    return local.date()


def allocate_contracts(total_qty: int, targets: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Floor whole contracts from positive weights. Remainder goes to the earliest enabled target.

    Disabled targets stay in the result with qty 0. One enabled target receives the full quantity.
    A quantity smaller than the enabled-target count leaves the later targets at zero contracts.
    """
    ordered = []
    for index, item in enumerate(targets):
        enabled = bool(item.get("enabled", True))
        weight = float(item.get("allocation", item.get("weight", 0)) or 0)
        ordered.append({**item, "enabled": enabled, "allocation": weight, "order": index})
    enabled = [item for item in ordered if item["enabled"] and item["allocation"] > 0]
    quantities = {id(item): 0 for item in ordered}
    if total_qty > 0 and enabled:
        weight_sum = sum(item["allocation"] for item in enabled)
        exact = [total_qty * item["allocation"] / weight_sum for item in enabled]
        floors = [int(value) for value in exact]
        left = total_qty - sum(floors)
        for offset, item in enumerate(enabled):
            quantities[id(item)] = floors[offset]
        for item in enabled:
            if left <= 0:
                break
            quantities[id(item)] += 1
            left -= 1
    result = []
    for item in ordered:
        qty = quantities[id(item)]
        result.append({key: value for key, value in item.items() if key != "order"} | {"qty": qty})
    return result


class ContractBlocked(RuntimeError):
    """Raised when code asks the desk to interpret an unverified 2.4.5 payload."""


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
                starting_balance REAL,
                risk_per_trade REAL,
                max_trades INTEGER,
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
            CREATE TABLE IF NOT EXISTS seen_keys (
                key TEXT PRIMARY KEY
            );
            CREATE TABLE IF NOT EXISTS positions (
                setup_id TEXT PRIMARY KEY,
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
            CREATE TABLE IF NOT EXISTS outbox (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                identity TEXT NOT NULL UNIQUE,
                body TEXT NOT NULL,
                status TEXT NOT NULL,
                attempts INTEGER NOT NULL
            );
            """
        )
        row = self.db.execute("SELECT id FROM settings WHERE id = 1").fetchone()
        if row is None:
            self.db.execute(
                """
                INSERT INTO settings (
                    id, confirmed, starting_balance, risk_per_trade, max_trades,
                    max_daily_loss, daily_target, kill_switch, allowed_roots
                ) VALUES (1, 0, NULL, NULL, NULL, 0, 0, 0, ?)
                """,
                (json.dumps(list(SUPPORTED_ROOTS)),),
            )
        self.db.execute(
            "INSERT INTO meta (key, value) VALUES ('chart_version', ?) ON CONFLICT(key) DO NOTHING",
            (CONTRACT_VERSION,),
        )
        self.db.execute(
            "INSERT INTO meta (key, value) VALUES ('contract_status', ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            ("blocked: " + MISSING_SOURCE,),
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
        starting_balance: float,
        risk_per_trade: float,
        max_trades: int,
        allowed_roots: list[str],
        max_daily_loss: float = 0.0,
        daily_target: float = 0.0,
        kill_switch: bool = False,
    ) -> None:
        unknown = [root for root in allowed_roots if root not in POINT_VALUES]
        if unknown:
            raise ValueError(f"Unsupported roots: {', '.join(unknown)}")
        if starting_balance <= 0 or risk_per_trade <= 0 or max_trades < 1:
            raise ValueError("Starting balance, risk per trade, and max trades must be set")
        self.db.execute(
            """
            UPDATE settings SET confirmed = 1, starting_balance = ?, risk_per_trade = ?,
                max_trades = ?, max_daily_loss = ?, daily_target = ?, kill_switch = ?, allowed_roots = ?
            WHERE id = 1
            """,
            (
                starting_balance,
                risk_per_trade,
                max_trades,
                max_daily_loss,
                daily_target,
                int(kill_switch),
                json.dumps(allowed_roots),
            ),
        )
        self.db.commit()

    def missing_entry_settings(self) -> str | None:
        current = self.settings()
        if not current["confirmed"] or current["starting_balance"] is None or current["risk_per_trade"] is None:
            return "Paper entry needs a confirmed starting balance and risk per trade from the owner"
        if current["kill_switch"]:
            return "Kill switch is on"
        return None

    def guard_entry(self, *, root: str, trades_today: int, daily_pnl: float, open_positions: int) -> str | None:
        """Guards block a new paper entry. They do not block an exit."""
        missing = self.missing_entry_settings()
        if missing:
            return missing
        current = self.settings()
        if root not in current["allowed_roots"]:
            return f"{root} is not in the allowed markets"
        if current["max_trades"] is not None and trades_today >= int(current["max_trades"]):
            return f"Max trades reached ({trades_today}/{current['max_trades']})"
        if current["max_daily_loss"] and daily_pnl <= -float(current["max_daily_loss"]):
            return "Daily loss limit is hit"
        if current["daily_target"] and daily_pnl >= float(current["daily_target"]):
            return "Daily profit limit is hit"
        if open_positions:
            return "A paper position is already open"
        return None

    def ingest(self, raw: str | bytes) -> dict[str, Any]:
        """Validate JSON, then audit it. Do not book or notify from an unverified contract."""
        text = raw.decode() if isinstance(raw, bytes) else raw
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError as exc:
            self.db.execute("BEGIN")
            self._audit("invalid-json", str(exc), text)
            self.db.commit()
            return {"ok": False, "booked": 0, "notifications": [], "audited": 1, "detail": "invalid json"}
        items = parsed if isinstance(parsed, list) else [parsed]
        if not isinstance(parsed, (dict, list)):
            self.db.execute("BEGIN")
            self._audit("invalid-json", "Body must be an object or array", text)
            self.db.commit()
            return {"ok": False, "booked": 0, "notifications": [], "audited": 1, "detail": "invalid json"}
        valid: list[dict[str, Any]] = []
        try:
            self.db.execute("BEGIN")
            for index, item in enumerate(items):
                if not isinstance(item, dict) or not item.get("event"):
                    self._audit("invalid-item", f"Item {index} is not an event object", json.dumps(item))
                    continue
                valid.append(item)
            for item in valid:
                self._audit(
                    "unverified-contract",
                    MISSING_SOURCE,
                    json.dumps(item, sort_keys=True),
                )
            self.db.commit()
        except Exception:
            self.db.rollback()
            raise
        return {
            "ok": True,
            "booked": 0,
            "notifications": [],
            "audited": len(items),
            "detail": MISSING_SOURCE,
        }

    def _audit(self, status: str, detail: str, body: str | None) -> None:
        self.db.execute(
            "INSERT INTO audits (received_at, status, detail, body) VALUES (?, ?, ?, ?)",
            (datetime.now(CHICAGO).isoformat(), status, detail, body),
        )

    def audits(self) -> list[dict[str, Any]]:
        return [dict(row) for row in self.db.execute("SELECT * FROM audits ORDER BY id")]

    def book_entry(self, setup_id: str, *, root: str, qty: int, price: float) -> dict[str, Any]:
        reason = self.guard_entry(root=root, trades_today=0, daily_pnl=0.0, open_positions=len(self.positions()))
        if reason:
            return {"ok": False, "reason": reason, "booked": 0}
        self.record_fill(setup_id, qty=qty, price=price, pnl=0.0, kind="entry")
        return {"ok": True, "booked": 1}

    def record_exit(self, setup_id: str, *, qty: int, price: float, pnl: float) -> None:
        """An exit is recorded even when entry guards would refuse a new trade."""
        self.record_fill(setup_id, qty=qty, price=price, pnl=pnl, kind="exit")

    def record_fill(self, setup_id: str, *, qty: int, price: float, pnl: float, kind: str = "fill") -> None:
        """Book a paper fill inside one transaction with its outbox identity. Used by tests and later contract wiring."""
        identity = f"fill:{setup_id}:{kind}:{qty}:{price}"
        try:
            self.db.execute("BEGIN")
            self.db.execute(
                "INSERT INTO ledger (setup_id, kind, qty, price, pnl, detail) VALUES (?, ?, ?, ?, ?, ?)",
                (setup_id, kind, qty, price, pnl, kind),
            )
            self.db.execute(
                "INSERT INTO positions (setup_id, state, payload) VALUES (?, 'OPEN', ?) "
                "ON CONFLICT(setup_id) DO UPDATE SET state = 'OPEN', payload = excluded.payload",
                (setup_id, json.dumps({"qty": qty, "price": price})),
            )
            self.db.execute(
                "INSERT INTO outbox (identity, body, status, attempts) VALUES (?, ?, 'pending', 0)",
                (identity, json.dumps({"setupId": setup_id, "kind": kind, "qty": qty, "price": price, "pnl": pnl})),
            )
            self.db.commit()
        except Exception:
            self.db.rollback()
            raise

    def positions(self) -> list[dict[str, Any]]:
        rows = []
        for row in self.db.execute("SELECT setup_id, state, payload FROM positions"):
            rows.append({"setup_id": row["setup_id"], "state": row["state"], "payload": json.loads(row["payload"])})
        return rows

    def outbox(self) -> list[dict[str, Any]]:
        return [dict(row) | {"body": json.loads(row["body"])} for row in self.db.execute("SELECT * FROM outbox ORDER BY id")]

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

    def retry_pending(self) -> list[dict[str, Any]]:
        """Return notices still awaiting acceptance. Retry does not insert another fill."""
        rows = []
        for row in self.db.execute("SELECT * FROM outbox WHERE status IN ('pending', 'uncertain') ORDER BY id"):
            self.db.execute("UPDATE outbox SET attempts = attempts + 1 WHERE id = ?", (row["id"],))
            rows.append(dict(row) | {"body": json.loads(row["body"])})
        self.db.commit()
        return rows

    def ledger_count(self) -> int:
        row = self.db.execute("SELECT COUNT(*) AS n FROM ledger").fetchone()
        return int(row["n"])

    def notifications(self) -> list[dict[str, Any]]:
        """Chat notices are outbox rows. An idle desk has none."""
        return [item for item in self.outbox() if item["status"] != "delivered"]

    def remember_key(self, key: str) -> bool:
        try:
            self.db.execute("BEGIN")
            self.db.execute("INSERT INTO seen_keys (key) VALUES (?)", (key,))
            self.db.commit()
            return True
        except sqlite3.IntegrityError:
            self.db.rollback()
            return False


def paper_r(pnl: float, initial_stop_risk: float) -> float | None:
    """Paper R uses original stop risk. Later stop moves do not change the denominator."""
    if initial_stop_risk <= 0:
        return None
    return pnl / initial_stop_risk
