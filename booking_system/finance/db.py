"""Finance transactions through the core pool (booking_system.db): settings are transaction-local, nothing outlives the transaction."""

import uuid

from booking_system import db

STATEMENT_TIMEOUT = "8s"
LOCK_TIMEOUT = "5s"
SETTINGS = (("statement_timeout", STATEMENT_TIMEOUT), ("lock_timeout", LOCK_TIMEOUT))


def new_id(prefix):
    return prefix + "_" + str(uuid.uuid4())


def transaction(fn):
    return db.transaction(fn, SETTINGS)
