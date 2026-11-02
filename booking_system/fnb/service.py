"""F&B outlet operations at PMS level: outlets, menu items, checks, discounts, voids, settlement (direct at the outlet or to a guest folio).
Transaction codes, taxes, business dates and postings are Finance's: an item carries a Finance charge code, and settlement posts through
finance.service post_charge_in / post_payment_in on this module's cursor, so the check closes only if every posting is accepted.
Every write reads first. A name close to an existing one (not equal) is returned for clarification unless confirm_new is set.
Results are dicts: {"ok": ...} on success, {"error": ...} when refused, {"clarify": [...]} on a near match."""

from decimal import ROUND_HALF_UP, Decimal
from difflib import SequenceMatcher

import psycopg2

from booking_system.content_store import client
from booking_system.finance import service as finance
from booking_system.fnb.db import new_id, transaction

NEAR = 0.8
CENT = Decimal("0.01")
SOURCE = "fnb"
OUTLET_TYPES = ("restaurant", "bar", "room_service", "cafe", "banquet", "other")
STATUSES = ("active", "inactive")
menu_docs = client["leon_hotel_memory"]["fnb_menu_item"]


class _Refused(Exception):
    def __init__(self, result):
        super().__init__(result.get("error"))
        self.result = result


def _run(fn):
    """transaction(fn). A _Refused raised inside, or a database refusal (constraint, Finance trigger), rolls the whole
    transaction back and is returned as the result."""
    try:
        return transaction(fn)
    except _Refused as refused:
        return refused.result
    except psycopg2.Error as error:
        return {"error": (error.diag.message_primary or str(error)).strip()}


def _name(value):
    return " ".join(str(value or "").split()).upper()


def _money(value):
    return Decimal(str(value)).quantize(CENT, rounding=ROUND_HALF_UP)


def _choice(value, allowed, label):
    return "" if value in allowed else label + " must be one of " + ", ".join(allowed)


def _near(cur, table, name_col, scope, name):
    where = " AND ".join(col + " = %s" for col in scope)
    cur.execute("SELECT * FROM " + table + " WHERE " + where, tuple(scope.values()))
    return [row for row in cur.fetchall() if SequenceMatcher(None, row[name_col], name).ratio() >= NEAR]


def _save(cur, table, id_col, prefix, scope, name_col, name, values, row_id, confirm_new):
    """READ by id, then by exact name in scope, then near names; UPDATE the found row or INSERT a new one."""
    where = " AND ".join(col + " = %s" for col in scope)
    if row_id:
        cur.execute("SELECT * FROM " + table + " WHERE " + id_col + " = %s AND " + where + " FOR UPDATE", (row_id, *scope.values()))
        current = cur.fetchone()
        if current is None:
            return {"error": table + " " + row_id + " not found"}
    else:
        cur.execute("SELECT * FROM " + table + " WHERE " + name_col + " = %s AND " + where + " FOR UPDATE", (name, *scope.values()))
        current = cur.fetchone()
    fields = dict(values, **{name_col: name})
    if current is None:
        near = [] if confirm_new else _near(cur, table, name_col, scope, name)
        if near:
            return {"clarify": near, "message": "Similar " + name_col + " exists; pass its id to update it, or confirm_new to create " + name}
        row = dict(scope, **fields, **{id_col: new_id(prefix)})
        cur.execute("INSERT INTO " + table + " (" + ", ".join(row) + ") VALUES (" + ", ".join(["%s"] * len(row)) + ") RETURNING *",
                    tuple(row.values()))
        return {"ok": cur.fetchone(), "action": "created"}
    changed = {col: value for col, value in fields.items() if current[col] != value}
    if not changed:
        return {"ok": current, "action": "unchanged"}
    cur.execute("UPDATE " + table + " SET " + ", ".join(col + " = %s" for col in changed) + " WHERE " + id_col + " = %s RETURNING *",
                (*changed.values(), current[id_col]))
    return {"ok": cur.fetchone(), "action": "updated"}


def _delete(cur, table, id_col, row_id, hotel_id, references):
    """READ the row and its references; delete only an unreferenced row."""
    cur.execute("SELECT * FROM " + table + " WHERE " + id_col + " = %s AND hotel_id = %s FOR UPDATE", (row_id, hotel_id))
    row = cur.fetchone()
    if row is None:
        return {"error": table + " " + row_id + " not found"}
    for ref_table, ref_col in references:
        cur.execute("SELECT 1 FROM " + ref_table + " WHERE " + ref_col + " = %s LIMIT 1", (row_id,))
        if cur.fetchone():
            return {"error": table + " " + row_id + " is used by " + ref_table + "; set it inactive instead"}
    cur.execute("DELETE FROM " + table + " WHERE " + id_col + " = %s", (row_id,))
    return {"ok": row, "action": "deleted"}


