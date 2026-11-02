"""What the main agent holds. The index is short. A full entry is loaded only when the desk names that id."""

CATALOG = {
    "sellable_inventory": {
        "store": "postgresql",
        "status": "approved",
        "tables": ("room_inventory_nightly", "booking_room_night", "daily_pricing", "room_type"),
        "columns": {
            "room_inventory_nightly": ("hotel_id", "room_type_id", "stay_date", "total_inventory", "blocked_quantity", "stop_sell", "min_stay", "closed_to_arrival"),
            "booking_room_night": ("hotel_id", "booking_id", "room_type_id", "stay_date", "room_seq", "night_status"),
            "daily_pricing": ("hotel_id", "room_type_id", "stay_date", "base_price", "saved_price"),
            "room_type": ("hotel_id", "room_type_id", "room_type_name", "status"),
        },
        "natural_key": ("hotel_id", "room_type_id", "stay_date"),
        "formula": "total_inventory - blocked_quantity - booked room nights, by room type and stay date. A booked room night is one booking_room_night row with night_status booked or checked_in. The room type is named by room_type.room_type_name joined on room_type_id. Overbooking is booked room nights greater than total_inventory minus blocked_quantity. Show that excess and the booking_id values on that night. Do not report the excess as zero rooms left. A shortage is rooms left of zero for that room type. stop_sell true means the night cannot be sold. min_stay is the shortest stay accepted. closed_to_arrival true means a stay cannot start that night. The SELECT returns rooms left, base_price, stop_sell, min_stay, and closed_to_arrival",
        "missing": "no inventory",
        "notes": (
            "Only a night with night_status booked or checked_in consumes a room. cancelled, released, and no_show nights do not.",
            "stop_sell, min_stay, and closed_to_arrival restrict the night. They do not change rooms left.",
            "A night with no inventory row is no inventory, so the rules are unknown, not false.",
            "The checkout morning is not a night slept.",
            "A night answer states rooms left, base_price, stop_sell, min_stay, and closed_to_arrival.",
            "A mixed question is one SELECT that returns every named part, and it may use up to six statements. A part with no row is the missing value, and that part is still said.",
        ),
        "version": "7",
    },
    "allotment": {
        "store": "postgresql",
        "status": "approved",
        "tables": ("room_allotments", "room_type"),
        "columns": {
            "room_allotments": ("hotel_id", "channel", "room_type_id", "stay_date", "rooms_allotted", "release_date"),
            "room_type": ("hotel_id", "room_type_id", "room_type_name"),
        },
        "natural_key": ("hotel_id", "channel", "room_type_id", "stay_date"),
        "formula": "rooms_allotted is the channel hold for that room type on that night, through release_date",
        "missing": "no allotment",
        "notes": (
            "A missing row is no allotment, not zero.",
            "release_date is the last night the hold applies.",
            "channel is the name stored on the row.",
            "An allotment answer states rooms_allotted, or no allotment when the row is missing.",
            "A mixed question is one SELECT that returns every named part, and it may use up to six statements. A part with no row is the missing value, and that part is still said.",
        ),
        "version": "3",
    },
    "guest_stay": {
        "store": "postgresql",
        "status": "approved",
        "tables": ("room", "booking_room_night", "booking", "guest"),
        "columns": {
            "room": ("hotel_id", "room_id", "room_number", "room_type_id", "status"),
            "booking_room_night": ("hotel_id", "booking_id", "room_id", "stay_date", "night_status", "checked_in_at", "checked_out_at"),
            "booking": ("hotel_id", "booking_id", "guest_id", "booking_status"),
            "guest": ("hotel_id", "guest_id", "guest_name"),
        },
        "natural_key": ("hotel_id", "room_id", "stay_date"),
        "formula": "A room is in house on a night when a booking_room_night row for that room_id and stay_date has night_status booked or checked_in. The guest is booking.guest_id, named by guest.guest_name. A hotel list for a date is every room with status active LEFT JOIN that night's booking_room_night rows: a booked or checked_in night is in house, no such row is an empty room. checked_in_at set means the guest has arrived in that room",
        "missing": "no guest",
        "notes": (
            "The guest is guest.guest_name. Do not invent a guest name.",
            "A night whose room_id is NULL has no room assigned yet. It does not occupy a room_number.",
            "When the question is the whole hotel, do not require a room_number.",
            "The checkout morning is not a night.",
            "A room answer states the room_number as in house or empty.",
            "In-house rooms are occupied. Empty rooms are vacant. A vacant room is not sellable when stop_sell is true or blocked_quantity equals total_inventory.",
            "A mixed question is one SELECT that returns every named part, and it may use up to six statements. A part with no row is the missing value, and that part is still said.",
        ),
        "version": "4",
    },
    "reservation_night": {
        "store": "postgresql",
        "status": "approved",
        "tables": ("booking", "booking_room_night", "booking_summary", "guest"),
        "columns": {
            "booking": ("hotel_id", "booking_id", "guest_id", "booking_status", "channel", "channel_booking_ref", "group_name", "rate_plan", "adults", "children", "currency", "gross_amount", "booked_at", "cancelled_at", "no_show_at"),
            "booking_room_night": ("hotel_id", "booking_id", "stay_date", "room_seq", "room_type_id", "room_id", "night_status", "sell_rate"),
            "booking_summary": ("hotel_id", "booking_id", "check_in_date", "check_out_date", "nights", "rooms_booked"),
            "guest": ("hotel_id", "guest_id", "guest_name"),
        },
        "natural_key": ("hotel_id", "booking_id", "stay_date", "room_seq"),
        "formula": "A reservation night is a booking_room_night row joined to booking on booking_id; room_seq numbers the rooms of one booking. booking_status confirmed is a live booking. booking_status cancelled is a cancellation at cancelled_at. booking_status no_show is a guest who did not arrive, at no_show_at. night_status released is a night given back by a shortened or moved stay. booked_at is when the reservation was made. booking_summary gives each booking's check_in_date, check_out_date, nights, and rooms_booked. There is no inventory snapshot at booked_at",
        "missing": "no reservation",
        "notes": (
            "The guest is guest.guest_name joined on guest_id. Do not invent a guest name.",
            "stay_date is a night slept. The checkout morning is not a night.",
            "State the earliest and latest booked_at or stay_date when the asked window is longer than the rows.",
            "A mixed question is one SELECT that returns every named part, and it may use up to six statements. A part with no row is the missing value, and that part is still said.",
        ),
        "version": "1",
    },
    "arrival_departure": {
        "store": "postgresql",
        "status": "approved",
        "tables": ("booking_summary", "booking_room_night", "guest"),
        "columns": {
            "booking_summary": ("hotel_id", "booking_id", "guest_id", "booking_status", "check_in_date", "check_out_date", "nights", "rooms_booked"),
            "booking_room_night": ("hotel_id", "booking_id", "stay_date", "room_id", "night_status", "checked_in_at", "checked_out_at"),
            "guest": ("hotel_id", "guest_id", "guest_name"),
        },
        "natural_key": ("hotel_id", "booking_id", "stay_date"),
        "formula": "An arrival on a date is booking_summary.check_in_date, the first night, of a confirmed booking. Arrivals in the next seven days are check_in_date from CURRENT_DATE through CURRENT_DATE plus 6 days. A departure on a date is booking_summary.check_out_date, the morning after the last night. A guest has arrived when a booking_room_night row of the booking has checked_in_at set (night_status checked_in or checked_out); the departure is recorded at checked_out_at. A confirmed booking has not arrived when none of its nights has checked_in_at. When no date is named, include confirmed bookings with check_in_date on or before CURRENT_DATE. When check-in or leaving is mentioned, the SELECT returns whether the guest has arrived and the check_out_date",
        "missing": "no reservation",
        "notes": (
            "The guest is guest.guest_name joined on guest_id. Do not invent a guest name.",
            "stay_date is a night slept. The checkout morning is not a night.",
            "A night with checked_in_at NULL means the guest has not arrived for that night.",
            "An arrival answer states check_in_date and whether the guest has arrived. A departure answer states check_out_date.",
            "A mixed question is one SELECT that returns every named part, and it may use up to six statements. A part with no row is the missing value, and that part is still said.",
        ),
        "version": "1",
    },
    "cancel_pattern": {
        "store": "postgresql",
        "status": "approved",
        "tables": ("booking", "guest", "guest_identity"),
        "columns": {
            "booking": ("hotel_id", "booking_id", "guest_id", "booking_status", "cancelled_at", "no_show_at", "booked_at"),
            "guest": ("hotel_id", "guest_id", "guest_name"),
            "guest_identity": ("hotel_id", "guest_id", "identity_type", "identity_value"),
        },
        "natural_key": ("hotel_id", "guest_id"),
        "formula": "A pattern counts cancelled and no_show bookings for one guest_id against that guest's own bookings. booking_status cancelled is a cancellation recorded at cancelled_at. booking_status no_show is a guest who did not arrive, recorded at no_show_at. A guest named by phone or email is found through guest_identity (identity_type phone in E.164 form, or email in lower case). One cancellation is not a pattern",
        "missing": "no reservation",
        "notes": (
            "The guest is guest.guest_name. Do not invent a guest name.",
            "A booking that is not no_show is not a no-show.",
            "State the earliest and latest booked_at when the asked window is longer than the rows.",
            "A mixed question is one SELECT that returns every named part, and it may use up to six statements. A part with no row is the missing value, and that part is still said.",
        ),
        "version": "1",
    },
    "booking_change": {
        "store": "postgresql",
        "status": "approved",
        "tables": ("booking_modification_log", "booking", "user_account"),
        "columns": {
            "booking_modification_log": ("hotel_id", "booking_id", "modified_at", "modified_by_user_id", "field_changed", "old_value", "new_value", "change_reason"),
            "booking": ("hotel_id", "booking_id", "guest_id", "booking_status"),
            "user_account": ("user_id", "user_name"),
        },
        "natural_key": ("hotel_id", "booking_id"),
        "formula": "booking_modification_log is a recorded change, joined to booking on booking_id. The person who made it is user_account.user_name joined on modified_by_user_id. No log row means no recorded change",
        "missing": "no reservation",
        "notes": (
            "No log row means no recorded change. It does not prove that nothing was edited elsewhere.",
            "A mixed question is one SELECT that returns every named part, and it may use up to six statements. A part with no row is the missing value, and that part is still said.",
        ),
        "version": "1",
    },
    "guest_profile": {
        "store": "postgresql",
        "status": "approved",
        "tables": ("guest", "guest_identity", "booking", "booking_balance"),
        "columns": {
            "guest": ("hotel_id", "guest_id", "guest_name", "nationality", "vip", "status"),
            "guest_identity": ("hotel_id", "guest_id", "identity_type", "identity_value"),
            "booking": ("hotel_id", "booking_id", "guest_id", "booking_status"),
            "booking_balance": ("hotel_id", "booking_id", "currency", "gross_amount", "amount_paid", "balance_due"),
        },
        "natural_key": ("hotel_id", "guest_id"),
        "formula": "A guest is a guest row. vip true means the guest is a VIP. Phone and email are guest_identity rows (identity_type phone or email). The same SELECT returns booking_balance joined to booking on booking_id for that guest_id. gross_amount is the charge, amount_paid is what was paid net of refunds, balance_due is what is still owed",
        "missing": "no guest",
        "notes": (
            "guest_name is the name stored on guest. Do not invent a name.",
            "Preferences, notes, language, and ID document fields are in the guest's profile document, not in a table.",
            "A profile read includes vip, gross_amount, amount_paid, and balance_due. A booking without gross_amount has no recorded charge.",
            "A balance answer states gross_amount, amount_paid, and balance_due.",
            "vip is the stored boolean. Do not invent a VIP.",
            "A mixed question is one SELECT that returns every named part, and it may use up to six statements. A part with no row is the missing value, and that part is still said.",
        ),
        "version": "1",
    },
    "guest_need": {
        "store": "postgresql",
        "status": "approved",
        "tables": ("guest_preference", "guest_request", "guest_complaint", "guest_identity"),
        "columns": {
            "guest_preference": ("hotel_id", "guest_id", "preference", "preference_value", "recorded_at"),
            "guest_request": ("hotel_id", "guest_id", "booking_id", "request", "requested_for", "status", "recorded_at", "done_at"),
            "guest_complaint": ("hotel_id", "guest_id", "booking_id", "complaint", "recorded_at"),
            "guest_identity": ("hotel_id", "guest_id", "identity_type", "identity_value"),
        },
        "natural_key": ("hotel_id", "guest_id"),
        "formula": "A preference is guest_preference. A request is guest_request (status open, done or cancelled). A complaint is guest_complaint. Each row has guest_id; the guest's phone is the guest_identity identity_value with identity_type phone for that guest_id",
        "missing": "no guest",
        "notes": (
            "A missing preference, request, or complaint row is none. A recorded preference or request is not a guarantee that the booked room_type matches it.",
            "A null preference, request, or complaint is none.",
            "A mixed question is one SELECT that returns every named part, and it may use up to six statements. A part with no row is the missing value, and that part is still said.",
        ),
        "version": "1",
    },
    "guest_history": {
        "store": "postgresql",
        "status": "approved",
        "tables": ("guest_message", "guest_identity", "booking_summary", "booking_room_night"),
        "columns": {
            "guest_message": ("hotel_id", "guest_phone", "channel", "sent_at", "direction", "body", "status"),
            "guest_identity": ("hotel_id", "guest_id", "identity_type", "identity_value"),
            "booking_summary": ("hotel_id", "booking_id", "guest_id", "booking_status", "channel", "check_in_date", "check_out_date", "nights", "rooms_booked"),
            "booking_room_night": ("hotel_id", "booking_id", "stay_date", "room_type_id", "room_id", "night_status"),
        },
        "natural_key": ("hotel_id", "guest_id"),
        "formula": "An interaction is a guest_message row for that hotel_id and guest_phone; guest_phone is the guest_identity identity_value with identity_type phone. Stay history is booking_summary and booking_room_night for the same guest_id",
        "missing": "no guest",
        "notes": (
            "stay_date is a night slept. The checkout morning is not a night.",
            "booking_status confirmed is a live stay. cancelled, checked_in, checked_out, and no_show are the booking's own status.",
            "A missing message row is no interaction.",
            "A mixed question is one SELECT that returns every named part, and it may use up to six statements. A part with no row is the missing value, and that part is still said.",
        ),
        "version": "1",
    },
    "inventory_gap": {
        "store": "postgresql",
        "status": "approved",
        "tables": ("room_inventory_nightly", "booking_room_night", "room", "room_block"),
        "columns": {
            "room_inventory_nightly": ("hotel_id", "room_type_id", "stay_date", "total_inventory", "blocked_quantity"),
            "booking_room_night": ("hotel_id", "booking_id", "room_type_id", "stay_date", "room_id", "night_status"),
            "room": ("hotel_id", "room_id", "room_type_id", "room_number", "status"),
            "room_block": ("hotel_id", "room_id", "block_type", "start_date", "end_date"),
        },
        "natural_key": ("hotel_id", "room_type_id", "stay_date"),
        "formula": "booked is the count of booking_room_night rows with night_status booked or checked_in for that room_type_id and night. occupied is those rows with room_id assigned. physical is the count of room rows with status active for that room_type_id. A room_block covering the night (start_date to end_date) takes that room out of order or out of service. A booking count is not an occupied room count. blocked_quantity is held back, not occupied. Overbooking is booked greater than total_inventory minus blocked_quantity. A difference between booked and occupied, without that excess, is unassigned nights. A discrepancy is booked different from occupied, or total_inventory different from physical when physical is greater than zero. When booked equals occupied and total_inventory equals physical, there is no discrepancy",
        "missing": "no inventory",
        "notes": (
            "Only a night with night_status booked or checked_in consumes a room.",
            "physical counts active room rows. It does not count a room type document.",
            "A room type with no active room rows is no physical count, not a discrepancy against total_inventory.",
            "A mixed question is one SELECT that returns every named part, and it may use up to six statements. A part with no row is the missing value, and that part is still said.",
        ),
        "version": "3",
    },
}

