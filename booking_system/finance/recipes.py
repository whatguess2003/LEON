"""General recipes for PMS finance, in the seed_general body shape. Definitions only: the coordinator publishes them.
Postings are dated by business_date (the hotel's night-audit date), not by calendar time. Validate: python -m booking_system.finance.recipes"""

from booking_system.semantic.seed_general import PARENT, SOURCE, _HOTEL_CURRENCY, _PERIOD_NAMES, _PERIODS

MODULE = "finance"
_HOTEL_NAME = "(SELECT hotel_name FROM hotel WHERE hotel_id = %(hotel_id)s) AS hotel_name"
_PERIOD_KEYS = ["hotel_name", "period", "period_start", "period_end", "days_in_period"]
_CURRENCY = [
    ("hotel_currency", "(SELECT code FROM hotel_currency)"),
    ("mixed_currency", "COUNT(*) FILTER (WHERE s.currency <> (SELECT code FROM hotel_currency)) > 0"),
]
_COMMON_LIMITS = [
    "Rows cover " + _PERIOD_NAMES + ". business_date is the hotel's night-audit date; the open business date can lag the calendar today by one day.",
    "Every period has a row for every grain member of the hotel (its active codes and any code that has posted); a period or member "
    "with no posting reads zero, including dates before the hotel started posting through the finance module.",
    "Corrections are reversal postings dated on the business date they were made; sums are net of them, so a correction lands in the "
    "period of the reversal, not of the original posting. posting_count includes reversal rows.",
    "Every amount is in the hotel currency: the database refuses a posting in another currency. mixed_currency is kept for the shared contract and is false.",
]


_CODES = (
    "codes AS (SELECT t.transaction_code_id, t.transaction_code, t.transaction_type, t.payment_method, t.gl_export_code, d.department_name "
    "FROM transaction_code t JOIN department d ON d.department_id = t.department_id WHERE t.hotel_id = %(hotel_id)s "
    "AND (t.status = 'active' OR EXISTS (SELECT 1 FROM finance_posting x WHERE x.transaction_code_id = t.transaction_code_id)))"
)
_REVENUE_TYPES = "transaction_type IN ('charge','adjustment')"
_PAYMENT_TYPES = "transaction_type IN ('payment','refund')"


def _period_sql(source, dims, measures, members, total=""):
    """One row per named period and grain member, from the period scaffold and the hotel's members, so a period or member with no
    posting reads zero. Members are the hotel's active codes and any code that has posted. total labels an extra all-members row."""
    on = "".join(" AND s." + dim + " IS NOT DISTINCT FROM m." + dim for dim in dims)
    keys = "p.period, p.period_start, p.period_end"
    grouping = "GROUP BY " + keys + "".join(", m." + dim for dim in dims)
    labels = "".join("m." + dim + ", " for dim in dims)
    having = ""
    if total:
        grouping = "GROUP BY GROUPING SETS ((" + keys + "), (" + keys + "".join(", m." + dim for dim in dims) + "))"
        labels = "".join("CASE WHEN GROUPING(m." + dim + ") = 1 THEN '" + total + "' ELSE m." + dim + " END AS " + dim + ", " for dim in dims)
        having = "HAVING GROUPING(m." + dims[0] + ") = 1 OR m." + dims[0] + " IS NOT NULL\n"
    return (
        "WITH " + _PERIODS + ",\n" + _HOTEL_CURRENCY + ",\n" + _CODES + ",\n" + "s0 AS (" + source + "),\n" + "m AS (" + members + ")\n"
        "SELECT " + _HOTEL_NAME + ", p.period, p.period_start, p.period_end, p.period_end - p.period_start + 1 AS days_in_period, "
        + labels + ",\n    ".join(expr + " AS " + name for name, expr in measures) + "\n"
        "FROM periods p " + ("LEFT JOIN m ON TRUE" if total else "CROSS JOIN m") + "\n"
        "LEFT JOIN s0 s ON s.business_date BETWEEN p.period_start AND p.period_end" + on + "\n"
        + grouping + "\n" + having
        + "ORDER BY p.period_end - p.period_start, p.period_start" + ("".join(", GROUPING(m." + dim + ") DESC" for dim in dims[:1]) if total else "")
        + "".join(", " + ("" if total else "m.") + dim for dim in dims)
    )


