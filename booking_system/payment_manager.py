"""Desk receipts on the `payment` table. A refund is its own row (payment_kind 'refund') whose reference is the refunded payment_id;
a void flips payment_status. The booking_balance view gives gross, paid and due. Each writer is one transaction that reads first."""

import sys

from booking_system.guest_manager import Refused, run
from booking_system.hotel_registry import new_id


def _booking(cur, hotel_id, booking_id):
    cur.execute("SELECT b.booking_id, h.currency FROM booking b JOIN hotel h ON h.hotel_id = b.hotel_id "
                "WHERE b.hotel_id = %s AND b.booking_id = %s FOR UPDATE OF b", (hotel_id, booking_id))
    found = cur.fetchone()
    if not found:
        raise Refused("Booking " + str(booking_id or "") + " not found")
    return found


def _insert(cur, hotel_id, user_id, booking_id, kind, amount, currency, method, reference):
    payment_id = new_id("pay")
    cur.execute(
        """INSERT INTO payment (payment_id, hotel_id, booking_id, payment_kind, amount, currency, payment_method, payment_status,
                                reference, recorded_by_user_id)
           VALUES (%s, %s, %s, %s, %s, %s, %s, 'received', %s, %s)""",
        (payment_id, hotel_id, booking_id, kind, amount, currency.strip(), method, reference, user_id),
    )
    return payment_id


def record_payment(hotel_id, user_id, booking_id, amount, payment_method, reference=None, payment_kind="payment"):
    """-> {'payment_id', 'booking_id', 'amount', 'currency', 'balance_due'}; the currency is the hotel's."""
    def write(cur):
        booking = _booking(cur, hotel_id, booking_id)
        if float(amount or 0) <= 0:
            raise Refused("Amount must be greater than zero")
        method = str(payment_method or "").strip().upper()
        if not method:
            raise Refused("Payment method is required")
        payment_id = _insert(cur, hotel_id, user_id, booking_id, payment_kind, amount, booking["currency"], method,
                             str(reference).strip() or None if reference is not None else None)
        cur.execute("SELECT balance_due FROM booking_balance WHERE booking_id = %s", (booking_id,))
        due = cur.fetchone()["balance_due"]
        return {"payment_id": payment_id, "booking_id": booking_id, "amount": float(amount), "currency": booking["currency"].strip(),
                "balance_due": None if due is None else float(due)}
    return run(write)


def _payments(cur, hotel_id, booking_id):
    """The booking's payment-kind rows in ledger order, each with payment_seq and whether a received refund points at it."""
    cur.execute(
        """SELECT p.*, u.user_name,
                  ROW_NUMBER() OVER (ORDER BY p.paid_at, p.payment_id) AS payment_seq,
                  EXISTS (SELECT 1 FROM payment r WHERE r.booking_id = p.booking_id AND r.payment_kind = 'refund'
                          AND r.payment_status = 'received' AND r.reference = p.payment_id) AS refunded
           FROM payment p LEFT JOIN user_account u ON u.user_id = p.recorded_by_user_id
           WHERE p.hotel_id = %s AND p.booking_id = %s AND p.payment_kind = 'payment'
           ORDER BY payment_seq""",
        (hotel_id, booking_id),
    )
    return cur.fetchall()


def record_refund(hotel_id, user_id, booking_id, payment_seq, reason=None):
    """Refund the full amount of the booking's payment number payment_seq (1 = first payment) with the same method."""
    def write(cur):
        _booking(cur, hotel_id, booking_id)
        found = [row for row in _payments(cur, hotel_id, booking_id) if row["payment_seq"] == int(payment_seq)]
        if not found:
            raise Refused("Payment " + str(payment_seq) + " not found on booking " + booking_id)
        paid = found[0]
        if paid["payment_status"] != "received":
            raise Refused("Payment " + str(payment_seq) + " is " + paid["payment_status"])
        if paid["refunded"]:
            raise Refused("Payment " + str(payment_seq) + " is already refunded")
        refund_id = _insert(cur, hotel_id, user_id, booking_id, "refund", paid["amount"], paid["currency"], paid["payment_method"],
                            paid["payment_id"])
        return {"payment_id": refund_id, "refunded_payment_id": paid["payment_id"], "booking_id": booking_id,
                "amount": float(paid["amount"]), "currency": paid["currency"].strip(), "reason": reason}
    return run(write)


def void_payment(hotel_id, user_id, payment_id):
    """Mark a received payment or refund void (entered in error)."""
    def write(cur):
        cur.execute("SELECT payment_id, payment_status FROM payment WHERE hotel_id = %s AND payment_id = %s FOR UPDATE",
                    (hotel_id, payment_id))
        found = cur.fetchone()
        if not found:
            raise Refused("Payment " + str(payment_id or "") + " not found")
        if found["payment_status"] == "void":
            return {"payment_id": payment_id, "payment_status": "void", "unchanged": True}
        cur.execute("UPDATE payment SET payment_status = 'void' WHERE payment_id = %s", (payment_id,))
        return {"payment_id": payment_id, "payment_status": "void"}
    return run(write)


def read_ledger(hotel_id, booking_id):
    """{'booking_id', 'currency', 'gross_amount', 'amount_paid', 'balance_due', 'entries': payments, 'refunds'} or {'error'}.
    An entry's payment_status is 'refunded' once a received refund references it."""
    def read(cur):
        cur.execute("SELECT * FROM booking_balance WHERE hotel_id = %s AND booking_id = %s", (hotel_id, booking_id))
        balance = cur.fetchone()
        if not balance:
            raise Refused("Booking " + str(booking_id or "") + " not found")
        entries = [{
            "payment_seq": int(row["payment_seq"]), "payment_id": row["payment_id"], "payment_ref": row["payment_id"],
            "payment_amount": float(row["amount"]), "currency": row["currency"].strip(), "payment_method": row["payment_method"],
            "payment_status": "refunded" if row["refunded"] else row["payment_status"], "payment_date": row["paid_at"].isoformat(),
            "recorded_by": row["user_name"] or "", "reference": row["reference"] or "", "note": row["reference"] or "",
        } for row in _payments(cur, hotel_id, booking_id)]
        cur.execute("SELECT payment_id, amount, payment_method, payment_status, reference, paid_at FROM payment "
                    "WHERE hotel_id = %s AND booking_id = %s AND payment_kind = 'refund' ORDER BY paid_at", (hotel_id, booking_id))
        refunds = [{"payment_id": row["payment_id"], "amount": float(row["amount"]), "payment_method": row["payment_method"],
                    "payment_status": row["payment_status"], "refunded_payment_id": row["reference"], "paid_at": row["paid_at"].isoformat()}
                   for row in cur.fetchall()]
        money = {name: None if balance[name] is None else float(balance[name]) for name in ("gross_amount", "amount_paid", "balance_due")}
        return dict(money, booking_id=booking_id, currency=(balance["currency"] or "").strip(), entries=entries, refunds=refunds)
    return run(read)


# `from booking_system.payment_manager import payment_manager` keeps working: the name is this module.
payment_manager = sys.modules[__name__]