PARENTS = {
    "B01": ("B01a", "B01b", "B01c", "B01d"),
    "B02": ("B02a", "B02b", "B02c"),
    "B03": ("B03a", "B03b", "B03c", "B03d"),
}

ALIASES = {
    "B01a": (
        "confirmed_reservation", "confirmed_reservations", "confirmed reservations",
        "reservation count", "how many reservations", "how many confirmed reservations",
        "booking count", "booking status",
    ),
    "B01b": (
        "arrival", "arrivals", "guests coming tomorrow", "coming tomorrow",
        "confirmed arrivals", "check-in", "check in", "reservation arrival date",
        "who is arriving", "departure", "checked out", "not arrived",
    ),
    "B01c": ("cancelled_booking", "cancelled bookings", "cancellation", "no_show", "no show"),
    "B01d": ("booking_change", "booking change", "modified reservation"),
    "B02a": ("guest_profile", "guest profile", "guest balance", "vip guest"),
    "B02b": (
        "guest_preference", "guest preference", "room preference", "previous room",
        "returning guest", "guest request",
    ),
    "B02c": ("guest_history", "guest history", "stay history", "returning guests"),
    "B03a": ("rooms_left", "rooms left", "accommodate", "availability", "room availability"),
    "B03b": ("in_house_guest", "occupied room", "empty room", "who is in house"),
    "B03c": ("channel_allotment", "channel allotment"),
    "B03d": ("inventory_discrepancy", "inventory discrepancy", "booked versus occupied"),
}