def _outlet(cur, hotel_id, outlet_id, lock=""):
    cur.execute("SELECT * FROM outlet WHERE outlet_id = %s AND hotel_id = %s " + lock, (outlet_id, hotel_id))
    return cur.fetchone()


def _open_day(cur, hotel_id):
    """Finance's open business date and the hotel currency, or None when the hotel has no open business date."""
    cur.execute("""SELECT d.business_date, btrim(h.currency) AS currency FROM business_day d JOIN hotel h ON h.hotel_id = d.hotel_id
                   WHERE d.hotel_id = %s AND d.status = 'open'""", (hotel_id,))
    return cur.fetchone()


# ---------- outlets and menu ----------

def read_outlets(hotel_id):
    def run(cur):
        cur.execute("SELECT * FROM outlet WHERE hotel_id = %s ORDER BY outlet_name", (hotel_id,))
        return {"ok": cur.fetchall()}
    return _run(run)


def save_outlet(hotel_id, outlet_name, outlet_type, status="active", outlet_id=None, confirm_new=False):
    problem = _choice(outlet_type, OUTLET_TYPES, "outlet_type") or _choice(status, STATUSES, "status")
    if problem:
        return {"error": problem}
    return _run(lambda cur: _save(cur, "outlet", "outlet_id", "out", {"hotel_id": hotel_id}, "outlet_name", _name(outlet_name),
                                         {"outlet_type": outlet_type, "status": status}, outlet_id, confirm_new))


def delete_outlet(hotel_id, outlet_id):
    return _run(lambda cur: _delete(cur, "outlet", "outlet_id", outlet_id, hotel_id,
                                           (("fnb_menu_item", "outlet_id"), ("fnb_check", "outlet_id"), ("finance_posting", "outlet_id"))))


_MENU_SQL = """
    SELECT i.*, t.transaction_code, t.is_taxable, d.department_name,
           COALESCE((SELECT json_agg(x.tax_code ORDER BY x.tax_code) FROM transaction_code_tax l JOIN tax_code x ON x.tax_code_id = l.tax_code_id
                     WHERE l.transaction_code_id = t.transaction_code_id), '[]') AS tax_codes
    FROM fnb_menu_item i JOIN transaction_code t ON t.transaction_code_id = i.transaction_code_id
    JOIN department d ON d.department_id = t.department_id"""


def read_menu(hotel_id, outlet_id):
    """Items with their Finance charge code, department and tax codes, plus the content document."""
    def run(cur):
        cur.execute(_MENU_SQL + " WHERE i.hotel_id = %s AND i.outlet_id = %s ORDER BY d.department_name, i.item_name", (hotel_id, outlet_id))
        items = cur.fetchall()
        content = {doc["item_id"]: doc for doc in menu_docs.find({"hotel_id": hotel_id, "outlet_id": outlet_id})}
        return {"ok": [dict(item, content=content.get(item["item_id"])) for item in items]}
    return _run(run)


def save_menu_item(hotel_id, outlet_id, item_name, price, transaction_code, status="active",
                   description=None, image_url=None, item_id=None, confirm_new=False):
    """transaction_code is a Finance charge code (e.g. FNB_FOOD); it decides the item's department and taxes.
    description / image_url go to the fnb_menu_item document."""
    problem = _choice(status, STATUSES, "status")
    if problem:
        return {"error": problem}
    code = finance.resolve_transaction_code(hotel_id, transaction_code)
    if not code["ok"]:
        return {"error": code["error"], "similar": code.get("similar")}
    if code["transaction_code"]["transaction_type"] != "charge":
        return {"error": code["transaction_code"]["transaction_code"] + " is not a charge code"}

    def run(cur):
        if _outlet(cur, hotel_id, outlet_id) is None:
            return {"error": "outlet " + outlet_id + " not found"}
        result = _save(cur, "fnb_menu_item", "item_id", "fmi", {"hotel_id": hotel_id, "outlet_id": outlet_id}, "item_name", _name(item_name),
                       {"price": _money(price), "transaction_code_id": code["transaction_code"]["transaction_code_id"], "status": status},
                       item_id, confirm_new)
        if "ok" in result and (description is not None or image_url is not None):
            item = result["ok"]
            menu_docs.write_in(cur, item["item_id"], {"item_id": item["item_id"], "hotel_id": hotel_id, "outlet_id": outlet_id,
                                                     "item_name": item["item_name"], "description": description, "image_url": image_url})
        return result
    return _run(run)


