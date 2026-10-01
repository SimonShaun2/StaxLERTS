"""Decision rules for StaxBot 2.4.0.

The Pine script staxbot_2_4_0.pine follows these rules. This file is the check
that can run here. It does not read market data and it does not place trades.
"""

from __future__ import annotations

from dataclasses import dataclass, field


def stop_price(anchor: float, direction: int, preset: str, tick: float, atr: float, tight_ticks: int, med_ticks: int, large_atr: float) -> float:
    if preset == "Tight":
        buffer = tight_ticks * tick
    elif preset == "Large":
        buffer = large_atr * atr
    else:
        buffer = med_ticks * tick
    return anchor - direction * buffer


def reference_risk(entry: float, anchor: float, direction: int, tick: float, med_ticks: int) -> float:
    medium = stop_price(anchor, direction, "Medium", tick, 0.0, 0, med_ticks, 0.0)
    return (entry - medium) * direction


def targets(entry: float, ref_risk: float, direction: int, tp_r: float) -> tuple[float, float, float]:
    return (
        entry + direction * tp_r * ref_risk,
        entry + direction * tp_r * 2.0 * ref_risk,
        entry + direction * tp_r * 3.0 * ref_risk,
    )


def geometry_ok(direction: int, stop: float, entry: float, tp1: float, tp2: float, tp3: float, ref_risk: float, tp_r: float) -> bool:
    if ref_risk <= 0:
        return False
    ordered = stop < entry < tp1 < tp2 < tp3 if direction == 1 else stop > entry > tp1 > tp2 > tp3
    r1 = abs(tp1 - entry) / ref_risk
    r2 = abs(tp2 - entry) / ref_risk
    r3 = abs(tp3 - entry) / ref_risk
    labels = abs(r1 - tp_r) < 1e-9 and abs(r2 - tp_r * 2) < 1e-9 and abs(r3 - tp_r * 3) < 1e-9
    return ordered and labels


def grade(disp: bool, bias: bool, volume: bool, session: bool, fifth: bool) -> str:
    score = int(disp) + int(bias) + int(volume) + int(session) + int(fifth)
    if score == 5:
        return "A+"
    if score == 4 and (disp or session):
        return "A"
    return "B"


def displaced(mode: str, body: float, atr: float, body_mult: float, beyond: float, zone_width: float) -> bool:
    if mode == "Close beyond zone by zone width":
        return beyond >= zone_width
    return body >= body_mult * atr


@dataclass
class Plan:
    plan_id: int
    move_id: int
    scenario: str
    origin: str
    direction: int
    entry: float
    anchor: float
    preset: str
    stop: float
    tp1: float
    tp2: float
    tp3: float
    ref_risk: float
    grade: str
    state: str
    shelf: float
    alerts: list[str] = field(default_factory=list)


@dataclass
class Book:
    plans: list[Plan] = field(default_factory=list)
    fills: int = 0
    next_id: int = 1
    next_move: int = 1
    reject: str = ""


def arm(book: Book, origin: str, direction: int, entry: float, anchor: float, shelf: float, preset: str, tick: float, atr: float, tight_ticks: int, med_ticks: int, large_atr: float, tp_r: float, fifth: bool, disp: bool, bias: bool, volume: bool, session: bool, min_grade: str) -> Plan | None:
    scenario = "B" if origin == "shelf" else "A"
    ref = reference_risk(entry, anchor, direction, tick, med_ticks)
    tp1, tp2, tp3 = targets(entry, ref, direction, tp_r)
    stop = stop_price(anchor, direction, preset, tick, atr, tight_ticks, med_ticks, large_atr)
    plan_id = book.next_id
    book.next_id += 1
    if not geometry_ok(direction, stop, entry, tp1, tp2, tp3, ref, tp_r):
        book.reject = "PLAN REJECTED: geometry."
        return None
    g = grade(disp, bias, volume, session, fifth)
    take = min_grade == "Off" or g == "A+" or (min_grade == "A" and g == "A")
    if not take:
        return None
    plan = Plan(plan_id, book.next_move - 1, scenario, origin, direction, entry, anchor, preset, stop, tp1, tp2, tp3, ref, g, "ARMED", shelf)
    plan.alerts.append("plan")
    book.plans.append(plan)
    book.reject = ""
    return plan


