"""Stay rules the schema and the hotel's policies already describe.

Booking and payment policies stay in the booking_policies documents. Operating times and the optional late-departure fee stay in
hotel settings. This module reads those and writes the columns that record the outcome: penalty_amount, late_fee_amount, and the
desk refunds a penalty produces. Availability, allotment release and the folio mirror live next to the tables they change.
"""

from datetime import datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal
from zoneinfo import ZoneInfo

from booking_system.guest_manager import Refused

CENT = Decimal("0.01")
POLICIES = ("leon_hotel_memory", "booking_policies")


def _money(value):
    return Decimal(str(value)).quantize(CENT, rounding=ROUND_HALF_UP)


def _whole(value):
    """A non-negative whole number, or None when the policy did not store one."""
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, int):
        return value if value >= 0 else None
    if isinstance(value, float) and value.is_integer():
        return int(value) if value >= 0 else None
    if isinstance(value, Decimal) and value == value.to_integral_value():
        number = int(value)
        return number if number >= 0 else None
    text = str(value).strip()
    if text.isdigit():
        return int(text)
    return None


def _clock(value):
    return datetime.strptime(str(value or "").strip(), "%H:%M").time()


def policy_details(cur, hotel_id, policy_type):
    """The active policy's details on this cursor, or None when the hotel has no such document."""
    cur.execute(
        """SELECT body->'details' AS details FROM content_document
           WHERE database_name = %s AND collection_name = %s
             AND body->>'hotel_id' = %s AND body->>'policy_type' = %s AND body->>'status' = 'active'
           LIMIT 1""",
        (POLICIES[0], POLICIES[1], hotel_id, policy_type),
    )
    found = cur.fetchone()
    details = found["details"] if found else None
    return details if isinstance(details, dict) else None


def penalty_total(night_rates, penalty_nights):
    """Sum of each stay date's sell rates, for the first penalty_nights dates only."""
    count = _whole(penalty_nights)
    if not count:
        return Decimal("0.00")
    return sum((_money(rate) for rate in list(night_rates)[:count]), Decimal("0.00"))


def cancel_is_free(arrival, now, window_hours):
    """True when check-in is still at least window_hours away."""
    return arrival - now >= timedelta(hours=int(window_hours))


def refund_target(status, free, paid, penalty, refund_percent):
    """What to refund: a free cancel returns everything paid; a charged cancel returns refund_percent of it; a no-show returns
    only the amount paid above the penalty."""
    paid = _money(paid)
    if status == "cancelled" and free:
        return paid
    if status == "cancelled":
        return _money(paid * _money(refund_percent) / Decimal(100))
    return max(Decimal("0.00"), _money(paid) - _money(penalty))


def deposit_is_short(today, check_in, window_days, paid, due):
    """The deposit is due from window_days before arrival, and short when paid is below it."""
    return today >= check_in - timedelta(days=int(window_days)) and _money(paid) < _money(due)


def before_checkin_time(now_local, arrival_date, opens):
    """On the arrival date, local time before the check-in clock."""
    return now_local.date() == arrival_date and now_local.time() < opens


def departure_is_late(now_local, check_out_date, checkout_time):
    """After the departure date, or after the check-out clock on that date."""
    if now_local.date() > check_out_date:
        return True
    if now_local.date() < check_out_date:
        return False
    return now_local.time() > checkout_time


def _log(cur, hotel_id, booking_id, user_id, field, old, new, reason):
    from booking_system.hotel_registry import new_id
    cur.execute(
        """INSERT INTO booking_modification_log (modification_id, hotel_id, booking_id, modified_by_user_id, field_changed, old_value,
                                                 new_value, change_reason)
           VALUES (%s, %s, %s, %s, %s, %s, %s, %s)""",
        (new_id("bkm"), hotel_id, booking_id, user_id, field, None if old is None else str(old), None if new is None else str(new), reason),
    )


def _night_rates(cur, booking_id):
    cur.execute(
        """SELECT stay_date, SUM(sell_rate) AS night_rate FROM booking_room_night
           WHERE booking_id = %s GROUP BY stay_date ORDER BY stay_date""",
        (booking_id,),
    )
    return [row["night_rate"] for row in cur.fetchall()]


def _close_plan(details, status):
    """The numbers a cancel or no-show needs from the booking policy, or None when that block is not stored."""
    if status == "cancelled":
        block = details.get("cancellation_policy") if isinstance(details.get("cancellation_policy"), dict) else {}
        window, nights, percent = _whole(block.get("window_hours")), _whole(block.get("penalty_nights")), _whole(block.get("refund_percent"))
        if window is None or nights is None or percent is None:
            return None
        return {"penalty_nights": nights, "window_hours": window, "refund_percent": percent}
    if status == "no_show":
        block = details.get("no_show_policy") if isinstance(details.get("no_show_policy"), dict) else {}
        nights = _whole(block.get("penalty_nights"))
        if nights is None:
            return None
        return {"penalty_nights": nights, "window_hours": None, "refund_percent": None}
    return None


