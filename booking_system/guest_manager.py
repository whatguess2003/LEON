"""Guests. Computable fields live in guest (name, nationality, vip, status) and guest_identity (phones E.164, emails lower-case);
profile text, preferences and the desk brief live in the `guests` document keyed by guest_id. Desk tasks are guest_request rows;
messages are guest_message rows keyed by (channel, source_ref). Writes run in one transaction and read first; a refusal returns
{'error': reason}."""

import os
import sys
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import psycopg2

from booking_system import core_refs, db
from booking_system.content_store import _with, client
from booking_system.hotel_registry import new_id, read_hotel_by_id

store = client
records = client["leon_hotel_memory"]
guest_docs = records["guests"]

# Profile document fields the desk edits; everything computable is a column or an identity.
PROFILE_TEXT = ("country", "date_of_birth", "gender", "company", "address", "identity_type", "identity_number", "special_request",
                "preferences", "notes", "language")


class Refused(Exception):
    """A business refusal raised inside a transaction: it rolls back and becomes {'error': reason}."""


def run(fn):
    try:
        return db.transaction(fn)
    except Refused as reason:
        return {"error": str(reason)}
    except psycopg2.errors.InvalidParameterValue as unset:
        return {"error": str(unset.diag.message_primary)}


# ---------- identities ----------

def _phone(value):
    text = str(value or "").strip()
    digits = "".join(ch for ch in text if ch.isdigit())
    return "+" + digits if text.startswith("+") and 7 <= len(digits) <= 15 else ""


def _email(value):
    text = str(value or "").strip().lower()
    return text if text.find("@") > 0 else ""


def _name(value):
    return str(value or "").strip().upper()


def _upper_or_none(value):
    return str(value or "").strip().upper() or None


COLUMNS = {"guest_name": _name, "nationality": _upper_or_none, "vip": bool}


def identities(phone=None, email=None):
    """[(identity_type, stored value)] for the usable values."""
    pairs = (("phone", _phone(phone)), ("email", _email(email)))
    return [(kind, value) for kind, value in pairs if value]


def find_guest(cur, hotel_id, pairs):
    """guest_id of the first identity a guest holds, or None."""
    for kind, value in pairs:
        found = core_refs.guest_by_identity(cur, hotel_id, kind, value)
        if found:
            return found["guest_id"]
    return None


def bind_identities(cur, hotel_id, guest_id, pairs):
    """Attach each identity no guest holds yet; one held by another guest stays with that guest."""
    for kind, value in pairs:
        if core_refs.guest_by_identity(cur, hotel_id, kind, value) is None:
            cur.execute(
                "INSERT INTO guest_identity (identity_id, hotel_id, guest_id, identity_type, identity_value) VALUES (%s, %s, %s, %s, %s)",
                (new_id("gid"), hotel_id, guest_id, kind, value),
            )


def ensure_guest(cur, hotel_id, user_id, guest_name, phone=None, email=None, nationality=None):
    """guest_id found by phone, then email; otherwise a new guest. Missing identities are attached."""
    pairs = identities(phone, email)
    guest_id = find_guest(cur, hotel_id, pairs)
    if guest_id is None:
        name = _name(guest_name)
        if not name:
            raise Refused("guest_name is required")
        guest_id = new_id("gst")
        cur.execute(
            """INSERT INTO guest (guest_id, hotel_id, guest_name, nationality, status, created_by_user_id)
               VALUES (%s, %s, %s, %s, 'active', %s)""",
            (guest_id, hotel_id, name, _upper_or_none(nationality), user_id),
        )
    bind_identities(cur, hotel_id, guest_id, pairs)
    return guest_id


# ---------- the guest document ----------

def write_document(cur, hotel_id, guest_id, fields):
    """Merge fields into the guest's document inside the caller's transaction."""
    if not fields:
        return
    key, body = guest_docs.staged_update({"guest_id": guest_id}, {"$set": dict(fields, hotel_id=hotel_id)}, upsert=True)
    guest_docs.write_in(cur, key, body)


def read_document(guest_id):
    return guest_docs.find_one({"guest_id": guest_id}, {"_id": 0}) or {}