def _recipe(catalog_id, title, problem, keyphrases, default, concepts, keys, columns, limitations, sql, grain="night", periodic=True):
    return {
        "catalog_id": catalog_id,
        "parent": PARENT,
        "module_name": MODULE,
        "title": title,
        "purpose": problem,
        "problem": problem,
        "preconditions": [
            "The hotel runs the finance module: postings, cashier shifts and business dates are in finance tables.",
            "Only the session hotel is bound; the read filters hotel_id = %(hotel_id)s."
            + (" Select the row whose period is the requested day or range." if periodic else " Rows are the current state, not a period."),
        ],
        "inputs": {
            "hotel_id": "bound by the platform from the session hotel",
            "start": "MM/DD/YYYY inclusive start, bound as %(start)s and read with to_date(start, 'MM/DD/YYYY')",
            "end": "MM/DD/YYYY inclusive end, bound as %(end)s and read with to_date(end, 'MM/DD/YYYY')",
        } if periodic else {
            "hotel_id": "bound by the platform from the session hotel",
            "date_or_range": "none; current state",
        },
        "output_grain": grain,
        "outputs": list(keys) + [name for name, _expr in columns],
        "default_output": default,
        "limitations": (_COMMON_LIMITS if periodic else _COMMON_LIMITS[3:]) + ["Default reading: " + default + "."] + list(limitations),
        "verification": "Executed for the validation hotel against finance postings, shifts and business days; totals checked against direct sums of finance_posting.",
        "keyphrases": list(keyphrases),
        "aliases": list(keyphrases),
        "concepts": [{"name": name, "formula": formula, "tables": tables} for name, formula, tables in concepts],
        "solution": {
            "sql_template": sql,
            "parameters": ["hotel_id", "start", "end"] if periodic else ["hotel_id"],
            "method": SOURCE,
        },
        "validation": {"expected_value": ""},
    }


_POSTINGS = (
    "SELECT p.business_date, p.transaction_type, p.amount, p.quantity, p.currency, p.reverses_posting_id, t.transaction_code, t.gl_export_code, "
    "t.payment_method, d.department_name, x.tax_code, p.parent_posting_id, "
    "hotel_setting_at(p.hotel_id, 'finance.tax_pricing', p.business_date) #>> '{}' AS tax_pricing, "
    "(SELECT COALESCE(SUM(c.amount), 0) FROM finance_posting c WHERE c.parent_posting_id = p.posting_id AND c.transaction_type = 'tax') AS child_tax "
    "FROM finance_posting p JOIN transaction_code t ON t.transaction_code_id = p.transaction_code_id "
    "JOIN department d ON d.department_id = t.department_id LEFT JOIN tax_code x ON x.tax_code_id = p.tax_code_id "
    "WHERE p.hotel_id = %(hotel_id)s"
)
# The hotel's tax pricing setting over each period (one value, or several when it changed inside the period).
_PRICING_IN_EFFECT = ("(SELECT string_agg(DISTINCT hotel_setting_at(%(hotel_id)s, 'finance.tax_pricing', g.d::date) #>> '{}', ', ') "
                      "FROM generate_series(p.period_start, p.period_end, interval '1 day') g(d))")
_TAXED = [
    ("tax_on_revenue", "COALESCE(SUM(s.child_tax), 0)"),
    ("gross_revenue", "COALESCE(SUM(s.amount + s.child_tax), 0)"),
    ("revenue_at_quoted_prices", "COALESCE(SUM(CASE WHEN s.tax_pricing = 'inclusive' THEN s.amount + s.child_tax ELSE s.amount END), 0)"),
    ("tax_pricing_in_effect", _PRICING_IN_EFFECT),
]

