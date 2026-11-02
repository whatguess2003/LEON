"""Area API routers. Each module exposes `router` (fastapi.APIRouter) and `ACCESS` [("METHOD /path", module, level)], registered with
booking_system.access at import by main.py."""

AREAS = ("rooms", "bookings", "frontdesk", "operations")