KEYPHRASES = {
    "B01a": ("confirmed_reservation", "booking_status", "reservation_count", "booking_channel", "reservation_created"),
    "B01b": ("arrival", "departure", "not_arrived", "checked_in_guest", "checked_out_guest"),
    "B01c": ("cancelled_booking", "no_show"),
    "B01d": ("booking_change",),
    "B02a": ("guest_profile", "guest_balance", "vip_guest"),
    "B02b": ("guest_preference", "guest_request", "guest_complaint"),
    "B02c": ("guest_history", "guest_message"),
    "B03a": ("rooms_left", "room_price", "blocked_room", "night_rule"),
    "B03b": ("in_house_guest", "occupied_room", "empty_room"),
    "B03c": ("channel_allotment",),
    "B03d": ("inventory_discrepancy",),
}

INDEX = {
    "F01": {
        "name": "Database Discovery",
        "description": "Discover schemas, tables, columns, data types, keys, and relationships.",
        "store": "postgresql",
        "concepts": (),
    },
    "F02": {
        "name": "Business Semantics",
        "description": "Business terminology, metrics, calculations, and operational rules.",
        "store": "postgresql",
        "concepts": (),
    },
    "B01": {
        "name": "Reservation Intelligence",
        "description": "Full reservation domain. Prefer a section when the question is narrower.",
        "store": "postgresql",
        "concepts": ("reservation_night", "arrival_departure", "cancel_pattern", "booking_change"),
    },
    "B01a": {
        "name": "Reservation nights",
        "description": "Booking status, nights, channel, and booked_at.",
        "store": "postgresql",
        "concepts": ("reservation_night",),
    },
    "B01b": {
        "name": "Arrivals and departures",
        "description": "Next arrivals, not-arrived nights, departure mornings, and check-in flag.",
        "store": "postgresql",
        "concepts": ("arrival_departure",),
    },
    "B01c": {
        "name": "Cancellation patterns",
        "description": "Cancelled and no-show counts by guest.",
        "store": "postgresql",
        "concepts": ("cancel_pattern",),
    },
    "B01d": {
        "name": "Booking changes",
        "description": "Recorded booking modifications.",
        "store": "postgresql",
        "concepts": ("booking_change",),
    },
    "B02": {
        "name": "Guest Intelligence",
        "description": "Full guest domain. Prefer a section when the question is narrower.",
        "store": "postgresql",
        "concepts": ("guest_profile", "guest_need", "guest_history"),
    },
    "B02a": {
        "name": "Guest profile and balance",
        "description": "Guest name, VIP, profile fields, and payment balance.",
        "store": "postgresql",
        "concepts": ("guest_profile",),
    },
    "B02b": {
        "name": "Guest needs",
        "description": "Preferences, requests, and complaints.",
        "store": "postgresql",
        "concepts": ("guest_need",),
    },
    "B02c": {
        "name": "Guest history",
        "description": "Stay history and guest messages.",
        "store": "postgresql",
        "concepts": ("guest_history",),
    },
    "B03": {
        "name": "Inventory Intelligence",
        "description": "Full inventory domain. Prefer a section when the question is narrower.",
        "store": "postgresql",
        "concepts": ("sellable_inventory", "guest_stay", "allotment", "inventory_gap"),
    },
    "B03a": {
        "name": "Sellable inventory",
        "description": "Rooms left, price, stop-sell, min stay, and closed to arrival.",
        "store": "postgresql",
        "concepts": ("sellable_inventory",),
    },
    "B03b": {
        "name": "Room occupancy",
        "description": "Who is in a room, who is in house, and which rooms are empty.",
        "store": "postgresql",
        "concepts": ("guest_stay",),
    },
    "B03c": {
        "name": "Channel allotment",
        "description": "Channel holds for a room type on a night.",
        "store": "postgresql",
        "concepts": ("allotment",),
    },
    "B03d": {
        "name": "Inventory discrepancy",
        "description": "Booked versus occupied versus physical room counts.",
        "store": "postgresql",
        "concepts": ("inventory_gap",),
    },
    "B04": {
        "name": "Revenue Intelligence",
        "description": "Booking value, amount paid, and balance.",
        "store": "postgresql",
        "concepts": (),
    },
    "B05": {
        "name": "Pricing Intelligence",
        "description": "Room rates and pricing changes.",
        "store": "postgresql",
        "concepts": (),
    },
    "B06": {
        "name": "Distribution Intelligence",
        "description": "Booking channels and direct bookings.",
        "store": "postgresql",
        "concepts": (),
    },
    "B07": {
        "name": "Financial Intelligence",
        "description": "Operating expenses, profitability, and cash flow.",
        "store": "unconfirmed",
        "concepts": (),
    },
    "B08": {
        "name": "Operational Intelligence",
        "description": "Housekeeping, room readiness, and service delivery.",
        "store": "postgresql",
        "concepts": (),
    },
    "B09": {
        "name": "Staff Intelligence",
        "description": "Staff assignments, attendance, and workload.",
        "store": "unconfirmed",
        "concepts": (),
    },
    "B10": {
        "name": "Service Quality Intelligence",
        "description": "Complaints, reviews, and service failures.",
        "store": "unconfirmed",
        "concepts": (),
    },
    "B11": {
        "name": "Communication Intelligence",
        "description": "Conversations, messages, and unresolved requests.",
        "store": "contextual",
        "concepts": (),
    },
    "B12": {
        "name": "Task and Workflow Intelligence",
        "description": "Pending tasks, ownership, and completion.",
        "store": "contextual",
        "concepts": (),
    },
}