def delete_menu_item(hotel_id, item_id):
    def run(cur):
        result = _delete(cur, "fnb_menu_item", "item_id", item_id, hotel_id, (("fnb_check_line", "item_id"),))
        if "ok" in result:
            cur.execute("DELETE FROM content_document WHERE database_name = %s AND collection_name = %s AND document_key = %s",
                        (menu_docs.database_name, menu_docs.collection_name, item_id))
        return result
    return _run(run)


# ---------- checks ----------

def _check(cur, hotel_id, check_id, lock=True):
    cur.execute("SELECT * FROM fnb_check WHERE check_id = %s AND hotel_id = %s" + (" FOR UPDATE" if lock else ""), (check_id, hotel_id))
    return cur.fetchone()


def _open_check(cur, hotel_id, check_id):
    check = _check(cur, hotel_id, check_id)
    if check is None:
        return None, "check " + check_id + " not found"
    if check["status"] != "open":
        return None, "check " + str(check["check_number"]) + " is " + check["status"]
    return check, ""


def _code_groups(cur, check_id):
    """Net per Finance charge code over live lines: the amounts settlement posts."""
    cur.execute("""SELECT t.transaction_code, SUM(a.gross) AS gross, SUM(a.discount) AS discount, SUM(a.net) AS net
                   FROM fnb_check_line_amount a JOIN transaction_code t ON t.transaction_code_id = a.transaction_code_id
                   WHERE a.check_id = %s GROUP BY t.transaction_code ORDER BY t.transaction_code""", (check_id,))
    return cur.fetchall()


def _running(hotel_id, groups):
    """Subtotal and discount at menu prices; net, tax and total as Finance would split each code's price under the hotel's tax
    settings (inclusive: the total is the price; exclusive: tax is added). A refused quote leaves net, tax and total unknown, with the reason."""
    out = {"subtotal": sum((g["gross"] for g in groups), Decimal(0)), "discount_total": sum((g["discount"] for g in groups), Decimal(0)),
           "price_after_discount": sum((g["net"] for g in groups), Decimal(0)), "net": Decimal(0), "tax_total": Decimal(0), "total": Decimal(0),
           "lines": len(groups), "tax_refused": []}
    for group in groups:
        quote = finance.quote_tax(hotel_id, group["transaction_code"], group["net"])
        if quote["ok"]:
            out.update(net=out["net"] + Decimal(quote["net"]), tax_total=out["tax_total"] + Decimal(quote["tax_total"]),
                       total=out["total"] + Decimal(quote["total"]), tax_pricing=quote["tax_pricing"])
        else:
            out["tax_refused"].append({"transaction_code": group["transaction_code"], "error": quote["error"]})
    if out["tax_refused"]:
        out.update(net=None, tax_total=None, total=None)
    return out


def _detail(cur, check):
    cur.execute("""SELECT l.*, i.item_name, t.transaction_code, a.gross, a.net FROM fnb_check_line l
                   JOIN fnb_menu_item i ON i.item_id = l.item_id JOIN transaction_code t ON t.transaction_code_id = l.transaction_code_id
                   LEFT JOIN fnb_check_line_amount a ON a.line_id = l.line_id WHERE l.check_id = %s ORDER BY l.line_seq""", (check["check_id"],))
    lines = cur.fetchall()
    cur.execute("""SELECT p.posting_id, p.folio_type, p.folio_ref, t.transaction_code, p.transaction_type, p.amount, p.tax_code_id, p.source_ref
                   FROM finance_posting p JOIN transaction_code t ON t.transaction_code_id = p.transaction_code_id
                   WHERE p.hotel_id = %s AND p.source_module = %s AND split_part(p.source_ref, '/', 1) = %s ORDER BY p.posted_at, p.posting_id""",
                (check["hotel_id"], SOURCE, check["check_id"]))
    return dict(check, lines=lines, postings=cur.fetchall(), groups=_code_groups(cur, check["check_id"]))


def read_check(hotel_id, check_id):
    """The check, its lines, its Finance postings, and (while open) a running total with Finance-quoted tax."""
    def run(cur):
        check = _check(cur, hotel_id, check_id, lock=False)
        return {"ok": _detail(cur, check)} if check else {"error": "check " + check_id + " not found"}
    result = _run(run)
    if "ok" in result and result["ok"]["status"] == "open":
        result["ok"]["running"] = _running(hotel_id, result["ok"]["groups"])
    return result


