"""F&B outlet general recipes (PMS level). Same body, period handling, currency flags and %(hotel_id)s scoping as
semantic/seed_general.py; the coordinator publishes them. Rows: one per named period and outlet, plus an ALL OUTLETS row per period."""

from booking_system.semantic.seed_general import _HOTEL_CURRENCY, _PERIOD_NAMES, _PERIODS

SOURCE = "general_seed"
PARENT = "B01"
MODULE = "fnb"
ALL_OUTLETS = "ALL OUTLETS"

_POSTED = "p.transaction_type IN ('charge', 'adjustment')"
_FACTS = (
    "outlets AS (SELECT outlet_id, outlet_name FROM outlet WHERE hotel_id = %(hotel_id)s),\n"
    "fnb_posted AS (SELECT p.outlet_id, p.business_date AS d, "
    "SUM(p.amount) FILTER (WHERE " + _POSTED + ") AS net, "
    "SUM(p.amount) FILTER (WHERE p.transaction_type = 'tax') AS tax, "
    "SUM(p.amount) FILTER (WHERE p.folio_type = 'guest' AND (" + _POSTED + " OR p.transaction_type = 'tax')) AS room_total, "
    "SUM(p.amount) FILTER (WHERE p.folio_type = 'outlet' AND (" + _POSTED + " OR p.transaction_type = 'tax')) AS direct_total, "
    "COUNT(*) FILTER (WHERE p.currency <> (SELECT code FROM hotel_currency)) AS foreign_postings, "
    "CASE WHEN hotel_setting_at(%(hotel_id)s, 'finance.tax_pricing', p.business_date) #>> '{}' = 'inclusive' "
    "THEN COALESCE(SUM(p.amount) FILTER (WHERE " + _POSTED + " OR p.transaction_type = 'tax'), 0) "
    "ELSE COALESCE(SUM(p.amount) FILTER (WHERE " + _POSTED + "), 0) END AS quoted "
    "FROM finance_posting p WHERE p.hotel_id = %(hotel_id)s AND p.source_module = 'fnb' AND p.outlet_id IS NOT NULL "
    "GROUP BY p.outlet_id, p.business_date),\n"
    "fnb_checks AS (SELECT c.outlet_id, c.business_date AS d, "
    "COUNT(*) FILTER (WHERE c.status = 'closed') AS closed_checks, "
    "SUM(c.covers) FILTER (WHERE c.status = 'closed') AS covers, "
    "COUNT(*) FILTER (WHERE c.status = 'open') AS open_checks, "
    "COUNT(*) FILTER (WHERE c.status = 'void') AS void_checks, "
    "SUM(c.discount_total) FILTER (WHERE c.status = 'closed') AS discount, "
    "SUM(c.total) FILTER (WHERE c.status = 'closed') AS check_total, "
    "COUNT(*) FILTER (WHERE c.status = 'closed' AND c.settlement = 'room') AS room_checks, "
    "COUNT(*) FILTER (WHERE c.status = 'closed' AND c.settlement = 'direct') AS direct_checks, "
    "COUNT(*) FILTER (WHERE c.status = 'closed' AND c.currency <> (SELECT code FROM hotel_currency)) AS foreign_checks "
    "FROM fnb_check c WHERE c.hotel_id = %(hotel_id)s GROUP BY c.outlet_id, c.business_date),\n"
    "fnb_lines AS (SELECT c.outlet_id, c.business_date AS d, "
    "SUM(round(l.quantity * l.unit_price, 2)) FILTER (WHERE c.status = 'closed' AND NOT l.voided) AS gross, "
    "COUNT(*) FILTER (WHERE c.status = 'closed' AND NOT l.voided AND l.discount_amount > 0) AS discounted_lines, "
    "SUM(round(l.quantity * l.unit_price, 2)) FILTER (WHERE c.status = 'void' AND NOT l.voided) AS void_check_value, "
    "COUNT(*) FILTER (WHERE l.voided) AS voided_lines, "
    "SUM(round(l.quantity * l.unit_price, 2)) FILTER (WHERE l.voided) AS voided_line_value "
    "FROM fnb_check c JOIN fnb_check_line l ON l.check_id = c.check_id WHERE c.hotel_id = %(hotel_id)s "
    "GROUP BY c.outlet_id, c.business_date)"
)
_DAILY_COLUMNS = {
    "fnb_posted": ("net", "tax", "room_total", "direct_total", "foreign_postings", "quoted"),
    "fnb_checks": ("closed_checks", "covers", "open_checks", "void_checks", "discount", "check_total", "room_checks", "direct_checks",
                   "foreign_checks"),
    "fnb_lines": ("gross", "discounted_lines", "void_check_value", "voided_lines", "voided_line_value"),
}
_DAILY = (
    "daily AS (SELECT o.outlet_id, o.outlet_name, span.d, "
    + ", ".join("COALESCE(" + cte + "." + col + ", 0) AS " + col for cte, cols in _DAILY_COLUMNS.items() for col in cols)
    + " FROM span CROSS JOIN outlets o "
    + " ".join("LEFT JOIN " + cte + " ON " + cte + ".outlet_id = o.outlet_id AND " + cte + ".d = span.d" for cte in _DAILY_COLUMNS)
    + ")"
)