CONTEXT = {
    "room_encyclopedia": "a PostgreSQL document",
    "user_profile": "a PostgreSQL document",
}

ACTION_CONCEPT = {
    "check_availability": "sellable_inventory",
    "occupancy": "guest_stay",
}


def _registry():
    from booking_system.semantic.registry import list_active_catalogs, parents_map, concept_entry
    return list_active_catalogs, parents_map, concept_entry


def catalog_row(catalog_id):
    """One active Postgres catalog row."""
    list_active_catalogs, _parents, _concept = _registry()
    name = str(catalog_id or "")
    for row in list_active_catalogs():
        if row["catalog_id"] == name:
            return row
    return None


def index_ids():
    list_active_catalogs, _parents, _concept = _registry()
    return [row["catalog_id"] for row in list_active_catalogs()]


def _desk_row(row):
    return row.get("store") == "postgresql" and bool(row.get("concepts"))


def desk_ids():
    """Ready PostgreSQL sections and their parents. Empty catalogs stay out of the desk."""
    list_active_catalogs, _parents, _concept = _registry()
    return [row["catalog_id"] for row in list_active_catalogs() if _desk_row(row)]


def keyphrase_index():
    """Exact phrase to catalog_id from the active Postgres bodies."""
    list_active_catalogs, _parents, _concept = _registry()
    found = {}
    for row in list_active_catalogs():
        body = row.get("body") or {}
        for phrase in body.get("keyphrases") or []:
            name = str(phrase or "").strip()
            if name and name not in found:
                found[name] = row["catalog_id"]
    return found