def refund_up_to(cur, hotel_id, user_id, booking_id, target):
    """Refund up to target against received payments, newest first, never past what each payment still has left."""
    from booking_system.payment_manager import _insert
    left = _money(target)
    if left <= 0:
        return Decimal("0.00")
    cur.execute(
        """SELECT p.payment_id, p.currency, p.payment_method,
                  p.amount - COALESCE((SELECT SUM(r.amount) FROM payment r
                                       WHERE r.booking_id = p.booking_id AND r.payment_kind = 'refund'
                                         AND r.payment_status = 'received' AND r.reference = p.payment_id), 0) AS remaining
           FROM payment p
           WHERE p.hotel_id = %s AND p.booking_id = %s AND p.payment_kind = 'payment' AND p.payment_status = 'received'
           ORDER BY p.paid_at DESC, p.payment_id DESC""",
        (hotel_id, booking_id),
    )
    done = Decimal("0.00")
    for row in cur.fetchall():
        piece = min(_money(row["remaining"]), left)
        if piece <= 0:
            continue
        _insert(cur, hotel_id, user_id, booking_id, "refund", piece, row["currency"], row["payment_method"], row["payment_id"])
        done += piece
        left -= piece
        if left <= 0:
            break
    return done


def settle_close(cur, hotel_id, user_id, booking_id, status, reason):
    """Set penalty_amount from the booking policy and refund what that outcome returns. None when the hotel has no booking policy,
    so a cancel keeps the previous behaviour and historical balances stay on gross_amount."""
    details = policy_details(cur, hotel_id, "booking")
    plan = _close_plan(details, status) if details else None
    if not plan:
        return None
    cur.execute("SELECT timezone FROM hotel WHERE hotel_id = %s", (hotel_id,))
    zone = ZoneInfo(cur.fetchone()["timezone"])
    cur.execute("SELECT MIN(stay_date) AS check_in FROM booking_room_night WHERE booking_id = %s", (booking_id,))
    check_in = cur.fetchone()["check_in"]
    free = False
    if plan["window_hours"] is not None and check_in is not None:
        cur.execute("SELECT hotel_setting_required(%s, 'front_desk.checkin_time', %s) #>> '{}' AS opens", (hotel_id, check_in))
        arrival = datetime.combine(check_in, _clock(cur.fetchone()["opens"]), tzinfo=zone)
        free = cancel_is_free(arrival, datetime.now(zone), plan["window_hours"])
    penalty = Decimal("0.00") if free else penalty_total(_night_rates(cur, booking_id), plan["penalty_nights"])
    cur.execute("SELECT penalty_amount FROM booking WHERE booking_id = %s", (booking_id,))
    previous = cur.fetchone()["penalty_amount"]
    cur.execute("UPDATE booking SET penalty_amount = %s WHERE booking_id = %s", (penalty, booking_id))
    _log(cur, hotel_id, booking_id, user_id, "penalty_amount", previous, penalty, reason)
    cur.execute("SELECT amount_paid FROM booking_balance WHERE booking_id = %s", (booking_id,))
    paid = cur.fetchone()["amount_paid"]
    refunded = refund_up_to(cur, hotel_id, user_id, booking_id, refund_target(status, free, paid, penalty, plan["refund_percent"]))
    return {"penalty_amount": float(penalty), "refunded": float(refunded), "cancellation_free": free}


def deposit_refusal(cur, hotel_id, today, booking):
    """A sentence naming the deposit still due, or '' when no payment policy applies or the deposit is met."""
    details = policy_details(cur, hotel_id, "payment")
    if not details or details.get("deadline_policy") != "before_check_in":
        return ""
    percent, window = details.get("payment_threshold_percent"), _whole(details.get("payment_window_days"))
    if window is None or isinstance(percent, bool) or percent is None:
        return ""
    try:
        rate = _money(percent)
    except Exception:
        return ""
    gross = Decimal(booking["gross_amount"] or 0)
    due = _money(gross * rate / Decimal(100))
    if due <= 0 or booking.get("check_in_date") is None:
        return ""
    cur.execute("SELECT amount_paid, currency FROM booking_balance WHERE booking_id = %s", (booking["booking_id"],))
    balance = cur.fetchone()
    paid = Decimal(balance["amount_paid"] or 0)
    if not deposit_is_short(today, booking["check_in_date"], window, paid, due):
        return ""
    currency = str(balance["currency"] or booking.get("currency") or "").strip()
    return "Deposit due " + currency + " " + format(due, ".2f") + "; paid " + format(paid, ".2f")


def checkin_clock_refusal(cur, hotel_id, zone, today, check_in_date):
    """'' or the sentence that names the check-in time, on the arrival date before that time."""
    if today != check_in_date:
        return ""
    cur.execute("SELECT hotel_setting_required(%s, 'front_desk.checkin_time', %s) #>> '{}' AS opens", (hotel_id, today))
    opens = _clock(cur.fetchone()["opens"])
    if before_checkin_time(datetime.now(ZoneInfo(zone)), check_in_date, opens):
        return "Check-in opens at " + opens.strftime("%H:%M")
    return ""