def read_checks(hotel_id, outlet_id, business_date=None, status=None):
    def run(cur):
        day = business_date
        if day is None:
            opened = _open_day(cur, hotel_id)
            if opened is None:
                return {"error": "the hotel has no open business date"}
            day = opened["business_date"]
        cur.execute(
            """SELECT c.*, n.net AS running_net FROM fnb_check c
               CROSS JOIN LATERAL (SELECT COALESCE(SUM(net), 0) AS net FROM fnb_check_line_amount a WHERE a.check_id = c.check_id) n
               WHERE c.hotel_id = %s AND c.outlet_id = %s AND c.business_date = %s AND (%s::text IS NULL OR c.status = %s)
               ORDER BY c.check_number""",
            (hotel_id, outlet_id, day, status, status),
        )
        return {"ok": cur.fetchall(), "business_date": day}
    return _run(run)


def open_check(hotel_id, outlet_id, covers, table_number, user_id, confirm_new=False):
    """Dated on Finance's open business date. An open check already on the same table is returned for clarification unless confirm_new."""
    def run(cur):
        outlet = _outlet(cur, hotel_id, outlet_id, "FOR UPDATE")
        if outlet is None:
            return {"error": "outlet " + outlet_id + " not found"}
        if outlet["status"] != "active":
            return {"error": "outlet " + outlet["outlet_name"] + " is inactive"}
        day = _open_day(cur, hotel_id)
        if day is None:
            return {"error": "the hotel has no open business date"}
        if table_number and not confirm_new:
            cur.execute("SELECT * FROM fnb_check WHERE outlet_id = %s AND table_number = %s AND status = 'open'", (outlet_id, table_number))
            same = cur.fetchall()
            if same:
                return {"clarify": same, "message": "Table " + table_number + " already has an open check; add to it, or confirm_new"}
        cur.execute("SELECT COALESCE(MAX(check_number), 0) + 1 AS n FROM fnb_check WHERE outlet_id = %s AND business_date = %s",
                    (outlet_id, day["business_date"]))
        cur.execute(
            """INSERT INTO fnb_check (check_id, hotel_id, outlet_id, business_date, check_number, table_number, covers, status, currency, opened_by_user_id)
               VALUES (%s, %s, %s, %s, %s, %s, %s, 'open', %s, %s) RETURNING *""",
            (new_id("fck"), hotel_id, outlet_id, day["business_date"], cur.fetchone()["n"], table_number, covers, day["currency"], user_id),
        )
        return {"ok": cur.fetchone(), "action": "created"}
    return _run(run)


def update_check(hotel_id, check_id, covers, table_number):
    def run(cur):
        _, problem = _open_check(cur, hotel_id, check_id)
        if problem:
            return {"error": problem}
        cur.execute("UPDATE fnb_check SET covers = %s, table_number = %s WHERE check_id = %s RETURNING *", (covers, table_number, check_id))
        return {"ok": cur.fetchone(), "action": "updated"}
    return _run(run)


def add_line(hotel_id, check_id, item_id, quantity, user_id):
    """Price and Finance charge code are copied from the menu item at the time of ordering."""
    def run(cur):
        check, problem = _open_check(cur, hotel_id, check_id)
        if problem:
            return {"error": problem}
        cur.execute("SELECT * FROM fnb_menu_item WHERE item_id = %s AND outlet_id = %s", (item_id, check["outlet_id"]))
        item = cur.fetchone()
        if item is None:
            return {"error": "item " + item_id + " is not on this outlet's menu"}
        if item["status"] != "active":
            return {"error": item["item_name"] + " is inactive"}
        cur.execute("SELECT COALESCE(MAX(line_seq), 0) + 1 AS n FROM fnb_check_line WHERE check_id = %s", (check_id,))
        cur.execute(
            """INSERT INTO fnb_check_line (line_id, check_id, line_seq, item_id, transaction_code_id, quantity, unit_price, added_by_user_id)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s) RETURNING *""",
            (new_id("fcl"), check_id, cur.fetchone()["n"], item_id, item["transaction_code_id"], Decimal(str(quantity)), item["price"], user_id),
        )
        return {"ok": cur.fetchone(), "action": "created"}
    return _run(run)


def _line(cur, check_id, line_id):
    cur.execute("SELECT * FROM fnb_check_line WHERE line_id = %s AND check_id = %s FOR UPDATE", (line_id, check_id))
    line = cur.fetchone()
    if line is None:
        return None, "line " + line_id + " not found on this check"
    if line["voided"]:
        return None, "line " + str(line["line_seq"]) + " is voided"
    return line, ""


