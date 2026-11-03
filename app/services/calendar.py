"""Calendar use cases on top of CalDAV. iCloud is the source of truth: nothing is
cached in the database; calendar metadata is cached in memory for a few minutes.

All event calendars of the account are MARTY's to manage (the user started from
scratch and manages them through the bot). Task lists (VTODO) are ignored.
"""

import asyncio
import re
import time
import unicodedata
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import date, datetime, timedelta
from datetime import time as dtime
from typing import Protocol
from uuid import uuid4
from zoneinfo import ZoneInfo

from app.integrations.apple_calendar import (
    CalendarError,
    EventSpec,
    Occurrence,
    RemoteCalendar,
    RemoteObject,
    ical,
)

COLORS = {
    "rojo": "#FF3B30FF",
    "naranja": "#FF9500FF",
    "amarillo": "#FFCC00FF",
    "verde": "#34C759FF",
    "turquesa": "#30B0C7FF",
    "azul": "#007AFFFF",
    "morado": "#AF52DEFF",
    "rosa": "#FF2D55FF",
    "marrón": "#A2845EFF",
    "gris": "#8E8E93FF",
}
CONFLICT_WINDOW = timedelta(days=28)


class CalendarNotFoundError(CalendarError):
    pass


class EventNotFoundError(CalendarError):
    pass


class CalendarBackend(Protocol):
    async def calendars(self) -> list[RemoteCalendar]: ...
    async def create_calendar(self, name: str, color: str) -> str: ...
    async def update_calendar(
        self, url: str, *, name: str | None = None, color: str | None = None
    ) -> None: ...
    async def delete_calendar(self, url: str) -> None: ...
    async def events_between(
        self, calendar_url: str, start: datetime, end: datetime
    ) -> list[RemoteObject]: ...
    async def get_object(self, url: str) -> RemoteObject | None: ...
    async def count(self, calendar_url: str) -> int: ...
    async def create_object(self, calendar_url: str, uid: str, ics: str) -> RemoteObject: ...
    async def update_object(self, current: RemoteObject, ics: str) -> RemoteObject: ...
    async def delete_object(self, current: RemoteObject) -> None: ...


@dataclass(frozen=True)
class FoundEvent:
    calendar: RemoteCalendar
    remote: RemoteObject
    spec: EventSpec

    @property
    def event_id(self) -> str:
        return event_id_of(self.remote.url)

    @property
    def uid(self) -> str:
        return ical.uid_of(self.remote.ics)


@dataclass(frozen=True)
class Slot:
    start: datetime
    end: datetime


def normalize(name: str) -> str:
    decomposed = unicodedata.normalize("NFKD", name.casefold().strip())
    return "".join(c for c in decomposed if not unicodedata.combining(c))


def color_name(hex_color: str | None) -> str | None:
    if not hex_color:
        return None
    wanted = hex_color.upper()[:7]
    return next((name for name, value in COLORS.items() if value[:7] == wanted), hex_color)


_EVENT_ID = re.compile(r"^([^/]+)/([^/]+\.ics)$")


def _segment(calendar_url: str) -> str:
    return calendar_url.rstrip("/").rsplit("/", 1)[-1]


def event_id_of(object_url: str) -> str:
    """ "<calendar segment>/<file>.ics": short, stable, and resolvable only inside
    one of the user's calendars."""
    calendar_part, _, file = object_url.rpartition("/")
    return f"{_segment(calendar_part)}/{file}"


def day_of(value: datetime | date) -> date:
    return value.date() if isinstance(value, datetime) else value


def _overlaps(a_start: datetime, a_end: datetime, b_start: datetime, b_end: datetime) -> bool:
    return a_start < b_end and b_start < a_end