def guest_row(cur, hotel_id, guest_id):
    """The guest with its first phone and email, or None."""
    cur.execute(
        """SELECT g.guest_id, g.guest_name, g.nationality, g.vip, g.status, g.created_at,
                  (SELECT identity_value FROM guest_identity i WHERE i.guest_id = g.guest_id AND i.identity_type = 'phone'
                   ORDER BY identity_value LIMIT 1) AS guest_phone,
                  (SELECT identity_value FROM guest_identity i WHERE i.guest_id = g.guest_id AND i.identity_type = 'email'
                   ORDER BY identity_value LIMIT 1) AS email
           FROM guest g WHERE g.hotel_id = %s AND g.guest_id = %s""",
        (hotel_id, guest_id),
    )
    return cur.fetchone()


def _guest_id_by_phone(cur, hotel_id, guest_phone):
    found = core_refs.guest_by_identity(cur, hotel_id, "phone", _phone(guest_phone))
    return found["guest_id"] if found else None


def read_guest(hotel_id, guest_phone):
    """Guest columns, phone, email and document fields for a phone at this hotel, or None."""
    def read(cur):
        guest_id = _guest_id_by_phone(cur, hotel_id, guest_phone)
        return guest_row(cur, hotel_id, guest_id) if guest_id else None
    row = db.transaction(read)
    if not row:
        return None
    record = dict(read_document(row["guest_id"]), **row)
    record["guest_email"] = row["email"] or ""
    record["created_at"] = row["created_at"].isoformat()
    return record


def write_guest_document(hotel_id, guest_phone, fields):
    """Merge content fields (desk brief, approvals, tasks, profile text) into the document of the guest with this phone."""
    def write(cur):
        guest_id = _guest_id_by_phone(cur, hotel_id, guest_phone)
        if not guest_id:
            raise Refused("Guest profile not found")
        write_document(cur, hotel_id, guest_id, fields)
        return {"guest_id": guest_id}
    return run(write)


# ---------- profile CRUD ----------

def create_guest_profile(hotel_id, user_id, guest_phone, guest_name, email=None, nationality=None, profile=None):
    """A new guest for a phone no guest at this hotel holds."""
    def write(cur):
        phone = _phone(guest_phone)
        if not phone:
            raise Refused("Invalid phone format. Use international format: +CountryCodePhoneNumber (e.g. +60123456789)")
        held = core_refs.guest_by_identity(cur, hotel_id, "phone", phone)
        if held:
            raise Refused("Phone " + phone + " already belongs to guest " + held["guest_name"])
        guest_id = ensure_guest(cur, hotel_id, user_id, guest_name, phone, email, nationality)
        write_document(cur, hotel_id, guest_id, profile or {})
        return {"guest_id": guest_id, "guest_phone": phone, "guest_name": _name(guest_name)}
    return run(write)


def update_guest_profile(hotel_id, user_id, guest_phone, fields):
    """fields: guest columns (guest_name, nationality, vip), email, and PROFILE_TEXT document fields."""
    unknown = sorted(set(fields) - set(COLUMNS) - set(PROFILE_TEXT) - {"email"})
    if unknown:
        return {"error": "Unknown guest fields: " + ", ".join(unknown)}

    def write(cur):
        guest_id = _guest_id_by_phone(cur, hotel_id, guest_phone)
        if not guest_id:
            raise Refused("Guest profile not found")
        columns = {name: COLUMNS[name](fields[name]) for name in COLUMNS if name in fields}
        if "guest_name" in columns and not columns["guest_name"]:
            raise Refused("guest_name is required")
        if columns:
            cur.execute("UPDATE guest SET " + ", ".join(name + " = %s" for name in columns) + " WHERE hotel_id = %s AND guest_id = %s",
                        tuple(columns.values()) + (hotel_id, guest_id))
        if "email" in fields:
            _replace_email(cur, hotel_id, guest_id, _email(fields["email"]))
        write_document(cur, hotel_id, guest_id, {name: fields[name] for name in PROFILE_TEXT if name in fields})
        return {"guest_id": guest_id, "updated": sorted(fields)}
    return run(write)


