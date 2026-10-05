"""Estimates of the GPS UART load. Pure arithmetic, no hardware imports (also used by tools/replay.py)."""

# Approximate NMEA line sizes in bytes (incl. CRLF) used to estimate UART load.
_BASE_BYTES = 72 + 82 + 36            # RMC + GGA + ZDA, every fix
_GSX_GPS_BYTES = 60 + 3 * 70          # GSA + ~3 GSV lines, GPS
_GSX_BD_BYTES = 60 + 2 * 70           # GSA + ~2 GSV lines, BeiDou
_GSX_EVERY = 5                        # SET_NMEA_OUTPUT sends GSA/GSV every 5th fix


def _cycle_bytes(beidou):
    """(bytes in an ordinary fix, extra bytes added on the GSA/GSV fix)."""
    return _BASE_BYTES, _GSX_GPS_BYTES + (_GSX_BD_BYTES if beidou else 0)


def nmea_load(baud, fix_interval_ms, beidou):
    """Estimated UART load as fractions of capacity: (average, worst fix cycle). > 1.0 = overloaded."""
    base, extra = _cycle_bytes(beidou)
    capacity = baud / 10.0 * fix_interval_ms / 1000.0
    return (base + extra / _GSX_EVERY) / capacity, (base + extra) / capacity


def nmea_burst_ms(baud, beidou):
    """Line time in ms of the largest fix cycle: how late the first sentence of a cycle can arrive."""
    base, extra = _cycle_bytes(beidou)
    return int((base + extra) * 10000 / baud)
