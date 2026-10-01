"""Decision rules for StaxBot 2.5.0.

The Pine script staxbot_2_5_0.pine follows these rules. This file is the check
that can run here. It does not read market data and it does not place trades.
"""

from __future__ import annotations

from dataclasses import dataclass, field


def stop_price(anchor: float, direction: int, preset: str, atr: float, tight_atr: float, med_atr: float, large_atr: float) -> float:
    if preset == "Tight":
        buffer = tight_atr * atr
    elif preset == "Large":
        buffer = large_atr * atr
    else:
        buffer = med_atr * atr
    return anchor - direction * buffer


def reference_risk(entry: float, med_anchor: float, direction: int, atr: float, med_atr: float) -> float:
    medium = stop_price(med_anchor, direction, "Medium", atr, 0.0, med_atr, 0.0)
    return (entry - medium) * direction


def order_stops(entry: float, direction: int, tight: float, medium: float, large: float) -> tuple[float, float, float]:
    """Tight stays at or inside Medium. Large stays at or beyond Medium."""
    d_m = (entry - medium) * direction
    d_t = (entry - tight) * direction
    d_l = (entry - large) * direction
    if d_m != d_m:  # NaN
        return tight, medium, large
    d_t2 = d_m if d_t != d_t or d_t > d_m or d_t <= 0 else d_t
    d_l2 = d_m if d_l != d_l or d_l < d_m else d_l
    return entry - direction * d_t2, medium, entry - direction * d_l2


def medium_distance_ok(distance: float, atr: float, min_atr: float, max_atr: float) -> bool:
    if distance < min_atr * atr:
        return False
    if max_atr > 0 and distance > max_atr * atr:
        return False
    return True


def scenario_allowed(shelf_on: bool, gap_on: bool, origin: str) -> bool:
    if origin == "shelf":
        return shelf_on
    if origin == "gap":
        return gap_on
    return False


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


def strong_displacement(body: float, atr: float, mult: float = 1.5) -> bool:
    return body >= mult * atr


def break_arms(bars_since_cross: int, window: int, close: float, shelf: float, direction: int, displaced_bar: bool) -> bool:
    beyond = close > shelf if direction == 1 else close < shelf
    return 0 <= bars_since_cross < window and beyond and displaced_bar


def stop_hit(direction: int, low: float, high: float, px: float) -> bool:
    return low <= px if direction == 1 else high >= px


def stop_exit_price(direction: int, bar_open: float, stop: float) -> float:
    return min(bar_open, stop) if direction == 1 else max(bar_open, stop)


def target_hit(direction: int, low: float, high: float, px: float) -> bool:
    return high >= px if direction == 1 else low <= px


def session_count_reset(open_min_ct: int, prev_open_min_ct: int | None) -> bool:
    return open_min_ct >= 17 * 60 and (prev_open_min_ct is None or prev_open_min_ct < 17 * 60)


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
    inv: float = 0.0
    alerts: list[str] = field(default_factory=list)


@dataclass
class Book:
    plans: list[Plan] = field(default_factory=list)
    fills: int = 0
    next_id: int = 1
    next_move: int = 1
    reject: str = ""


