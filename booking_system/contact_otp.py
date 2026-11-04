"""One-time codes for a WhatsApp number or an email address.

A WhatsApp code goes out as a normal Cloud API message. A confirmed code stays valid for the rest of onboarding."""

import hashlib
import hmac
import secrets
from datetime import datetime, timedelta, timezone

from booking_system import db

TABLES = (
    ("contact_otp", """
        channel      VARCHAR(16)  NOT NULL CHECK (channel IN ('whatsapp','email')),
        address      VARCHAR(255) NOT NULL,
        code_hash    VARCHAR(64)  NOT NULL DEFAULT '',
        expires_at   TIMESTAMPTZ  NOT NULL,
        attempts     SMALLINT     NOT NULL DEFAULT 0 CHECK (attempts >= 0),
        confirmed_at TIMESTAMPTZ,
        PRIMARY KEY (channel, address),
        CHECK (channel <> 'whatsapp' OR (left(address, 1) = '+'
               AND translate(substr(address, 2), '0123456789', '') = '' AND char_length(address) BETWEEN 8 AND 16)),
        CHECK (channel <> 'email' OR (address = lower(btrim(address)) AND position('@' in address) > 1))"""),
)
_HOLD = timedelta(minutes=30)
_LIFE = timedelta(minutes=10)
_TRIES = 5


def ensure(cur, seen):
    """Area hook: the code table, after the core identity tables."""
    for name, body in TABLES:
        if name not in seen["relations"]:
            cur.execute("CREATE TABLE " + name + " (" + body + ")")
            seen["relations"].add(name)


def address(channel, value):
    """The stored phone or email, or '' when the channel or the value is not usable."""
    from booking_system.user_manager import normalize
    kind = {"whatsapp": "phone", "email": "email"}.get(channel)
    stored = normalize(kind, value) if kind else ""
    if channel == "whatsapp":
        digits = stored[1:] if stored.startswith("+") else ""
        return stored if digits.isdigit() and 7 <= len(digits) <= 15 else ""
    if channel == "email":
        return stored if stored.count("@") == 1 and not stored.startswith("@") and not stored.endswith("@") else ""
    return ""


def _hash(code):
    from booking_system.access import JWT_SECRET
    return hmac.new(JWT_SECRET.encode(), str(code or "").strip().encode(), hashlib.sha256).hexdigest()


def _now():
    return datetime.now(timezone.utc)


def _send_whatsapp(address_value, code):
    from booking_system.front_desk_manager import intelligent_front_desk
    sent = intelligent_front_desk.send_whatsapp_otp(address_value, code)
    return str(sent.get("error") or "")


def _send_email(address_value, code):
    return "Email delivery is not configured"


_SEND = {"whatsapp": _send_whatsapp, "email": _send_email}


def dispatch(channel, address_value, code):
    """Hand the code to the person. Empty when the message was accepted, otherwise the reason it was not."""
    sender = _SEND.get(channel)
    if sender is None:
        return "That channel is not usable"
    return sender(address_value, code)


def issue(channel, address_value):
    """Store a code after it is sent. 'waiting' when a live code is already out, 'invalid' when the address is not usable, otherwise the send error."""
    stored = address(channel, address_value)
    if not stored:
        return "invalid"

    def waiting(cur):
        cur.execute("SELECT code_hash, expires_at, attempts, confirmed_at FROM contact_otp WHERE channel = %s AND address = %s",
                    (channel, stored))
        row = cur.fetchone()
        moment = _now()
        if row and not row["confirmed_at"] and row["expires_at"] > moment and row["attempts"] < _TRIES and row["code_hash"]:
            return "waiting"
        return ""

    held = db.transaction(waiting)
    if held:
        return held
    code = str(secrets.randbelow(1000000)).zfill(6)
    error = dispatch(channel, stored, code)
    if error:
        return error

    def store(cur):
        cur.execute(
            """INSERT INTO contact_otp (channel, address, code_hash, expires_at, attempts, confirmed_at)
               VALUES (%s, %s, %s, %s, 0, NULL)
               ON CONFLICT (channel, address) DO UPDATE
                 SET code_hash = EXCLUDED.code_hash, expires_at = EXCLUDED.expires_at, attempts = 0, confirmed_at = NULL""",
            (channel, stored, _hash(code), _now() + _LIFE),
        )
    db.transaction(store)
    return "sent"


def check(channel, address_value, code):
    """True when the code matches a live, unconfirmed send. A miss counts toward the retry limit."""
    stored = address(channel, address_value)
    given = str(code or "").strip()
    if not stored or not given:
        return False

    def run(cur):
        cur.execute("SELECT code_hash, expires_at, attempts, confirmed_at FROM contact_otp WHERE channel = %s AND address = %s",
                    (channel, stored))
        row = cur.fetchone()
        moment = _now()
        if not row or row["confirmed_at"] or not row["code_hash"] or row["expires_at"] <= moment or row["attempts"] >= _TRIES:
            return False
        if not hmac.compare_digest(row["code_hash"], _hash(given)):
            cur.execute("UPDATE contact_otp SET attempts = attempts + 1 WHERE channel = %s AND address = %s", (channel, stored))
            return False
        cur.execute("UPDATE contact_otp SET confirmed_at = %s, code_hash = '' WHERE channel = %s AND address = %s",
                    (moment, channel, stored))
        return True
    return db.transaction(run)


def confirmed(channel, address_value):
    """True when this address was confirmed recently enough to finish opening the account."""
    stored = address(channel, address_value)
    if not stored:
        return False
    found = db.row(
        "SELECT confirmed_at FROM contact_otp WHERE channel = %s AND address = %s AND confirmed_at IS NOT NULL AND confirmed_at > %s",
        (channel, stored, _now() - _HOLD),
    )
    return bool(found)


def exhausted(channel, address_value):
    stored = address(channel, address_value)
    if not stored:
        return False
    found = db.row(
        "SELECT attempts, confirmed_at FROM contact_otp WHERE channel = %s AND address = %s AND confirmed_at IS NULL AND attempts >= %s",
        (channel, stored, _TRIES),
    )
    return bool(found)
