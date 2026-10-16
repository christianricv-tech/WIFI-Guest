"""
app/limiter.py — shared slowapi Limiter instance.

Kept in its own module so both the FastAPI app factory (app/main.py) and the
RADIUS router (app/routers/radius.py) can import it without a circular import.

Requirement 11.6: enforce rate limiting on the RADIUS validation endpoint.
"""

from slowapi import Limiter
from slowapi.util import get_remote_address

# Key by client IP address (request.client.host), forwarded-proxy aware via
# get_remote_address. The per-route limit string is applied with the
# @limiter.limit(...) decorator on the endpoint.
limiter = Limiter(key_func=get_remote_address)