def arm(book: Book, origin: str, direction: int, entry: float, anchor: float, shelf: float, preset: str, atr: float, tight_atr: float, med_atr: float, large_atr: float, tp_r: float, fifth: bool, disp: bool, bias: bool, volume: bool, session: bool, min_grade: str, tight_anchor: float | None = None, med_anchor: float | None = None, min_stop_atr: float = 0.5, max_stop_atr: float = 3.0) -> Plan | None:
    scenario = "B" if origin == "shelf" else "A"
    tight_px = anchor if tight_anchor is None else tight_anchor
    med_px = anchor if med_anchor is None else med_anchor
    plan_id = book.next_id
    book.next_id += 1
    if med_px is None:
        book.reject = "PLAN REJECTED: geometry."
        return None
    medium = stop_price(med_px, direction, "Medium", atr, tight_atr, med_atr, large_atr)
    raw_tight = stop_price(tight_px, direction, "Tight", atr, tight_atr, med_atr, large_atr)
    raw_large = stop_price(anchor, direction, "Large", atr, tight_atr, med_atr, large_atr)
    tight, medium, large = order_stops(entry, direction, raw_tight, medium, raw_large)
    ref = (entry - medium) * direction
    tp1, tp2, tp3 = targets(entry, ref, direction, tp_r)
    stop = tight if preset == "Tight" else large if preset == "Large" else medium
    if not geometry_ok(direction, medium, entry, tp1, tp2, tp3, ref, tp_r):
        book.reject = "PLAN REJECTED: geometry."
        return None
    if not medium_distance_ok(ref, atr, min_stop_atr, max_stop_atr):
        book.reject = "PLAN REJECTED: stop distance."
        return None
    min_dist = min_stop_atr * atr
    if (entry - stop) * direction < min_dist:
        stop = entry - direction * min_dist
    g = grade(disp, bias, volume, session, fifth)
    take = min_grade == "Off" or g == "A+" or (min_grade == "A" and g == "A")
    if not take:
        return None
    inv = tight_px if origin == "gap" else shelf
    plan = Plan(plan_id, book.next_move - 1, scenario, origin, direction, entry, stop, preset, stop, tp1, tp2, tp3, ref, g, "ARMED", shelf, inv)
    plan.alerts.append("plan")
    book.plans.append(plan)
    book.reject = ""
    return plan


def _trades(price: float, low: float, high: float) -> bool:
    return low <= price <= high


def zone_hit(direction: int, shelf: float, low: float, high: float, tol: float) -> bool:
    if direction == 1:
        return low <= shelf and high >= shelf - tol
    return high >= shelf and low <= shelf + tol


def entry_traded(plan: Plan, low: float, high: float, tol: float) -> bool:
    if plan.origin == "shelf":
        return zone_hit(plan.direction, plan.shelf, low, high, tol)
    return _trades(plan.entry, low, high)


def _wrong(direction: int, close: float, shelf: float, tol: float = 0.0) -> bool:
    return shelf - close >= tol if direction == 1 else close - shelf >= tol


def step_plan(plan: Plan, low: float, high: float, close: float, pause: bool, live_room: bool, expired: bool, tol: float = 0.0) -> str:
    """Return the event produced this bar. Empty string means the plan stayed armed."""
    if plan.state != "ARMED":
        return ""
    level = plan.inv
    if _wrong(plan.direction, close, level, tol):
        plan.state = "INVALIDATED"
        plan.alerts.append("plan_cancel")
        return "invalidated"
    touched = entry_traded(plan, low, high, tol)
    if touched and _trades(plan.stop, low, high):
        return "ambiguous"
    if not pause and live_room and touched and not _wrong(plan.direction, close, level, tol):
        plan.state = "TRIGGERED"
        plan.alerts.append("entry")
        return "fill"
    if target_hit(plan.direction, low, high, plan.tp1) and not touched:
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


def test_stop_presets_move_only_the_stop() -> None:
    # Short shelf. Entry 7750. Break-candle high 7756. Displacement-candle high 7764. Leg extreme 7780.
    entry, atr = 7750.0, 8.0
    tight_px, med_px, leg_px = 7756.0, 7764.0, 7780.0
    ref = reference_risk(entry, med_px, -1, atr, 0.1)
    tp = targets(entry, ref, -1, 1.0)
    stops = {}
    for preset in ("Tight", "Medium", "Large"):
        book = Book()
        plan = arm(book, "shelf", -1, entry, leg_px, entry, preset, atr, 0.05, 0.1, 0.25, 1.0, True, True, True, True, True, "Off", tight_anchor=tight_px, med_anchor=med_px)
        assert plan is not None
        assert (plan.tp1, plan.tp2, plan.tp3) == tp
        assert plan.entry == entry
        assert geometry_ok(-1, plan.stop, entry, *tp, ref, 1.0)
        stops[preset] = plan.stop
    assert stops["Tight"] == 7756.4
    assert stops["Medium"] == 7764.8
    assert stops["Large"] == 7782.0
    assert stops["Tight"] <= stops["Medium"] <= stops["Large"]
    # A tight anchor past the medium candle is pulled back to the medium stop.
    clamped = arm(Book(), "shelf", -1, entry, leg_px, entry, "Tight", atr, 0.05, 0.1, 0.25, 1.0, True, True, True, True, True, "Off", tight_anchor=7770.0, med_anchor=med_px)
    assert clamped is not None and clamped.stop == stops["Medium"]
    # Gap tight uses the far edge. Targets stay on the medium candle.
    gap_entry, far_edge = 7752.0, 7758.0
    gap_ref = reference_risk(gap_entry, med_px, -1, atr, 0.1)
    gap = arm(Book(), "gap", -1, gap_entry, leg_px, entry, "Tight", atr, 0.05, 0.1, 0.25, 1.0, True, True, True, True, True, "Off", tight_anchor=far_edge, med_anchor=med_px)
    assert gap is not None
    assert gap.stop == far_edge + 0.05 * atr
    assert gap.tp1 == targets(gap_entry, gap_ref, -1, 1.0)[0]