def _replace_email(cur, hotel_id, guest_id, email):
    if email:
        held = core_refs.guest_by_identity(cur, hotel_id, "email", email)
        if held and held["guest_id"] != guest_id:
            raise Refused("Email " + email + " already belongs to guest " + held["guest_name"])
        if held:
            return
    cur.execute("DELETE FROM guest_identity WHERE hotel_id = %s AND guest_id = %s AND identity_type = 'email'", (hotel_id, guest_id))
    bind_identities(cur, hotel_id, guest_id, identities(email=email))


def set_guest_status(hotel_id, user_id, guest_phone, status):
    """active | inactive."""
    def write(cur):
        guest_id = _guest_id_by_phone(cur, hotel_id, guest_phone)
        if not guest_id:
            raise Refused("Guest profile not found")
        cur.execute("UPDATE guest SET status = %s WHERE hotel_id = %s AND guest_id = %s", (status, hotel_id, guest_id))
        return {"guest_id": guest_id, "status": status}
    return run(write)


def deactivate_guest(hotel_id, user_id, guest_phone):
    return set_guest_status(hotel_id, user_id, guest_phone, "inactive")


# ---------- reads ----------

def guest_booking_totals(hotel_id, guest_id):
    """(bookings not cancelled, gross of bookings that stayed or will stay, hotel currency)."""
    found = db.row(
        """SELECT COUNT(*) FILTER (WHERE b.booking_status <> 'cancelled') AS bookings,
                  COALESCE(SUM(b.gross_amount) FILTER (WHERE b.booking_status NOT IN ('cancelled', 'no_show')), 0) AS spent,
                  MAX(h.currency) AS currency
           FROM hotel h LEFT JOIN booking b ON b.hotel_id = h.hotel_id AND b.guest_id = %s
           WHERE h.hotel_id = %s""",
        (guest_id, hotel_id),
    )
    return int(found["bookings"]), float(found["spent"]), str(found["currency"] or "").strip()


def read_guest_profile(hotel_id, guest_phone):
    """{'profile': text, 'guest': record} for an active guest, or {'error'}."""
    record = read_guest(hotel_id, guest_phone)
    if not record or record["status"] != "active":
        return {"error": "No guest profile found for " + str(guest_phone or "") + ". Create the guest profile first."}
    bookings, spent, currency = guest_booking_totals(hotel_id, record["guest_id"])
    record.update(total_bookings=bookings, total_spent=spent, currency=currency)
    lines = ["GUEST PROFILE (" + str(record["guest_phone"] or "") + ")", "Name: " + record["guest_name"],
             "Email: " + (record["email"] or "-"), "Nationality: " + (record["nationality"] or "-"), "VIP: " + str(record["vip"]),
             "Status: " + record["status"]]
    lines += [name.replace("_", " ").title() + ": " + str(record[name]) for name in PROFILE_TEXT if record.get(name)]
    lines += ["Total Bookings: " + str(bookings), "Total Spent: " + currency + " " + format(spent, ".2f")]
    return {"profile": "\n".join(lines), "guest": record}


def search_guests(hotel_id, query):
    """Active guests whose name, phone or email contains query."""
    text = str(query or "").strip().upper()
    rows = db.rows(
        """SELECT g.guest_id, g.guest_name, g.nationality,
                  (SELECT identity_value FROM guest_identity i WHERE i.guest_id = g.guest_id AND i.identity_type = 'phone'
                   ORDER BY identity_value LIMIT 1) AS guest_phone,
                  (SELECT identity_value FROM guest_identity i WHERE i.guest_id = g.guest_id AND i.identity_type = 'email'
                   ORDER BY identity_value LIMIT 1) AS email
           FROM guest g
           WHERE g.hotel_id = %s AND g.status = 'active'
             AND (%s = '' OR strpos(g.guest_name, %s) > 0
                  OR EXISTS (SELECT 1 FROM guest_identity i WHERE i.guest_id = g.guest_id AND strpos(upper(i.identity_value), %s) > 0))
           ORDER BY g.guest_name""",
        (hotel_id, text, text, text),
    )
    countries = {doc.get("guest_id"): doc.get("country") for doc in guest_docs.find({"hotel_id": hotel_id}, {"guest_id": 1, "country": 1})}
    return [{"guest_id": row["guest_id"], "guest_phone": row["guest_phone"] or "", "guest_name": row["guest_name"],
             "email": row["email"] or "", "nationality": row["nationality"] or "", "country": countries.get(row["guest_id"]) or ""}
            for row in rows]