def _period_sql(columns):
    return (
        "WITH " + ",\n".join([_PERIODS, _HOTEL_CURRENCY, _FACTS, _DAILY]) + "\n"
        "SELECT (SELECT hotel_name FROM hotel WHERE hotel_id = %(hotel_id)s) AS hotel_name, p.period, p.period_start, p.period_end, "
        "p.period_end - p.period_start + 1 AS days_in_period, "
        "CASE WHEN GROUPING(x.outlet_name) = 1 THEN '" + ALL_OUTLETS + "' ELSE x.outlet_name END AS outlet_name,\n    "
        + ",\n    ".join(expr + " AS " + name for name, expr in columns) + ",\n    "
        "(SELECT code FROM hotel_currency) AS hotel_currency, SUM(x.foreign_checks + x.foreign_postings) > 0 AS mixed_currency, "
        "SUM(x.foreign_checks) AS foreign_currency_checks, SUM(x.open_checks) AS open_checks, "
        "SUM(x.check_total) - SUM(x.net + x.tax) AS posting_gap\n"
        "FROM periods p JOIN daily x ON x.d BETWEEN p.period_start AND p.period_end\n"
        "GROUP BY GROUPING SETS ((p.period, p.period_start, p.period_end), (p.period, p.period_start, p.period_end, x.outlet_name))\n"
        "ORDER BY p.period_end - p.period_start, p.period_start, GROUPING(x.outlet_name) DESC, x.outlet_name"
    )


def _sum(col):
    return "SUM(x." + col + ")"


def _rate(num, den, scale="1.0"):
    return "ROUND(" + scale + " * SUM(x." + num + ") / NULLIF(SUM(x." + den + "), 0), 2)"


_KEYS = ["hotel_name", "period", "period_start", "period_end", "days_in_period", "outlet_name"]
_FLAGS = ["hotel_currency", "mixed_currency", "foreign_currency_checks", "open_checks", "posting_gap"]
_LIMITS = [
    "Rows cover only these periods of business dates: " + _PERIOD_NAMES + ". Another calendar date or range is not answered by this recipe.",
    "Each period has one row per outlet (outlet_name) and one ALL OUTLETS row that sums every outlet.",
    "Revenue and tax are Finance's postings from F&B (finance_posting, source_module 'fnb'), on the business date they posted: the same "
    "figures the daily close reports. A check posts when it is settled; open checks (open_checks) are not yet revenue and voided checks never are.",
    "Covers, checks, discounts and voids come from the F&B checks (fnb_check, fnb_check_line) on their business_date.",
    "posting_gap is closed check totals minus posted charges and tax; a non-zero value means closed checks that did not reach Finance "
    "(or were corrected there) and must be reconciled before quoting revenue as final.",
    "net is the sale after discounts and before tax; total includes tax. Menu prices follow the hotel's finance.tax_pricing setting in "
    "effect on each business date: tax-exclusive prices add tax on top, tax-inclusive prices contain it (the check total is the menu "
    "price and Finance posts the net and tax inside it). gross_revenue is the menu-price value of the lines, so it includes tax under "
    "inclusive pricing.",
    "hotel_currency is the hotel's configured currency; mixed_currency is true when a counted check or posting is in another currency. "
    "Do not state a total as one currency when mixed_currency is true.",
]