_REVENUE_COLUMNS = [
    ("net_revenue", "COALESCE(SUM(s.amount), 0)"),
    ("charge_amount", "COALESCE(SUM(s.amount) FILTER (WHERE s.transaction_type = 'charge'), 0)"),
    ("adjustment_amount", "COALESCE(SUM(s.amount) FILTER (WHERE s.transaction_type = 'adjustment'), 0)"),
    ("reversed_amount", "COALESCE(SUM(s.amount) FILTER (WHERE s.reverses_posting_id IS NOT NULL), 0)"),
    ("posting_count", "COUNT(s.business_date)"),
] + _TAXED + _CURRENCY

_REVENUE_LIMIT = ("Revenue is the net of charge and adjustment postings (allowances are negative), before tax; tax postings are in "
                  "fin_tax_collected. It is posted revenue, so it starts when the hotel starts posting through the finance module and "
                  "differs from booking-value readings such as daily_room_revenue.")
_PRICING_LIMIT = ("Postings always store the net and the tax apart, whatever the hotel's finance.tax_pricing setting. gross_revenue is net plus "
                  "the tax posted on it. revenue_at_quoted_prices reads each posting by the setting in effect on its business date: the gross "
                  "under tax-inclusive pricing (the price the guest was quoted), the net under tax-exclusive pricing; use it for 'revenue' as "
                  "the hotel quotes prices. tax_pricing_in_effect lists the setting over the period (two values when it changed inside the "
                  "period; empty when the hotel has not answered it).")

_PAYMENT_COLUMNS = [
    ("payments_received", "COALESCE(-SUM(s.amount) FILTER (WHERE s.transaction_type = 'payment'), 0)"),
    ("refunds_paid", "COALESCE(SUM(s.amount) FILTER (WHERE s.transaction_type = 'refund'), 0)"),
    ("net_received", "COALESCE(-SUM(s.amount), 0)"),
    ("posting_count", "COUNT(s.business_date)"),
] + _CURRENCY
_TAX_COLUMNS = [
    ("tax_amount", "COALESCE(SUM(s.amount), 0)"),
    ("taxable_base", "COALESCE(SUM((SELECT b.amount FROM finance_posting b WHERE b.posting_id = s.parent_posting_id)), 0)"),
    ("posting_count", "COUNT(s.business_date)"),
    ("tax_pricing_in_effect", _PRICING_IN_EFFECT),
] + _CURRENCY
_ALL_CASHIERS = "ALL CASHIERS"
_SHIFT_COLUMNS = [
    ("shifts_closed", "COUNT(s.business_date) FILTER (WHERE s.status = 'closed')"),
    ("shifts_open", "COUNT(s.business_date) FILTER (WHERE s.status = 'open')"),
    ("opening_float_total", "COALESCE(SUM(s.opening_float), 0)"),
    ("expected_cash_total", "COALESCE(SUM(s.expected_cash), 0)"),
    ("counted_cash_total", "COALESCE(SUM(s.counted_cash), 0)"),
    ("variance_total", "COALESCE(SUM(s.variance), 0)"),
    ("absolute_variance_total", "COALESCE(SUM(ABS(s.variance)), 0)"),
    ("shifts_with_variance", "COUNT(s.business_date) FILTER (WHERE s.variance <> 0)"),
] + _CURRENCY

