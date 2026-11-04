"""People, sign-in identities and hotel membership. A person is one user_account (usr_); each phone, email and username is a
user_identity row bound to it. Credentials and language live in the user_profile document keyed by user_id. Every write reads first."""

from passlib.hash import bcrypt
from psycopg2.extras import RealDictCursor

from booking_system import access, db, hotel_registry
from booking_system.content_store import client

profile_col = client["leon_manager_system"]["user_profile"]


def _phone(value):
    text = str(value or "").strip()
    digits = "".join(ch for ch in text if ch.isdigit())
    return "+" + digits if text.startswith("+") and digits else ""


def _lower(value):
    return str(value or "").strip().lower()


NORMALIZE = {"phone": _phone, "email": _lower, "username": _lower}


def normalize(identity_type, value):
    """The stored form of an identity value, or '' when the type is unknown or the value is empty."""
    return NORMALIZE[identity_type](value) if identity_type in NORMALIZE else ""


def find_user(cur, identity_type, value):
    cur.execute(
        """SELECT a.user_id, a.user_name, a.status FROM user_identity i JOIN user_account a ON a.user_id = i.user_id
           WHERE i.identity_type = %s AND i.identity_value = %s""",
        (identity_type, normalize(identity_type, value)),
    )
    return cur.fetchone()


def _identities(identities):
    """[(type, stored value)] for the non-empty values; raises on an unknown type or a value that normalizes to ''."""
    kept = []
    for identity_type, value in identities.items():
        if value in (None, ""):
            continue
        stored = normalize(identity_type, value)
        if not stored:
            raise ValueError("invalid " + str(identity_type) + ": " + str(value))
        kept.append((identity_type, stored))
    return kept


def _create_user(cur, user_name, identities, password, primary_language, nationality):
    """READ each identity first: one already bound to another person is refused. Returns (user_id, '') or (None, reason)."""
    kept = _identities(identities)
    if not kept:
        return None, "a phone, email or username is required"
    owners = set()
    for identity_type, stored in kept:
        found = find_user(cur, identity_type, stored)
        if found:
            owners.add(found["user_id"])
    if owners:
        return None, "already registered: " + ", ".join(sorted(t + " " + v for t, v in kept))
    user_id = hotel_registry.new_id("usr")
    cur.execute("INSERT INTO user_account (user_id, user_name, status) VALUES (%s, %s, 'active')", (user_id, str(user_name).strip().upper()))
    for identity_type, stored in kept:
        cur.execute("INSERT INTO user_identity (identity_id, user_id, identity_type, identity_value) VALUES (%s, %s, %s, %s)",
                    (hotel_registry.new_id("uid"), user_id, identity_type, stored))
    body = {"user_id": user_id, "primary_language": primary_language, "nationality": nationality}
    if str(password or ""):
        body["password_hash"] = bcrypt.hash(str(password))
    key, body = profile_col.staged_insert(body)
    profile_col.write_in(cur, key, body)
    return user_id, ""


def signup(user_name, identities, password, primary_language, nationality):
    """identities: {'phone': ..., 'email': ..., 'username': ...}. A password may be empty when a code confirmed the person.
    An email is accepted only after the Leon code confirmed it. Returns {'user_id'} or {'error'}."""
    from booking_system import contact_otp
    if not str(user_name or "").strip():
        return {"error": "name is required"}
    email = normalize("email", (identities or {}).get("email"))
    if email and not contact_otp.confirmed("email", email):
        return {"error": "confirm the email with the Leon code first"}
    try:
        user_id, reason = db.transaction(lambda cur: _create_user(cur, user_name, identities, password, primary_language, nationality))
    except ValueError as error:
        return {"error": str(error)}
    return {"user_id": user_id} if user_id else {"error": reason}