def _recipe(catalog_id, title, problem, keyphrases, default, concepts, columns, limitations):
    return {
        "catalog_id": catalog_id,
        "parent": PARENT,
        "module_name": MODULE,
        "title": title,
        "purpose": problem,
        "problem": problem,
        "preconditions": [
            "The hotel has a configured time zone; CURRENT_DATE is the hotel-local today.",
            "Only the session hotel is bound; the read filters hotel_id = %(hotel_id)s. Select the row whose period is the requested day "
            "or range and whose outlet_name is the requested outlet, or ALL OUTLETS.",
        ],
        "inputs": {
            "hotel_id": "bound by the platform from the session hotel",
            "start": "MM/DD/YYYY inclusive start, bound as %(start)s and read with to_date(start, 'MM/DD/YYYY')",
            "end": "MM/DD/YYYY inclusive end, bound as %(end)s and read with to_date(end, 'MM/DD/YYYY')",
            "outlet": "chosen by row: outlet_name, or ALL OUTLETS",
        },
        "output_grain": "outlet",
        "outputs": _KEYS + [name for name, _expr in columns] + _FLAGS,
        "default_output": default,
        "limitations": _LIMITS + ["Default reading: " + default + ". The other readings are alternatives a manager definition may choose."] + list(limitations),
        "verification": "Executed for the validation hotel; rows checked against independent reads of finance_posting, fnb_check and fnb_check_line.",
        "keyphrases": list(keyphrases),
        "aliases": list(keyphrases),
        "concepts": [{"name": name, "formula": formula, "tables": tables} for name, formula, tables in concepts],
        "solution": {"sql_template": _period_sql(columns), "parameters": ["hotel_id", "start", "end"], "method": SOURCE},
        "validation": {"expected_value": ""},
    }


_TOTAL = "SUM(x.net + x.tax)"


def _per(expr, den, scale="1.0"):
    return "ROUND(" + scale + " * " + expr + " / NULLIF(SUM(x." + den + "), 0), 2)"