def expand_ids(chosen):
    """Parents become their sections. Named sections stay. Parents come from Postgres."""
    _list, parents_map, _concept = _registry()
    parents = parents_map()
    expanded = []
    for map_id in chosen or []:
        name = str(map_id or "")
        children = parents.get(name)
        if children:
            for child in children:
                if child not in expanded:
                    expanded.append(child)
            continue
        if name and name not in expanded:
            expanded.append(name)
    return expanded


def ready_ids(chosen):
    """Active Postgres catalogs that already have a formula, plus database discovery."""
    list_active_catalogs, _parents, _concept = _registry()
    by_id = {row["catalog_id"]: row for row in list_active_catalogs()}
    ready = []
    for map_id in expand_ids(chosen):
        entry = by_id.get(str(map_id or ""))
        if not entry or entry.get("store") != "postgresql":
            continue
        if map_id == "F01" or entry.get("concepts"):
            ready.append(map_id)
    return ready


def structure_text(concept):
    """Columns of one approved concept from Postgres."""
    _list, _parents, concept_entry = _registry()
    entry = concept_entry(concept) or {}
    parts = []
    for table, columns in (entry.get("columns") or {}).items():
        parts.append(str(table) + "(" + ", ".join(columns) + ")")
    return "; ".join(parts)