class CalendarService:
    def __init__(
        self,
        backend: CalendarBackend,
        tz: ZoneInfo,
        *,
        cache_seconds: float = 300.0,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self._backend = backend
        self.tz = tz
        self._cache_seconds = cache_seconds
        self._monotonic = monotonic
        self._cached: list[RemoteCalendar] | None = None
        self._cached_at = 0.0

    # --- Calendars -------------------------------------------------------------------

    async def calendars(self, *, refresh: bool = False) -> list[RemoteCalendar]:
        """Event calendars, cached briefly (they change rarely)."""
        stale = self._monotonic() - self._cached_at > self._cache_seconds
        if self._cached is None or refresh or stale:
            remote = await self._backend.calendars()
            self._cached = sorted(
                (c for c in remote if "VEVENT" in c.components or not c.components),
                key=lambda c: normalize(c.name),
            )
            self._cached_at = self._monotonic()
        return self._cached

    async def find_calendar(self, name: str) -> RemoteCalendar:
        calendars = await self.calendars()
        wanted = normalize(name)
        for calendar in calendars:
            if normalize(calendar.name) == wanted:
                return calendar
        names = ", ".join(c.name for c in calendars) or "none"
        raise CalendarNotFoundError(f"no calendar named {name!r}; existing calendars: {names}")

    async def create_calendar(self, name: str, color: str) -> RemoteCalendar:
        if any(normalize(c.name) == normalize(name) for c in await self.calendars(refresh=True)):
            raise CalendarError(f"a calendar named {name!r} already exists")
        await self._backend.create_calendar(name, COLORS[color])
        return await self.find_calendar_fresh(name)

    async def find_calendar_fresh(self, name: str) -> RemoteCalendar:
        await self.calendars(refresh=True)
        return await self.find_calendar(name)

    async def update_calendar(
        self, name: str, *, new_name: str | None = None, color: str | None = None
    ) -> RemoteCalendar:
        calendar = await self.find_calendar(name)
        if new_name is not None and normalize(new_name) != normalize(calendar.name):
            if any(normalize(c.name) == normalize(new_name) for c in await self.calendars()):
                raise CalendarError(f"a calendar named {new_name!r} already exists")
        await self._backend.update_calendar(
            calendar.url, name=new_name, color=COLORS[color] if color else None
        )
        return await self.find_calendar_fresh(new_name or calendar.name)

    async def count_events(self, name: str) -> int:
        return await self._backend.count((await self.find_calendar(name)).url)

    async def delete_calendar(self, name: str) -> RemoteCalendar:
        calendar = await self.find_calendar(name)
        await self._backend.delete_calendar(calendar.url)
        await self.calendars(refresh=True)
        return calendar

    # --- Reading events ----------------------------------------------------------------

    async def events(
        self, start: datetime, end: datetime, calendar: str | None = None
    ) -> list[Occurrence]:
        """Occurrences overlapping [start, end) across all calendars (or one)."""
        targets = [await self.find_calendar(calendar)] if calendar else await self.calendars()
        batches = await asyncio.gather(
            *(self._backend.events_between(c.url, start, end) for c in targets)
        )
        occurrences: list[Occurrence] = []
        for target, objects in zip(targets, batches, strict=True):
            occurrences.extend(
                ical.expand(
                    [o.ics for o in objects],
                    start,
                    end,
                    self.tz,
                    target.name,
                    event_ids=[event_id_of(o.url) for o in objects],
                )
            )
        return sorted(occurrences, key=lambda o: ical.sort_key(o.start, self.tz))

    async def find_event(self, event_id: str) -> FoundEvent:
        match = _EVENT_ID.match(event_id.strip())
        if match is None:
            raise EventNotFoundError(f"invalid event_id {event_id!r}; take it from list_events")
        segment, file = match.groups()
        calendars = await self.calendars()
        calendar = next((c for c in calendars if _segment(c.url) == segment), None)
        remote = (
            await self._backend.get_object(calendar.url.rstrip("/") + "/" + file)
            if calendar is not None
            else None
        )
        if calendar is None or remote is None:
            raise EventNotFoundError(f"no event with id {event_id!r}; it may have been deleted")
        return FoundEvent(calendar, remote, ical.describe(remote.ics))

    async def free_slots(
        self,
        first_day: date,
        last_day: date,
        *,
        min_minutes: int,
        day_start: dtime,
        day_end: dtime,
        now: datetime,
    ) -> dict[date, list[Slot]]:
        """Free time per day between `day_start` and `day_end`. Timed, busy events
        block time; all-day events do not."""
        window_start = datetime.combine(first_day, day_start, self.tz)
        window_end = datetime.combine(last_day, day_end, self.tz)
        busy = [
            (o.start, o.end)
            for o in await self.events(window_start, window_end)
            if o.busy and not o.all_day
        ]
        result: dict[date, list[Slot]] = {}
        day = first_day
        while day <= last_day:
            cursor = max(datetime.combine(day, day_start, self.tz), now.astimezone(self.tz))
            limit = datetime.combine(day, day_end, self.tz)
            slots = []
            for b_start, b_end in sorted(busy):
                if b_end <= cursor or b_start >= limit:
                    continue
                if b_start > cursor and b_start - cursor >= timedelta(minutes=min_minutes):
                    slots.append(Slot(cursor, b_start))
                cursor = max(cursor, b_end)
            if limit - cursor >= timedelta(minutes=min_minutes):
                slots.append(Slot(cursor, limit))
            result[day] = slots
            day += timedelta(days=1)
        return result

    async def conflicts(
        self, spec: EventSpec, *, ignore_uid: str | None = None
    ) -> list[Occurrence]:
        """Busy timed events overlapping the new or changed event (for recurring
        events, over its next four weeks)."""
        if not isinstance(spec.start, datetime):
            return []
        spec = ical.first_matching_start(spec)
        horizon = spec.start + (CONFLICT_WINDOW if spec.weekly_days else timedelta(0))
        mine = ical.expand(
            [ical.new_event("probe@marty", spec, spec.start)],
            spec.start,
            horizon + (spec.end - spec.start),
            self.tz,
        )
        others = [
            o
            for o in await self.events(spec.start, horizon + (spec.end - spec.start))
            if o.busy and not o.all_day and o.uid != ignore_uid
        ]
        clashes: list[Occurrence] = []
        for other in others:
            overlapping = any(_overlaps(m.start, m.end, other.start, other.end) for m in mine)  # type: ignore[arg-type]
            if overlapping and all(c.uid != other.uid for c in clashes):
                clashes.append(other)
        return clashes[:5]

    # --- Writing events ------------------------------------------------------------------

    async def create_event(self, calendar: str, spec: EventSpec, now: datetime) -> FoundEvent:
        target = await self.find_calendar(calendar)
        uid = f"{uuid4()}@marty"
        ics = ical.new_event(uid, spec, now)
        remote = await self._backend.create_object(target.url, uid, ics)
        return FoundEvent(target, remote, ical.describe(ics))

    async def update_event(self, found: FoundEvent, spec: EventSpec, now: datetime) -> FoundEvent:
        ics = ical.update_event(found.remote.ics, spec, now)
        remote = await self._backend.update_object(found.remote, ics)
        return replace(found, remote=remote, spec=ical.describe(ics))

    async def skip_occurrence(self, found: FoundEvent, day: date, now: datetime) -> FoundEvent:
        day_start = datetime.combine(day, dtime.min, self.tz)
        day_end = day_start + timedelta(days=1)
        occurrences = [
            o
            for o in ical.expand([found.remote.ics], day_start, day_end, self.tz)
            if day_of(o.start) == day
        ]
        if not occurrences:
            raise EventNotFoundError(f"the event does not happen on {day.isoformat()}")
        ics = ical.add_exception(found.remote.ics, occurrences[0].start, now)
        remote = await self._backend.update_object(found.remote, ics)
        return replace(found, remote=remote)

    async def delete_event(self, found: FoundEvent) -> None:
        await self._backend.delete_object(found.remote)
