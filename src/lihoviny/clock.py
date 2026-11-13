"""Čas zvenku: agenti a adaptéry se ptají hodin, nikdy přímo `datetime.now()` ani `time.sleep()`.

V testech `FakeClock` posouvá čas okamžitě, takže rozvrh, limity a pauzy jdou testovat bez čekání.
"""

import time
from datetime import UTC, datetime, timedelta
from typing import Protocol
from zoneinfo import ZoneInfo

# Rozvrh i web počítají v pražském čase; ukládá se UTC.
PRAGUE = ZoneInfo("Europe/Prague")


class Clock(Protocol):
    def now(self) -> datetime: ...

    def sleep(self, seconds: float) -> None: ...


class SystemClock:
    def now(self) -> datetime:
        return datetime.now(UTC)

    def sleep(self, seconds: float) -> None:
        time.sleep(seconds)


class FakeClock:
    def __init__(self, start: datetime):
        self._now = start
        self.sleeps: list[float] = []

    def now(self) -> datetime:
        return self._now

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.advance(seconds=seconds)

    def advance(self, **delta: float) -> None:
        self._now += timedelta(**delta)