def _trades(price: float, low: float, high: float) -> bool:
    return low <= price <= high


def _wrong(direction: int, close: float, shelf: float) -> bool:
    return close < shelf if direction == 1 else close > shelf


def step_plan(plan: Plan, low: float, high: float, close: float, pause: bool, live_room: bool, expired: bool) -> str:
    """Return the event produced this bar. Empty string means the plan stayed armed."""
    if plan.state != "ARMED":
        return ""
    if _wrong(plan.direction, close, plan.shelf):
        plan.state = "INVALIDATED"
        plan.scenario = "E"
        plan.alerts.append("plan_cancel")
        return "invalidated"
    if _trades(plan.entry, low, high) and _trades(plan.stop, low, high):
        return "ambiguous"
    if not pause and live_room and _trades(plan.entry, low, high) and not _wrong(plan.direction, close, plan.shelf):
        plan.state = "TRIGGERED"
        plan.alerts.append("entry")
        return "fill"
    if _trades(plan.tp1, low, high) and not _trades(plan.entry, low, high):
        plan.state = "EXPIRED"
        plan.scenario = "D"
        plan.alerts.append("plan_cancel")
        return "ran"
    if expired:
        plan.state = "EXPIRED"
        plan.alerts.append("plan_cancel")
        return "expired"
    return ""


def on_fill(book: Book, filled: Plan) -> None:
    book.fills += 1
    filled.state = "LIVE"
    for other in book.plans:
        if other is not filled and other.move_id == filled.move_id and other.state == "ARMED":
            other.state = "CANCELLED"
            other.alerts.append("plan_cancel")


def test_stop_presets_share_the_leg_and_leave_targets() -> None:
    entry, anchor, tick, atr = 7750.0, 7764.0, 0.25, 8.0
    ref = reference_risk(entry, anchor, -1, tick, 2)
    tp = targets(entry, ref, -1, 1.0)
    for preset in ("Tight", "Medium", "Large"):
        stop = stop_price(anchor, -1, preset, tick, atr, 1, 2, 0.5)
        assert stop > anchor
        assert geometry_ok(-1, stop, entry, *tp, ref, 1.0)
        assert targets(entry, ref, -1, 1.0) == tp
    assert stop_price(anchor, -1, "Tight", tick, atr, 1, 2, 0.5) == 7764.25
    assert stop_price(anchor, -1, "Medium", tick, atr, 1, 2, 0.5) == 7764.5
    assert stop_price(anchor, -1, "Large", tick, atr, 1, 2, 0.5) == 7768.0


def test_wrong_side_stop_is_rejected() -> None:
    book = Book()
    # The 2.2 drawing: short entry 7764 with the stop at 7760.75, between entry and the targets.
    plan = arm(book, "shelf", -1, 7764.0, 7760.75, 7764.0, "Medium", 0.25, 4.0, 1, 2, 0.5, 1.0, True, True, True, True, True, "Off")
    assert plan is None
    assert book.reject == "PLAN REJECTED: geometry."
    assert book.fills == 0


def test_shelf_arms_without_a_gap_and_a_gap_is_extra() -> None:
    book = Book()
    book.next_move = 3
    shelf = arm(book, "shelf", -1, 7750.0, 7764.0, 7750.0, "Medium", 0.25, 8.0, 1, 2, 0.5, 1.0, True, True, True, False, False, "Off")
    gap = arm(book, "gap", -1, 7758.0, 7764.0, 7750.0, "Medium", 0.25, 8.0, 1, 2, 0.5, 1.0, True, True, True, False, False, "Off")
    assert shelf is not None and gap is not None
    assert shelf.scenario == "B" and gap.scenario == "A"
    assert shelf.stop == gap.stop
    assert shelf.entry != gap.entry
    assert book.fills == 0