_FOLIO_SQL = (
    "WITH " + _HOTEL_CURRENCY + ",\n"
    "bounds AS (SELECT to_date(%(start)s, 'MM/DD/YYYY') AS period_start, to_date(%(end)s, 'MM/DD/YYYY') AS period_end),\n"
    "f AS (SELECT p.folio_type, p.folio_ref, p.currency, SUM(p.amount) AS balance FROM finance_posting p CROSS JOIN bounds "
    "WHERE p.hotel_id = %(hotel_id)s AND p.business_date BETWEEN bounds.period_start AND bounds.period_end "
    "GROUP BY p.folio_type, p.folio_ref, p.currency)\n"
    "SELECT " + _HOTEL_NAME + ", COALESCE(f.folio_type, 'all') AS folio_type, "
    "COUNT(*) FILTER (WHERE f.balance <> 0) AS open_folios, "
    "COUNT(*) FILTER (WHERE f.balance > 0) AS folios_owing, "
    "COALESCE(SUM(f.balance) FILTER (WHERE f.balance > 0), 0) AS amount_owed, "
    "COALESCE(-SUM(f.balance) FILTER (WHERE f.balance < 0), 0) AS credit_balances, "
    "COALESCE(SUM(f.balance), 0) AS net_balance, "
    "(SELECT code FROM hotel_currency) AS hotel_currency, "
    "COUNT(*) FILTER (WHERE f.currency <> (SELECT code FROM hotel_currency)) > 0 AS mixed_currency\n"
    "FROM f GROUP BY GROUPING SETS ((f.folio_type), ())\n"
    "ORDER BY f.folio_type IS NULL, f.folio_type"
)

_AGING_COLUMNS = ("not_due", "overdue_1_30", "overdue_31_60", "overdue_61_90", "overdue_90_plus", "uninvoiced", "unapplied_credit", "balance")
_AGING_SQL = (
    "WITH " + _HOTEL_CURRENCY + ",\n"
    "bounds AS (SELECT to_date(%(start)s, 'MM/DD/YYYY') AS period_start, to_date(%(end)s, 'MM/DD/YYYY') AS period_end),\n"
    "open_inv AS (SELECT b.ar_account_id, b.open_amount, b.due_date, b.currency FROM fin_invoice_balance b CROSS JOIN bounds "
    "WHERE b.hotel_id = %(hotel_id)s AND b.status = 'open' AND b.open_amount <> 0 "
    "AND b.invoice_date BETWEEN bounds.period_start AND bounds.period_end),\n"
    "aged AS (SELECT c.ar_account_id, "
    "COALESCE(SUM(i.open_amount) FILTER (WHERE bounds.period_end <= i.due_date), 0) AS not_due, "
    "COALESCE(SUM(i.open_amount) FILTER (WHERE bounds.period_end - i.due_date BETWEEN 1 AND 30), 0) AS overdue_1_30, "
    "COALESCE(SUM(i.open_amount) FILTER (WHERE bounds.period_end - i.due_date BETWEEN 31 AND 60), 0) AS overdue_31_60, "
    "COALESCE(SUM(i.open_amount) FILTER (WHERE bounds.period_end - i.due_date BETWEEN 61 AND 90), 0) AS overdue_61_90, "
    "COALESCE(SUM(i.open_amount) FILTER (WHERE bounds.period_end - i.due_date > 90), 0) AS overdue_90_plus, "
    "(SELECT COALESCE(SUM(p.amount), 0) FROM finance_posting p WHERE p.ar_account_id = c.ar_account_id "
    "AND p.business_date BETWEEN bounds.period_start AND bounds.period_end AND p.transaction_type NOT IN ('payment','refund') "
    "AND NOT EXISTS (SELECT 1 FROM ar_invoice_item it JOIN ar_invoice inv ON inv.ar_invoice_id = it.ar_invoice_id "
    "WHERE it.posting_id = p.posting_id AND inv.status = 'open')) AS uninvoiced, "
    "(SELECT COALESCE(SUM(-p.amount), 0) FROM finance_posting p WHERE p.ar_account_id = c.ar_account_id "
    "AND p.transaction_type IN ('payment','refund') AND p.business_date BETWEEN bounds.period_start AND bounds.period_end) "
    "- COALESCE((SELECT SUM(x.amount) FROM ar_allocation x JOIN finance_posting cr ON cr.posting_id = x.credit_posting_id "
    "WHERE cr.ar_account_id = c.ar_account_id AND cr.business_date BETWEEN bounds.period_start AND bounds.period_end), 0) AS unapplied_credit, "
    "(SELECT COALESCE(SUM(p.amount), 0) FROM finance_posting p WHERE p.ar_account_id = c.ar_account_id "
    "AND p.business_date BETWEEN bounds.period_start AND bounds.period_end) AS balance "
    "FROM ar_account c CROSS JOIN bounds LEFT JOIN open_inv i ON i.ar_account_id = c.ar_account_id "
    "WHERE c.hotel_id = %(hotel_id)s GROUP BY c.ar_account_id, bounds.period_start, bounds.period_end)\n"
    "SELECT " + _HOTEL_NAME + ", to_char(bounds.period_end, 'YYYY-MM-DD') AS as_of_business_date, "
    "CASE WHEN GROUPING(c.account_name) = 1 THEN 'ALL ACCOUNTS' ELSE c.account_name END AS account_name, "
    "CASE WHEN GROUPING(c.account_name) = 0 THEN MAX(c.account_type) END AS account_type, COALESCE(SUM(c.credit_limit), 0) AS credit_limit, "
    + ", ".join("COALESCE(SUM(a." + col + "), 0) AS " + col for col in _AGING_COLUMNS) + ", "
    "COALESCE(SUM(a.overdue_1_30 + a.overdue_31_60 + a.overdue_61_90 + a.overdue_90_plus), 0) AS overdue_total, "
    "COUNT(c.ar_account_id) FILTER (WHERE a.balance > c.credit_limit) AS accounts_over_limit, "
    "(SELECT code FROM hotel_currency) AS hotel_currency, "
    "COUNT(c.ar_account_id) FILTER (WHERE EXISTS (SELECT 1 FROM finance_posting p WHERE p.ar_account_id = c.ar_account_id "
    "AND p.business_date BETWEEN bounds.period_start AND bounds.period_end "
    "AND p.currency <> (SELECT code FROM hotel_currency))) > 0 AS mixed_currency\n"
    "FROM bounds CROSS JOIN ar_account c LEFT JOIN aged a ON a.ar_account_id = c.ar_account_id\n"
    "WHERE c.hotel_id = %(hotel_id)s AND (c.status = 'active' OR a.ar_account_id IS NOT NULL)\n"
    "GROUP BY GROUPING SETS ((c.account_name, bounds.period_start, bounds.period_end), (bounds.period_start, bounds.period_end))\n"
    "ORDER BY GROUPING(c.account_name), c.account_name"
)

