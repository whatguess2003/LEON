"""Desk receipts on the `payment` table. A refund is its own row (payment_kind 'refund') whose reference is the refunded payment_id;
a void flips payment_status. The booking_balance view gives gross, paid and due. Each writer is one transaction that reads first."""

import sys

from booking_system.guest_manager import Refused, run
from booking_system.hotel_registry import new_id


def _booking(cur, hotel_id, booking_id):
    cur.execute("SELECT b.booking_id, b.guest_id, h.currency FROM booking b JOIN hotel h ON h.hotel_id = b.hotel_id "
                "WHERE b.hotel_id = %s AND b.booking_id = %s FOR UPDATE OF b", (hotel_id, booking_id))
    found = cur.fetchone()
    if not found:
        raise Refused("Booking " + str(booking_id or "") + " not found")
    return found


def _insert(cur, hotel_id, user_id, booking_id, kind, amount, currency, method, reference, guest_id=None):
    """A payment is the guest's. booking_id is set only when the money is also attached to one stay."""
    if not guest_id and booking_id:
        cur.execute("SELECT guest_id FROM booking WHERE hotel_id = %s AND booking_id = %s", (hotel_id, booking_id))
        found = cur.fetchone()
        guest_id = found["guest_id"] if found else None
    if not guest_id:
        raise Refused("A payment needs a guest")
    payment_id = new_id("pay")
    cur.execute(
        """INSERT INTO payment (payment_id, hotel_id, guest_id, booking_id, payment_kind, amount, currency, payment_method,
                                payment_status, reference, recorded_by_user_id)
           VALUES (%s, %s, %s, %s, %s, %s, %s, %s, 'received', %s, %s)""",
        (payment_id, hotel_id, guest_id, booking_id, kind, amount, currency.strip(), method, reference, user_id),
    )
    return payment_id


# Desk method -> finance transaction_code.payment_method. A method with no finance code is still a desk receipt.
_FINANCE_METHOD = {"CASH": "cash", "CARD": "card", "BANK TRANSFER": "bank_transfer", "OTA": "channel_collect",
                   "CHEQUE": "other", "OTHERS": "other", "VOUCHER": "voucher"}


def _mirror_folio(cur, hotel_id, user_id, booking_id, payment_id, amount, method):
    """Post the desk receipt onto the guest folio when a business date is open and a matching payment code exists.
    Anything short of that leaves the receipt and records why the folio was skipped."""
    finance_method = _FINANCE_METHOD.get(str(method or "").strip().upper())
    if not finance_method:
        return {"mirrored": False, "reason": "no finance method"}
    cur.execute("SELECT 1 AS open FROM business_day WHERE hotel_id = %s AND status = 'open'", (hotel_id,))
    if not cur.fetchone():
        return {"mirrored": False, "reason": "no open business date"}
    cur.execute(
        """SELECT transaction_code FROM transaction_code
           WHERE hotel_id = %s AND transaction_type = 'payment' AND status = 'active' AND payment_method = %s
           ORDER BY transaction_code LIMIT 1""",
        (hotel_id, finance_method),
    )
    code = cur.fetchone()
    if not code:
        return {"mirrored": False, "reason": "no payment code"}
    shift_id = None
    if finance_method == "cash":
        cur.execute("SELECT cashier_shift_id FROM cashier_shift WHERE hotel_id = %s AND cashier_user_id = %s AND status = 'open'",
                    (hotel_id, user_id))
        shift = cur.fetchone()
        if not shift:
            return {"mirrored": False, "reason": "no cashier shift"}
        shift_id = shift["cashier_shift_id"]
    from booking_system.finance.service import post_payment_in
    cur.execute("SAVEPOINT desk_folio")
    try:
        posted = post_payment_in(cur, hotel_id, "guest", booking_id, code["transaction_code"], amount, "desk", payment_id,
                                 user_id=user_id, cashier_shift_id=shift_id, booking_id=booking_id, reference=payment_id,
                                 desk_payment_id=payment_id)
    except Exception as exc:
        cur.execute("ROLLBACK TO SAVEPOINT desk_folio")
        cur.execute("RELEASE SAVEPOINT desk_folio")
        return {"mirrored": False, "reason": str(exc)}
    if not posted.get("ok"):
        cur.execute("ROLLBACK TO SAVEPOINT desk_folio")
        cur.execute("RELEASE SAVEPOINT desk_folio")
        return {"mirrored": False, "reason": posted.get("error") or "folio post skipped"}
    cur.execute("RELEASE SAVEPOINT desk_folio")
    posting = (posted.get("posting") or {}).get("posting_id")
    return {"mirrored": True, "posting_id": posting}