def test_selected_stop_widens_to_the_minimum() -> None:
    entry, atr = 7750.0, 8.0
    plan = arm(Book(), "shelf", -1, entry, 7780.0, entry, "Tight", atr, 0.05, 0.1, 0.25, 1.0, True, True, True, True, True, "Off", tight_anchor=7751.0, med_anchor=7760.0)
    assert plan is not None
    assert plan.stop == entry + 0.5 * atr
    for preset in ("Tight", "Medium", "Large"):
        book = Book()
        got = arm(book, "shelf", -1, entry, 7780.0, entry, preset, atr, 0.05, 0.1, 0.25, 1.0, True, True, True, True, True, "Off", tight_anchor=7751.0, med_anchor=7752.0)
        assert got is None
        assert book.reject == "PLAN REJECTED: stop distance."


def test_gap_invalidates_beyond_the_far_edge() -> None:
    atr = 8.0
    tol = 0.25 * atr
    gap = arm(Book(), "gap", -1, 30899.75, 30940.0, 30870.0, "Medium", atr, 0.05, 0.1, 0.25, 1.0, True, True, True, True, True, "Off", tight_anchor=30910.0, med_anchor=30920.0)
    assert gap is not None and gap.inv == 30910.0
    # Close is back through the shelf at 30870 and short of the far edge.
    assert step_plan(gap, low=30879, high=30890, close=30885, pause=False, live_room=True, expired=False, tol=tol) == ""
    assert step_plan(gap, low=30900, high=30920, close=30910.0 + tol, pause=False, live_room=True, expired=False, tol=tol) == "invalidated"
    assert gap.scenario == "A"


def test_allowed_scenarios() -> None:
    assert scenario_allowed(True, True, "shelf") and scenario_allowed(True, True, "gap")
    assert scenario_allowed(False, True, "gap") and not scenario_allowed(False, True, "shelf")
    assert scenario_allowed(True, False, "shelf") and not scenario_allowed(True, False, "gap")


def test_medium_distance_gates_every_preset() -> None:
    entry, atr = 7750.0, 8.0
    for preset in ("Tight", "Medium", "Large"):
        book = Book()
        plan = arm(book, "shelf", -1, entry, 7790.0, entry, preset, atr, 0.05, 0.1, 0.5, 1.0, True, True, True, True, True, "Off", tight_anchor=7756.0, med_anchor=7790.0, max_stop_atr=3.0)
        assert plan is None
        assert book.reject == "PLAN REJECTED: stop distance."


def test_shelf_zone_fills_inside_the_tolerance() -> None:
    atr = 8.0
    book = Book()
    shelf = arm(book, "shelf", -1, 7750.0, 7764.0, 7750.0, "Medium", atr, 0.05, 0.1, 0.5, 1.0, True, True, True, True, True, "Off")
    assert shelf is not None
    tol = 0.25 * atr
    # The bar never prints 7750. It trades 7751, inside the shelf-to-tolerance zone.
    assert step_plan(shelf, low=7751, high=7752, close=7751, pause=False, live_room=True, expired=False, tol=tol) == "fill"
    gap = arm(Book(), "gap", -1, 7750.0, 7764.0, 7750.0, "Medium", atr, 0.05, 0.1, 0.5, 1.0, True, True, True, True, True, "Off")
    assert gap is not None
    assert step_plan(gap, low=7751, high=7752, close=7751, pause=False, live_room=True, expired=False, tol=tol) == ""


