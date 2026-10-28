from zoneinfo import ZoneInfo


def zone(tz: str) -> ZoneInfo:
    """Resolve an IANA name, falling back to UTC if it's unknown or the tz
    database is missing (Windows ships none unless ``tzdata`` is installed)."""
    try:
        return ZoneInfo(tz)
    except Exception:
        return ZoneInfo("UTC")