def test_ran_through_target_does_not_count() -> None:
    book = Book()
    book.next_move = 1
    plan = arm(book, "shelf", -1, 7764.0, 7780.0, 7764.0, "Medium", 0.25, 8.0, 1, 2, 0.5, 1.0, False, True, False, False, False, "Off")
    assert plan is not None
    event = step_plan(plan, low=plan.tp1 - 1, high=plan.entry - 1, close=plan.tp1 - 1, pause=False, live_room=True, expired=False)
    assert event == "ran"
    assert plan.scenario == "D"
    assert "entry" not in plan.alerts
    assert book.fills == 0


def test_fill_order_and_sibling_cancel() -> None:
    book = Book()
    book.next_move = 4
    shelf = arm(book, "shelf", -1, 7750.0, 7764.0, 7750.0, "Medium", 0.25, 8.0, 1, 2, 0.5, 1.0, True, True, True, True, True, "A")
    gap = arm(book, "gap", -1, 7758.0, 7764.0, 7750.0, "Medium", 0.25, 8.0, 1, 2, 0.5, 1.0, True, True, True, True, True, "A")
    assert shelf is not None and gap is not None
    # Reclaim of the shelf invalidates before any fill.
    assert step_plan(shelf, low=7740, high=7760, close=7752, pause=False, live_room=True, expired=False) == "invalidated"
    assert shelf.scenario == "E" and "entry" not in shelf.alerts
    # A bar through the gap entry and the stop does not fill.
    assert step_plan(gap, low=min(gap.entry, gap.stop) - 1, high=max(gap.entry, gap.stop) + 1, close=shelf.shelf - 1, pause=False, live_room=True, expired=False) == "ambiguous"
    assert gap.state == "ARMED" and book.fills == 0
    # Overlap of the gap entry, close still through the shelf, fills. Sibling of this move is already invalidated.
    fresh = arm(book, "gap", -1, 7758.0, 7764.0, 7750.0, "Medium", 0.25, 8.0, 1, 2, 0.5, 1.0, True, True, True, True, True, "Off")
    other = arm(book, "shelf", -1, 7750.0, 7764.0, 7750.0, "Medium", 0.25, 8.0, 1, 2, 0.5, 1.0, True, True, True, True, True, "Off")
    assert fresh is not None and other is not None
    assert step_plan(fresh, low=7756, high=7760, close=7748, pause=False, live_room=True, expired=False) == "fill"
    on_fill(book, fresh)
    assert book.fills == 1
    assert other.state == "CANCELLED"
    assert "entry" not in other.alerts


def test_pause_blocks_the_fill() -> None:
    book = Book()
    plan = arm(book, "shelf", -1, 7750.0, 7764.0, 7750.0, "Medium", 0.25, 8.0, 1, 2, 0.5, 1.0, True, True, False, False, False, "Off")
    assert plan is not None
    assert step_plan(plan, low=7748, high=7751, close=7749, pause=True, live_room=True, expired=False) == ""
    assert plan.state == "ARMED" and book.fills == 0


def test_displacement_uses_one_definition() -> None:
    assert displaced("Body >= ATR x", body=8, atr=4, body_mult=1, beyond=0, zone_width=10) is True
    assert displaced("Body >= ATR x", body=3, atr=4, body_mult=1, beyond=99, zone_width=1) is False
    assert displaced("Close beyond zone by zone width", body=0, atr=4, body_mult=1, beyond=10, zone_width=10) is True
    assert displaced("Close beyond zone by zone width", body=99, atr=1, body_mult=0, beyond=9, zone_width=10) is False


def test_grade_fifth_flag_changes_the_letter() -> None:
    assert grade(True, True, True, True, True) == "A+"
    assert grade(True, True, True, True, False) == "A"
    assert grade(False, True, True, True, False) == "B"


if __name__ == "__main__":
    test_stop_presets_share_the_leg_and_leave_targets()
    test_wrong_side_stop_is_rejected()
    test_shelf_arms_without_a_gap_and_a_gap_is_extra()
    test_ran_through_target_does_not_count()
    test_fill_order_and_sibling_cancel()
    test_pause_blocks_the_fill()
    test_displacement_uses_one_definition()
    test_grade_fifth_flag_changes_the_letter()
    print("engine rules ok")
