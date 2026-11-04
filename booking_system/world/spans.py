"""Pure calendar shaping: per-night grid entries from the desk calendar read become stay spans. No database access here.

A night belongs to the date it starts on (hotel-local). A span covers consecutive nights of one booking on one room; its check-out
is the morning after its last night. A booking with nights on several rooms (a group or a room move) yields one span per run."""

from datetime import date, timedelta

# Desk calendar stay_status per night: assigned (booked), checked_in, checked_out.
_IN_HOUSE = "checked_in"
_DEPARTED = "checked_out"


def _day(text):
    try:
        return date.fromisoformat(str(text or "")[:10])
    except ValueError:
        return None


def floor_of(room_number):
    """Leading digits of a room number without its last two (301 -> 3, 1205 -> 12). Rooms without that shape are floor None."""
    digits = ""
    for char in str(room_number or ""):
        if not char.isdigit():
            break
        digits += char
    if len(digits) < 3:
        return None
    return int(digits[:-2])


def span_state(statuses, first_night, last_night, today):
    """One label for the span the client can colour and badge. Statuses are the per-night stay_status values.

    in_house / due_out: a night is checked in (due_out when the check-out morning is today or earlier)
    arriving: booked, first night is today      late_arrival: booked, first night already passed and nobody checked in
    departed: every night checked out           upcoming: booked, first night in the future"""
    checkout = last_night + timedelta(days=1)
    if _IN_HOUSE in statuses:
        return "due_out" if checkout <= today else "in_house"
    if statuses and all(status == _DEPARTED for status in statuses):
        return "departed"
    if _DEPARTED in statuses:
        return "departed"
    if first_night == today:
        return "arriving"
    if first_night < today:
        return "late_arrival"
    return "upcoming"


def build_spans(calendar, window_start, window_end, today):
    """calendar: the desk read (rooms[].entries keyed by date). Returns spans that touch [window_start, window_end].

    Each span: booking_id, room_number, room_type, guest_name, channel, first_night, last_night, check_out, nights, night_statuses,
    state, continues_before, continues_after."""
    start, end = _day(window_start), _day(window_end)
    spans = []
    for room in calendar.get("rooms") or []:
        entries = room.get("entries") or {}
        current = None
        for key in sorted(entries):
            entry = entries[key] or {}
            night = _day(key)
            booking_id = entry.get("booking_id")
            if night is None or not booking_id or entry.get("status") != "assigned":
                current = None
                continue
            if current and current["booking_id"] == booking_id and current["_last"] + timedelta(days=1) == night:
                current["_last"] = night
                current["night_statuses"].append(entry.get("stay_status") or "")
                continue
            current = {"booking_id": booking_id, "room_number": room.get("room_number"), "room_type": room.get("room_type"),
                       "guest_name": entry.get("guest_name") or "", "channel": entry.get("channel") or "",
                       "_first": night, "_last": night, "night_statuses": [entry.get("stay_status") or ""]}
            spans.append(current)
    shaped = []
    for span in spans:
        first, last = span.pop("_first"), span.pop("_last")
        if last < start or first > end:
            continue
        span.update(first_night=first.isoformat(), last_night=last.isoformat(), check_out=(last + timedelta(days=1)).isoformat(),
                    nights=(last - first).days + 1, state=span_state(span["night_statuses"], first, last, today),
                    continues_before=first < start, continues_after=last > end)
        shaped.append(span)
    rooms_per_booking = {}
    for span in shaped:
        rooms_per_booking.setdefault(span["booking_id"], set()).add(span["room_number"])
    for span in shaped:
        span["rooms_in_booking"] = len(rooms_per_booking[span["booking_id"]])
    return shaped


def build_blocks(calendar):
    """Out-of-order spans per room from the desk read."""
    blocks = []
    for room in calendar.get("rooms") or []:
        for block in room.get("ooo_linear") or []:
            blocks.append({"room_number": room.get("room_number"), "first_night": block["start"], "last_night": block["end"],
                           "reason": block.get("reason") or ""})
    return blocks


def build_unassigned(calendar, window_start, window_end):
    """Nights booked on a room type but not yet on a room, counted per type and date inside the window."""
    start, end = _day(window_start), _day(window_end)
    found = []
    for room_type, by_date in sorted((calendar.get("unassigned_by_type") or {}).items()):
        for key, items in sorted(by_date.items()):
            night = _day(key)
            if night and start <= night <= end:
                found.append({"room_type": room_type, "night": key, "count": len(items),
                              "booking_ids": sorted({item.get("booking_id") for item in items if item.get("booking_id")})})
    return found