def _stays(hotel_id, guest_id):
    bookings = db.rows(
        """SELECT s.booking_id, s.booking_status, s.check_in_date, s.check_out_date, s.gross_amount,
                  (SELECT string_agg(DISTINCT t.room_type_name, ', ') FROM booking_room_night n JOIN room_type t ON t.room_type_id = n.room_type_id
                   WHERE n.booking_id = s.booking_id) AS room_type
           FROM booking_summary s WHERE s.hotel_id = %s AND s.guest_id = %s
           ORDER BY s.check_in_date, s.booking_id""",
        (hotel_id, guest_id),
    )
    rooms = {}
    for row in db.rows(
        """SELECT n.booking_id, r.room_number, MIN(n.stay_date) AS first_night, MAX(n.stay_date) AS last_night,
                  (array_agg(n.night_status ORDER BY n.stay_date DESC))[1] AS stay_status
           FROM booking_room_night n JOIN room r ON r.room_id = n.room_id JOIN booking b ON b.booking_id = n.booking_id
           WHERE b.hotel_id = %s AND b.guest_id = %s
           GROUP BY n.booking_id, r.room_number ORDER BY r.room_number""",
        (hotel_id, guest_id),
    ):
        rooms.setdefault(row["booking_id"], []).append({
            "room_number": row["room_number"], "stay_status": row["stay_status"],
            "check_in": row["first_night"].isoformat(), "check_out": (row["last_night"] + timedelta(days=1)).isoformat(),
        })
    return bookings, rooms


def read_guest_with_bookings(hotel_id, guest_phone, zone):
    """Guest record plus past, current and future bookings with their assigned rooms and the message thread."""
    guest = read_guest(hotel_id, guest_phone)
    if not guest or guest["status"] != "active":
        return None
    hotel = read_hotel_by_id(hotel_id)
    today = datetime.now(ZoneInfo(zone)).date()
    bookings, rooms = _stays(hotel_id, guest["guest_id"])
    past, current, future = [], [], []
    for row in bookings:
        item = {"booking_id": row["booking_id"], "booking_status": row["booking_status"], "room_type": row["room_type"] or "",
                "check_in": row["check_in_date"].isoformat(), "check_out": row["check_out_date"].isoformat(),
                "total_amount": float(row["gross_amount"] or 0), "rooms": rooms.get(row["booking_id"], [])}
        last_night = row["check_out_date"] - timedelta(days=1)
        bucket = past if row["booking_status"] == "cancelled" or last_night < today else (future if row["check_in_date"] > today else current)
        bucket.append(item)
    messages = list_guest_messages(hotel_id, guest["guest_phone"])
    from booking_system.desk_actions import action_names, approval_names
    tasks = [{"task": row["request"], "request_id": row["request_id"], "status": "open"} for row in open_requests(hotel_id, guest["guest_id"])]
    return {
        "guest": dict(guest, desk_tasks=tasks), "currency": hotel["currency"], "today": today.isoformat(), "past": past, "current": current,
        "future": future, "messages": messages, "whatsapp_window": whatsapp_text_window(messages),
        "desk_require_approval": approval_names(hotel_id), "executional_names": action_names(),
    }


def hotels_for_phone(guest_phone):
    """[{'hotel_id', 'hotel_name'}] of the hotels with an active guest holding this phone."""
    return db.rows(
        """SELECT h.hotel_id, h.hotel_name FROM guest_identity i JOIN guest g ON g.guest_id = i.guest_id JOIN hotel h ON h.hotel_id = g.hotel_id
           WHERE i.identity_type = 'phone' AND i.identity_value = %s AND g.status = 'active' ORDER BY h.hotel_name""",
        (_phone(guest_phone),),
    )


