import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
# booking_system.access reads JWT_SECRET at import; the pure tests never sign anything with it.
os.environ.setdefault("JWT_SECRET", "unit-test-secret-only")