RECIPES = [
    _recipe(
        "fnb_daily_outlet_revenue", "F&B outlet revenue by period",
        "F&B outlet revenue for a business date or range, per outlet and for all outlets, as posted to Finance: net sales after "
        "discounts, tax, total, with gross and discounts from the checks.",
        ["f&b revenue", "fnb revenue", "outlet revenue", "restaurant revenue", "restaurant sales", "bar sales", "food and beverage sales",
         "how much did the restaurant make"],
        "net_revenue",
        [("net_revenue", "SUM(finance_posting.amount) of F&B charge and adjustment postings", ["finance_posting"]),
         ("tax_total", "SUM(finance_posting.amount) of F&B tax postings", ["finance_posting"]),
         ("gross_revenue", "SUM(quantity * unit_price) of live lines on closed checks", ["fnb_check", "fnb_check_line"])],
        [
            ("net_revenue", _sum("net")),
            ("tax_total", _sum("tax")),
            ("total_incl_tax", _TOTAL),
            ("gross_revenue", _sum("gross")),
            ("discount_total", _sum("discount")),
            ("closed_checks", _sum("closed_checks")),
            ("revenue_at_quoted_prices", _sum("quoted")),
            ("tax_pricing_in_effect", "(SELECT string_agg(DISTINCT hotel_setting_at(%(hotel_id)s, 'finance.tax_pricing', g.d::date) #>> '{}', ', ') "
                                      "FROM generate_series(p.period_start, p.period_end, interval '1 day') g(d))"),
        ],
        ["Revenue by transaction code or Finance department is Finance's report (daily close); this recipe splits by outlet.",
         "revenue_at_quoted_prices reads each business date by the tax pricing in effect then: total_incl_tax under tax-inclusive "
         "pricing, net_revenue under tax-exclusive pricing. tax_pricing_in_effect lists the setting over the period (two values when "
         "it changed inside it)."],
    ),
    _recipe(
        "fnb_daily_covers", "F&B covers by period",
        "F&B covers (guests served) for a business date or range, per outlet and for all outlets, with closed checks and covers per check.",
        ["covers", "f&b covers", "restaurant covers", "guests served", "how many covers", "how many diners"],
        "covers",
        [("covers", "SUM(fnb_check.covers) over closed checks", ["fnb_check"])],
        [
            ("covers", _sum("covers")),
            ("closed_checks", _sum("closed_checks")),
            ("covers_per_check", _per("SUM(x.covers)", "closed_checks")),
        ],
        ["Covers are the number entered on the check by staff; open and voided checks are not counted."],
    ),
    _recipe(
        "fnb_daily_average_check", "F&B average check by period",
        "F&B average check for a business date or range, per outlet and for all outlets: posted net revenue per cover and per check.",
        ["average check", "average spend per cover", "average cover spend", "spend per head", "average bill", "check average"],
        "avg_check_per_cover",
        [("avg_check_per_cover", "posted F&B net revenue / covers on closed checks", ["finance_posting", "fnb_check"]),
         ("avg_check_per_check", "posted F&B net revenue / closed checks", ["finance_posting", "fnb_check"])],
        [
            ("avg_check_per_cover", _per("SUM(x.net)", "covers")),
            ("avg_check_per_check", _per("SUM(x.net)", "closed_checks")),
            ("avg_total_per_cover_incl_tax", _per(_TOTAL, "covers")),
            ("net_revenue", _sum("net")),
            ("covers", _sum("covers")),
            ("closed_checks", _sum("closed_checks")),
        ],
        ["Averages are NULL when there are no closed checks or covers in the row; they are only meaningful when posting_gap is 0."],
    ),
    _recipe(
        "fnb_daily_voids_discounts", "F&B voids and discounts by period",
        "F&B voids and discounts for a business date or range, per outlet and for all outlets: voided lines and checks with their value, "
        "discount total, discounted lines, and discount as a share of gross.",
        ["voids", "voided items", "void report", "discounts", "discount report", "comps", "how much did we discount"],
        "discount_total",
        [("discount_total", "SUM(fnb_check.discount_total) over closed checks", ["fnb_check"]),
         ("voided_line_value", "SUM(quantity * unit_price) of voided fnb_check_line rows", ["fnb_check_line"])],
        [
            ("discount_total", _sum("discount")),
            ("discount_pct_of_gross", _per("SUM(x.discount)", "gross", "100.0")),
            ("discounted_lines", _sum("discounted_lines")),
            ("voided_lines", _sum("voided_lines")),
            ("voided_line_value", _sum("voided_line_value")),
            ("void_checks", _sum("void_checks")),
            ("void_check_value", _sum("void_check_value")),
            ("gross_revenue", _sum("gross")),
        ],
        ["voided_lines counts lines voided on any check; void_check_value is the gross of the live lines on checks voided as a whole. "
         "Discounts count closed checks only. Reasons are on the lines (discount_reason, void_reason), not in this summary."],
    ),
    _recipe(
        "fnb_daily_settlement_mix", "F&B room charge vs direct payment by period",
        "F&B settlement mix for a business date or range, per outlet and for all outlets: posted totals charged to guest folios versus "
        "settled at the outlet, their shares, and the check counts of each.",
        ["room charge vs direct", "charged to room", "room charges from restaurant", "outlet payments", "paid at outlet",
         "settlement mix", "f&b charged to rooms"],
        "room_charged_share_pct",
        [("room_charged_share_pct", "100 * F&B postings (charge + tax) on guest folios / all F&B postings (charge + tax)", ["finance_posting"])],
        [
            ("room_charged_share_pct", _per("SUM(x.room_total)", "net + x.tax", "100.0")),
            ("room_charged_total", _sum("room_total")),
            ("room_charged_checks", _sum("room_checks")),
            ("direct_paid_total", _sum("direct_total")),
            ("direct_paid_checks", _sum("direct_checks")),
            ("total_incl_tax", _TOTAL),
        ],
        ["Totals include tax. Room-charged checks post to the guest folio (folio_type guest); direct checks post to the outlet folio "
         "(folio_type outlet) with their payments. Payment methods are Finance's daily close report."],
    ),
]