def discount_line(hotel_id, check_id, line_id, amount, reason):
    """amount 0 removes the discount."""
    def run(cur):
        _, problem = _open_check(cur, hotel_id, check_id)
        _, problem = (None, problem) if problem else _line(cur, check_id, line_id)
        if problem:
            return {"error": problem}
        value = _money(amount)
        if value > 0 and not str(reason or "").strip():
            return {"error": "a discount needs a reason"}
        cur.execute("UPDATE fnb_check_line SET discount_amount = %s, discount_reason = %s WHERE line_id = %s RETURNING *",
                    (value, str(reason).strip() if value > 0 else None, line_id))
        return {"ok": cur.fetchone(), "action": "updated"}
    return _run(run)


def discount_check(hotel_id, check_id, percent, reason):
    """Sets each live line's discount to percent of its gross."""
    def run(cur):
        _, problem = _open_check(cur, hotel_id, check_id)
        if problem:
            return {"error": problem}
        rate = Decimal(str(percent))
        if not Decimal(0) <= rate <= Decimal(100):
            return {"error": "percent must be between 0 and 100"}
        if rate > 0 and not str(reason or "").strip():
            return {"error": "a discount needs a reason"}
        cur.execute(
            """UPDATE fnb_check_line SET discount_amount = round(round(quantity * unit_price, 2) * %s / 100, 2),
                      discount_reason = CASE WHEN %s > 0 THEN %s END
               WHERE check_id = %s AND NOT voided RETURNING *""",
            (rate, rate, str(reason or "").strip(), check_id),
        )
        return {"ok": cur.fetchall(), "action": "updated"}
    return _run(run)


def void_line(hotel_id, check_id, line_id, reason, user_id):
    def run(cur):
        _, problem = _open_check(cur, hotel_id, check_id)
        _, problem = (None, problem) if problem else _line(cur, check_id, line_id)
        if problem:
            return {"error": problem}
        if not str(reason or "").strip():
            return {"error": "a void needs a reason"}
        cur.execute(
            "UPDATE fnb_check_line SET voided = true, void_reason = %s, voided_by_user_id = %s, voided_at = now() WHERE line_id = %s RETURNING *",
            (str(reason).strip(), user_id, line_id),
        )
        return {"ok": cur.fetchone(), "action": "voided"}
    return _run(run)


def void_check(hotel_id, check_id, reason, user_id):
    """Only an open check can be voided; a settled check is corrected the hotel's way (fnb.closed_check_correction):
    adjust_closed_check, or request_reopen then decide_reopen."""
    def run(cur):
        _, problem = _open_check(cur, hotel_id, check_id)
        if problem:
            return {"error": problem}
        if not str(reason or "").strip():
            return {"error": "a void needs a reason"}
        cur.execute(
            "UPDATE fnb_check SET status = 'void', void_reason = %s, closed_at = now(), closed_by_user_id = %s WHERE check_id = %s RETURNING *",
            (str(reason).strip(), user_id, check_id),
        )
        return {"ok": cur.fetchone(), "action": "voided"}
    return _run(run)


def _settlement_ref(check):
    """'<check_id>' for the first settlement, '<check_id>/r<n>' after the check was reopened n times."""
    return check["check_id"] + ("/r" + str(check["settlement_round"]) if check["settlement_round"] else "")


# ---------- closed-check corrections: the hotel's fnb.closed_check_correction decides the only path ----------

def _closed_check(cur, hotel_id, check_id, method):
    """The closed check, locked, when the hotel's correction method on the open business date is method; else _Refused."""
    check = _check(cur, hotel_id, check_id)
    if check is None:
        raise _Refused({"error": "check " + check_id + " not found"})
    if check["status"] != "closed":
        raise _Refused({"error": "check " + str(check["check_number"]) + " is " + check["status"] + "; only a settled check is corrected"})
    day = _open_day(cur, hotel_id)
    if day is None:
        raise _Refused({"error": "the hotel has no open business date"})
    cur.execute("SELECT hotel_setting_required(%s, 'fnb.closed_check_correction', %s) #>> '{}' AS method", (hotel_id, day["business_date"]))
    chosen = cur.fetchone()["method"]
    if chosen != method:
        raise _Refused({"error": "this hotel corrects settled checks by " + chosen + " (hotel setting fnb.closed_check_correction), not " + method,
                        "method": chosen})
    return check


def _check_folio(cur, check):
    cur.execute("""SELECT folio_type, folio_ref, booking_id FROM finance_posting
                   WHERE hotel_id = %s AND source_module = %s AND split_part(source_ref, '/', 1) = %s AND transaction_type = 'charge'
                     AND reverses_posting_id IS NULL ORDER BY posted_at DESC, posting_id LIMIT 1""", (check["hotel_id"], SOURCE, check["check_id"]))
    return cur.fetchone()


