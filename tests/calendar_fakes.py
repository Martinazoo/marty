"""In-memory stand-in for the CalDAV client, used by service, tool and command tests."""

from collections import Counter
from datetime import UTC, datetime
from itertools import count

from app.integrations.apple_calendar import (
    CalendarConflictError,
    RemoteCalendar,
    RemoteObject,
    ical,
)

HOME = "https://caldav.test/123/calendars/"


class FakeBackend:
    def __init__(self) -> None:
        self.calendar_list: list[RemoteCalendar] = []
        self.objects: dict[str, RemoteObject] = {}
        self.calls: Counter[str] = Counter()
        self._etags = count(1)

    def add_calendar(
        self, name: str, color: str = "#34C759FF", components: tuple[str, ...] = ("VEVENT",)
    ) -> str:
        url = f"{HOME}{name.lower().replace(' ', '-')}/"
        self.calendar_list.append(RemoteCalendar(url, name, color, components))
        return url

    def _etag(self) -> str:
        return f'"{next(self._etags)}"'

    def in_calendar(self, calendar_url: str) -> list[RemoteObject]:
        return [o for url, o in self.objects.items() if url.startswith(calendar_url)]

    async def calendars(self) -> list[RemoteCalendar]:
        self.calls["calendars"] += 1
        return list(self.calendar_list)

    async def create_calendar(self, name: str, color: str) -> str:
        return self.add_calendar(name, color)

    async def update_calendar(
        self, url: str, *, name: str | None = None, color: str | None = None
    ) -> None:
        self.calendar_list = [
            RemoteCalendar(c.url, name or c.name, color or c.color, c.components)
            if c.url == url
            else c
            for c in self.calendar_list
        ]

    async def delete_calendar(self, url: str) -> None:
        self.calendar_list = [c for c in self.calendar_list if c.url != url]
        for obj in self.in_calendar(url):
            del self.objects[obj.url]

    async def events_between(
        self, calendar_url: str, start: datetime, end: datetime
    ) -> list[RemoteObject]:
        self.calls["events_between"] += 1
        return [o for o in self.in_calendar(calendar_url) if ical.expand([o.ics], start, end, UTC)]

    async def get_object(self, url: str) -> RemoteObject | None:
        self.calls["get_object"] += 1
        return self.objects.get(url)

    async def count(self, calendar_url: str) -> int:
        return len(self.in_calendar(calendar_url))

    async def create_object(self, calendar_url: str, uid: str, ics: str) -> RemoteObject:
        obj = RemoteObject(f"{calendar_url}{uid.split('@')[0]}.ics", self._etag(), ics)
        self.objects[obj.url] = obj
        return obj

    async def update_object(self, current: RemoteObject, ics: str) -> RemoteObject:
        if self.objects[current.url].etag != current.etag:
            raise CalendarConflictError("changed")
        obj = RemoteObject(current.url, self._etag(), ics)
        self.objects[obj.url] = obj
        return obj

    async def delete_object(self, current: RemoteObject) -> None:
        if self.objects[current.url].etag != current.etag:
            raise CalendarConflictError("changed")
        del self.objects[current.url]