def concept_text(name):
    """Full concept text from the active Postgres body only."""
    _list, _parents, concept_entry = _registry()
    entry = concept_entry(name)
    if not entry:
        return ""
    column_parts = []
    for table, columns in (entry.get("columns") or {}).items():
        column_parts.append(str(table) + "(" + ", ".join(columns) + ")")
    lines = [
        str(name) + " is operational truth in PostgreSQL.",
        "Natural key " + " + ".join(entry.get("natural_key") or ()) + ".",
        "Tables " + ", ".join(entry.get("tables") or ()) + ".",
        "Columns " + "; ".join(column_parts) + ".",
        "Rule " + str(entry.get("formula") or "") + ".",
        "A missing row is " + str(entry.get("missing") or "missing") + ".",
    ]
    for note in entry.get("notes") or ():
        lines.append(str(note))
    return "\n".join(lines)


def entries_text(map_ids):
    """Full entries for the ids the desk named. Loaded from Postgres only."""
    list_active_catalogs, _parents, _concept = _registry()
    by_id = {row["catalog_id"]: row for row in list_active_catalogs()}
    seen = []
    lines = []
    for map_id in ready_ids(map_ids):
        entry = by_id.get(map_id) or {}
        for concept in entry.get("concepts") or []:
            name = str(concept.get("name") or "")
            if not name or name in seen:
                continue
            seen.append(name)
            text = concept_text(name)
            if text:
                lines.append(text)
    return "\n".join(lines)


def index_text():
    """Desk index from Postgres. Ready sections and their parents only. Formulas stay out."""
    list_active_catalogs, _parents, _concept = _registry()
    lines = ["Name catalog_ids from this index. Prefer a section over its parent. A question may name more than one."]
    for row in list_active_catalogs():
        if not _desk_row(row):
            continue
        lines.append(
            row["catalog_id"] + " " + str(row.get("title") or row["catalog_id"]) + ". "
            + str(row.get("purpose") or "") + "."
        )
    return "\n".join(lines)


def semantic_text():
    """Desk index from Postgres. Formulas stay out."""
    lines = [
        "You hold this catalog index. You do not query.",
        index_text(),
        "Prefer the narrowest section that answers the sentence. Name a parent only for a full-domain investigation.",
        "The guest key is guest_id. guest_name is the name on guest; phone and email are guest_identity rows.",
    ]
    return "\n".join(lines)
