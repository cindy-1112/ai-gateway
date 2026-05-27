from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo


BEIJING_TZ = ZoneInfo("Asia/Shanghai")


def beijing_now() -> datetime:
    return datetime.now(BEIJING_TZ).replace(tzinfo=None)


def beijing_today_start() -> datetime:
    return beijing_now().replace(hour=0, minute=0, second=0, microsecond=0)


def beijing_month_start() -> datetime:
    return beijing_now().replace(day=1, hour=0, minute=0, second=0, microsecond=0)


def iso_beijing(value: datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is not None:
        value = value.astimezone(BEIJING_TZ).replace(tzinfo=None)
    return value.isoformat() + "+08:00"


def parse_beijing_datetime(value: str, *, date_as_end: bool = False) -> datetime:
    raw = str(value).strip()
    if len(raw) == 10:
        parsed = datetime.fromisoformat(raw)
        if date_as_end:
            parsed = parsed + timedelta(days=1) - timedelta(seconds=1)
        return parsed

    parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    if parsed.tzinfo is not None:
        return parsed.astimezone(BEIJING_TZ).replace(tzinfo=None)
    return parsed