def desk_queue(hotel_id):
    """Open approval rows (guest documents) and open requests (guest_request) of one hotel, each with the guest's name and phone."""
    guests = {row["guest_id"]: row for row in db.rows(
        """SELECT g.guest_id, g.guest_name,
                  (SELECT identity_value FROM guest_identity i WHERE i.guest_id = g.guest_id AND i.identity_type = 'phone'
                   ORDER BY identity_value LIMIT 1) AS guest_phone
           FROM guest g WHERE g.hotel_id = %s AND g.status = 'active'""",
        (hotel_id,),
    )}
    approvals = []
    for doc in guest_docs.find({"hotel_id": hotel_id}):
        guest = guests.get(doc.get("guest_id"))
        if guest:
            who = {"guest_phone": guest["guest_phone"] or "", "guest_name": guest["guest_name"]}
            approvals += [dict(row, **who) for row in doc.get("approval_actions") or [] if isinstance(row, dict)]
    tasks = [{"guest_phone": guests[row["guest_id"]]["guest_phone"] or "", "guest_name": guests[row["guest_id"]]["guest_name"],
              "task": row["request"], "request_id": row["request_id"]}
             for row in open_requests(hotel_id) if row["guest_id"] in guests]
    return approvals, tasks


# ---------- guest requests (desk tasks and asked-for services) ----------

def open_requests(hotel_id, guest_id=None):
    """Open requests of the hotel, or of one guest, oldest first."""
    return db.rows(
        """SELECT request_id, guest_id, booking_id, request, requested_for, recorded_at FROM guest_request
           WHERE hotel_id = %s AND status = 'open' AND (%s::varchar IS NULL OR guest_id = %s) ORDER BY recorded_at, request_id""",
        (hotel_id, guest_id, guest_id))


def add_requests(hotel_id, user_id, guest_id, texts, booking_id=None, requested_for=None):
    """READ the guest's open requests; each text not already open is created. Returns the request_ids created."""
    wanted = [str(text or "").strip() for text in texts or [] if str(text or "").strip()]

    def write(cur):
        cur.execute("SELECT request FROM guest_request WHERE hotel_id = %s AND guest_id = %s AND status = 'open'", (hotel_id, guest_id))
        held = {row["request"] for row in cur.fetchall()}
        made = []
        for text in wanted:
            if text in held:
                continue
            request_id = new_id("grq")
            cur.execute(
                """INSERT INTO guest_request (request_id, hotel_id, guest_id, booking_id, request, requested_for, status, recorded_by_user_id)
                   VALUES (%s, %s, %s, %s, %s, %s, 'open', %s)""",
                (request_id, hotel_id, guest_id, booking_id, text[:500], requested_for, user_id))
            held.add(text)
            made.append(request_id)
        return made
    return run(write)


def close_request(hotel_id, user_id, guest_phone, request, status="done"):
    """Mark the guest's open request with this text (or this request_id) done or cancelled. {'error'} when none is open."""
    def write(cur):
        guest_id = _guest_id_by_phone(cur, hotel_id, guest_phone)
        if not guest_id:
            raise Refused("Guest profile not found")
        cur.execute(
            """UPDATE guest_request SET status = %s, done_at = now(), done_by_user_id = %s
               WHERE hotel_id = %s AND guest_id = %s AND status = 'open' AND (request = %s OR request_id = %s) RETURNING request_id""",
            (status, user_id, hotel_id, guest_id, str(request or "").strip(), str(request or "").strip()))
        closed = [row["request_id"] for row in cur.fetchall()]
        if not closed:
            raise Refused("Task not found")
        return {"request_ids": closed, "status": status}
    return run(write)


# ---------- guest messages ----------

def _with_pg(fn):
    """fn(cur) on the autocommit content connection (tuple rows)."""
    return _with(fn)


def _ensure_guest_operations():
    """guest_message is created by schema_guests with the core schema."""


def _message_rows(where, params):
    rows = db.rows(
        """SELECT m.message_id, m.hotel_id, h.hotel_name, m.guest_phone, m.channel, m.sent_at, m.source_ref, m.body, m.direction, m.status,
                  m.media_mime, m.media_note
           FROM guest_message m LEFT JOIN hotel h ON h.hotel_id = m.hotel_id WHERE """ + where + " ORDER BY m.sent_at NULLS FIRST, m.message_id",
        params)
    return [dict(row, sent_at=row["sent_at"].isoformat() if row["sent_at"] else "", hotel_name=row["hotel_name"] or "") for row in rows]


def messages_for_phone(guest_phone):
    """Messages for one phone at every hotel, and those not filed under a hotel yet."""
    return _message_rows("m.guest_phone = %s", (_phone(guest_phone),))