def adjust_closed_check(hotel_id, check_id, transaction_code, amount, reason, user_id):
    """folio_adjustment: the check stays closed; an adjustment (Finance code of type adjustment, signed amount, usually negative)
    posts on the folio the check was settled to, and the correction is recorded."""
    def run(cur):
        check = _closed_check(cur, hotel_id, check_id, "folio_adjustment")
        if not str(reason or "").strip():
            return {"error": "a correction needs a reason"}
        folio = _check_folio(cur, check)
        cur.execute("SELECT COUNT(*) + 1 AS n FROM fnb_check_correction WHERE check_id = %s", (check_id,))
        number = cur.fetchone()["n"]
        posted = finance.post_adjustment_in(cur, hotel_id, folio["folio_type"], folio["folio_ref"], transaction_code, amount, str(reason).strip(),
                                            SOURCE, check_id + "/adj/" + str(number), user_id=user_id, booking_id=folio["booking_id"],
                                            outlet_id=check["outlet_id"], reference="check " + str(check["check_number"]))
        if not posted["ok"]:
            raise _Refused({"error": posted["error"]})
        cur.execute("""INSERT INTO fnb_check_correction (correction_id, hotel_id, check_id, method, status, reason, amount, transaction_code_id,
                                                         settlement_round, requested_by_user_id)
                       VALUES (%s, %s, %s, 'folio_adjustment', 'applied', %s, %s, %s, %s, %s) RETURNING *""",
                    (new_id("fcc"), hotel_id, check_id, str(reason).strip(), _money(amount), posted["posting"]["transaction_code_id"],
                     check["settlement_round"], user_id))
        return {"ok": cur.fetchone(), "action": "adjusted", "posting": posted["posting"], "taxes": posted["taxes"]}
    return _run(run)


def request_reopen(hotel_id, check_id, reason, user_id):
    """reopen_with_approval, step 1: anyone at the outlet asks; nothing changes on the check until a manager approves."""
    def run(cur):
        check = _closed_check(cur, hotel_id, check_id, "reopen_with_approval")
        if not str(reason or "").strip():
            return {"error": "a reopen request needs a reason"}
        cur.execute("SELECT * FROM fnb_check_correction WHERE check_id = %s AND status = 'requested'", (check_id,))
        waiting = cur.fetchone()
        if waiting:
            return {"ok": waiting, "action": "exists"}
        cur.execute("""INSERT INTO fnb_check_correction (correction_id, hotel_id, check_id, method, status, reason, settlement_round, requested_by_user_id)
                       VALUES (%s, %s, %s, 'reopen_with_approval', 'requested', %s, %s, %s) RETURNING *""",
                    (new_id("fcc"), hotel_id, check_id, str(reason).strip(), check["settlement_round"], user_id))
        return {"ok": cur.fetchone(), "action": "requested"}
    return _run(run)


def decide_reopen(hotel_id, correction_id, approve, user_id, cashier_shift_id=None):
    """reopen_with_approval, step 2, by a manager other than the requester. Approve: every live posting of the check's settlement
    (charges with their taxes, payments) is reversed on the open business date, and the check is open again for its next settlement
    round. A cash payment reversal needs the approver's open cashier_shift_id (Finance rule)."""
    def run(cur):
        cur.execute("SELECT * FROM fnb_check_correction WHERE correction_id = %s AND hotel_id = %s FOR UPDATE", (correction_id, hotel_id))
        request = cur.fetchone()
        if request is None or request["method"] != "reopen_with_approval":
            return {"error": "reopen request " + correction_id + " not found"}
        if request["status"] != "requested":
            return {"error": "reopen request is already " + request["status"]}
        if request["requested_by_user_id"] == user_id:
            return {"error": "the person who asked to reopen cannot approve it"}
        check = _closed_check(cur, hotel_id, request["check_id"], "reopen_with_approval")
        cur.execute("""UPDATE fnb_check_correction SET status = %s, decided_by_user_id = %s, decided_at = now() WHERE correction_id = %s""",
                    ("approved" if approve else "rejected", user_id, correction_id))
        if not approve:
            return {"ok": dict(request, status="rejected"), "action": "rejected"}
        cur.execute("""SELECT p.posting_id FROM finance_posting p
                       WHERE p.hotel_id = %s AND p.source_module = %s AND split_part(p.source_ref, '/', 1) = %s AND p.parent_posting_id IS NULL
                         AND p.reverses_posting_id IS NULL AND NOT EXISTS (SELECT 1 FROM finance_posting r WHERE r.reverses_posting_id = p.posting_id)
                       ORDER BY p.posted_at, p.posting_id""", (hotel_id, SOURCE, check["check_id"]))
        reversed_ids = []
        for number, row in enumerate(cur.fetchall(), 1):
            done = finance.reverse_posting_in(cur, hotel_id, row["posting_id"], "reopen approved: " + request["reason"], SOURCE,
                                              _settlement_ref(check) + "/reopen/" + str(number), user_id=user_id, cashier_shift_id=cashier_shift_id)
            if not done["ok"]:
                raise _Refused({"error": done["error"], "posting_id": row["posting_id"]})
            reversed_ids.append(row["posting_id"])
        cur.execute("""UPDATE fnb_check SET status = 'open', settlement = NULL, booking_id = NULL, room_id = NULL, subtotal = NULL,
                              discount_total = NULL, tax_total = NULL, total = NULL, closed_at = NULL, closed_by_user_id = NULL,
                              settlement_round = settlement_round + 1 WHERE check_id = %s""", (check["check_id"],))
        return {"ok": _detail(cur, _check(cur, hotel_id, check["check_id"])), "action": "reopened", "reversed": reversed_ids}
    return _run(run)