def test_wrong_side_stop_is_rejected() -> None:
    book = Book()
    # The 2.2 drawing: short entry 7764 with the stop at 7760.75, between entry and the targets.
    plan = arm(book, "shelf", -1, 7764.0, 7760.75, 7764.0, "Medium", 4.0, 0.05, 0.1, 0.5, 1.0, True, True, True, True, True, "Off")
    assert plan is None
    assert book.reject == "PLAN REJECTED: geometry."
    assert book.fills == 0


def test_shelf_arms_without_a_gap_and_a_gap_is_extra() -> None:
    book = Book()
    book.next_move = 3
    shelf = arm(book, "shelf", -1, 7750.0, 7764.0, 7750.0, "Medium", 8.0, 0.05, 0.1, 0.5, 1.0, True, True, True, False, False, "Off")
    gap = arm(book, "gap", -1, 7758.0, 7764.0, 7750.0, "Medium", 8.0, 0.05, 0.1, 0.5, 1.0, True, True, True, False, False, "Off")
    assert shelf is not None and gap is not None
    assert shelf.scenario == "B" and gap.scenario == "A"
    assert shelf.stop == gap.stop
    assert shelf.entry != gap.entry
    assert book.fills == 0


def test_ran_through_target_does_not_count() -> None:
    book = Book()
    book.next_move = 1
    plan = arm(book, "shelf", -1, 7764.0, 7780.0, 7764.0, "Medium", 8.0, 0.05, 0.1, 0.5, 1.0, False, True, False, False, False, "Off")
    assert plan is not None
    event = step_plan(plan, low=plan.tp1 - 1, high=plan.entry - 1, close=plan.tp1 - 1, pause=False, live_room=True, expired=False)
    assert event == "ran"
    assert plan.scenario == "D"
    assert "entry" not in plan.alerts
    assert book.fills == 0
    beyond = arm(book, "shelf", -1, 7764.0, 7780.0, 7764.0, "Medium", 8.0, 0.05, 0.1, 0.5, 1.0, False, True, False, False, False, "Off")
    assert beyond is not None
    # The whole bar is past TP1, so the target is not inside the bar. The one-sided test still expires it.
    assert step_plan(beyond, low=beyond.tp1 - 5, high=beyond.tp1 - 1, close=beyond.tp1 - 2, pause=False, live_room=True, expired=False) == "ran"


def test_fill_order_and_sibling_cancel() -> None:
    book = Book()
    book.next_move = 4
    shelf = arm(book, "shelf", -1, 7750.0, 7764.0, 7750.0, "Medium", 8.0, 0.05, 0.1, 0.5, 1.0, True, True, True, True, True, "A")
    gap = arm(book, "gap", -1, 7758.0, 7764.0, 7750.0, "Medium", 8.0, 0.05, 0.1, 0.5, 1.0, True, True, True, True, True, "A")
    assert shelf is not None and gap is not None
    # Reclaim of the shelf invalidates before any fill.
    assert step_plan(shelf, low=7740, high=7760, close=7752, pause=False, live_room=True, expired=False) == "invalidated"
    assert shelf.scenario == "B" and "entry" not in shelf.alerts
    # A bar through the gap entry and the stop does not fill.
    assert step_plan(gap, low=min(gap.entry, gap.stop) - 1, high=max(gap.entry, gap.stop) + 1, close=shelf.shelf - 1, pause=False, live_room=True, expired=False) == "ambiguous"
    assert gap.state == "ARMED" and book.fills == 0
    # Overlap of the gap entry, close still through the shelf, fills. Sibling of this move is already invalidated.
    fresh = arm(book, "gap", -1, 7758.0, 7764.0, 7750.0, "Medium", 8.0, 0.05, 0.1, 0.5, 1.0, True, True, True, True, True, "Off")
    other = arm(book, "shelf", -1, 7750.0, 7764.0, 7750.0, "Medium", 8.0, 0.05, 0.1, 0.5, 1.0, True, True, True, True, True, "Off")
    assert fresh is not None and other is not None
    assert step_plan(fresh, low=7756, high=7760, close=7748, pause=False, live_room=True, expired=False) == "fill"
    on_fill(book, fresh)
    assert book.fills == 1
    assert other.state == "CANCELLED"
    assert "entry" not in other.alerts


