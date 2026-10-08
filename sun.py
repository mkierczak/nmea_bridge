"""Where the sun is: elevation above the horizon from a position and the UTC date and time (pure maths, no hardware
imports, good to a fraction of a degree: the standard low-precision formulae). Used for the automatic night mode."""
import math

DARK_BELOW_DEG = -3.0     # the display dims when the sun is this far below the horizon (a little after sunset)...
LIGHT_ABOVE_DEG = -1.0    # ... and goes back to day when it is above this (a little before sunrise): no flicker


def elevation_deg(lat_deg, lon_deg, utc_days, utc_ms):
    """Sun elevation in degrees. utc_days: days since 1970-01-01, utc_ms: milliseconds of the UTC day."""
    n = utc_days + utc_ms / 86400000.0 - 10957.5        # days since J2000.0 (2000-01-01 12:00 UTC)
    mean_lon = (280.460 + 0.9856474 * n) % 360.0
    anomaly = math.radians((357.528 + 0.9856003 * n) % 360.0)
    ecliptic = math.radians(mean_lon + 1.915 * math.sin(anomaly) + 0.020 * math.sin(2 * anomaly))
    tilt = math.radians(23.439 - 0.0000004 * n)
    declination = math.asin(math.sin(tilt) * math.sin(ecliptic))
    right_ascension = math.degrees(math.atan2(math.cos(tilt) * math.sin(ecliptic), math.cos(ecliptic)))
    sidereal = (280.46061837 + 360.98564736629 * n) % 360.0
    hour_angle = math.radians(sidereal + lon_deg - right_ascension)
    lat = math.radians(lat_deg)
    s = math.sin(lat) * math.sin(declination) + math.cos(lat) * math.cos(declination) * math.cos(hour_angle)
    return math.degrees(math.asin(max(-1.0, min(1.0, s))))


def is_dark(elevation, was_dark):
    """Dark or light, with hysteresis between DARK_BELOW_DEG and LIGHT_ABOVE_DEG so it does not flicker at dusk."""
    if was_dark:
        return elevation < LIGHT_ABOVE_DEG
    return elevation < DARK_BELOW_DEG