def list_guest_messages(hotel_id, guest_phone):
    return _message_rows("m.hotel_id = %s AND m.guest_phone = %s", (hotel_id, _phone(guest_phone)))


def whatsapp_text_window(messages):
    """Free text is open for 24 hours after the guest's latest real inbound WhatsApp message."""
    inbound = [str(row.get("sent_at") or "") for row in messages or []
               if row.get("channel") == "whatsapp" and row.get("direction") == "in" and row.get("status") != "simulated"]
    latest = max(inbound, default="")
    if not latest:
        return {"open": False, "until": ""}
    try:
        opened = datetime.fromisoformat(latest)
    except ValueError:
        return {"open": False, "until": ""}
    if opened.tzinfo is None:
        opened = opened.replace(tzinfo=timezone.utc)
    until = opened + timedelta(hours=24)
    return {"open": datetime.now(timezone.utc) < until, "until": until.isoformat()}


_MESSAGE_FIELDS = ("hotel_id", "guest_phone", "sent_at", "body", "direction", "status", "media_mime", "media_note")


def _write_message(channel, source_ref, fields, replace):
    """READ the message by channel and source_ref. Found: replace=True overwrites every field, otherwise only the non-empty ones.
    Missing: create it."""
    ref = str(source_ref or "").strip()
    if not ref:
        return {"error": "source_ref is required"}

    def write(cur):
        cur.execute("SELECT " + ", ".join(_MESSAGE_FIELDS) + " FROM guest_message WHERE channel = %s AND source_ref = %s FOR UPDATE",
                    (channel, ref))
        found = cur.fetchone()
        if found:
            current = {name: found[name] for name in _MESSAGE_FIELDS}
            current.update({name: value for name, value in fields.items() if replace or value not in (None, "")})
            cur.execute("UPDATE guest_message SET " + ", ".join(name + " = %s" for name in _MESSAGE_FIELDS)
                        + " WHERE channel = %s AND source_ref = %s", tuple(current[name] for name in _MESSAGE_FIELDS) + (channel, ref))
            return "updated"
        row = dict.fromkeys(_MESSAGE_FIELDS, "")
        row.update({name: value for name, value in fields.items() if value is not None})
        row["hotel_id"] = row["hotel_id"] or None
        row["sent_at"] = row["sent_at"] or None
        cur.execute("INSERT INTO guest_message (message_id, channel, source_ref, " + ", ".join(_MESSAGE_FIELDS) + ") VALUES (%s, %s, %s"
                    + ", %s" * len(_MESSAGE_FIELDS) + ")", (new_id("msg"), channel, ref) + tuple(row[name] for name in _MESSAGE_FIELDS))
        return "created"
    return run(write)


def _moment(value):
    text = str(value or "").strip()
    return datetime.fromisoformat(text.replace("Z", "+00:00")) if text else None


def save_guest_message(hotel_id, guest_phone, channel, sent_at, body, direction, source_ref, status="", media_mime="", media_note=""):
    """One message of a known guest at this hotel, keyed by channel and source_ref."""
    return _write_message(str(channel or "").strip().lower(), source_ref, {
        "hotel_id": hotel_id, "guest_phone": _phone(guest_phone), "sent_at": _moment(sent_at), "body": str(body or ""),
        "direction": str(direction or "").strip().lower(), "status": str(status or ""), "media_mime": str(media_mime or ""),
        "media_note": str(media_note or "")}, True)


def save_whatsapp_event(source_ref, guest_phone="", hotel_id="", sent_at="", body="", direction="", status="", media_mime="", media_name=""):
    """READ the WhatsApp message by id; set the non-empty fields when it exists, otherwise create it. hotel_id is '' while the
    guest's hotel is not known."""
    return _write_message("whatsapp", source_ref, {
        "hotel_id": hotel_id or None, "guest_phone": _phone(guest_phone), "sent_at": _moment(sent_at), "body": str(body or ""),
        "direction": str(direction or "").strip().lower(), "status": str(status or ""), "media_mime": str(media_mime or ""),
        "media_note": str(media_name or "")}, False)


