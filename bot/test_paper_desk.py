"""Offline checks for the durable paper desk.

Fixtures are synthetic. Nothing here binds port 8791 or talks to Sam.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from execution_bot import route_get, route_post
from paper_desk import (
    CONTRACT_STATUS,
    PaperDesk,
    allocate_contracts,
    contract_spec,
    derived_ref_risk,
    paper_r,
    session_date,
)

CHICAGO = ZoneInfo("America/Chicago")


def _desk(tmp: Path) -> PaperDesk:
    return PaperDesk(tmp / "desk.sqlite")


def _event(**overrides) -> dict:
    body = {
        "source": "staxbot",
        "event": "plan",
        "eventId": "setup:plan:1",
        "setupId": "MESZ2026:1:1",
        "plan_id": 1,
        "move_id": 1,
        "scenario": "B",
        "state": "ARMED",
        "origin": "shelf",
        "side": "long",
        "entry": 100.0,
        "stop": 90.0,
        "stop_preset": "Medium",
        "price": 100.0,
        "target": 110.0,
        "targets": [{"id": "TP1", "price": 110.0, "allocation": 1, "r": 1.0}],
        "grade": "A",
        "timeframe": "5",
        "ticker": "MESZ2026",
        "root": "MES",
        "exchange": "CME",
        "timestamp": "2026-09-30T10:00:00",
    }
    body.update(overrides)
    return body


def _raw(**overrides) -> str:
    return json.dumps(_event(**overrides))


def _arm(desk: PaperDesk, **overrides) -> None:
    desk.confirm_settings(sizing_mode="fixed", fixed_qty=1, allowed_roots=["MES", "MNQ", "MGC", "M2K", "MYM"])
    if overrides:
        desk.confirm_settings(allowed_roots=["MES", "MNQ", "MGC", "M2K", "MYM"], **overrides)


def test_contract_does_not_read_a_pine_file(tmp_path: Path) -> None:
    desk = _desk(tmp_path)
    assert desk.contract_status() == CONTRACT_STATUS
    assert "missing" not in desk.contract_status()
    assert desk.health()["pineFileRequired"] is False
    result = desk.ingest(_raw())
    assert result["booked"] == 0
    assert result["notifications"]
    assert "Waiting for the retest" in result["notifications"][0]
    assert desk.ledger_count() == 0
    desk.close()


def test_invalid_json_and_array_item(tmp_path: Path) -> None:
    desk = _desk(tmp_path)
    bad = desk.ingest("{")
    assert bad["ok"] is False
    assert desk.ledger_count() == 0
    mixed = desk.ingest(json.dumps([_event(), "nope", {"event": "entry", "setupId": "other"}]))
    assert mixed["booked"] == 0
    statuses = [row["status"] for row in desk.audits()]
    assert "invalid-item" in statuses
    assert any("Waiting for the retest" in item["text"] for item in desk.notifications())
    desk.close()


def test_quarantine_after_invalid_sibling(tmp_path: Path) -> None:
    desk = _desk(tmp_path)
    _arm(desk)
    bad = {
        "source": "staxbot",
        "event": "entry",
        "setupId": "MESZ2026:1:1",
        "ticker": "MESZ2026",
        "exchange": "CME",
        "timeframe": "5",
    }
    good = _event(event="entry", eventId="MESZ2026:1:1:entry:2", state="TRIGGERED")
    result = desk.ingest(json.dumps([bad, good]))
    assert result["booked"] == 0
    assert desk.ledger_count() == 0
    assert any(row["status"] == "quarantine" for row in desk.audits())
    desk.close()


def test_idle_day_has_zero_notifications(tmp_path: Path) -> None:
    desk = _desk(tmp_path)
    assert desk.notifications() == []
    assert desk.ingest("[]")["notifications"] == []
    desk.close()


def test_proposals_do_not_activate_and_fixed_mode_skips_dollar_risk(tmp_path: Path) -> None:
    desk = _desk(tmp_path)
    desk.store_proposals({"contracts": 3, "max_trades": 20, "daily_limits": "off", "roots": ["MES", "MNQ"]})
    assert desk.settings()["confirmed"] is False
    refused = desk.ingest(_raw(event="entry", eventId="e1", state="TRIGGERED"))
    assert desk.ledger_count() == 0
    assert "Paper sizing is not configured" in refused["notifications"][0]
    desk.confirm_settings(sizing_mode="fixed", fixed_qty=2, allowed_roots=["MES"])
    assert desk.settings()["risk_per_trade"] is None
    taken = desk.ingest(_raw(event="entry", eventId="e2", setupId="sized", state="TRIGGERED"))
    assert taken["booked"] == 1
    assert desk.positions()[0]["payload"]["qty"] == 2
    assert "opened 2 contracts" in taken["notifications"][0]
    desk.close()


def test_risk_mode_and_exact_root(tmp_path: Path) -> None:
    desk = _desk(tmp_path)
    desk.confirm_settings(sizing_mode="risk", risk_per_trade=50, allowed_roots=["MES"])
    taken = desk.ingest(_raw(event="entry", eventId="e1", state="TRIGGERED"))
    assert desk.positions()[0]["payload"]["qty"] == 1
    assert desk.positions()[0]["payload"]["point_value"] == 5.0
    assert taken["booked"] == 1
    short = desk.ingest(_raw(
        event="entry", eventId="e-short", setupId="other", state="TRIGGERED",
        root="NQ", ticker="NQZ2026", entry=100, stop=90, price=100, target=110,
        targets=[{"id": "TP1", "price": 110.0, "allocation": 1, "r": 1}],
    ))
    assert short["booked"] == 0
    assert "No contract spec for NQ" in short["notifications"][0]
    messy = desk.ingest(_raw(
        event="entry", eventId="e-messy", setupId="messy", state="TRIGGERED", root="MNQZ2026", ticker="MNQZ2026",
    ))
    assert "No contract spec for MNQZ2026" in messy["notifications"][0]
    assert contract_spec("MNQ") == 2.0
    assert contract_spec("MNQZ2026") is None
    desk.close()


def test_allocation_puts_all_remainder_on_the_earliest_target() -> None:
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
            {"id": "TP1", "allocation": 1},
            {"id": "TP2", "allocation": 1},
            {"id": "TP3", "allocation": 1},
        ],
    )
    assert [item["qty"] for item in short] == [1, 0, 0]
    leftover = allocate_contracts(
        8,
        [
            {"id": "TP3", "allocation": 1},
            {"id": "TP1", "allocation": 1},
            {"id": "TP2", "allocation": 1},
        ],
    )
    assert [item["id"] for item in leftover] == ["TP1", "TP2", "TP3"]
    assert [item["qty"] for item in leftover] == [4, 2, 2]


def test_duplicate_exit_price_and_same_id_targets(tmp_path: Path) -> None:
    desk = _desk(tmp_path)
    desk.confirm_settings(sizing_mode="fixed", fixed_qty=2, allowed_roots=["MES"])
    targets = [
        {"id": "TP1", "price": 110.0, "allocation": 1, "r": 1},
        {"id": "TP2", "price": 120.0, "allocation": 1, "r": 2},
    ]
    desk.ingest(_raw(event="entry", eventId="setup:entry:2", state="TRIGGERED", targets=targets, target=110))
    first = _event(
        event="exit", eventId="setup:exit:3", state="CLOSED", reason="TP", targetId="TP1",
        price=100.0, targets=targets, target=110,
    )
    raw = json.dumps(first)[:-1] + ',"price":110.0}'
    desk.ingest(raw)
    second = json.dumps(_event(
        event="exit", eventId="setup:exit:3", state="CLOSED", reason="TP", targetId="TP2",
        price=120.0, targets=targets, target=110,
    ))
    second = second[:-1] + ',"price":120.0}'
    desk.ingest(second)
    position = desk.positions()[0]
    assert position["state"] == "CLOSED"
    assert position["payload"]["remaining"] == 0
    assert desk.ledger_count() == 3
    again = desk.ingest(raw)
    assert again["booked"] == 0
    assert desk.ledger_count() == 3
    desk.close()


def test_conflicting_duplicate_and_schema_selection(tmp_path: Path) -> None:
    desk = _desk(tmp_path)
    _arm(desk)
    conflict = json.dumps(_event(event="entry", eventId="e", state="TRIGGERED"))[:-1] + ',"stop":50}'
    refused = desk.ingest(conflict)
    assert refused["booked"] == 0
    assert any(row["status"] == "invalid-event" for row in desk.audits())
    schema1 = _event(event="entry", eventId="s1", state="TRIGGERED", schemaVersion=1, pineVersion="2.4.5")
    assert desk.ingest(json.dumps(schema1))["booked"] == 1
    dup_price = json.dumps(_event(
        event="exit", eventId="s1:exit", state="CLOSED", reason="SL", targetId="STOP", schemaVersion=1,
    ))[:-1] + ',"price":90}'
    blocked = desk.ingest(dup_price)
    assert blocked["booked"] == 0
    assert any(row["status"] == "invalid-event" for row in desk.audits())
    unknown = _event(event="entry", eventId="future", state="TRIGGERED", schemaVersion=2)
    assert desk.ingest(json.dumps(unknown))["booked"] == 0
    assert any(row["status"] == "unsupported-schema" for row in desk.audits())
    desk.close()


def test_pine_version_is_not_identity(tmp_path: Path) -> None:
    desk = _desk(tmp_path)
    _arm(desk)
    entry = _event(event="entry", eventId="same", state="TRIGGERED", pineVersion="2.4.5", hudNote="first")
    assert desk.ingest(json.dumps(entry))["booked"] == 1
    retry = _event(event="entry", eventId="same", state="TRIGGERED", pineVersion="2.4.6", hudNote="retry")
    assert desk.ingest(json.dumps(retry))["booked"] == 0
    assert desk.ledger_count() == 1
    for version, suffix in (("2.5.0", "a"), (None, "b")):
        payload = _event(
            event="plan", eventId=f"plan-{suffix}", setupId=f"plan-{suffix}",
            state="ARMED", move_id=suffix,
        )
        if version is None:
            payload.pop("pineVersion", None)
        else:
            payload["pineVersion"] = version
        payload["scenario"] = "custom-shelf"
        payload["grade"] = "C+"
        note = desk.ingest(json.dumps(payload))["notifications"][0]
        assert "C+" in note
        assert "Waiting for the retest" in note
    desk.close()


def test_source_namespaces_do_not_share_a_ticker_id(tmp_path: Path) -> None:
    desk = _desk(tmp_path)
    desk.confirm_settings(sizing_mode="fixed", fixed_qty=1, allowed_roots=["MES", "MNQ"])
    desk.ingest(_raw(event="entry", eventId="same-id", setupId="same-setup", state="TRIGGERED"))
    desk.ingest(_raw(
        event="entry", eventId="same-id", setupId="same-setup", state="TRIGGERED",
        ticker="MNQZ2026", root="MNQ", exchange="CME",
    ))
    assert len(desk.positions()) == 2
    assert {item["payload"]["root"] for item in desk.positions()} == {"MES", "MNQ"}
    desk.close()


def test_sibling_cancel_does_not_close_the_filled_plan(tmp_path: Path) -> None:
    desk = _desk(tmp_path)
    _arm(desk)
    shelf = _event(event="entry", eventId="shelf:entry:2", setupId="shelf", state="TRIGGERED", origin="shelf", scenario="B")
    gap = _event(
        event="plan_cancel", eventId="gap:plan_cancel:2", setupId="gap", state="CANCELLED",
        origin="gap", scenario="A", reason="sibling filled", entry=102, stop=92, price=102, target=112,
        targets=[{"id": "TP1", "price": 112.0, "allocation": 1, "r": 1}],
    )
    result = desk.ingest(json.dumps([shelf, gap]))
    assert desk.positions()[0]["state"] == "OPEN"
    text = " ".join(result["notifications"])
    assert "opened 1 contract" in text
    assert "other plan on this move filled" in text
    assert desk.positions()[0]["payload"]["remaining"] == 1
    desk.close()


def test_every_cancel_and_exit_reason(tmp_path: Path) -> None:
    desk = _desk(tmp_path)
    _arm(desk)
    reasons = {
        "replaced": "it was replaced",
        "reclaim close": "price reclaimed the shelf",
        "ran without retest": "price ran to the target without a retest",
        "expired": "it expired",
        "session": "the session ended",
        "sibling filled": "the other plan on this move filled",
        "failed breakout": "the breakout failed",
    }
    for reason, phrase in reasons.items():
        note = desk.ingest(_raw(
            event="plan_cancel", eventId=f"c-{reason}", setupId=f"c-{reason}", state="EXPIRED", reason=reason,
        ))["notifications"][0]
        assert phrase in note
        assert desk.ledger_count() == 0
    desk.ingest(_raw(event="entry", eventId="live", setupId="live", state="TRIGGERED"))
    exits = {
        "SL": (90.0, "stop", "The long failed"),
        "BE": (100.0, "breakeven", None),
        "TRAILED": (101.0, "trailed stop", None),
        "RECLAIM": (99.0, "reclaim", None),
        "FLAT": (100.0, "session flatten", None),
    }
    # One position: the first final exit closes it. Later reasons each get their own position.
    desk.ingest(_exit("live", "SL", 90.0))
    assert "The long failed" in desk.notifications()[-1]["text"]
    assert "stop" in desk.notifications()[-1]["text"]
    for index, (reason, (price, phrase, _)) in enumerate(exits.items()):
        if reason == "SL":
            continue
        setup = f"live-{reason}"
        desk.ingest(_raw(event="entry", eventId=f"en-{setup}", setupId=setup, state="TRIGGERED"))
        note = desk.ingest(_exit(setup, reason, price))["notifications"][0]
        assert phrase in note
        assert index >= 0
    desk.close()


def _exit(setup: str, reason: str, price: float, target: str | None = None) -> str:
    if target is None:
        target = {"SL": "STOP", "BE": "STOP", "TRAILED": "STOP", "RECLAIM": "RECLAIM", "FLAT": "FLAT"}.get(reason, "TP1")
    body = _event(
        event="exit", eventId=f"{setup}:exit:{reason}", setupId=setup, state="CLOSED",
        reason=reason, targetId=target, price=price,
    )
    return json.dumps(body)


def test_entry_before_plan_and_late_plan(tmp_path: Path) -> None:
    desk = _desk(tmp_path)
    _arm(desk)
    entry = desk.ingest(_raw(event="entry", eventId="e", state="TRIGGERED", grade="B"))
    assert entry["booked"] == 1
    late = desk.ingest(_raw(event="plan", eventId="p", state="ARMED", grade="A+"))
    assert late["notifications"] == []
    assert desk.positions()[0]["state"] == "OPEN"
    assert desk.ledger_count() == 1
    desk.close()


def test_rejected_entry_still_accepts_a_chart_exit(tmp_path: Path) -> None:
    desk = _desk(tmp_path)
    note = desk.ingest(_raw(event="entry", eventId="e", state="TRIGGERED"))["notifications"][0]
    assert "Paper sizing is not configured" in note
    closed = desk.ingest(_exit("MESZ2026:1:1", "SL", 90.0))["notifications"][0]
    assert "closed at 90" in closed
    assert "P&L" not in closed
    assert desk.ledger_count() == 0
    assert "The book is flat" in closed
    desk.close()


def test_out_of_order_final_exit_does_not_reopen(tmp_path: Path) -> None:
    desk = _desk(tmp_path)
    _arm(desk)
    entry = _event(event="entry", eventId="e", state="TRIGGERED")
    stop = json.loads(_exit("MESZ2026:1:1", "SL", 90.0))
    target = json.loads(_exit("MESZ2026:1:1", "TP", 110.0, "TP1"))
    target["eventId"] = "MESZ2026:1:1:exit:SL"
    result = desk.ingest(json.dumps([entry, stop, target]))
    assert desk.positions()[0]["state"] == "CLOSED"
    assert desk.positions()[0]["payload"]["remaining"] == 0
    assert desk.ledger_count() == 2
    assert "TP1 reached" in " ".join(result["notifications"])
    assert any(row["status"] == "reconciliation" for row in desk.audits())
    desk.close()


def test_zero_allocation_and_disabled_tp1_stay_frozen(tmp_path: Path) -> None:
    desk = _desk(tmp_path)
    desk.confirm_settings(sizing_mode="fixed", fixed_qty=1, allowed_roots=["MES"])
    targets = [
        {"id": "TP1", "price": 110.0, "allocation": 1, "r": 1},
        {"id": "TP2", "price": 120.0, "allocation": 1, "r": 2},
        {"id": "TP3", "price": 130.0, "allocation": 1, "r": 3},
    ]
    desk.ingest(_raw(event="entry", eventId="e", state="TRIGGERED", targets=targets))
    milestone = desk.ingest(_exit("MESZ2026:1:1", "TP", 120.0, "TP2"))["notifications"][0]
    assert "TP2 reached" in milestone
    assert "paper desk closed" not in milestone
    assert desk.positions()[0]["payload"]["remaining"] == 1
    desk.close()

    other = PaperDesk(tmp_path / "other.sqlite")
    other.confirm_settings(sizing_mode="fixed", fixed_qty=3, allowed_roots=["MES"])
    weighted = [
        {"id": "TP1", "price": 110.0, "allocation": 1, "r": 1, "enabled": False},
        {"id": "TP2", "price": 120.0, "allocation": 1, "r": 2},
        {"id": "TP3", "price": 130.0, "allocation": 1, "r": 3},
    ]
    other.ingest(_raw(event="entry", eventId="e2", setupId="s2", state="TRIGGERED", targets=weighted))
    quantities = [item["qty"] for item in other.positions()[0]["payload"]["allocations"]]
    assert quantities == [0, 2, 1]
    other.ingest(json.dumps(_event(
        event="stop_update", eventId="s2:stop:4", setupId="s2", state="LIVE", stop=95, reason="trail", realizedR=0.5,
    )))
    again = [item["qty"] for item in other.positions()[0]["payload"]["allocations"]]
    assert again == [0, 2, 1]
    assert other.positions()[0]["payload"]["initial_stop"] == 90
    assert other.positions()[0]["payload"]["stop"] == 95
    assert "P&L" not in other.notifications()[-1]["text"]
    assert "paper stop moved from 90 to 95" in other.notifications()[-1]["text"]
    other.close()


def test_reference_r_is_not_rebuilt_from_the_selected_stop(tmp_path: Path) -> None:
    desk = _desk(tmp_path)
    _arm(desk)
    targets = [{"id": "TP1", "price": 110.0, "allocation": 1, "r": 1.0}]
    desk.ingest(_raw(
        event="entry", eventId="e", state="TRIGGERED", entry=100, stop=80, price=100, target=110, targets=targets,
    ))
    stored = desk.positions()[0]["payload"]["allocations"][0]
    assert stored["price"] == 110.0
    assert stored["chart_r"] == 1.0
    assert stored["planned_rr"] == 0.5
    assert derived_ref_risk(100, 110, 1) == 10
    assert paper_r(50, 100) == 0.5
    desk.close()


def test_guards_block_entries_and_still_allow_exits(tmp_path: Path) -> None:
    desk = _desk(tmp_path)
    desk.confirm_settings(
        sizing_mode="fixed", fixed_qty=1, allowed_roots=["MES"], max_trades=1, max_daily_loss=40, kill_switch=False,
    )
    when = datetime(2026, 9, 30, 12, 0, tzinfo=CHICAGO)
    desk.ingest(_raw(event="entry", eventId="e1", setupId="s1", state="TRIGGERED"), now=when)
    desk.ingest(_exit("s1", "SL", 90.0), now=when)
    blocked = desk.ingest(_raw(event="entry", eventId="e2", setupId="s2", state="TRIGGERED"), now=when)
    assert "Max trades reached" in blocked["notifications"][0]
    assert desk.ledger_count() == 2
    desk.confirm_settings(
        sizing_mode="fixed", fixed_qty=1, allowed_roots=["MES"], max_trades=5, max_daily_loss=40,
    )
    loss_block = desk.ingest(_raw(event="entry", eventId="e3", setupId="s3", state="TRIGGERED"), now=when)
    assert "Daily loss limit is hit" in loss_block["notifications"][0]
    desk.confirm_settings(
        sizing_mode="fixed", fixed_qty=1, allowed_roots=["MES"], max_trades=5, max_daily_loss=0, kill_switch=True,
    )
    killed = desk.ingest(_raw(event="entry", eventId="e4", setupId="s4", state="TRIGGERED"), now=when)
    assert "Kill switch is on" in killed["notifications"][0]
    # The closed position can still be echoed by a distinct stop update, and a fresh open one can exit.
    desk.confirm_settings(
        sizing_mode="fixed", fixed_qty=1, allowed_roots=["MES"], kill_switch=False, max_daily_loss=0,
    )
    desk.ingest(_raw(event="entry", eventId="e5", setupId="s5", state="TRIGGERED"), now=when)
    desk.confirm_settings(
        sizing_mode="fixed", fixed_qty=1, allowed_roots=["MES"], kill_switch=True, max_daily_loss=0, max_trades=1,
    )
    closed = desk.ingest(_exit("s5", "FLAT", 101.0), now=when)
    assert closed["booked"] == 1
    assert desk.positions()[-1]["state"] == "CLOSED"
    desk.close()


def test_restart_retry_and_rollover_keep_the_book(tmp_path: Path) -> None:
    path = tmp_path / "desk.sqlite"
    desk = PaperDesk(path)
    desk.confirm_settings(sizing_mode="fixed", fixed_qty=1, allowed_roots=["MES"], max_trades=1)
    morning = datetime(2026, 3, 8, 16, 30, tzinfo=CHICAGO)
    desk.ingest(_raw(event="entry", eventId="e", setupId="s", state="TRIGGERED"), now=morning)
    identity = desk.outbox()[0]["identity"]
    desk.mark_uncertain(identity)
    assert desk.retry_pending(now=morning)[0]["identity"] == identity
    assert desk.ledger_count() == 1
    desk.close()
    again = PaperDesk(path)
    assert again.positions()[0]["state"] == "OPEN"
    assert again.ledger_count() == 1
    assert again.ingest(_raw(event="entry", eventId="e", setupId="s", state="TRIGGERED"), now=morning)["booked"] == 0
    evening = datetime(2026, 3, 8, 17, 5, tzinfo=CHICAGO)
    nxt = again.ingest(_raw(event="entry", eventId="e2", setupId="s2", state="TRIGGERED"), now=evening)
    assert nxt["booked"] == 1
    assert len(again.positions()) == 2
    again.confirm_delivery(identity)
    assert all(item["identity"] != identity for item in again.notifications())
    again.close()


def test_chicago_day_roll_respects_dst() -> None:
    spring = session_date(datetime(2026, 3, 8, 16, 59, tzinfo=CHICAGO))
    spring_next = session_date(datetime(2026, 3, 8, 17, 0, tzinfo=CHICAGO))
    assert spring_next == spring + timedelta(days=1)
    fall = session_date(datetime(2026, 11, 1, 16, 59, tzinfo=CHICAGO))
    fall_next = session_date(datetime(2026, 11, 1, 17, 0, tzinfo=CHICAGO))
    assert fall_next == fall + timedelta(days=1)


def test_database_file_is_owner_readable_only(tmp_path: Path) -> None:
    desk = _desk(tmp_path)
    mode = desk.path.stat().st_mode & 0o777
    assert mode == 0o600
    desk.close()


def test_rollback_drops_uncommitted_fill(tmp_path: Path) -> None:
    desk = _desk(tmp_path)
    desk.db.execute("BEGIN")
    desk.db.execute(
        "INSERT INTO ledger (setup_id, kind, qty, price, pnl, detail) VALUES ('s', 'fill', 1, 1, 1, 'x')"
    )
    desk.db.rollback()
    assert desk.ledger_count() == 0
    desk.close()


def test_alert_generation_keeps_exits_and_blocks_new_entries(tmp_path: Path) -> None:
    desk = _desk(tmp_path)
    _arm(desk)
    opened = desk.ingest(_raw(event="entry", eventId="e", setupId="s", state="TRIGGERED"))
    instance = json.dumps(["staxbot", "CME", "MESZ2026", "5"], separators=(",", ":"))
    desk.disable_entries(instance)
    blocked = desk.ingest(_raw(event="entry", eventId="e2", setupId="s2", state="TRIGGERED"))
    assert "New entries are disabled" in blocked["notifications"][0]
    closed = desk.ingest(_exit("s", "SL", 90.0))
    assert closed["booked"] == 1
    assert opened["booked"] == 1
    desk.close()


def test_two_plans_and_http_routes(tmp_path: Path) -> None:
    desk = _desk(tmp_path)
    shelf = _event(origin="shelf", scenario="B", setupId="shelf", eventId="shelf:plan:1")
    gap = _event(
        origin="gap", scenario="A", setupId="gap", eventId="gap:plan:1", entry=102, stop=92, price=102, target=112,
        targets=[{"id": "TP1", "price": 112.0, "allocation": 1, "r": 1}],
    )
    note = desk.ingest(json.dumps([shelf, gap]))["notifications"][0]
    assert "shelf plan at 100" in note
    assert "gap plan at 102" in note
    status, body = route_post("/webhook/trade-signal", _raw(event="plan", eventId="http-1", setupId="http").encode(), desk)
    assert status == 200
    assert body["notifications"]
    other, mirrored = route_post("/api/alert", _raw(event="plan", eventId="http-2", setupId="http-2").encode(), desk)
    assert other == 200
    assert mirrored["notifications"]
    demo, demo_body = route_post("/api/demo", b"{}", desk)
    assert demo == 410
    assert demo_body["success"] is False
    assert desk.ledger_count() == 0
    released, release_body = route_post("/api/release", b"{}", desk)
    assert released == 200
    assert release_body["data"]["released"] == 0
    assert desk.ledger_count() == 0
    desk.close()


def test_paper_r_uses_original_risk() -> None:
    assert paper_r(50, 100) == 0.5
    assert paper_r(0, 0) is None


def test_final_exit_target_ids_follow_the_pine_contract(tmp_path: Path) -> None:
    desk = _desk(tmp_path)
    _arm(desk)
    desk.ingest(_raw(event="entry", eventId="e", setupId="s", state="TRIGGERED"))
    wrong = desk.ingest(_exit("s", "FLAT", 100.0, "STOP"))
    assert wrong["booked"] == 0
    assert desk.positions()[0]["state"] == "OPEN"
    assert any(row["status"] == "invalid-event" for row in desk.audits())
    closed = desk.ingest(_exit("s", "FLAT", 101.0, "FLAT"))
    assert closed["booked"] == 1
    assert "session flatten" in closed["notifications"][0]
    desk.ingest(_raw(event="entry", eventId="e2", setupId="s2", state="TRIGGERED"))
    reclaim = desk.ingest(_exit("s2", "RECLAIM", 99.0, "RECLAIM"))
    assert reclaim["booked"] == 1
    assert "reclaim" in reclaim["notifications"][0]
    desk.close()


def test_sqlite_is_the_dashboard_settings_and_delivery_store(tmp_path: Path) -> None:
    desk = _desk(tmp_path)
    state, body = route_get("/api/state", desk)
    assert state == 200
    assert body["delivery"] == "outbox"
    assert body["inbox"] == []
    assert body["position"] is None
    assert body["passTarget"] is None
    assert body["paperReady"] is False
    assert "forwardUrl" not in body
    assert "forwardToken" not in body
    saved, after = route_post("/api/settings", json.dumps({
        "sizingMode": "fixed",
        "fixedQty": 1,
        "watchMarkets": ["MES"],
        "maxTrades": 4,
        "riskPerTrade": 250,
        "forwardUrl": "https://example.invalid/hook",
        "forwardToken": "secret-token",
    }).encode(), desk)
    assert saved == 200
    assert after["paperReady"] is True
    assert after["samKeySet"] is True
    assert after["forwardUrlSet"] is True
    assert "secret-token" not in json.dumps(after)
    assert "example.invalid" not in json.dumps(after)
    route_post("/webhook/trade-signal", _raw(event="plan", eventId="p", setupId="s").encode(), desk)
    shown = route_get("/api/state", desk)[1]
    assert shown["plan"]["setupId"] == "s"
    assert shown["inbox"] and "Waiting for the retest" in shown["inbox"][0]["text"]
    identity = shown["inbox"][0]["identity"]
    released = route_post("/api/release", json.dumps({"identity": identity}).encode(), desk)[1]
    assert released["data"]["released"] == 1
    assert route_get("/api/state", desk)[1]["inbox"] == []
    assert desk.ledger_count() == 0
    flat = route_post("/api/flatten", b"{}", desk)[1]
    assert flat["success"] is False
    assert route_post("/api/reset", b"{}", desk)[0] == 410
    desk.close()


def test_legacy_snapshot_does_not_activate_limits_or_rebook(tmp_path: Path) -> None:
    desk = _desk(tmp_path)
    result = desk.import_legacy_snapshot({
        "equity": 25000,
        "riskPerTrade": 250,
        "dailyTarget": 600,
        "forwardUrl": "https://example.invalid/hook",
        "forwardToken": "secret-token",
        "position": {"ticker": "MESZ2026", "side": "long", "qty": 1},
        "inbox": [{"payload": _event(event="plan", eventId="migrated", setupId="migrated")}],
    })
    assert result["sizingConfirmed"] is False
    assert result["startingBalance"] is None
    assert result["openPositionsNoted"] == 1
    assert result["ingested"] == 1
    assert desk.ledger_count() == 0
    assert desk.positions() == []
    view = desk.dashboard()
    assert view["paperReady"] is False
    assert view["samKeySet"] is True
    assert "secret-token" not in json.dumps(view)
    assert any("Waiting for the retest" in item["text"] for item in view["inbox"])
    desk.close()


def test_version_label_exit_price_and_headsups_do_not_book(tmp_path: Path) -> None:
    desk = _desk(tmp_path)
    _arm(desk)
    armed = desk.ingest(_raw(event="plan", eventId="p", setupId="s", state="ARMED", version="2.5.0", fp="fp", level="prior day"))
    assert armed["booked"] == 0
    assert "Waiting for the retest" in armed["notifications"][0]
    retry = desk.ingest(_raw(event="plan", eventId="p", setupId="s", state="ARMED", version="2.4.7"))
    assert retry["booked"] == 0
    assert retry["notifications"] == []
    desk.ingest(_raw(event="entry", eventId="e", setupId="s", state="TRIGGERED", version="2.5.0"))
    watched = desk.ingest(json.dumps({
        "source": "staxbot",
        "event": "watch",
        "eventId": "watch:10",
        "version": "2.5.0",
        "fp": "fp",
        "provisional": False,
        "level": "prior day",
        "range_high": 110.0,
        "range_low": 100.0,
        "timestamp": "2026-09-30T10:00:00",
    }))
    assert watched["booked"] == 0
    assert watched["notifications"] == ["A compressed range is on watch at prior day, 100 to 110."]
    other_market = desk.ingest(json.dumps({
        "source": "staxbot",
        "event": "watch",
        "eventId": "watch:10",
        "version": "2.5.0",
        "fp": "fp",
        "level": "prior week",
        "range_high": 21000.0,
        "range_low": 20900.0,
        "timestamp": "2026-09-30T10:00:00",
    }))
    assert other_market["notifications"]
    assert desk.ingest(json.dumps({
        "source": "staxbot",
        "event": "watch",
        "eventId": "watch:10",
        "version": "2.5.0",
        "fp": "fp",
        "level": "prior day",
        "range_high": 110.0,
        "range_low": 100.0,
    }))["notifications"] == []
    forming = desk.ingest(json.dumps({
        "source": "staxbot",
        "event": "break_forming",
        "eventId": "break_forming:11",
        "version": "2.5.0",
        "fp": "fp",
        "provisional": True,
        "side": "long",
        "entry": 110.0,
        "stop": 100.0,
        "target": 120.0,
        "level": "shelf",
        "timestamp": "2026-09-30T10:05:00",
    }))
    assert forming["booked"] == 0
    assert "provisional long break is forming" in forming["notifications"][0]
    assert desk.positions()[0]["state"] == "OPEN"
    cancelled = desk.ingest(json.dumps({
        "source": "staxbot",
        "event": "break_cancelled",
        "eventId": "break_cancelled:11",
        "version": "2.5.0",
        "fp": "fp",
        "provisional": False,
        "side": "long",
        "entry": 110.0,
        "stop": 100.0,
        "target": 120.0,
        "timestamp": "2026-09-30T10:05:00",
    }))
    assert "was cancelled" in cancelled["notifications"][0]
    assert desk.positions()[0]["state"] == "OPEN"
    assert desk.ledger_count() == 1
    wrong = desk.ingest(_raw(
        event="exit", eventId="s:exit:SL:12", setupId="s", state="CLOSED",
        reason="SL", targetId="SL", price=100.0, exit_price=91.0, version="2.5.0",
    ))
    assert wrong["booked"] == 0
    assert desk.positions()[0]["state"] == "OPEN"
    closed = desk.ingest(_raw(
        event="exit", eventId="s:exit:STOP:SL:12", setupId="s", state="CLOSED",
        reason="SL", targetId="STOP", price=100.0, exit_price=91.0, version="2.5.0", fp="fp",
    ))
    assert closed["booked"] == 1
    assert "closed at 91" in closed["notifications"][0]
    assert desk.positions()[0]["state"] == "CLOSED"
    fills = [row for row in desk.db.execute("SELECT price, kind FROM ledger WHERE kind = 'exit'")]
    assert fills[-1]["price"] == 91.0
    desk.ingest(_raw(event="entry", eventId="e2", setupId="s2", state="TRIGGERED"))
    desk.ingest(json.dumps(_event(
        event="stop_update", eventId="s2:stop:4", setupId="s2", state="LIVE",
        stop=95, reason="trail", realizedR=0.5, locked_r=0.5, version="2.5.0",
    )))
    assert "Locked R 0.5" in desk.notifications()[-1]["text"]
    assert desk.dashboard()["delivery"] == "outbox"
    desk.close()
