"""Offline checks for the durable paper desk. They do not open port 8791."""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from paper_desk import (
    MISSING_SOURCE,
    PaperDesk,
    allocate_contracts,
    paper_r,
    session_date,
)

CHICAGO = ZoneInfo("America/Chicago")


def _desk(tmp: Path) -> PaperDesk:
    return PaperDesk(tmp / "desk.sqlite")


def test_missing_contract_does_not_book_or_notify(tmp_path: Path) -> None:
    desk = _desk(tmp_path)
    result = desk.ingest('{"event":"plan","setupId":"s","entry":1,"stop":2}')
    assert result["booked"] == 0
    assert result["notifications"] == []
    assert desk.notifications() == []
    assert desk.ledger_count() == 0
    assert MISSING_SOURCE in desk.contract_status()
    assert any(row["status"] == "unverified-contract" for row in desk.audits())
    desk.close()


def test_invalid_json_and_array_item(tmp_path: Path) -> None:
    desk = _desk(tmp_path)
    bad = desk.ingest("{")
    assert bad["ok"] is False
    assert desk.ledger_count() == 0
    mixed = desk.ingest(json.dumps([{"event": "plan"}, "nope", {"event": "entry"}]))
    assert mixed["booked"] == 0
    assert mixed["notifications"] == []
    statuses = [row["status"] for row in desk.audits()]
    assert statuses.count("invalid-item") == 1
    assert statuses.count("unverified-contract") == 2
    desk.close()


def test_idle_day_has_zero_notifications(tmp_path: Path) -> None:
    desk = _desk(tmp_path)
    assert desk.notifications() == []
    assert desk.ingest("[]")["notifications"] == []
    desk.close()


def test_proposals_do_not_activate(tmp_path: Path) -> None:
    desk = _desk(tmp_path)
    desk.store_proposals({"contracts": 3, "max_trades": 20, "daily_limits": "off", "roots": ["MES", "MNQ"]})
    assert desk.settings()["confirmed"] is False
    refused = desk.book_entry("s", root="MES", qty=1, price=1)
    assert refused["ok"] is False
    assert "confirmed" in refused["reason"]
    assert desk.ledger_count() == 0
    desk.close()


def test_confirmed_settings_and_kill_switch(tmp_path: Path) -> None:
    desk = _desk(tmp_path)
    desk.confirm_settings(starting_balance=1000, risk_per_trade=50, max_trades=2, allowed_roots=["MES"])
    taken = desk.book_entry("s", root="MES", qty=1, price=10)
    assert taken["ok"] is True
    blocked = desk.guard_entry(root="MNQ", trades_today=0, daily_pnl=0, open_positions=0)
    assert blocked and "MNQ" in blocked
    desk.confirm_settings(
        starting_balance=1000, risk_per_trade=50, max_trades=2, allowed_roots=["MES"], kill_switch=True
    )
    assert desk.book_entry("s2", root="MES", qty=1, price=11)["ok"] is False
    before = desk.ledger_count()
    desk.record_exit("s", qty=1, price=12, pnl=10)
    assert desk.ledger_count() == before + 1
    desk.close()


def test_allocation_rules() -> None:
    one = allocate_contracts(4, [{"id": "TP1", "enabled": True, "allocation": 1}])
    assert one[0]["qty"] == 4
    skip = allocate_contracts(
        3,
        [
            {"id": "TP1", "enabled": False, "allocation": 5},
            {"id": "TP2", "enabled": True, "allocation": 1},
            {"id": "TP3", "enabled": True, "allocation": 1},
        ],
    )
    assert [item["qty"] for item in skip] == [0, 2, 1]
    short = allocate_contracts(
        1,
        [
            {"id": "TP1", "enabled": True, "allocation": 1},
            {"id": "TP2", "enabled": True, "allocation": 1},
            {"id": "TP3", "enabled": True, "allocation": 1},
        ],
    )
    assert [item["qty"] for item in short] == [1, 0, 0]


def test_paper_r_uses_original_risk() -> None:
    assert paper_r(50, 100) == 0.5
    assert paper_r(0, 0) is None


def test_restart_keeps_position_and_outbox(tmp_path: Path) -> None:
    path = tmp_path / "desk.sqlite"
    desk = PaperDesk(path)
    desk.confirm_settings(starting_balance=1000, risk_per_trade=50, max_trades=2, allowed_roots=["MES"])
    desk.book_entry("s", root="MES", qty=2, price=5)
    desk.close()
    again = PaperDesk(path)
    assert again.positions()[0]["setup_id"] == "s"
    assert again.outbox()[0]["status"] == "pending"
    assert again.ledger_count() == 1
    again.close()


def test_rollback_drops_uncommitted_fill(tmp_path: Path) -> None:
    desk = _desk(tmp_path)
    desk.db.execute("BEGIN")
    desk.db.execute(
        "INSERT INTO ledger (setup_id, kind, qty, price, pnl, detail) VALUES ('s', 'fill', 1, 1, 1, 'x')"
    )
    desk.db.rollback()
    assert desk.ledger_count() == 0
    desk.close()


def test_retry_does_not_book_again_and_timeout_is_uncertain(tmp_path: Path) -> None:
    desk = _desk(tmp_path)
    desk.confirm_settings(starting_balance=1000, risk_per_trade=50, max_trades=2, allowed_roots=["MES"])
    desk.book_entry("s", root="MES", qty=1, price=5)
    identity = desk.outbox()[0]["identity"]
    desk.mark_uncertain(identity)
    assert desk.outbox()[0]["status"] == "uncertain"
    retried = desk.retry_pending()
    assert len(retried) == 1
    assert desk.ledger_count() == 1
    desk.confirm_delivery(identity)
    assert desk.notifications() == []
    desk.close()


def test_chicago_day_roll_respects_dst() -> None:
    spring = session_date(datetime(2026, 3, 8, 16, 59, tzinfo=CHICAGO))
    spring_next = session_date(datetime(2026, 3, 8, 17, 0, tzinfo=CHICAGO))
    assert spring_next == spring + __import__("datetime").timedelta(days=1)
    fall = session_date(datetime(2026, 11, 1, 16, 59, tzinfo=CHICAGO))
    fall_next = session_date(datetime(2026, 11, 1, 17, 0, tzinfo=CHICAGO))
    assert fall_next == fall + __import__("datetime").timedelta(days=1)


def test_database_file_is_owner_readable_only(tmp_path: Path) -> None:
    desk = _desk(tmp_path)
    mode = desk.path.stat().st_mode & 0o777
    assert mode == 0o600
    desk.close()
