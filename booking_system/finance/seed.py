"""Standard PMS finance setup for any hotel, written once as explicit rows the manager edits: departments, transaction codes with
USALI-style export codes, and tax codes. Per default D12 the tax codes get no rules and the charge codes are non-taxable, so the
desk shows 'no tax configured' instead of computing 0%. Run: python -m booking_system.finance.seed <hotel_id>"""

import sys

from booking_system.finance import service

DEPARTMENTS = (("ROOMS", "operated"), ("FOOD_AND_BEVERAGE", "operated"), ("OTHER_OPERATED", "operated"),
               ("ADMIN_GENERAL", "undistributed"), ("SALES_MARKETING", "undistributed"))

# (code, type, department, payment_method, gl_export_code)
CODES = (
    ("ROOM", "charge", "ROOMS", None, "4010"),
    ("EXTRA_BED", "charge", "ROOMS", None, "4020"),
    ("LATE_CO", "charge", "ROOMS", None, "4020"),
    ("NO_SHOW_FEE", "charge", "ROOMS", None, "4030"),
    ("CANCEL_FEE", "charge", "ROOMS", None, "4030"),
    ("ROOM_ADJ", "adjustment", "ROOMS", None, "4090"),
    ("FNB_FOOD", "charge", "FOOD_AND_BEVERAGE", None, "4110"),
    ("FNB_BEVERAGE", "charge", "FOOD_AND_BEVERAGE", None, "4120"),
    ("FNB_ADJ", "adjustment", "FOOD_AND_BEVERAGE", None, "4190"),
    ("LAUNDRY", "charge", "OTHER_OPERATED", None, "4310"),
    ("MISC", "charge", "OTHER_OPERATED", None, "4900"),
    ("COMMISSION", "adjustment", "SALES_MARKETING", None, "6010"),
    ("TAX_SST", "tax", "ADMIN_GENERAL", None, "2310"),
    ("TAX_SERVICE_CHARGE", "tax", "ADMIN_GENERAL", None, "2320"),
    ("TAX_TOURISM", "tax", "ADMIN_GENERAL", None, "2330"),
    ("CASH", "payment", "ADMIN_GENERAL", "cash", "1010"),
    ("CARD", "payment", "ADMIN_GENERAL", "card", "1020"),
    ("BANK_TRANSFER", "payment", "ADMIN_GENERAL", "bank_transfer", "1030"),
    ("CHANNEL_COLLECT", "payment", "ADMIN_GENERAL", "channel_collect", "1210"),
    ("REFUND_CASH", "refund", "ADMIN_GENERAL", "cash", "1010"),
    ("REFUND_CARD", "refund", "ADMIN_GENERAL", "card", "1020"),
    ("CITY_LEDGER", "transfer", "ADMIN_GENERAL", None, "1200"),
)

# (tax code, basis, unit, posting code)
TAX_CODES = (
    ("SST", "percent", None, "TAX_SST"),
    ("SERVICE_CHARGE", "percent", None, "TAX_SERVICE_CHARGE"),
    ("TOURISM_TAX", "per_unit", "night", "TAX_TOURISM"),
)


def seed(hotel_id):
    """Idempotent: each row goes through the read-first save, so an existing row (and a manager's edits to it) is kept."""
    results = []
    existing = service.list_transaction_codes(hotel_id)
    if not existing["ok"]:
        return [existing]
    known = {row["transaction_code"] for row in existing["transaction_codes"]}
    for name, kind in DEPARTMENTS:
        results.append(service.save_department(hotel_id, name, kind, confirm_new=True))
    for code, kind, department, method, export in CODES:
        if code not in known:
            results.append(service.save_transaction_code(hotel_id, code, kind, department, False, payment_method=method,
                                                         gl_export_code=export, confirm_new=True))
    taxes = {row["tax_code"] for row in service.list_tax_codes(hotel_id)["tax_codes"]}
    for tax, basis, unit, posting in TAX_CODES:
        if tax not in taxes:
            results.append(service.save_tax_code(hotel_id, tax, basis, posting, unit=unit, confirm_new=True))
    return results


if __name__ == "__main__":
    from dotenv import load_dotenv
    load_dotenv()
    for row in seed(sys.argv[1]):
        print(row.get("result") or row.get("error"))
