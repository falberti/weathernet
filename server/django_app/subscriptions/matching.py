from probes.models import Probe

from .geo import haversine_km


def nearest_active_probe(latitude: float, longitude: float):
    """Returns (probe, distance_km) for the closest active probe with
    coordinates set, or None if there are no eligible probes at all.

    Returns the nearest probe regardless of distance -- callers
    compare distance_km against settings.SUBSCRIPTION_MAX_DISTANCE_KM
    themselves. O(n) over active probes with coordinates: fine at this
    project's scale (a handful of probes), no spatial index needed.
    """
    best = None
    probes = Probe.objects.filter(
        is_active=True, location_latitude__isnull=False, location_longitude__isnull=False
    )
    for probe in probes:
        distance = haversine_km(
            latitude, longitude, float(probe.location_latitude), float(probe.location_longitude)
        )
        if best is None or distance < best[1]:
            best = (probe, distance)
    return best