def _seed_roles(cur, hotel_id):
    """The D16 role matrix for one hotel: every role x module row. Returns {role_name: role_id}."""
    cur.execute("SELECT role_name, role_id FROM access_role WHERE hotel_id = %s", (hotel_id,))
    roles = {row["role_name"]: row["role_id"] for row in cur.fetchall()}
    for role_name, (cap, levels) in access.ROLES.items():
        if role_name not in roles:
            roles[role_name] = hotel_registry.new_id("rol")
            cur.execute("INSERT INTO access_role (role_id, hotel_id, role_name, daily_investigation_cap) VALUES (%s, %s, %s, %s)",
                        (roles[role_name], hotel_id, role_name, cap))
        for module_name, level in levels.items():
            cur.execute("INSERT INTO role_module (role_id, module_name, access_level) VALUES (%s, %s, %s) ON CONFLICT DO NOTHING",
                        (roles[role_name], module_name, level))
    return roles


def _join(cur, hotel_id, user_id, role_id, invited_by):
    cur.execute("SELECT status FROM hotel_user WHERE hotel_id = %s AND user_id = %s", (hotel_id, user_id))
    if cur.fetchone():
        return "already a user of this hotel"
    cur.execute(
        """INSERT INTO hotel_user (hotel_id, user_id, role_id, status, invited_by_user_id, activated_at)
           VALUES (%s, %s, %s, 'active', %s, now())""",
        (hotel_id, user_id, role_id, invited_by),
    )
    cur.execute("UPDATE user_account SET last_hotel_id = %s WHERE user_id = %s", (hotel_id, user_id))
    return ""


def create_hotel(user_id, hotel_name, currency, zone, city_name):
    """The hotel row, its roles, the creator as GENERAL_MANAGER, and the hotel_profile document, in one transaction."""
    reason = hotel_registry.validate(currency, zone)
    if reason:
        return {"error": reason}
    hotel = str(hotel_name or "").strip().upper()
    if not hotel:
        return {"error": "hotel name is required"}
    if hotel_registry.read_hotel(hotel):
        return {"error": "hotel " + hotel + " is already registered"}

    def run(cur):
        hotel_registry.write_hotel(cur, hotel, currency, zone)
        hotel_id = hotel_registry.read_hotel_with(cur, hotel)["hotel_id"]
        named = cur.connection.cursor(cursor_factory=RealDictCursor)
        roles = _seed_roles(named, hotel_id)
        problem = _join(named, hotel_id, user_id, roles["GENERAL_MANAGER"], None)
        if problem:
            raise ValueError(problem)
        hotel_registry.write_profile(cur, hotel_id, city_name)
        return hotel_id
    try:
        hotel_id = db.transaction(run, dict_rows=False)
    except ValueError as error:
        return {"error": str(error)}
    return {"hotel_id": hotel_id, "hotel_name": hotel}


def add_user(hotel_id, by_user_id, user_name, identities, role_name, password, primary_language, nationality):
    """READ first: an existing person (by any identity) joins the hotel; otherwise the person is created. Active at once.
    The WhatsApp invite and first-sign-in password step are the next stage."""
    def run(cur):
        cur.execute("SELECT role_id FROM access_role WHERE hotel_id = %s AND role_name = %s", (hotel_id, str(role_name).strip().upper()))
        role = cur.fetchone()
        if not role:
            return {"error": "no role " + str(role_name) + " at this hotel"}
        owners = {found["user_id"] for found in (find_user(cur, t, v) for t, v in _identities(identities)) if found}
        if len(owners) > 1:
            return {"error": "those identities belong to different people"}
        if owners:
            user_id = owners.pop()
        else:
            user_id, reason = _create_user(cur, user_name, identities, password, primary_language, nationality)
            if not user_id:
                return {"error": reason}
        problem = _join(cur, hotel_id, user_id, role["role_id"], by_user_id)
        return {"error": problem} if problem else {"user_id": user_id}
    try:
        return db.transaction(run)
    except ValueError as error:
        return {"error": str(error)}