def assert_stay_length(cur, hotel_id, room_type_id, nights):
    """Refuse a closed arrival, a stay shorter than the arrival night's min_stay, or a stay longer than the booking policy allows."""
    if not nights:
        raise Refused("stay nights are required")
    arrival = nights[0]
    cur.execute(
        """SELECT n.min_stay, n.closed_to_arrival, t.room_type_name
           FROM room_inventory_nightly n JOIN room_type t ON t.room_type_id = n.room_type_id
           WHERE n.room_type_id = %s AND n.stay_date = %s""",
        (room_type_id, arrival),
    )
    row = cur.fetchone()
    label = row["room_type_name"] if row else "This room"
    if row and row["closed_to_arrival"]:
        raise Refused(label + " is closed to arrival on " + arrival.isoformat())
    if row and row["min_stay"] is not None and len(nights) < int(row["min_stay"]):
        raise Refused(label + " requires at least " + str(int(row["min_stay"])) + " nights from " + arrival.isoformat())
    details = policy_details(cur, hotel_id, "booking")
    cap = _whole(details.get("max_stay_nights")) if details else None
    if cap is not None and len(nights) > cap:
        raise Refused("A stay can be at most " + str(cap) + " nights")


def apply_late_fee(cur, hotel_id, user_id, booking_id):
    """Set late_fee_amount once when departure is after the check-out clock and the hotel has a fee above zero.
    Returns {'applied', 'late_fee_amount'}. A missing fee setting leaves the booking unchanged."""
    cur.execute("SELECT late_fee_amount FROM booking WHERE hotel_id = %s AND booking_id = %s FOR UPDATE", (hotel_id, booking_id))
    booking = cur.fetchone()
    if not booking:
        return {"applied": False}
    if booking["late_fee_amount"] is not None:
        return {"applied": False, "late_fee_amount": float(booking["late_fee_amount"])}
    cur.execute("SELECT 1 AS held FROM booking_room_night WHERE booking_id = %s AND night_status = 'checked_in' LIMIT 1", (booking_id,))
    if not cur.fetchone():
        return {"applied": False}
    cur.execute("SELECT check_out_date FROM booking_summary WHERE booking_id = %s", (booking_id,))
    summary = cur.fetchone()
    if not summary or summary["check_out_date"] is None:
        return {"applied": False}
    cur.execute("SELECT timezone FROM hotel WHERE hotel_id = %s", (hotel_id,))
    zone = ZoneInfo(cur.fetchone()["timezone"])
    now = datetime.now(zone)
    cur.execute("SELECT hotel_setting_required(%s, 'front_desk.checkout_time', %s) #>> '{}' AS closes", (hotel_id, now.date()))
    closes = _clock(cur.fetchone()["closes"])
    if not departure_is_late(now, summary["check_out_date"], closes):
        return {"applied": False}
    cur.execute("SELECT hotel_setting_at(%s, 'front_desk.late_checkout_fee', %s) #>> '{}' AS fee", (hotel_id, now.date()))
    raw = cur.fetchone()["fee"]
    if raw is None:
        return {"applied": False}
    fee = _money(raw)
    if fee <= 0:
        return {"applied": False}
    cur.execute("UPDATE booking SET late_fee_amount = %s WHERE booking_id = %s", (fee, booking_id))
    _log(cur, hotel_id, booking_id, user_id, "late_fee_amount", None, fee, "late departure")
    return {"applied": True, "late_fee_amount": float(fee)}


def stale_clean_rooms(cur, hotel_id, user_id):
    """Mark vacant rooms that have been clean or inspected for their auto_dirty_days. A checked-in room stays as it is."""
    from booking_system.front_desk_manager import _set_readiness
    cur.execute(
        """SELECT r.room_id, rm.room_number
           FROM room_readiness_status r
           JOIN room rm ON rm.room_id = r.room_id
           JOIN hotel h ON h.hotel_id = r.hotel_id
           WHERE r.hotel_id = %s
             AND r.readiness_status IN ('clean', 'inspected')
             AND r.clean_since IS NOT NULL
             AND (r.clean_since AT TIME ZONE h.timezone) + make_interval(days => r.auto_dirty_days)
                 <= (now() AT TIME ZONE h.timezone)
             AND NOT EXISTS (SELECT 1 FROM booking_room_night n
                             WHERE n.room_id = r.room_id AND n.night_status = 'checked_in')""",
        (hotel_id,),
    )
    rooms = cur.fetchall()
    for row in rooms:
        _set_readiness(cur, user_id, row["room_id"], row["room_number"], "dirty")
    return {"rooms": [row["room_number"] for row in rooms], "count": len(rooms)}


def mark_stale_clean_rooms(hotel_id, user_id):
    """stale_clean_rooms in its own transaction."""
    from booking_system.front_desk_manager import _run
    return _run(lambda cur: stale_clean_rooms(cur, hotel_id, user_id))
