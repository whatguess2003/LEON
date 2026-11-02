"""Synthetic F&B fixture through the service: one restaurant, a small menu on Finance's charge codes, and sample checks
(one charged to a room). Needs Finance's seed (FNB_FOOD, FNB_BEVERAGE, CARD) and an open business date; taxes follow those codes.
Run: python -m booking_system.fnb.seed "<hotel name>" <usr_ id> <in-house bkg_ id>"""

import sys

from booking_system.fnb import service
from booking_system.fnb.db import transaction
from booking_system.fnb.schema import ensure_schema

OUTLET = ("THE TERRACE", "restaurant")
PAYMENT_CODE = "CARD"
MENU = (
    ("NASI LEMAK", "FNB_FOOD", 18.00, "Coconut rice, sambal, anchovies, egg"),
    ("MEE GORENG", "FNB_FOOD", 16.00, "Fried yellow noodles"),
    ("CLUB SANDWICH", "FNB_FOOD", 24.00, "Chicken, egg, lettuce, fries"),
    ("TEH TARIK", "FNB_BEVERAGE", 6.00, "Pulled milk tea"),
    ("FRESH ORANGE JUICE", "FNB_BEVERAGE", 12.00, None),
    ("MINERAL WATER", "FNB_BEVERAGE", 4.00, None),
)


def _ok(result):
    if "ok" not in result:
        raise RuntimeError(str(result.get("error") or result.get("message")))
    return result["ok"]


def hotel_id(hotel_name):
    def run(cur):
        cur.execute("SELECT hotel_id FROM hotel WHERE hotel_name = %s", (hotel_name.strip().upper(),))
        row = cur.fetchone()
        return row["hotel_id"] if row else None
    return transaction(run)


def seed_setup(hotel):
    outlet = _ok(service.save_outlet(hotel, OUTLET[0], OUTLET[1], confirm_new=True))
    items = {name: _ok(service.save_menu_item(hotel, outlet["outlet_id"], name, price, code, description=description, confirm_new=True))
             for name, code, price, description in MENU}
    return {"outlet": outlet, "items": items}


def seed_checks(hotel, setup, user_id, booking_id):
    """Four checks on the open business date: card with a 10% discount, card with a voided line, a voided check, a room charge."""
    outlet_id = setup["outlet"]["outlet_id"]
    if _ok(service.read_checks(hotel, outlet_id)):
        return "checks already present"
    items = {name: item["item_id"] for name, item in setup["items"].items()}

    def check(covers, table, orders):
        opened = _ok(service.open_check(hotel, outlet_id, covers, table, user_id))
        lines = [_ok(service.add_line(hotel, opened["check_id"], items[name], qty, user_id)) for name, qty in orders]
        return opened["check_id"], lines

    first, _ = check(2, "T1", (("NASI LEMAK", 2), ("TEH TARIK", 2)))
    _ok(service.discount_check(hotel, first, 10, "regular guest"))
    total = _ok(service.read_check(hotel, first))["running"]["total"]
    _ok(service.settle_direct(hotel, first, [{"transaction_code": PAYMENT_CODE, "amount": total, "reference": "AUTH-0001"}], user_id))

    second, lines = check(3, "T2", (("CLUB SANDWICH", 1), ("MEE GORENG", 2), ("FRESH ORANGE JUICE", 3)))
    _ok(service.void_line(hotel, second, lines[2]["line_id"], "ordered by mistake", user_id))
    total = _ok(service.read_check(hotel, second))["running"]["total"]
    _ok(service.settle_direct(hotel, second, [{"transaction_code": PAYMENT_CODE, "amount": total, "reference": "AUTH-0002"}], user_id))

    third, _ = check(1, "T3", (("MINERAL WATER", 1),))
    _ok(service.void_check(hotel, third, "guest left", user_id))

    fourth, _ = check(2, "T4", (("CLUB SANDWICH", 2), ("FRESH ORANGE JUICE", 2)))
    return {"checks": [first, second, third, fourth], "room_charge": service.settle_room(hotel, fourth, booking_id, user_id)}


if __name__ == "__main__":
    from dotenv import load_dotenv

    load_dotenv()
    name, user, booking = sys.argv[1:4]
    print("schema", ensure_schema())
    hotel = hotel_id(name)
    if hotel is None:
        raise SystemExit("hotel " + name + " is not registered")
    setup = seed_setup(hotel)
    print("setup", setup["outlet"]["outlet_id"], len(setup["items"]), "items")
    print("checks", seed_checks(hotel, setup, user, booking))