def hotels_of(cur, user_id):
    cur.execute(
        """SELECT u.hotel_id, h.hotel_name, r.role_name FROM hotel_user u JOIN hotel h ON h.hotel_id = u.hotel_id
           JOIN access_role r ON r.role_id = u.role_id WHERE u.user_id = %s AND u.status = 'active' ORDER BY h.hotel_name""",
        (user_id,),
    )
    return cur.fetchall()


def _profile(user_id):
    return profile_col.find_one({"user_id": user_id}) or {}


def signin(identity_type, value, password, hotel_id=None):
    """Returns {'token', 'claims'} for one active hotel, {'choose': [hotels], 'token': account token} when several and none chosen,
    or {'error'}. The same person signs in by phone, email or username."""
    def run(cur):
        user = find_user(cur, identity_type, value)
        if not user or user["status"] != "active":
            return {"error": "unknown " + str(identity_type) + " or password"}
        profile = _profile(user["user_id"])
        if not profile.get("password_hash") or not bcrypt.verify(str(password or ""), profile["password_hash"]):
            return {"error": "unknown " + str(identity_type) + " or password"}
        hotels = hotels_of(cur, user["user_id"])
        chosen = [h for h in hotels if h["hotel_id"] == hotel_id] if hotel_id else hotels
        language = profile.get("primary_language")
        if len(chosen) != 1:
            return {"user_id": user["user_id"], "user_name": user["user_name"], "choose": hotels,
                    "token": access.account_token(user["user_id"], user["user_name"], language)}
        token, claims = access.issue_token(cur, chosen[0]["hotel_id"], user["user_id"], language)
        cur.execute("UPDATE user_account SET last_hotel_id = %s WHERE user_id = %s", (chosen[0]["hotel_id"], user["user_id"]))
        return {"user_id": user["user_id"], "token": token, "claims": claims}
    return db.transaction(run)


def session_for(user_id):
    """A session after a code confirmed the person. One hotel signs straight in; several (or none yet) leave the choice open."""
    def run(cur):
        cur.execute("SELECT user_id, user_name, status FROM user_account WHERE user_id = %s", (user_id,))
        user = cur.fetchone()
        if not user or user["status"] != "active":
            return {"error": "account is not active"}
        profile = _profile(user_id)
        hotels = hotels_of(cur, user_id)
        language = profile.get("primary_language")
        if len(hotels) == 1:
            token, claims = access.issue_token(cur, hotels[0]["hotel_id"], user_id, language)
            cur.execute("UPDATE user_account SET last_hotel_id = %s WHERE user_id = %s", (hotels[0]["hotel_id"], user_id))
            return {"user_id": user_id, "token": token, "claims": claims}
        return {"user_id": user_id, "user_name": user["user_name"], "choose": hotels,
                "token": access.account_token(user_id, user["user_name"], language)}
    return db.transaction(run)


def token_for(user_id, hotel_id):
    """Re-issue for one of the person's active hotels (switch_hotel, after onboarding). None when not a member."""
    language = _profile(user_id).get("primary_language")

    def run(cur):
        issued = access.issue_token(cur, hotel_id, user_id, language)
        return {"token": issued[0], "claims": issued[1]} if issued else None
    return db.transaction(run)


def read_user(user_id):
    """The person, their identities and their profile fields (no hash)."""
    def run(cur):
        cur.execute("SELECT user_id, user_name, status, last_hotel_id FROM user_account WHERE user_id = %s", (user_id,))
        user = cur.fetchone()
        if not user:
            return None
        cur.execute("SELECT identity_type, identity_value FROM user_identity WHERE user_id = %s ORDER BY identity_type", (user_id,))
        user = dict(user, identities=cur.fetchall(), hotels=hotels_of(cur, user_id))
        return user
    found = db.transaction(run)
    if found:
        profile = _profile(user_id)
        found["primary_language"] = profile.get("primary_language")
        found["nationality"] = profile.get("nationality")
    return found
