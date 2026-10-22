"""
Currency utilities (JWT-only source).

Rules:
- Currency must be provided via JWT claim 'hotel_currency' at signin.
- No fallback to env or defaults.
- Registry is in-memory per process. Orchestrator should register on each request.
"""

from typing import Optional, Dict

_HOTEL_CURRENCY_REGISTRY: Dict[str, str] = {}


def register_hotel_currency(hotel_name: str, currency_code: str) -> None:
    if not hotel_name or not currency_code:
        return
    _HOTEL_CURRENCY_REGISTRY[hotel_name.upper()] = currency_code.upper()


def get_hotel_currency_str(hotel_name: Optional[str]) -> Optional[str]:
    if not hotel_name:
        return None
    return _HOTEL_CURRENCY_REGISTRY.get(hotel_name.upper())


def get_currency_from_jwt(jwt_token: str) -> Optional[str]:
    if not jwt_token:
        return None
    try:
        import os
        import jwt as _jwt  # type: ignore
        secret = os.getenv("JWT_SECRET", "dev-secret")
        payload = _jwt.decode(jwt_token, secret, algorithms=["HS256"])  # type: ignore
        cur = payload.get("hotel_currency")
        hotel = payload.get("hotel_name")
        if hotel and cur:
            register_hotel_currency(hotel, cur)
        return cur
    except Exception:
        return None


def format_amount(amount: float, hotel_name: Optional[str]) -> str:
    code = get_hotel_currency_str(hotel_name)
    if code:
        return f"{code} {amount:,.2f}"
    return f"{amount:,.2f}"