def record_payment(hotel_id, user_id, booking_id, amount, payment_method, reference=None, payment_kind="payment"):
    """-> {'payment_id', 'booking_id', 'amount', 'currency', 'balance_due', 'folio'}; the currency is the hotel's.
    folio says whether the receipt also landed on the guest folio."""
    def write(cur):
        booking = _booking(cur, hotel_id, booking_id)
        if float(amount or 0) <= 0:
            raise Refused("Amount must be greater than zero")
        method = str(payment_method or "").strip().upper()
        if not method:
            raise Refused("Payment method is required")
        payment_id = _insert(cur, hotel_id, user_id, booking_id, payment_kind, amount, booking["currency"], method,
                             str(reference).strip() or None if reference is not None else None, booking["guest_id"])
        folio = _mirror_folio(cur, hotel_id, user_id, booking_id, payment_id, amount, method) if payment_kind == "payment" else {
            "mirrored": False, "reason": "not a receipt"}
        cur.execute("SELECT balance_due FROM booking_balance WHERE booking_id = %s", (booking_id,))
        due = cur.fetchone()["balance_due"]
        return {"payment_id": payment_id, "booking_id": booking_id, "amount": float(amount), "currency": booking["currency"].strip(),
                "balance_due": None if due is None else float(due), "folio": folio}
    return run(write)


_REFUNDED = """COALESCE((SELECT SUM(r.amount) FROM payment r WHERE r.booking_id = p.booking_id AND r.payment_kind = 'refund'
              AND r.payment_status = 'received' AND r.reference = p.payment_id), 0)"""


def _payments(cur, hotel_id, booking_id):
    """The booking's payment-kind rows in ledger order, each with payment_seq, refunded_amount, and refunded once that sum covers it."""
    cur.execute(
        """SELECT p.*, u.user_name,
                  ROW_NUMBER() OVER (ORDER BY p.paid_at, p.payment_id) AS payment_seq,
                  """ + _REFUNDED + """ AS refunded_amount,
                  """ + _REFUNDED + """ >= p.amount AS refunded
           FROM payment p LEFT JOIN user_account u ON u.user_id = p.recorded_by_user_id
           WHERE p.hotel_id = %s AND p.booking_id = %s AND p.payment_kind = 'payment'
           ORDER BY payment_seq""",
        (hotel_id, booking_id),
    )
    return cur.fetchall()


def _refund_payment(cur, hotel_id, user_id, booking_id, payment_seq, reason=None):
    """Refund whatever is still left of payment number payment_seq (1 = first payment) with the same method."""
    _booking(cur, hotel_id, booking_id)
    found = [row for row in _payments(cur, hotel_id, booking_id) if row["payment_seq"] == int(payment_seq)]
    if not found:
        raise Refused("Payment " + str(payment_seq) + " not found on booking " + booking_id)
    paid = found[0]
    if paid["payment_status"] != "received":
        raise Refused("Payment " + str(payment_seq) + " is " + paid["payment_status"])
    remaining = paid["amount"] - paid["refunded_amount"]
    if remaining <= 0:
        raise Refused("Payment " + str(payment_seq) + " is already refunded")
    refund_id = _insert(cur, hotel_id, user_id, booking_id, "refund", remaining, paid["currency"], paid["payment_method"],
                        paid["payment_id"])
    return {"payment_id": refund_id, "refunded_payment_id": paid["payment_id"], "booking_id": booking_id,
            "amount": float(remaining), "currency": paid["currency"].strip(), "reason": reason}


def record_refund(hotel_id, user_id, booking_id, payment_seq, reason=None):
    """Refund whatever is still left of the booking's payment number payment_seq (1 = first payment) with the same method."""
    return run(lambda cur: _refund_payment(cur, hotel_id, user_id, booking_id, payment_seq, reason))


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
    An entry's payment_status is 'refunded' once received refunds cover its amount."""
    def read(cur):
        cur.execute("SELECT * FROM booking_balance WHERE hotel_id = %s AND booking_id = %s", (hotel_id, booking_id))
        balance = cur.fetchone()
        if not balance:
            raise Refused("Booking " + str(booking_id or "") + " not found")
        entries = [{
            "payment_seq": int(row["payment_seq"]), "payment_id": row["payment_id"], "payment_ref": row["payment_id"],
            "payment_amount": float(row["amount"]), "currency": row["currency"].strip(), "payment_method": row["payment_method"],
            "payment_status": "refunded" if row["refunded"] else row["payment_status"],
            "refunded_amount": float(row["refunded_amount"]), "payment_date": row["paid_at"].isoformat(),
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