def save_media_note(source_ref, note):
    """Set the file note on one WhatsApp message by id."""
    found = db.row("SELECT 1 AS found FROM guest_message WHERE channel = 'whatsapp' AND source_ref = %s", (str(source_ref or ""),))
    if not found:
        return "missing"
    db.execute("UPDATE guest_message SET media_note = %s WHERE channel = 'whatsapp' AND source_ref = %s", (str(note or ""), str(source_ref or "")))
    return "updated"


def whatsapp_message(hotel_id, source_ref):
    """{'guest_phone', 'source_ref', 'hotel_id'} for one WhatsApp message of this hotel, or None."""
    rows = _message_rows("m.channel = 'whatsapp' AND m.source_ref = %s AND m.hotel_id = %s", (str(source_ref or ""), hotel_id))
    return {"guest_phone": rows[0]["guest_phone"], "source_ref": str(source_ref or ""), "hotel_id": hotel_id} if rows else None

# ---------- message media: S3-compatible bucket when configured, else guest_media_file ----------

def _object_store():
    """S3_API is the endpoint plus the bucket name."""
    import boto3
    from botocore.config import Config
    from urllib.parse import urlparse
    parsed = urlparse(os.getenv("S3_API") or "")
    bucket = parsed.path.strip("/")
    endpoint = parsed.scheme + "://" + parsed.netloc if parsed.scheme and parsed.netloc else ""
    if not endpoint or not bucket:
        raise RuntimeError("Object storage is not configured")
    s3 = boto3.client("s3", endpoint_url=endpoint, aws_access_key_id=os.getenv("S3_ACCESS_KEY_ID"),
                      aws_secret_access_key=os.getenv("S3_SECRET_ACCESS_KEY"), region_name="auto",
                      config=Config(signature_version="s3v4", s3={"addressing_style": "path"}))
    return s3, bucket


def _bucket_configured():
    return bool(os.getenv("S3_ACCESS_KEY_ID") and os.getenv("S3_SECRET_ACCESS_KEY"))


def save_guest_media(source_ref, data, mime, filename):
    """Store the file bytes of one WhatsApp message, keyed by message id."""
    import psycopg2
    from urllib.parse import quote
    name = str(source_ref or "")
    if not name:
        return "message id is required"
    if not _bucket_configured():
        def write(cur):
            cur.execute("SELECT 1 FROM guest_media_file WHERE source_ref = %s", (name,))
            if cur.fetchone():
                return "updated"
            cur.execute("INSERT INTO guest_media_file (source_ref, mime, filename, data) VALUES (%s, %s, %s, %s)",
                        (name, str(mime or "application/octet-stream"), str(filename or ""), psycopg2.Binary(data)))
            return "created"
        return _with(write)
    from botocore.exceptions import ClientError
    s3, bucket = _object_store()
    try:
        s3.head_object(Bucket=bucket, Key=name)
        return "updated"
    except ClientError as exc:
        if str((exc.response or {}).get("Error", {}).get("Code") or "") not in ("404", "NoSuchKey", "NotFound"):
            raise
    s3.put_object(Bucket=bucket, Key=name, Body=data, ContentType=str(mime or "application/octet-stream"),
                  Metadata={"filename": quote(str(filename or ""))})
    return "created"


def read_guest_media(source_ref):
    """{'data', 'mime', 'filename'} saved for one WhatsApp message id, or None."""
    from urllib.parse import unquote
    name = str(source_ref or "")
    if not name:
        return None
    if not _bucket_configured():
        def read(cur):
            cur.execute("SELECT mime, filename, data FROM guest_media_file WHERE source_ref = %s", (name,))
            return cur.fetchone()
        row = _with(read)
        return {"data": bytes(row[2]), "mime": row[0] or "application/octet-stream", "filename": row[1] or ""} if row else None
    from botocore.exceptions import ClientError
    s3, bucket = _object_store()
    try:
        found = s3.get_object(Bucket=bucket, Key=name)
    except ClientError as exc:
        if str((exc.response or {}).get("Error", {}).get("Code") or "") in ("404", "NoSuchKey", "NotFound"):
            return None
        raise
    return {"data": found["Body"].read(), "mime": found.get("ContentType") or "application/octet-stream",
            "filename": unquote(str((found.get("Metadata") or {}).get("filename") or ""))}


# `from booking_system.guest_manager import guest_manager` keeps working: the name is this module.
guest_manager = sys.modules[__name__]