def _post_charges(cur, check, folio_type, folio_ref, booking_id, user_id):
    """One Finance charge per charge code, priced after discounts; Finance splits net and tax per the hotel's tax settings.
    source_ref = '<settlement ref>/<code>'. A reopened check settles on the open business date.
    Returns (subtotal, discount_total, net, tax_total). A refusal raises _Refused, which rolls the close back."""
    groups = _code_groups(cur, check["check_id"])
    if not groups:
        raise _Refused({"error": "check " + str(check["check_number"]) + " has no live lines; void it instead"})
    net, tax = Decimal(0), Decimal(0)
    for group in groups:
        if group["net"] == 0:
            continue
        posted = finance.post_charge_in(cur, check["hotel_id"], folio_type, folio_ref, group["transaction_code"], group["net"], SOURCE,
                                        _settlement_ref(check) + "/" + group["transaction_code"], user_id=user_id, booking_id=booking_id,
                                        outlet_id=check["outlet_id"],
                                        business_date=None if check["settlement_round"] else check["business_date"],
                                        reference="check " + str(check["check_number"]))
        if not posted["ok"]:
            raise _Refused({"error": posted["error"], "transaction_code": group["transaction_code"]})
        net += Decimal(posted["posting"]["amount"])
        tax += sum((Decimal(row["amount"]) for row in posted["taxes"]), Decimal(0))
    return sum((g["gross"] for g in groups), Decimal(0)), sum((g["discount"] for g in groups), Decimal(0)), net, tax


def _close(cur, check, totals, settlement, booking_id, room_id, user_id):
    subtotal, discount, net, tax = totals
    cur.execute(
        """UPDATE fnb_check SET status = 'closed', settlement = %s, booking_id = %s, room_id = %s, subtotal = %s, discount_total = %s,
                  tax_total = %s, total = %s, closed_at = now(), closed_by_user_id = %s WHERE check_id = %s""",
        (settlement, booking_id, room_id, subtotal, discount, tax, net + tax, user_id, check["check_id"]),
    )
    return {"ok": _detail(cur, _check(cur, check["hotel_id"], check["check_id"]))}


def settle_direct(hotel_id, check_id, payments, user_id, cashier_shift_id=None):
    """Paid at the outlet: charges and payments on the outlet folio ('outlet', outlet_id), netting to 0.
    payments: [{transaction_code, amount, reference}] with Finance payment codes, adding up to the check total exactly.
    A cash code needs the user's open cashier_shift_id (Finance rule)."""
    def run(cur):
        check, problem = _open_check(cur, hotel_id, check_id)
        if problem:
            return {"error": problem}
        totals = _post_charges(cur, check, "outlet", check["outlet_id"], None, user_id)
        total = totals[2] + totals[3]
        paid = sum((_money(p["amount"]) for p in payments), Decimal(0))
        if paid != total:
            raise _Refused({"error": "payments " + str(paid) + " do not equal the check total " + str(total), "total": total})
        for n, payment in enumerate(payments, 1):
            posted = finance.post_payment_in(cur, hotel_id, "outlet", check["outlet_id"], payment["transaction_code"], _money(payment["amount"]),
                                             SOURCE, _settlement_ref(check) + "/pay/" + str(n), user_id=user_id, cashier_shift_id=cashier_shift_id,
                                             outlet_id=check["outlet_id"], reference=payment.get("reference"))
            if not posted["ok"]:
                raise _Refused({"error": posted["error"], "transaction_code": payment["transaction_code"]})
            if posted["posting"]["transaction_type"] != "payment":
                raise _Refused({"error": payment["transaction_code"] + " is not a payment code"})
        return _close(cur, check, totals, "direct", None, None, user_id)
    return _run(run)


