"""PMS finance: transaction codes, tax rules, folio postings, cashier shifts, city ledger, daily close, GL export mapping.
Every write reads first. A near-matching name returns a clarification unless confirm_new is set. Nothing is defaulted:
a taxable posting without an effective tax rule, a cash posting without an open shift, or a missing export code is refused."""

import difflib
from datetime import date, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal

import psycopg2

from booking_system.finance.db import new_id, transaction

CENT = Decimal("0.01")
SYSTEM_ACTORS = ("night_audit", "channel_aiosell", "migration", "scheduler")


class Refused(Exception):
    def __init__(self, error, **details):
        super().__init__(error)
        self.error = error
        self.details = details


def _plain(value):
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, dict):
        return {key: _plain(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain(item) for item in value]
    return value


def _run(fn):
    """{'ok': True, ...result} or {'ok': False, 'error': ..., details}. Database refusals (triggers, constraints) are returned the same way."""
    try:
        return _plain(dict({"ok": True}, **transaction(fn)))
    except Refused as refusal:
        return _plain(dict({"ok": False, "error": refusal.error}, **refusal.details))
    except psycopg2.Error as error:
        return {"ok": False, "error": (error.diag.message_primary or str(error)).strip()}


def _name(value):
    return str(value or "").strip().upper()


def _money(value):
    amount = Decimal(str(value))
    if amount != amount.quantize(CENT):
        raise Refused("amount has more than 2 decimals: " + str(value))
    return amount


def _actor(user_id, system):
    if (user_id is None) == (system is None) or (system is not None and system not in SYSTEM_ACTORS):
        raise Refused("exactly one actor is needed: user_id, or system in " + ", ".join(SYSTEM_ACTORS))
    return user_id, system


def _hotel(cur, hotel_id):
    cur.execute("SELECT hotel_id, hotel_name, currency FROM hotel WHERE hotel_id = %s", (hotel_id,))
    row = cur.fetchone()
    if not row:
        raise Refused("hotel is not registered", hotel_id=hotel_id)
    return dict(row, currency=row["currency"].strip())


def _one(cur, sql, args):
    cur.execute(sql, args)
    return cur.fetchone()


def _near(cur, table, column, hotel_id, value):
    cur.execute("SELECT " + column + " AS name FROM " + table + " WHERE hotel_id = %s", (hotel_id,))
    return difflib.get_close_matches(value, [row["name"] for row in cur.fetchall()], n=3, cutoff=0.8)


def _clarify_or_create(cur, table, column, hotel_id, value, confirm_new):
    similar = _near(cur, table, column, hotel_id, value)
    if similar and not confirm_new:
        raise Refused("no exact match; did you mean one of these? Repeat with confirm_new to create " + value, similar=similar)


def _find(cur, table, column, hotel_id, value, label):
    row = _one(cur, "SELECT * FROM " + table + " WHERE hotel_id = %s AND " + column + " = %s", (hotel_id, value))
    if not row:
        raise Refused(label + " " + value + " not found", similar=_near(cur, table, column, hotel_id, value))
    return row


# ---------- departments ----------

def list_departments(hotel_id):
    def run(cur):
        cur.execute("SELECT department_id, department_name, department_type FROM department WHERE hotel_id = %s ORDER BY department_name", (hotel_id,))
        return {"departments": cur.fetchall()}
    return _run(run)


def save_department(hotel_id, department_name, department_type, confirm_new=False):
    """department is shared with HR; same table and shape (department_type: operated, undistributed, fixed)."""
    name = _name(department_name)

    def run(cur):
        _hotel(cur, hotel_id)
        found = _one(cur, "SELECT * FROM department WHERE hotel_id = %s AND department_name = %s", (hotel_id, name))
        if found:
            if found["department_type"] == department_type:
                return {"result": "unchanged", "department": found}
            cur.execute("UPDATE department SET department_type = %s WHERE department_id = %s", (department_type, found["department_id"]))
            return {"result": "updated", "department": dict(found, department_type=department_type)}
        _clarify_or_create(cur, "department", "department_name", hotel_id, name, confirm_new)
        department_id = new_id("dep")
        cur.execute("INSERT INTO department (department_id, hotel_id, department_name, department_type) VALUES (%s, %s, %s, %s)",
                    (department_id, hotel_id, name, department_type))
        return {"result": "created", "department": {"department_id": department_id, "department_name": name, "department_type": department_type}}
    return _run(run)


def delete_department(hotel_id, department_name):
    def run(cur):
        found = _find(cur, "department", "department_name", hotel_id, _name(department_name), "department")
        if _one(cur, "SELECT 1 AS used FROM transaction_code WHERE department_id = %s LIMIT 1", (found["department_id"],)):
            raise Refused("department has transaction codes; move or delete them first")
        cur.execute("DELETE FROM department WHERE department_id = %s", (found["department_id"],))
        return {"result": "deleted", "department_id": found["department_id"]}
    return _run(run)


# ---------- transaction codes (shared contract) ----------

_CODE_SQL = """
    SELECT t.transaction_code_id, t.transaction_code, t.transaction_type, t.payment_method, t.is_taxable, t.gl_export_code, t.status,
           d.department_id, d.department_name,
           COALESCE((SELECT json_agg(json_build_object('tax_code_id', x.tax_code_id, 'tax_code', x.tax_code, 'tax_basis', x.tax_basis, 'unit', x.unit)
                                     ORDER BY x.tax_code)
                     FROM transaction_code_tax l JOIN tax_code x ON x.tax_code_id = l.tax_code_id
                     WHERE l.transaction_code_id = t.transaction_code_id), '[]') AS taxes
    FROM transaction_code t JOIN department d ON d.department_id = t.department_id
    WHERE t.hotel_id = %s"""


def list_transaction_codes(hotel_id):
    def run(cur):
        cur.execute(_CODE_SQL + " ORDER BY t.transaction_code", (hotel_id,))
        return {"transaction_codes": cur.fetchall()}
    return _run(run)


def _code(cur, hotel_id, transaction_code):
    code = _name(transaction_code)
    cur.execute(_CODE_SQL + " AND t.transaction_code = %s", (hotel_id, code))
    row = cur.fetchone()
    if not row:
        raise Refused("transaction code " + code + " not found", similar=_near(cur, "transaction_code", "transaction_code", hotel_id, code))
    return row


def resolve_transaction_code(hotel_id, transaction_code):
    """Contract for other modules: the code's id, type, department, payment method, taxes and GL export code. Unknown codes are refused."""
    return _run(lambda cur: {"transaction_code": _code(cur, hotel_id, transaction_code)})


def save_transaction_code(hotel_id, transaction_code, transaction_type, department_name, is_taxable, payment_method=None,
                          gl_export_code=None, tax_codes=(), status="active", confirm_new=False):
    """Creates or updates one code and replaces its tax links with tax_codes. A code with postings keeps its type."""
    code = _name(transaction_code)

    def run(cur):
        _hotel(cur, hotel_id)
        department = _find(cur, "department", "department_name", hotel_id, _name(department_name), "department")
        taxes = [_find(cur, "tax_code", "tax_code", hotel_id, _name(tax), "tax code") for tax in tax_codes]
        if is_taxable and not taxes:
            raise Refused("a taxable code needs at least one tax code")
        values = (transaction_type, department["department_id"], payment_method, bool(is_taxable), gl_export_code, status)
        found = _one(cur, "SELECT * FROM transaction_code WHERE hotel_id = %s AND transaction_code = %s", (hotel_id, code))
        if found:
            used = _one(cur, "SELECT 1 AS used FROM finance_posting WHERE transaction_code_id = %s LIMIT 1", (found["transaction_code_id"],))
            if used and found["transaction_type"] != transaction_type:
                raise Refused("code " + code + " has postings; its type stays " + found["transaction_type"])
            code_id, result = found["transaction_code_id"], "updated"
            cur.execute("""UPDATE transaction_code SET transaction_type = %s, department_id = %s, payment_method = %s, is_taxable = %s,
                           gl_export_code = %s, status = %s WHERE transaction_code_id = %s""", values + (code_id,))
            cur.execute("DELETE FROM transaction_code_tax WHERE transaction_code_id = %s", (code_id,))
        else:
            _clarify_or_create(cur, "transaction_code", "transaction_code", hotel_id, code, confirm_new)
            code_id, result = new_id("txc"), "created"
            cur.execute("""INSERT INTO transaction_code (transaction_code_id, hotel_id, transaction_code, transaction_type, department_id,
                           payment_method, is_taxable, gl_export_code, status) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)""",
                        (code_id, hotel_id, code) + values)
        for tax in taxes:
            cur.execute("INSERT INTO transaction_code_tax (transaction_code_tax_id, transaction_code_id, tax_code_id) VALUES (%s, %s, %s)",
                        (new_id("tct"), code_id, tax["tax_code_id"]))
        return {"result": result, "transaction_code": _code(cur, hotel_id, code)}
    return _run(run)


def set_gl_export_code(hotel_id, transaction_code, gl_export_code):
    def run(cur):
        found = _code(cur, hotel_id, transaction_code)
        cur.execute("UPDATE transaction_code SET gl_export_code = %s WHERE transaction_code_id = %s", (gl_export_code, found["transaction_code_id"]))
        return {"result": "updated", "transaction_code": _code(cur, hotel_id, transaction_code)}
    return _run(run)


def delete_transaction_code(hotel_id, transaction_code):
    def run(cur):
        found = _code(cur, hotel_id, transaction_code)
        if _one(cur, "SELECT 1 AS used FROM finance_posting WHERE transaction_code_id = %s LIMIT 1", (found["transaction_code_id"],)):
            raise Refused("code has postings; set status inactive instead")
        if _one(cur, "SELECT 1 AS used FROM tax_code WHERE tax_transaction_code_id = %s LIMIT 1", (found["transaction_code_id"],)):
            raise Refused("code posts a tax code; change that tax code first")
        cur.execute("DELETE FROM transaction_code WHERE transaction_code_id = %s", (found["transaction_code_id"],))
        return {"result": "deleted", "transaction_code_id": found["transaction_code_id"]}
    return _run(run)


# ---------- tax codes and rules ----------

def list_tax_codes(hotel_id):
    def run(cur):
        cur.execute("""
            SELECT x.tax_code_id, x.tax_code, x.tax_basis, x.unit, x.status, t.transaction_code AS tax_transaction_code,
                   COALESCE((SELECT json_agg(json_build_object('tax_rule_id', r.tax_rule_id, 'effective_from', r.effective_from,
                             'rate_percent', r.rate_percent, 'amount_per_unit', r.amount_per_unit, 'currency', r.currency) ORDER BY r.effective_from)
                             FROM tax_rule r WHERE r.tax_code_id = x.tax_code_id), '[]') AS rules
            FROM tax_code x JOIN transaction_code t ON t.transaction_code_id = x.tax_transaction_code_id
            WHERE x.hotel_id = %s ORDER BY x.tax_code""", (hotel_id,))
        return {"tax_codes": cur.fetchall()}
    return _run(run)


def save_tax_code(hotel_id, tax_code, tax_basis, tax_transaction_code, unit=None, status="active", confirm_new=False):
    code = _name(tax_code)

    def run(cur):
        _hotel(cur, hotel_id)
        posting = _code(cur, hotel_id, tax_transaction_code)
        if posting["transaction_type"] != "tax":
            raise Refused("tax_transaction_code must be a code of type tax")
        found = _one(cur, "SELECT * FROM tax_code WHERE hotel_id = %s AND tax_code = %s", (hotel_id, code))
        if found:
            if found["tax_basis"] != tax_basis and _one(cur, "SELECT 1 AS used FROM tax_rule WHERE tax_code_id = %s LIMIT 1", (found["tax_code_id"],)):
                raise Refused("tax code " + code + " has rules; its basis stays " + found["tax_basis"])
            cur.execute("UPDATE tax_code SET tax_basis = %s, unit = %s, tax_transaction_code_id = %s, status = %s WHERE tax_code_id = %s",
                        (tax_basis, unit, posting["transaction_code_id"], status, found["tax_code_id"]))
            return {"result": "updated", "tax_code_id": found["tax_code_id"]}
        _clarify_or_create(cur, "tax_code", "tax_code", hotel_id, code, confirm_new)
        tax_code_id = new_id("tax")
        cur.execute("""INSERT INTO tax_code (tax_code_id, hotel_id, tax_code, tax_basis, unit, tax_transaction_code_id, status)
                       VALUES (%s, %s, %s, %s, %s, %s, %s)""", (tax_code_id, hotel_id, code, tax_basis, unit, posting["transaction_code_id"], status))
        return {"result": "created", "tax_code_id": tax_code_id}
    return _run(run)


def save_tax_rule(hotel_id, tax_code, effective_from, rate_percent=None, amount_per_unit=None, currency=None):
    """One rule per (tax code, effective date). A rule already applied to a posting cannot change; add a later-dated rule instead."""
    def run(cur):
        tax = _find(cur, "tax_code", "tax_code", hotel_id, _name(tax_code), "tax code")
        rate = None if rate_percent is None else Decimal(str(rate_percent))
        per_unit = None if amount_per_unit is None else _money(amount_per_unit)
        found = _one(cur, "SELECT * FROM tax_rule WHERE tax_code_id = %s AND effective_from = %s", (tax["tax_code_id"], effective_from))
        if found:
            if _one(cur, "SELECT 1 AS used FROM finance_posting WHERE tax_rule_id = %s LIMIT 1", (found["tax_rule_id"],)):
                raise Refused("this rule is applied to postings; add a rule with a later effective_from")
            cur.execute("UPDATE tax_rule SET rate_percent = %s, amount_per_unit = %s, currency = %s WHERE tax_rule_id = %s",
                        (rate, per_unit, currency, found["tax_rule_id"]))
            return {"result": "updated", "tax_rule_id": found["tax_rule_id"]}
        rule_id = new_id("txr")
        cur.execute("""INSERT INTO tax_rule (tax_rule_id, tax_code_id, tax_basis, effective_from, rate_percent, amount_per_unit, currency)
                       VALUES (%s, %s, %s, %s, %s, %s, %s)""", (rule_id, tax["tax_code_id"], tax["tax_basis"], effective_from, rate, per_unit, currency))
        return {"result": "created", "tax_rule_id": rule_id}
    return _run(run)


def _setup(cur, hotel_id, business_date):
    """The hotel's finance settings in effect on the business date. Refuses while the module is off or a required setting is unanswered."""
    from booking_system.settings.service import module_refusal
    cur.execute("""SELECT hotel_module_state(%(h)s, 'finance', %(d)s) AS state, hotel_setting_at(%(h)s, 'finance.tax_pricing', %(d)s) AS pricing,
                          hotel_setting_at(%(h)s, 'finance.tax_calculation', %(d)s) AS calculation,
                          hotel_setting_at(%(h)s, 'finance.tax_order', %(d)s) AS tax_order,
                          hotel_setting_at(%(h)s, 'finance.cashier_variance_tolerance', %(d)s) AS tolerance""", {"h": hotel_id, "d": business_date})
    setup = cur.fetchone()
    refusal = module_refusal(setup["state"])
    if refusal:
        raise Refused("finance " + refusal + " (business date " + str(business_date) + ")", missing=setup["state"]["missing"])
    return setup


def _tax_specs(cur, hotel, code, business_date, units, setup):
    """[(tax_code row, rule row)] of a taxable code in the order they apply: the hotel's finance.tax_order when taxes compound.
    Refuses a missing rule, a missing unit count, a foreign per-unit currency, or a compounding tax missing from the order."""
    if not code["is_taxable"]:
        return []
    specs = []
    for link in code["taxes"]:
        tax = _one(cur, "SELECT * FROM tax_code WHERE tax_code_id = %s", (link["tax_code_id"],))
        if tax["status"] != "active":
            raise Refused("tax code " + tax["tax_code"] + " is inactive but linked to " + code["transaction_code"])
        rule = _one(cur, "SELECT * FROM tax_rule WHERE tax_code_id = %s AND effective_from <= %s ORDER BY effective_from DESC LIMIT 1",
                    (tax["tax_code_id"], business_date))
        if not rule:
            raise Refused("no tax rule for " + tax["tax_code"] + " effective on " + str(business_date) + "; posting refused")
        if tax["tax_basis"] != "percent" and rule["currency"].strip() != hotel["currency"]:
            raise Refused("tax rule " + tax["tax_code"] + " is in " + rule["currency"] + ", not the hotel currency")
        if tax["tax_basis"] != "percent" and tax["unit"] not in (units or {}):
            raise Refused("tax " + tax["tax_code"] + " is per " + tax["unit"] + "; pass units={'" + tax["unit"] + "': count}")
        specs.append((tax, rule))
    if setup["calculation"] == "compound":
        order = list(setup["tax_order"])
        unordered = [tax["tax_code"] for tax, _rule in specs if tax["tax_code"] not in order]
        if unordered:
            raise Refused("tax codes " + ", ".join(unordered) + " are not in the hotel setting finance.tax_order; update it before posting")
        specs.sort(key=lambda spec: order.index(spec[0]["tax_code"]))
    return specs


def _tax_values(specs, net, units, sign, compound, rounded):
    """Each tax of specs on net: a percent tax on the net (plus the taxes before it when compound), a per-unit tax per unit."""
    values, running = [], Decimal(0)
    for tax, rule in specs:
        if tax["tax_basis"] == "percent":
            value = (net + (running if compound else 0)) * rule["rate_percent"] / 100
            value = value.quantize(CENT, rounding=ROUND_HALF_UP) if rounded else value
        else:
            value = rule["amount_per_unit"] * Decimal(str(units[tax["unit"]])) * sign
        values.append(value)
        running += value
    return values


def _split(cur, hotel, code, price, business_date, units, setup):
    """(net, [(tax, rule, amount)]) of a charge priced at price under the hotel's settings on the business date.
    Exclusive: net is the price and taxes are added. Inclusive: net + taxes equal the price; the total is affine in the net
    (net * k + c), so net = (price - c) / k, rounded, and the rounding residual stays in the net so the parts add up to the price."""
    specs = _tax_specs(cur, hotel, code, business_date, units, setup)
    compound = setup["calculation"] == "compound"
    sign = 1 if price > 0 else -1
    net = price
    if setup["pricing"] == "inclusive" and specs:
        fixed = sum(_tax_values(specs, Decimal(0), units, sign, compound, False), Decimal(0))
        slope = 1 + sum(_tax_values(specs, Decimal(1), units, sign, compound, False), Decimal(0)) - fixed
        net = ((price - fixed) / slope).quantize(CENT, rounding=ROUND_HALF_UP)
    values = _tax_values(specs, net, units, sign, compound, True)
    if setup["pricing"] == "inclusive":
        net = price - sum(values, Decimal(0))
    return net, [(tax, rule, value) for (tax, rule), value in zip(specs, values) if value != 0]


def quote_tax(hotel_id, transaction_code, amount, units=None):
    """What a charge priced at amount would post on the open business date, without posting: net, tax lines and total, per the
    hotel's tax pricing (inclusive or exclusive) and calculation (simple or compound) in effect on that date."""
    def run(cur):
        hotel = _hotel(cur, hotel_id)
        day = _open_day(cur, hotel_id)
        setup = _setup(cur, hotel_id, day["business_date"])
        code = _code(cur, hotel_id, transaction_code)
        net, lines = _split(cur, hotel, code, _money(amount), day["business_date"], units, setup)
        tax_total = sum((v for _t, _r, v in lines), Decimal(0))
        return {"business_date": day["business_date"], "currency": hotel["currency"], "tax_pricing": setup["pricing"],
                "tax_calculation": setup["calculation"], "net": net, "tax_total": tax_total, "total": net + tax_total,
                "taxes": [{"tax_code": t["tax_code"], "tax_rule_id": r["tax_rule_id"], "amount": v} for t, r, v in lines]}
    return _run(run)


# ---------- business day ----------

def _open_day(cur, hotel_id, lock=""):
    day = _one(cur, "SELECT * FROM business_day WHERE hotel_id = %s AND status = 'open' " + lock, (hotel_id,))
    if not day:
        raise Refused("the hotel has no open business date")
    return day


def read_business_day(hotel_id):
    def run(cur):
        cur.execute("SELECT * FROM business_day WHERE hotel_id = %s ORDER BY business_date DESC LIMIT 7", (hotel_id,))
        rows = cur.fetchall()
        return {"open": next((row for row in rows if row["status"] == "open"), None), "recent": rows}
    return _run(run)


def open_business_day(hotel_id, business_date, user_id):
    """Starts the hotel's first business date. Later dates open only through close_business_day."""
    def run(cur):
        _hotel(cur, hotel_id)
        latest = _one(cur, "SELECT * FROM business_day WHERE hotel_id = %s ORDER BY business_date DESC LIMIT 1", (hotel_id,))
        if latest:
            raise Refused("business dates already run; the next one opens when " + str(latest["business_date"]) + " closes", latest=latest)
        day_id = new_id("bdy")
        cur.execute("INSERT INTO business_day (business_day_id, hotel_id, business_date, status, opened_by_user_id) VALUES (%s, %s, %s, 'open', %s)",
                    (day_id, hotel_id, business_date, user_id))
        return {"result": "opened", "business_day_id": day_id, "business_date": business_date}
    return _run(run)


_SUMMARY_SELECT = """
    SELECT p.transaction_code_id, p.transaction_type, t.department_id, t.payment_method, p.tax_code_id,
           COUNT(*) AS posting_count, SUM(p.quantity) AS quantity_total, SUM(p.amount) AS amount_total, p.currency
    FROM finance_posting p JOIN transaction_code t ON t.transaction_code_id = p.transaction_code_id
    WHERE p.business_day_id = %s
    GROUP BY p.transaction_code_id, p.transaction_type, t.department_id, t.payment_method, p.tax_code_id, p.currency"""


def _report(cur, day):
    """Revenue by department and code, payments by method, tax by tax code: from the snapshot when closed, live while open."""
    source = "daily_close_summary WHERE business_day_id = %s" if day["status"] == "closed" else "(" + _SUMMARY_SELECT + ") s0"
    cur.execute("""
        SELECT d.department_name, t.transaction_code, t.transaction_code_id, s.transaction_type, t.payment_method, x.tax_code, t.gl_export_code,
               s.posting_count, s.quantity_total, s.amount_total, s.currency
        FROM (SELECT * FROM """ + source + """) s
        JOIN transaction_code t ON t.transaction_code_id = s.transaction_code_id
        JOIN department d ON d.department_id = s.department_id
        LEFT JOIN tax_code x ON x.tax_code_id = s.tax_code_id
        ORDER BY d.department_name, t.transaction_code""", (day["business_day_id"],))
    rows = cur.fetchall()
    revenue = [r for r in rows if r["transaction_type"] in ("charge", "adjustment")]
    by_department = {}
    for row in revenue:
        by_department[row["department_name"]] = by_department.get(row["department_name"], Decimal(0)) + row["amount_total"]
    payments = {}
    for row in rows:
        if row["transaction_type"] in ("payment", "refund"):
            payments[row["payment_method"]] = payments.get(row["payment_method"], Decimal(0)) - row["amount_total"]
    taxes = [{"tax_code": r["tax_code"], "transaction_code": r["transaction_code"], "amount": r["amount_total"]} for r in rows if r["transaction_type"] == "tax"]
    return {"business_date": day["business_date"], "status": day["status"], "lines": rows,
            "revenue_by_department": by_department, "payments_by_method": payments, "tax_summary": taxes,
            "transfers_net": sum((r["amount_total"] for r in rows if r["transaction_type"] == "transfer"), Decimal(0))}


def daily_close_report(hotel_id, business_date):
    def run(cur):
        day = _one(cur, "SELECT * FROM business_day WHERE hotel_id = %s AND business_date = %s", (hotel_id, business_date))
        if not day:
            raise Refused("no business day " + str(business_date))
        return _report(cur, day)
    return _run(run)


def close_business_day(hotel_id, business_date, user_id):
    """Night audit close: the open date must be business_date and have no open cashier shift. Locks the date, snapshots the
    summary, and opens the next date, in one transaction."""
    def run(cur):
        day = _open_day(cur, hotel_id, "FOR UPDATE")
        if str(day["business_date"]) != str(business_date):
            raise Refused("the open business date is " + str(day["business_date"]) + ", not " + str(business_date))
        cur.execute("SELECT cashier_shift_id, cashier_user_id, opened_at FROM cashier_shift WHERE business_day_id = %s AND status = 'open'",
                    (day["business_day_id"],))
        open_shifts = cur.fetchall()
        if open_shifts:
            raise Refused("close these cashier shifts first", open_shifts=open_shifts)
        cur.execute(_SUMMARY_SELECT, (day["business_day_id"],))
        for row in cur.fetchall():
            cur.execute("""INSERT INTO daily_close_summary (daily_close_summary_id, hotel_id, business_day_id, business_date, transaction_code_id,
                           transaction_type, department_id, payment_method, tax_code_id, posting_count, quantity_total, amount_total, currency)
                           VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
                        (new_id("dcs"), hotel_id, day["business_day_id"], day["business_date"], row["transaction_code_id"], row["transaction_type"],
                         row["department_id"], row["payment_method"], row["tax_code_id"], row["posting_count"], row["quantity_total"],
                         row["amount_total"], row["currency"]))
        cur.execute("UPDATE business_day SET status = 'closed', closed_at = now(), closed_by_user_id = %s WHERE business_day_id = %s",
                    (user_id, day["business_day_id"]))
        next_id, next_date = new_id("bdy"), day["business_date"] + timedelta(days=1)
        cur.execute("INSERT INTO business_day (business_day_id, hotel_id, business_date, status, opened_by_user_id) VALUES (%s, %s, %s, 'open', %s)",
                    (next_id, hotel_id, next_date, user_id))
        report = _report(cur, dict(day, status="closed"))
        return dict(report, result="closed", next_business_date=next_date, next_business_day_id=next_id)
    return _run(run)


# ---------- cashier shifts ----------

def _shift_report(cur, shift):
    cur.execute("""SELECT payment_method, transaction_code, posting_count, net_received FROM fin_cashier_shift_activity
                   WHERE cashier_shift_id = %s ORDER BY payment_method, transaction_code""", (shift["cashier_shift_id"],))
    activity = cur.fetchall()
    cash = sum((row["net_received"] for row in activity if row["payment_method"] == "cash"), Decimal(0))
    return {"shift": shift, "activity": activity, "expected_cash": shift["opening_float"] + cash}


def open_cashier_shift(hotel_id, user_id, opening_float):
    def run(cur):
        hotel = _hotel(cur, hotel_id)
        day = _open_day(cur, hotel_id)
        current = _one(cur, "SELECT * FROM cashier_shift WHERE hotel_id = %s AND cashier_user_id = %s AND status = 'open'", (hotel_id, user_id))
        if current:
            raise Refused("this cashier already has an open shift", shift=current)
        shift_id = new_id("csh")
        cur.execute("""INSERT INTO cashier_shift (cashier_shift_id, hotel_id, business_day_id, cashier_user_id, currency, opening_float, status)
                       VALUES (%s, %s, %s, %s, %s, %s, 'open')""", (shift_id, hotel_id, day["business_day_id"], user_id, hotel["currency"], _money(opening_float)))
        return {"result": "opened", "cashier_shift_id": shift_id, "business_date": day["business_date"], "currency": hotel["currency"]}
    return _run(run)


def read_cashier_shift(hotel_id, cashier_shift_id):
    def run(cur):
        shift = _one(cur, "SELECT * FROM cashier_shift WHERE hotel_id = %s AND cashier_shift_id = %s", (hotel_id, cashier_shift_id))
        if not shift:
            raise Refused("cashier shift not found")
        return _shift_report(cur, shift)
    return _run(run)


def close_cashier_shift(hotel_id, cashier_shift_id, user_id, counted_cash, variance_reason=None):
    """Drawer reconciliation: expected = opening float + net cash taken in the shift; variance = counted - expected. The cashier closes
    their own shift. A variance beyond the hotel's finance.cashier_variance_tolerance (in effect on the open business date) needs a reason."""
    def run(cur):
        shift = _one(cur, "SELECT * FROM cashier_shift WHERE hotel_id = %s AND cashier_shift_id = %s FOR UPDATE", (hotel_id, cashier_shift_id))
        if not shift:
            raise Refused("cashier shift not found")
        if shift["status"] != "open":
            raise Refused("shift is already closed", shift=shift)
        if shift["cashier_user_id"] != user_id:
            raise Refused("only the shift's cashier can close it")
        setup = _setup(cur, hotel_id, _open_day(cur, hotel_id)["business_date"])
        report = _shift_report(cur, shift)
        variance = _money(counted_cash) - report["expected_cash"]
        tolerance = Decimal(setup["tolerance"])
        reason = str(variance_reason or "").strip()
        if abs(variance) > tolerance and not reason:
            raise Refused("variance " + str(variance) + " is beyond the hotel tolerance of " + str(tolerance) + "; give a variance_reason",
                          variance=variance, tolerance=tolerance)
        cur.execute("""UPDATE cashier_shift SET status = 'closed', closed_at = now(), expected_cash = %s, counted_cash = %s, variance_reason = %s
                       WHERE cashier_shift_id = %s""", (report["expected_cash"], _money(counted_cash), reason or None, cashier_shift_id))
        closed = _one(cur, "SELECT * FROM cashier_shift WHERE cashier_shift_id = %s", (cashier_shift_id,))
        return dict(report, result="closed", shift=closed, variance=closed["variance"], tolerance=tolerance)
    return _run(run)


# ---------- postings: the folio contract ----------

def _existing(cur, hotel_id, source_module, source_ref, code_id, amount):
    found = _one(cur, """SELECT * FROM finance_posting WHERE hotel_id = %s AND source_module = %s AND source_ref = %s AND parent_posting_id IS NULL""",
                 (hotel_id, source_module, source_ref))
    if found and (found["transaction_code_id"], found["amount"]) != (code_id, amount):
        raise Refused("source_ref " + source_ref + " is already posted with another code or amount", posting=found)
    return found


def _insert(cur, row):
    columns = list(row)
    cur.execute("INSERT INTO finance_posting (" + ", ".join(columns) + ") VALUES (" + ", ".join(["%s"] * len(columns)) + ")",
                [row[name] for name in columns])
    return row


def _children(cur, posting_id):
    cur.execute("SELECT * FROM finance_posting WHERE parent_posting_id = %s ORDER BY posted_at, posting_id", (posting_id,))
    return cur.fetchall()


def _posting(hotel_id, folio_type, folio_ref, transaction_code, amount, types, source_module, source_ref, user_id, system,
             quantity=1, booking_id=None, outlet_id=None, ar_account_id=None, cashier_shift_id=None, units=None, reference=None, reason=None,
             business_date=None, expected_tax=None, expected_tax_codes=None, desk_payment_id=None):
    """The posting as run(cur). business_date, expected_tax and expected_tax_codes, when given, must equal the open date, the
    computed tax total and the code's tax codes; a caller that computed its own tax is checked, never trusted."""
    def run(cur):
        _actor(user_id, system)
        hotel = _hotel(cur, hotel_id)
        day = _open_day(cur, hotel_id)
        if business_date is not None and str(business_date) != str(day["business_date"]):
            raise Refused("business date " + str(business_date) + " is not the open business date " + str(day["business_date"]))
        setup = _setup(cur, hotel_id, day["business_date"])
        code = _code(cur, hotel_id, transaction_code)
        if code["transaction_type"] not in types:
            raise Refused("code " + code["transaction_code"] + " is a " + code["transaction_type"] + " code; expected " + " or ".join(types))
        price = _money(amount) * (-1 if code["transaction_type"] == "payment" else 1)
        value, taxes = _split(cur, hotel, code, price, day["business_date"], units, setup)
        found = _existing(cur, hotel_id, source_module, source_ref, code["transaction_code_id"], value)
        if found:
            return {"result": "already_posted", "posting": found, "taxes": _children(cur, found["posting_id"])}
        if folio_type == "account":
            _account_limit(cur, hotel, ar_account_id or folio_ref, price)
        base = {"hotel_id": hotel_id, "business_day_id": day["business_day_id"], "business_date": day["business_date"], "folio_type": folio_type,
                "folio_ref": folio_ref, "booking_id": booking_id, "outlet_id": outlet_id,
                "ar_account_id": (ar_account_id or folio_ref) if folio_type == "account" else None, "quantity": Decimal(str(quantity)),
                "currency": hotel["currency"], "source_module": source_module, "source_ref": source_ref,
                "posted_by_user_id": user_id, "posted_by_system": system}
        if expected_tax_codes is not None and sorted(_name(t) for t in expected_tax_codes) != sorted(t["tax_code"] for t in code["taxes"]):
            raise Refused("code " + code["transaction_code"] + " carries taxes " + str([t["tax_code"] for t in code["taxes"]])
                          + ", not " + str(list(expected_tax_codes)))
        computed = sum((v for _t, _r, v in taxes), Decimal(0))
        if expected_tax is not None and _money(expected_tax) != computed:
            raise Refused("tax " + str(expected_tax) + " differs from the tax rules' " + str(computed) + " for " + code["transaction_code"])
        posted = dict(base, posting_id=new_id("pst"), transaction_code_id=code["transaction_code_id"],
                      transaction_type=code["transaction_type"], amount=value, cashier_shift_id=cashier_shift_id,
                      reference=reference, reason=reason)
        if desk_payment_id:
            posted["desk_payment_id"] = desk_payment_id
        posting = _insert(cur, posted)
        tax_rows = []
        for tax, rule, tax_amount in taxes:
            tax_rows.append(_insert(cur, dict(base, posting_id=new_id("pst"), transaction_code_id=tax["tax_transaction_code_id"],
                                              transaction_type="tax", amount=tax_amount, tax_code_id=tax["tax_code_id"],
                                              tax_rule_id=rule["tax_rule_id"], parent_posting_id=posting["posting_id"])))
        return {"result": "posted", "posting": posting, "taxes": tax_rows, "tax_pricing": setup["pricing"],
                "folio_balance": _balance(cur, hotel_id, folio_type, folio_ref)}
    return run


def _post(*args, **kwargs):
    return _run(_posting(*args, **kwargs))


def post_charge(hotel_id, folio_type, folio_ref, transaction_code, amount, source_module, source_ref, user_id=None, system=None,
                quantity=1, booking_id=None, outlet_id=None, ar_account_id=None, units=None, reference=None,
                business_date=None, expected_tax=None, expected_tax_codes=None):
    """A charge on a folio at the open business date, plus one tax posting per linked tax code. amount is the price as the hotel
    quotes it (positive): the net under tax-exclusive pricing, net plus taxes under tax-inclusive pricing (finance.tax_pricing)."""
    return _post(hotel_id, folio_type, folio_ref, transaction_code, amount, ("charge",), source_module, source_ref, user_id, system,
                 quantity, booking_id, outlet_id, ar_account_id, None, units, reference, None, business_date, expected_tax, expected_tax_codes)


def post_charge_in(cur, hotel_id, folio_type, folio_ref, transaction_code, amount, source_module, source_ref, user_id=None, system=None,
                   quantity=1, booking_id=None, outlet_id=None, ar_account_id=None, units=None, reference=None,
                   business_date=None, expected_tax=None, expected_tax_codes=None):
    """post_charge on the caller's cursor (dict rows), so the charge commits with the caller's own write. A refusal returns
    {'ok': False, ...} and writes nothing; a database error propagates so the caller rolls back."""
    try:
        return _plain(dict({"ok": True}, **_posting(hotel_id, folio_type, folio_ref, transaction_code, amount, ("charge",), source_module, source_ref,
                                                    user_id, system, quantity, booking_id, outlet_id, ar_account_id, None, units, reference, None,
                                                    business_date, expected_tax, expected_tax_codes)(cur)))
    except Refused as refusal:
        return _plain(dict({"ok": False, "error": refusal.error}, **refusal.details))


def post_adjustment(hotel_id, folio_type, folio_ref, transaction_code, amount, reason, source_module, source_ref, user_id=None, system=None,
                    quantity=1, booking_id=None, outlet_id=None, ar_account_id=None, units=None, reference=None):
    """An allowance or rebate (signed amount, usually negative) with its taxes. Corrections of a wrong posting use reverse_posting."""
    return _post(hotel_id, folio_type, folio_ref, transaction_code, amount, ("adjustment",), source_module, source_ref, user_id, system,
                 quantity, booking_id, outlet_id, ar_account_id, None, units, reference, reason)


def post_adjustment_in(cur, hotel_id, folio_type, folio_ref, transaction_code, amount, reason, source_module, source_ref, user_id=None,
                       system=None, booking_id=None, outlet_id=None, reference=None):
    """post_adjustment on the caller's cursor (dict rows); refusals return {'ok': False, ...} and write nothing."""
    try:
        return _plain(dict({"ok": True}, **_posting(hotel_id, folio_type, folio_ref, transaction_code, amount, ("adjustment",), source_module,
                                                    source_ref, user_id, system, 1, booking_id, outlet_id, None, None, None, reference,
                                                    reason)(cur)))
    except Refused as refusal:
        return _plain(dict({"ok": False, "error": refusal.error}, **refusal.details))


def post_payment(hotel_id, folio_type, folio_ref, transaction_code, amount, source_module, source_ref, user_id=None, system=None,
                 cashier_shift_id=None, booking_id=None, outlet_id=None, ar_account_id=None, reference=None, desk_payment_id=None):
    """A payment (code type payment, stored negative) or a refund (code type refund, stored positive). amount is positive.
    Cash needs the poster's open cashier_shift_id. desk_payment_id links a desk receipt, set only on this insert."""
    return _post(hotel_id, folio_type, folio_ref, transaction_code, amount, ("payment", "refund"), source_module, source_ref, user_id, system,
                 1, booking_id, outlet_id, ar_account_id, cashier_shift_id, None, reference, desk_payment_id=desk_payment_id)


def post_payment_in(cur, hotel_id, folio_type, folio_ref, transaction_code, amount, source_module, source_ref, user_id=None, system=None,
                    cashier_shift_id=None, booking_id=None, outlet_id=None, ar_account_id=None, reference=None, desk_payment_id=None):
    """post_payment on the caller's cursor (dict rows); refusals return {'ok': False, ...} and write nothing."""
    try:
        return _plain(dict({"ok": True}, **_posting(hotel_id, folio_type, folio_ref, transaction_code, amount, ("payment", "refund"), source_module,
                                                    source_ref, user_id, system, 1, booking_id, outlet_id, ar_account_id, cashier_shift_id, None,
                                                    reference, desk_payment_id=desk_payment_id)(cur)))
    except Refused as refusal:
        return _plain(dict({"ok": False, "error": refusal.error}, **refusal.details))


def _balance(cur, hotel_id, folio_type, folio_ref):
    row = _one(cur, "SELECT COALESCE(SUM(amount), 0) AS balance FROM finance_posting WHERE hotel_id = %s AND folio_type = %s AND folio_ref = %s",
               (hotel_id, folio_type, folio_ref))
    return row["balance"]


def _account_limit(cur, hotel, ar_account_id, value):
    account = _one(cur, "SELECT * FROM ar_account WHERE hotel_id = %s AND ar_account_id = %s FOR UPDATE", (hotel["hotel_id"], ar_account_id))
    if not account:
        raise Refused("city ledger account not found")
    if account["status"] != "active" and value > 0:
        raise Refused("city ledger account " + account["account_name"] + " is " + account["status"])
    balance = _balance(cur, hotel["hotel_id"], "account", ar_account_id)
    if value > 0 and balance + value > account["credit_limit"]:
        raise Refused("credit limit exceeded for " + account["account_name"], balance=balance, credit_limit=account["credit_limit"], amount=value)
    return account


def transfer_to_account(hotel_id, folio_type, folio_ref, ar_account_id, transaction_code, amount, source_module, source_ref, user_id=None,
                        system=None, booking_id=None, reference=None):
    """Direct bill: moves amount from a guest/outlet folio to a city-ledger account as two linked transfer postings."""
    def run(cur):
        _actor(user_id, system)
        hotel = _hotel(cur, hotel_id)
        day = _open_day(cur, hotel_id)
        code = _code(cur, hotel_id, transaction_code)
        if code["transaction_type"] != "transfer" or folio_type == "account":
            raise Refused("needs a transfer code, from a guest or outlet folio")
        value = _money(amount)
        if value <= 0:
            raise Refused("transfer amount must be positive")
        found = _existing(cur, hotel_id, source_module, source_ref, code["transaction_code_id"], -value)
        if found:
            return {"result": "already_posted", "posting": found}
        if value > _balance(cur, hotel_id, folio_type, folio_ref):
            raise Refused("transfer exceeds the folio balance", balance=_balance(cur, hotel_id, folio_type, folio_ref))
        account = _account_limit(cur, hotel, ar_account_id, value)
        out_id, in_id = new_id("pst"), new_id("pst")
        base = {"hotel_id": hotel_id, "business_day_id": day["business_day_id"], "business_date": day["business_date"], "booking_id": booking_id,
                "transaction_code_id": code["transaction_code_id"], "transaction_type": "transfer", "quantity": Decimal(1),
                "currency": hotel["currency"], "source_module": source_module, "reference": reference,
                "posted_by_user_id": user_id, "posted_by_system": system}
        out_row = _insert(cur, dict(base, posting_id=out_id, folio_type=folio_type, folio_ref=folio_ref, amount=-value,
                                    transfer_posting_id=in_id, source_ref=source_ref))
        in_row = _insert(cur, dict(base, posting_id=in_id, folio_type="account", folio_ref=account["ar_account_id"],
                                   ar_account_id=account["ar_account_id"], amount=value, transfer_posting_id=out_id, source_ref=source_ref + "#to"))
        return {"result": "posted", "from": out_row, "to": in_row, "folio_balance": _balance(cur, hotel_id, folio_type, folio_ref)}
    return _run(run)


def reverse_posting(hotel_id, posting_id, reason, source_module, source_ref, user_id=None, system=None, cashier_shift_id=None):
    """Correction: negates a posting with its taxes (and its transfer counterpart) on the open business date. Postings are never edited."""
    return _run(_reversal(hotel_id, posting_id, reason, source_module, source_ref, user_id, system, cashier_shift_id))


def reverse_posting_in(cur, hotel_id, posting_id, reason, source_module, source_ref, user_id=None, system=None, cashier_shift_id=None):
    """reverse_posting on the caller's cursor (dict rows); a refusal returns {'ok': False, ...} and writes nothing."""
    try:
        return _plain(dict({"ok": True}, **_reversal(hotel_id, posting_id, reason, source_module, source_ref, user_id, system, cashier_shift_id)(cur)))
    except Refused as refusal:
        return _plain(dict({"ok": False, "error": refusal.error}, **refusal.details))


def _reversal(hotel_id, posting_id, reason, source_module, source_ref, user_id, system, cashier_shift_id):
    def run(cur):
        _actor(user_id, system)
        hotel = _hotel(cur, hotel_id)
        day = _open_day(cur, hotel_id)
        original = _one(cur, "SELECT * FROM finance_posting WHERE hotel_id = %s AND posting_id = %s FOR UPDATE", (hotel_id, posting_id))
        if not original:
            raise Refused("posting not found")
        if original["parent_posting_id"] or original["reverses_posting_id"]:
            raise Refused("reverse the original charge, not a tax line or a reversal")
        group = [original] + _children(cur, posting_id)
        if original["transfer_posting_id"]:
            counterpart = _one(cur, "SELECT * FROM finance_posting WHERE posting_id = %s", (original["transfer_posting_id"],))
            group = [p for p in group] + [counterpart]
        ids = [p["posting_id"] for p in group]
        cur.execute("SELECT posting_id FROM finance_posting WHERE reverses_posting_id = ANY(%s)", (ids,))
        if cur.fetchall():
            raise Refused("posting is already reversed")
        cur.execute("""SELECT i.invoice_number FROM ar_invoice_item it JOIN ar_invoice i ON i.ar_invoice_id = it.ar_invoice_id
                       WHERE it.posting_id = ANY(%s) AND i.status = 'open'
                       UNION SELECT i.invoice_number FROM ar_allocation a JOIN ar_invoice i ON i.ar_invoice_id = a.ar_invoice_id
                       WHERE a.credit_posting_id = ANY(%s)""", (ids, ids))
        invoices = [row["invoice_number"] for row in cur.fetchall()]
        if invoices:
            raise Refused("posting is on an invoice or applied to one; void the invoice first", invoices=invoices)
        new_ids = {pid: new_id("pst") for pid in ids}
        rows = []
        for p in group:
            rows.append(_insert(cur, {
                "posting_id": new_ids[p["posting_id"]], "hotel_id": hotel_id, "business_day_id": day["business_day_id"],
                "business_date": day["business_date"], "folio_type": p["folio_type"], "folio_ref": p["folio_ref"], "booking_id": p["booking_id"],
                "ar_account_id": p["ar_account_id"], "outlet_id": p["outlet_id"], "transaction_code_id": p["transaction_code_id"],
                "transaction_type": p["transaction_type"], "quantity": p["quantity"], "amount": -p["amount"], "currency": hotel["currency"],
                "tax_code_id": p["tax_code_id"], "tax_rule_id": p["tax_rule_id"],
                "parent_posting_id": new_ids.get(p["parent_posting_id"]), "reverses_posting_id": p["posting_id"],
                "transfer_posting_id": new_ids.get(p["transfer_posting_id"]),
                "cashier_shift_id": cashier_shift_id if p["cashier_shift_id"] or p["posting_id"] == posting_id else None,
                "reason": reason, "source_module": source_module,
                "source_ref": source_ref if p["posting_id"] == posting_id else source_ref + "#" + p["posting_id"],
                "posted_by_user_id": user_id, "posted_by_system": system}))
        return {"result": "reversed", "reversals": rows, "folio_balance": _balance(cur, hotel_id, original["folio_type"], original["folio_ref"])}
    return run


def read_folio(hotel_id, folio_type, folio_ref):
    def run(cur):
        cur.execute("""SELECT p.*, t.transaction_code, x.tax_code FROM finance_posting p
                       JOIN transaction_code t ON t.transaction_code_id = p.transaction_code_id
                       LEFT JOIN tax_code x ON x.tax_code_id = p.tax_code_id
                       WHERE p.hotel_id = %s AND p.folio_type = %s AND p.folio_ref = %s ORDER BY p.posted_at, p.posting_id""",
                    (hotel_id, folio_type, folio_ref))
        rows = cur.fetchall()
        return {"postings": rows, "balance": sum((r["amount"] for r in rows), Decimal(0)), "currency": _hotel(cur, hotel_id)["currency"]}
    return _run(run)


# ---------- city ledger ----------

def list_ar_accounts(hotel_id):
    def run(cur):
        cur.execute("SELECT * FROM fin_ar_aging WHERE hotel_id = %s ORDER BY account_name", (hotel_id,))
        aging = {row["ar_account_id"]: row for row in cur.fetchall()}
        cur.execute("SELECT * FROM ar_account WHERE hotel_id = %s ORDER BY account_name", (hotel_id,))
        return {"accounts": [dict(row, aging=aging.get(row["ar_account_id"])) for row in cur.fetchall()]}
    return _run(run)


def save_ar_account(hotel_id, account_name, account_type, credit_limit, payment_terms_days, status="active", confirm_new=False):
    """Accounts are kept in the hotel currency, like every folio."""
    name = _name(account_name)

    def run(cur):
        hotel = _hotel(cur, hotel_id)
        values = (account_type, _money(credit_limit), int(payment_terms_days), status)
        found = _one(cur, "SELECT * FROM ar_account WHERE hotel_id = %s AND account_name = %s", (hotel_id, name))
        if found:
            cur.execute("UPDATE ar_account SET account_type = %s, credit_limit = %s, payment_terms_days = %s, status = %s WHERE ar_account_id = %s",
                        values + (found["ar_account_id"],))
            return {"result": "updated", "ar_account_id": found["ar_account_id"]}
        _clarify_or_create(cur, "ar_account", "account_name", hotel_id, name, confirm_new)
        account_id = new_id("ara")
        cur.execute("""INSERT INTO ar_account (ar_account_id, hotel_id, account_name, account_type, currency, credit_limit, payment_terms_days, status)
                       VALUES (%s, %s, %s, %s, %s, %s, %s, %s)""", (account_id, hotel_id, name, values[0], hotel["currency"]) + values[1:])
        return {"result": "created", "ar_account_id": account_id}
    return _run(run)


def issue_invoice(hotel_id, ar_account_id, user_id, posting_ids=None, all_uninvoiced=False):
    """Bills the given account postings (or every uninvoiced non-payment posting) on the open business date; due after the account's terms."""
    def run(cur):
        day = _open_day(cur, hotel_id)
        account = _one(cur, "SELECT * FROM ar_account WHERE hotel_id = %s AND ar_account_id = %s", (hotel_id, ar_account_id))
        if not account:
            raise Refused("city ledger account not found")
        if bool(posting_ids) == bool(all_uninvoiced):
            raise Refused("pass posting_ids or all_uninvoiced, not both or neither")
        cur.execute("""SELECT p.posting_id, p.amount FROM finance_posting p
                       WHERE p.ar_account_id = %s AND p.transaction_type NOT IN ('payment','refund')
                         AND NOT EXISTS (SELECT 1 FROM ar_invoice_item it JOIN ar_invoice i ON i.ar_invoice_id = it.ar_invoice_id
                                         WHERE it.posting_id = p.posting_id AND i.status = 'open')""", (ar_account_id,))
        open_items = {row["posting_id"]: row["amount"] for row in cur.fetchall()}
        chosen = list(open_items) if all_uninvoiced else list(posting_ids)
        missing = [pid for pid in chosen if pid not in open_items]
        if missing or not chosen:
            raise Refused("these postings are not uninvoiced items of the account", posting_ids=missing)
        total = sum(open_items[pid] for pid in chosen)
        if total <= 0:
            raise Refused("invoice total must be positive", total=total)
        cur.execute("SELECT pg_advisory_xact_lock(hashtext('ar_invoice|' || %s))", (hotel_id,))
        count = _one(cur, "SELECT COUNT(*) AS n FROM ar_invoice WHERE hotel_id = %s", (hotel_id,))["n"]
        invoice_id, number = new_id("inv"), "INV-" + str(count + 1).zfill(6)
        due = day["business_date"] + timedelta(days=account["payment_terms_days"])
        cur.execute("""INSERT INTO ar_invoice (ar_invoice_id, hotel_id, ar_account_id, invoice_number, invoice_date, due_date, currency, status, issued_by_user_id)
                       VALUES (%s, %s, %s, %s, %s, %s, %s, 'open', %s)""",
                    (invoice_id, hotel_id, ar_account_id, number, day["business_date"], due, account["currency"], user_id))
        for pid in chosen:
            cur.execute("INSERT INTO ar_invoice_item (ar_invoice_item_id, ar_invoice_id, posting_id) VALUES (%s, %s, %s)", (new_id("aii"), invoice_id, pid))
        return {"result": "issued", "ar_invoice_id": invoice_id, "invoice_number": number, "invoice_date": day["business_date"], "due_date": due,
                "total": total, "currency": account["currency"]}
    return _run(run)


def void_invoice(hotel_id, ar_invoice_id, reason, user_id):
    def run(cur):
        invoice = _one(cur, "SELECT * FROM ar_invoice WHERE hotel_id = %s AND ar_invoice_id = %s", (hotel_id, ar_invoice_id))
        if not invoice:
            raise Refused("invoice not found")
        cur.execute("UPDATE ar_invoice SET status = 'void', void_reason = %s, voided_by_user_id = %s WHERE ar_invoice_id = %s", (reason, user_id, ar_invoice_id))
        cur.execute("DELETE FROM ar_invoice_item WHERE ar_invoice_id = %s", (ar_invoice_id,))
        return {"result": "voided", "invoice_number": invoice["invoice_number"]}
    return _run(run)


def allocate_credit(hotel_id, ar_invoice_id, credit_posting_id, amount, user_id):
    def run(cur):
        if not _one(cur, "SELECT 1 AS found FROM ar_invoice WHERE hotel_id = %s AND ar_invoice_id = %s", (hotel_id, ar_invoice_id)):
            raise Refused("invoice not found")
        allocation_id = new_id("ala")
        cur.execute("""INSERT INTO ar_allocation (ar_allocation_id, ar_invoice_id, credit_posting_id, amount, allocated_by_user_id)
                       VALUES (%s, %s, %s, %s, %s)""", (allocation_id, ar_invoice_id, credit_posting_id, _money(amount), user_id))
        return {"result": "allocated", "ar_allocation_id": allocation_id,
                "invoice": _one(cur, "SELECT * FROM fin_invoice_balance WHERE ar_invoice_id = %s", (ar_invoice_id,))}
    return _run(run)


def record_account_payment(hotel_id, ar_account_id, transaction_code, amount, source_module, source_ref, user_id, ar_invoice_id=None,
                           cashier_shift_id=None, reference=None):
    """A payment received on a city-ledger account, applied to ar_invoice_id when given (otherwise it stays an unapplied credit)."""
    paid = post_payment(hotel_id, "account", ar_account_id, transaction_code, amount, source_module, source_ref, user_id=user_id,
                        cashier_shift_id=cashier_shift_id, ar_account_id=ar_account_id, reference=reference)
    if not paid["ok"] or not ar_invoice_id or paid["result"] == "already_posted":
        return paid
    applied = allocate_credit(hotel_id, ar_invoice_id, paid["posting"]["posting_id"], amount, user_id)
    return dict(paid, allocation=applied)


def ar_aging(hotel_id):
    def run(cur):
        cur.execute("SELECT * FROM fin_ar_aging WHERE hotel_id = %s ORDER BY account_name", (hotel_id,))
        accounts = cur.fetchall()
        cur.execute("SELECT * FROM fin_invoice_balance WHERE hotel_id = %s AND status = 'open' AND open_amount <> 0 ORDER BY due_date", (hotel_id,))
        return {"accounts": accounts, "open_invoices": cur.fetchall()}
    return _run(run)


# ---------- GL export mapping ----------

def gl_export(hotel_id, date_from, date_to):
    """Closed-day totals per transaction code with its external GL code. Refused while any exported code has no gl_export_code."""
    def run(cur):
        cur.execute("""SELECT * FROM fin_gl_export WHERE hotel_id = %s AND business_date BETWEEN %s AND %s
                       ORDER BY business_date, department_name, transaction_code""", (hotel_id, date_from, date_to))
        rows = cur.fetchall()
        unmapped = sorted({row["transaction_code"] for row in rows if row["gl_export_code"] is None})
        if unmapped:
            raise Refused("set gl_export_code on these transaction codes before exporting", unmapped=unmapped)
        cur.execute("""SELECT d::date AS business_date FROM generate_series(%s::date, %s::date, interval '1 day') d
                       WHERE NOT EXISTS (SELECT 1 FROM business_day b WHERE b.hotel_id = %s AND b.business_date = d::date AND b.status = 'closed')""",
                    (date_from, date_to, hotel_id))
        return {"rows": rows, "days_not_closed": [row["business_date"] for row in cur.fetchall()]}
    return _run(run)
