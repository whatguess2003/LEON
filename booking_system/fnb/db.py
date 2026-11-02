"""F&B transactions through the core pool (booking_system.db): settings are transaction-local, nothing outlives the transaction."""

import uuid

from booking_system import db

SETTINGS = (("statement_timeout", "8s"), ("lock_timeout", "5s"))


def new_id(prefix):
    return prefix + "_" + str(uuid.uuid4())


def transaction(fn):
    return db.transaction(fn, SETTINGS)