_SHIFTS = (
    "SELECT d.business_date, c.cashier_user_id, c.status, c.opening_float, c.expected_cash, c.counted_cash, c.variance, c.currency "
    "FROM cashier_shift c JOIN business_day d ON d.business_day_id = c.business_day_id WHERE c.hotel_id = %(hotel_id)s"
)

_CLOSE_SQL = (
    "WITH " + _HOTEL_CURRENCY + ",\n"
    "days AS (SELECT * FROM business_day WHERE hotel_id = %(hotel_id)s "
    "AND business_date BETWEEN to_date(%(start)s, 'MM/DD/YYYY') AND to_date(%(end)s, 'MM/DD/YYYY') ORDER BY business_date)\n"
    "SELECT " + _HOTEL_NAME + ", d.business_date, d.status, d.status = 'open' AS is_current_business_date, "
    "(d.closed_at AT TIME ZONE current_setting('TimeZone')) AS closed_at_local, "
    "d.closed_by_user_id, "
    "(SELECT COUNT(*) FROM cashier_shift c WHERE c.business_day_id = d.business_day_id AND c.status = 'open') AS open_cashier_shifts, "
    "(SELECT COUNT(*) FROM finance_posting p WHERE p.business_day_id = d.business_day_id) AS posting_count, "
    "(SELECT COALESCE(SUM(p.amount), 0) FROM finance_posting p WHERE p.business_day_id = d.business_day_id "
    "AND p.transaction_type IN ('charge','adjustment')) AS net_revenue, "
    "(SELECT COALESCE(-SUM(p.amount), 0) FROM finance_posting p WHERE p.business_day_id = d.business_day_id "
    "AND p.transaction_type IN ('payment','refund')) AS net_payments, "
    "(SELECT COUNT(*) FROM daily_close_summary s WHERE s.business_day_id = d.business_day_id) AS summary_rows, "
    "(SELECT code FROM hotel_currency) AS hotel_currency\n"
    "FROM days d ORDER BY d.business_date DESC"
)

