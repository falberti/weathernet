import logging
import time

import requests
from django.conf import settings

logger = logging.getLogger(__name__)

NOMINATIM_SEARCH_URL = "https://nominatim.openstreetmap.org/search"
# Nominatim's usage policy caps this at 1 request/second
# (https://operations.osmfoundation.org/policies/nominatim/) -- a
# little headroom over that. Enforced here, not just documented: a
# message flood (accidental or a deliberate DoS attempt against the
# bot -- Telegram itself imposes no limit on how fast someone can send
# messages to it) must never translate into hammering Nominatim fast
# enough to get this server's IP blocked, which would break geocoding
# for every legitimate user, not just the one flooding.
_MIN_REQUEST_INTERVAL_SECONDS = 1.1
_last_request_at = 0.0


class GeocodingError(Exception):
    """Raised on a network/service failure -- distinct from "no match
    found" (which isn't an error, just returns None), so callers can
    tell a user "try again in a moment" instead of "no such place".
    """


def _throttle() -> None:
    global _last_request_at
    elapsed = time.monotonic() - _last_request_at
    if elapsed < _MIN_REQUEST_INTERVAL_SECONDS:
        time.sleep(_MIN_REQUEST_INTERVAL_SECONDS - elapsed)
    _last_request_at = time.monotonic()


def geocode_place(query: str) -> dict | None:
    """Resolves a free-text place name via Nominatim, OpenStreetMap's
    open geocoder -- no API key required.

    Returns {"display_name", "latitude", "longitude"} for the top
    match, or None if nothing matched. Doesn't need to be precise --
    matching against a probe within a several-km radius is the whole
    point, not pinpointing an address.
    """
    _throttle()
    try:
        response = requests.get(
            NOMINATIM_SEARCH_URL,
            params={"q": query, "format": "json", "limit": 1},
            headers={"User-Agent": settings.NOMINATIM_USER_AGENT},
            timeout=10,
        )
        response.raise_for_status()
    except requests.RequestException as exc:
        logger.warning("Nominatim geocoding request failed for %r: %s", query, exc)
        raise GeocodingError(str(exc)) from exc

    results = response.json()
    if not results:
        return None

    top = results[0]
    return {
        "display_name": top["display_name"],
        "latitude": float(top["lat"]),
        "longitude": float(top["lon"]),
    }
