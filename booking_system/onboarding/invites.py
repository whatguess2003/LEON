"""Invites for imported staff accounts. An imported person joins the hotel as an 'invited' membership with no password; the manager
shares a one-time link (only its SHA-256 hash is stored) and the person sets their password by accepting it, which activates the
membership. A new link replaces the old one."""

import hashlib
import secrets
from datetime import datetime, timedelta, timezone

from booking_system import db
from booking_system.hotel_registry import new_id

VALID_DAYS = 14
LINK_PAGE = "onboarding.html?invite="


def _hash(token):
    return hashlib.sha256(str(token or "").encode("utf-8")).hexdigest()


def _expiry():
    return datetime.now(timezone.utc) + timedelta(days=VALID_DAYS)


def create_in(cur, hotel_id, user_id, invited_by, batch_id):
    """A pending invite on the caller's cursor; its link is issued later with reissue()."""
    invite_id = new_id("inv")
    cur.execute("""INSERT INTO onboarding_invite (invite_id, hotel_id, user_id, batch_id, invited_by, token_hash, status, expires_at)
                   VALUES (%s, %s, %s, %s, %s, %s, 'pending', %s)""",
                (invite_id, hotel_id, user_id, batch_id, invited_by, _hash(secrets.token_urlsafe(32)), _expiry()))
    return invite_id


def reissue(hotel_id, invite_id):
    """A fresh one-time link for a pending invite: {'ok', 'link', 'user_name', 'expires_at'}."""
    token = secrets.token_urlsafe(32)

    def run(cur):
        cur.execute("""SELECT i.status, a.user_name FROM onboarding_invite i JOIN user_account a ON a.user_id = i.user_id
                       WHERE i.invite_id = %s AND i.hotel_id = %s FOR UPDATE OF i""", (invite_id, hotel_id))
        found = cur.fetchone()
        if not found or found["status"] != "pending":
            return {"ok": False, "error": "no pending invite " + str(invite_id)}
        expires = _expiry()
        cur.execute("UPDATE onboarding_invite SET token_hash = %s, expires_at = %s WHERE invite_id = %s", (_hash(token), expires, invite_id))
        return {"ok": True, "invite_id": invite_id, "link": LINK_PAGE + token, "user_name": found["user_name"], "expires_at": expires.isoformat()}
    return db.transaction(run)


def read(token):
    """The pending invite a link names: {'user_name', 'hotel_name', 'has_password'} or None."""
    from booking_system.user_manager import _profile
    found = db.row("""SELECT i.user_id, a.user_name, h.hotel_name FROM onboarding_invite i JOIN user_account a ON a.user_id = i.user_id
                      JOIN hotel h ON h.hotel_id = i.hotel_id
                      WHERE i.token_hash = %s AND i.status = 'pending' AND i.expires_at > now()""", (_hash(token),))
    return dict(found, has_password=bool(_profile(found["user_id"]).get("password_hash"))) if found else None


def accept(token, password):
    """Set the password (a person who already has one proves it instead), activate the membership, and sign in.
    Returns {'ok', 'token', 'claims'} or {'ok': False, 'error'}."""
    from passlib.hash import bcrypt

    from booking_system import user_manager
    if len(str(password or "")) < 8:
        return {"ok": False, "error": "the password needs at least 8 characters"}

    def run(cur):
        cur.execute("""SELECT i.invite_id, i.hotel_id, i.user_id, h.hotel_name FROM onboarding_invite i JOIN hotel h ON h.hotel_id = i.hotel_id
                       WHERE i.token_hash = %s AND i.status = 'pending' AND i.expires_at > now() FOR UPDATE OF i""", (_hash(token),))
        invite = cur.fetchone()
        if not invite:
            return {"ok": False, "error": "this invite link is not valid any more; ask your manager for a new one"}
        profile = user_manager._profile(invite["user_id"])
        if profile.get("password_hash") and not bcrypt.verify(str(password), profile["password_hash"]):
            return {"ok": False, "error": "you already have a LEON password; use that one"}
        if not profile.get("password_hash"):
            key, body = user_manager.profile_col.staged_update({"user_id": invite["user_id"]},
                                                               {"$set": {"password_hash": bcrypt.hash(str(password))}}, upsert=True)
            user_manager.profile_col.write_in(cur, key, body)
        cur.execute("""UPDATE hotel_user SET status = 'active', activated_at = now(), access_version = access_version + 1
                       WHERE hotel_id = %s AND user_id = %s AND status = 'invited'""", (invite["hotel_id"], invite["user_id"]))
        cur.execute("UPDATE onboarding_invite SET status = 'accepted', accepted_at = now() WHERE invite_id = %s", (invite["invite_id"],))
        return {"ok": True, "hotel_id": invite["hotel_id"], "hotel_name": invite["hotel_name"], "user_id": invite["user_id"]}
    done = db.transaction(run)
    if not done["ok"]:
        return done
    signed = user_manager.token_for(done["user_id"], done["hotel_id"])
    return dict(done, token=signed["token"])


def listing(hotel_id):
    return db.rows("""SELECT i.invite_id, i.status, i.expires_at, i.accepted_at, a.user_name, r.role_name FROM onboarding_invite i
                      JOIN user_account a ON a.user_id = i.user_id
                      LEFT JOIN hotel_user u ON u.hotel_id = i.hotel_id AND u.user_id = i.user_id
                      LEFT JOIN access_role r ON r.role_id = u.role_id
                      WHERE i.hotel_id = %s ORDER BY i.created_at""", (hotel_id,))