RECIPES = [
    _recipe(
        "fin_revenue_by_department", "Posted revenue by department",
        "Posted revenue (net of allowances, before tax) by department for a business day or range, from folio postings.",
        ["revenue by department", "departmental revenue", "department sales", "rooms revenue posted", "f&b revenue", "revenue split by department"],
        "net_revenue",
        [("net_revenue", "SUM(finance_posting.amount) of charge and adjustment postings, grouped by transaction_code.department_id",
          ["finance_posting", "transaction_code", "department"])],
        _PERIOD_KEYS + ["department_name"], _REVENUE_COLUMNS, [_REVENUE_LIMIT, _PRICING_LIMIT],
        _period_sql(_POSTINGS + " AND p.transaction_type IN ('charge','adjustment')", ["department_name"], _REVENUE_COLUMNS,
                    "SELECT DISTINCT department_name FROM codes WHERE " + _REVENUE_TYPES),
        grain="department",
    ),
    _recipe(
        "fin_revenue_by_transaction_code", "Posted revenue by transaction code",
        "Posted revenue (net of allowances, before tax) by transaction code, with its department and GL export code, for a business day or range.",
        ["revenue by transaction code", "revenue by charge code", "sales by code", "transaction code report", "revenue by gl code"],
        "net_revenue",
        [("net_revenue", "SUM(finance_posting.amount) of charge and adjustment postings per transaction_code", ["finance_posting", "transaction_code"])],
        _PERIOD_KEYS + ["department_name", "transaction_code", "gl_export_code"],
        _REVENUE_COLUMNS[:1] + [("quantity", "COALESCE(SUM(s.quantity), 0)")] + _REVENUE_COLUMNS[3:],
        [_REVENUE_LIMIT, _PRICING_LIMIT, "gl_export_code is the code in the hotel's external accounting system; NULL means it is not mapped yet."],
        _period_sql(_POSTINGS + " AND p.transaction_type IN ('charge','adjustment')", ["department_name", "transaction_code", "gl_export_code"],
                    _REVENUE_COLUMNS[:1] + [("quantity", "COALESCE(SUM(s.quantity), 0)")] + _REVENUE_COLUMNS[3:],
                    "SELECT DISTINCT department_name, transaction_code, gl_export_code FROM codes WHERE " + _REVENUE_TYPES),
        grain="transaction_code",
    ),
    _recipe(
        "fin_payments_by_method", "Payments by method",
        "Payments received and refunds paid by payment method (cash, card, bank transfer, channel collect, voucher) for a business day or range.",
        ["payments by method", "payment methods", "cash taken", "card payments", "how much cash did we collect", "payment breakdown", "refunds"],
        "net_received",
        [("net_received", "-SUM(finance_posting.amount) of payment and refund postings, grouped by transaction_code.payment_method",
          ["finance_posting", "transaction_code"])],
        _PERIOD_KEYS + ["payment_method"], _PAYMENT_COLUMNS,
        ["Payments are stored negative on the folio and refunds positive; outputs are shown as money in (received) and money out (refunds).",
         "Payments on city-ledger accounts are included; transfers to the city ledger are not payments."],
        _period_sql(_POSTINGS + " AND p.transaction_type IN ('payment','refund')", ["payment_method"], _PAYMENT_COLUMNS,
                    "SELECT DISTINCT payment_method FROM codes WHERE " + _PAYMENT_TYPES),
        grain="payment_method",
    ),
    _recipe(
        "fin_tax_collected", "Tax collected by tax code",
        "Tax posted (collected) by tax code, with the taxable base it was computed on, for a business day or range.",
        ["tax collected", "tax report", "sst collected", "service charge collected", "tourism tax", "tax summary"],
        "tax_amount",
        [("tax_amount", "SUM(finance_posting.amount) of tax postings per tax_code", ["finance_posting", "tax_code"]),
         ("taxable_base", "SUM of the parent charge amounts of those tax postings", ["finance_posting"])],
        _PERIOD_KEYS + ["tax_code"], _TAX_COLUMNS,
        ["A tax posting exists only when its tax code has a rule effective on the business date; the finance module refuses a taxable "
         "posting without one, so there is no silent 0% tax. A transaction code that is not taxable posts no tax row.",
         "Each tax follows the hotel's settings in effect on the business date: finance.tax_pricing exclusive adds it on top of the "
         "quoted price, inclusive takes it out of the quoted price; finance.tax_calculation compound computes each tax on the net plus "
         "the taxes before it in finance.tax_order. taxable_base is always the net charge the tax was posted on (for a compound tax "
         "the earlier taxes it also applied to are in their own rows). Per-unit taxes (for example tourism tax per night) are per unit, "
         "not a share of the base. tax_pricing_in_effect lists the pricing setting over the period."],
        _period_sql(_POSTINGS + " AND p.transaction_type = 'tax'", ["tax_code"], _TAX_COLUMNS,
                    "SELECT x.tax_code FROM tax_code x WHERE x.hotel_id = %(hotel_id)s AND (x.status = 'active' OR EXISTS "
                    "(SELECT 1 FROM finance_posting y WHERE y.tax_code_id = x.tax_code_id))"),
        grain="tax_code",
    ),
    _recipe(
        "fin_outstanding_folio_balances", "Outstanding folio balances",
        "Outstanding folio balances by folio type (guest, city-ledger account, outlet) and in total for a business day or range: folios with postings in that span, amount owed, and credit balances.",
        ["outstanding folio balance", "open folios", "folio balances", "guest ledger", "who owes on their folio", "open balances"],
        "amount_owed",
        [("amount_owed", "SUM(fin_folio_balance.balance) over folios with a positive balance", ["fin_folio_balance", "finance_posting"])],
        ["hotel_name", "folio_type"],
        [("open_folios", ""), ("folios_owing", ""), ("amount_owed", ""), ("credit_balances", ""), ("net_balance", ""),
         ("hotel_currency", ""), ("mixed_currency", "")],
        ["Figures are finance postings whose business_date is from start through end inclusive.",
         "The row with folio_type 'all' totals every folio type.",
         "A folio is identified by (folio_type, folio_ref); guest folio_ref is the core folio or booking reference.",
         "Balances are folio postings only; bookings without postings have no folio balance here."],
        _FOLIO_SQL, grain="folio_type",
    ),
    _recipe(
        "fin_city_ledger_aging", "City ledger aging",
        "Accounts receivable aging of the city ledger (direct-bill company, travel agent and channel accounts) for a business day or range: "
        "open invoices issued in that span, aged as of the end date, with uninvoiced charges, unapplied credits and balance per account and in total.",
        ["ar aging", "city ledger aging", "accounts receivable aging", "receivables aging", "overdue invoices", "direct bill balances", "who owes us"],
        "overdue_total",
        [("overdue_total", "open invoice amount issued in the span whose due date is before the span end", ["fin_invoice_balance", "ar_invoice", "ar_allocation"])],
        ["hotel_name", "as_of_business_date", "account_name"],
        [("account_type", ""), ("credit_limit", "")] + [(col, "") for col in _AGING_COLUMNS]
        + [("overdue_total", ""), ("accounts_over_limit", ""), ("hotel_currency", ""), ("mixed_currency", "")],
        ["Aging is by invoice due date against the span end. Invoices are those issued from start through end inclusive. "
         "Every active city-ledger account has a row, and the ALL ACCOUNTS row is always returned; an account with nothing in the span reads zero. "
         "as_of_business_date is the span end.",
         "uninvoiced counts account postings not yet on an open invoice; unapplied_credit counts payments not yet allocated to an invoice. "
         "balance is the net account folio balance (charges minus payments).",
         "The row with account_name 'ALL ACCOUNTS' totals every account."],
        _AGING_SQL, grain="account",
    ),
    _recipe(
        "fin_cashier_variance", "Cashier drawer variance",
        "Cash drawer reconciliation by cashier for a business day or range: shifts closed and still open, expected cash, counted cash, and over/short variance.",
        ["cashier variance", "cash over short", "drawer variance", "cash drawer", "cashier reconciliation", "till variance", "shift report"],
        "variance_total",
        [("variance_total", "SUM(cashier_shift.counted_cash - cashier_shift.expected_cash) of closed shifts", ["cashier_shift", "business_day"])],
        _PERIOD_KEYS + ["cashier_user_id"], _SHIFT_COLUMNS,
        ["expected cash is the opening float plus net cash taken in the shift (cash payments minus cash refunds), fixed when the shift closes; "
         "open shifts have no expected, counted or variance yet.",
         "A positive variance is cash over, a negative one cash short. Shifts are dated by the business date they were opened on.",
         "Each period has one row per cashier who has opened a shift at the hotel and one " + _ALL_CASHIERS + " row (cashier_user_id) "
         "that totals every cashier; a period with no shift reads zero shifts and zero variance."],
        _period_sql(_SHIFTS, ["cashier_user_id"], _SHIFT_COLUMNS,
                    "SELECT DISTINCT cashier_user_id FROM cashier_shift WHERE hotel_id = %(hotel_id)s", _ALL_CASHIERS),
        grain="cashier_shift",
    ),
    _recipe(
        "fin_daily_close_status", "Night audit close status",
        "Night audit / daily close status of the latest business dates: which date is open, when each was closed and by whom, open cashier shifts, "
        "and the posted revenue and payments of each date.",
        ["night audit", "daily close", "business date", "is the day closed", "close status", "end of day", "current business date"],
        "status",
        [("status", "business_day.status per business_date; one open date per hotel", ["business_day"])],
        ["hotel_name", "business_date"],
        [("status", ""), ("is_current_business_date", ""), ("closed_at_local", ""), ("closed_by_user_id", ""), ("open_cashier_shifts", ""),
         ("posting_count", ""), ("net_revenue", ""), ("net_payments", ""), ("summary_rows", ""), ("hotel_currency", "")],
        ["Returns each business date from start through end inclusive. A closed date is locked: no posting can be added; corrections post on the open date.",
         "net_revenue is charges and adjustments before tax; net_payments is payments minus refunds. summary_rows is the size of the close snapshot "
         "(0 while open)."],
        _CLOSE_SQL, periodic=True,
    ),
]


def validate(keys=None):
    """Contract and read-only execution through the publication gate (fixture, outputs, hotel isolation). Nothing is written."""
    from booking_system.semantic.library import validate_publication
    from booking_system.semantic.recipe import recipe_contract
    results = []
    for body in RECIPES:
        if keys and body["catalog_id"] not in keys:
            continue
        missing = recipe_contract(body)
        report = {"ok": False, "error": "recipe contract " + " ".join(missing)} if missing else validate_publication(body["catalog_id"], body)
        results.append({"catalog_id": body["catalog_id"], "ok": report.get("ok"), "error": report.get("error"), "rows": (report.get("rows") or [])[:6]})
    return results


if __name__ == "__main__":
    import json
    import sys
    from dotenv import load_dotenv
    load_dotenv()
    for result in validate(keys=sys.argv[1:]):
        print(json.dumps(result, default=str), flush=True)