def _in_house(cur, hotel_id, booking_id, business_date):
    """([room_id], '') for the rooms the booking has checked in on the business date or the night before (departure day), else (None, reason)."""
    cur.execute("SELECT booking_status FROM booking WHERE booking_id = %s AND hotel_id = %s", (booking_id, hotel_id))
    if cur.fetchone() is None:
        return None, "booking " + booking_id + " not found at this hotel"
    cur.execute(
        """SELECT DISTINCT room_id FROM booking_room_night WHERE booking_id = %s AND hotel_id = %s AND night_status = 'checked_in'
           AND stay_date BETWEEN %s::date - 1 AND %s::date""",
        (booking_id, hotel_id, business_date, business_date),
    )
    rooms = [row["room_id"] for row in cur.fetchall()]
    if not rooms:
        return None, "booking " + booking_id + " is not in house on " + business_date.isoformat()
    return rooms, ""


def settle_room(hotel_id, check_id, booking_id, user_id, folio_ref=None, room_id=None):
    """Charged to the guest folio ('guest', folio_ref or booking_id). room_id is needed only when several rooms of the booking are in house."""
    def run(cur):
        check, problem = _open_check(cur, hotel_id, check_id)
        if problem:
            return {"error": problem}
        rooms, problem = _in_house(cur, hotel_id, booking_id, check["business_date"])
        if problem:
            return {"error": problem}
        if room_id is None and len(rooms) > 1:
            return {"clarify": rooms, "message": "booking " + booking_id + " has several rooms in house; name the room_id"}
        if room_id is not None and room_id not in rooms:
            return {"error": "room " + room_id + " is not in house for booking " + booking_id}
        totals = _post_charges(cur, check, "guest", folio_ref or booking_id, booking_id, user_id)
        return _close(cur, check, totals, "room", booking_id, room_id or rooms[0], user_id)
    return _run(run)


# ---------- daily close ----------

def revenue_summary(hotel_id, business_date=None):
    """Per outlet for one business date (default: Finance's open date). Revenue, tax and payments are read from finance_posting
    (source_module 'fnb'); checks, covers, discounts and voids from the checks; open checks are what the close must resolve."""
    def run(cur):
        day = business_date
        if day is None:
            opened = _open_day(cur, hotel_id)
            if opened is None:
                return {"error": "the hotel has no open business date"}
            day = opened["business_date"]
        args = (hotel_id, day)
        cur.execute(
            """SELECT p.outlet_id, o.outlet_name, p.folio_type, t.transaction_code, p.transaction_type, t.payment_method,
                      COUNT(*) AS postings, SUM(p.amount) AS amount, p.currency
               FROM finance_posting p JOIN transaction_code t ON t.transaction_code_id = p.transaction_code_id JOIN outlet o ON o.outlet_id = p.outlet_id
               WHERE p.hotel_id = %s AND p.business_date = %s AND p.source_module = 'fnb'
               GROUP BY p.outlet_id, o.outlet_name, p.folio_type, t.transaction_code, p.transaction_type, t.payment_method, p.currency
               ORDER BY o.outlet_name, p.transaction_type, t.transaction_code, p.folio_type""",
            args,
        )
        postings = cur.fetchall()
        cur.execute(
            """SELECT o.outlet_id, o.outlet_name, c.status, COUNT(*) AS checks, SUM(c.covers) AS covers,
                      SUM(c.discount_total) AS discount_total,
                      SUM((SELECT COUNT(*) FROM fnb_check_line l WHERE l.check_id = c.check_id AND l.voided)) AS voided_lines
               FROM fnb_check c JOIN outlet o ON o.outlet_id = c.outlet_id WHERE c.hotel_id = %s AND c.business_date = %s
               GROUP BY o.outlet_id, o.outlet_name, c.status ORDER BY o.outlet_name, c.status""",
            args,
        )
        checks = cur.fetchall()
        cur.execute("""SELECT check_id, outlet_id, check_number, table_number, opened_at FROM fnb_check
                       WHERE hotel_id = %s AND business_date = %s AND status = 'open' ORDER BY outlet_id, check_number""", args)
        return {"ok": {"business_date": day, "postings": postings, "checks": checks, "open_checks": cur.fetchall()}}
    return _run(run)