def test_pause_blocks_the_fill() -> None:
    book = Book()
    plan = arm(book, "shelf", -1, 7750.0, 7764.0, 7750.0, "Medium", 8.0, 0.05, 0.1, 0.5, 1.0, True, True, False, False, False, "Off")
    assert plan is not None
    assert step_plan(plan, low=7748, high=7751, close=7749, pause=True, live_room=True, expired=False) == ""
    assert plan.state == "ARMED" and book.fills == 0


def test_displacement_uses_one_definition() -> None:
    assert displaced("Body >= ATR x", body=8, atr=4, body_mult=1, beyond=0, zone_width=10) is True
    assert displaced("Body >= ATR x", body=3, atr=4, body_mult=1, beyond=99, zone_width=1) is False
    assert displaced("Close beyond zone by zone width", body=0, atr=4, body_mult=1, beyond=10, zone_width=10) is True
    assert displaced("Close beyond zone by zone width", body=99, atr=1, body_mult=0, beyond=9, zone_width=10) is False


def test_one_sided_exits_and_reclaim_tolerance() -> None:
    assert stop_hit(-1, 7740, 7764, 7764) is True
    assert stop_hit(-1, 7740, 7763.75, 7764) is False
    assert target_hit(-1, 7690, 7695, 7700) is True
    assert stop_hit(1, 7700, 7760, 7700) is True
    assert target_hit(1, 7740, 7800, 7800) is True
    assert _wrong(-1, 7750.25, 7750, 0.5) is False
    assert _wrong(-1, 7750.5, 7750, 0.5) is True
    assert _wrong(1, 7749.75, 7750, 0.5) is False
    assert _wrong(1, 7749.5, 7750, 0.5) is True


def test_break_window_and_strong_flag() -> None:
    assert break_arms(0, 3, 7748, 7750, -1, False) is False
    assert break_arms(0, 3, 7748, 7750, -1, True) is True
    assert break_arms(1, 3, 7748, 7750, -1, True) is True
    assert break_arms(3, 3, 7748, 7750, -1, True) is False
    assert displaced("Body >= ATR x", body=5, atr=4, body_mult=1, beyond=0, zone_width=10) is True
    assert strong_displacement(5, 4, 1.5) is False
    assert grade(False, True, True, True, True) == "A"


def test_gap_through_stop_uses_the_open() -> None:
    assert stop_exit_price(-1, 7770.0, 7764.0) == 7770.0
    assert stop_exit_price(-1, 7760.0, 7764.0) == 7764.0
    assert stop_exit_price(1, 7690.0, 7700.0) == 7690.0
    assert stop_exit_price(1, 7710.0, 7700.0) == 7700.0


def test_count_resets_at_chicago_open() -> None:
    assert session_count_reset(17 * 60, 16 * 60 + 55) is True
    assert session_count_reset(0, 23 * 60 + 55) is False
    assert session_count_reset(18 * 60, 17 * 60) is False


def test_grade_fifth_flag_changes_the_letter() -> None:
    assert grade(True, True, True, True, True) == "A+"
    assert grade(True, True, True, True, False) == "A"
    assert grade(False, True, True, True, False) == "B"


if __name__ == "__main__":
    test_stop_presets_move_only_the_stop()
    test_selected_stop_widens_to_the_minimum()
    test_gap_invalidates_beyond_the_far_edge()
    test_allowed_scenarios()
    test_medium_distance_gates_every_preset()
    test_shelf_zone_fills_inside_the_tolerance()
    test_wrong_side_stop_is_rejected()
    test_shelf_arms_without_a_gap_and_a_gap_is_extra()
    test_ran_through_target_does_not_count()
    test_fill_order_and_sibling_cancel()
    test_pause_blocks_the_fill()
    test_displacement_uses_one_definition()
    test_one_sided_exits_and_reclaim_tolerance()
    test_break_window_and_strong_flag()
    test_gap_through_stop_uses_the_open()
    test_count_resets_at_chicago_open()
    test_grade_fifth_flag_changes_the_letter()
    print("engine rules ok")
