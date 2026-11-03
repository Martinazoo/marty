from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

import pytest

from app.integrations.apple_calendar import CalendarConflictError, CalendarError, EventSpec
from app.services.calendar import (
    CalendarNotFoundError,
    CalendarService,
    EventNotFoundError,
    color_name,
    normalize,
)
from tests.calendar_fakes import FakeBackend

TZ = ZoneInfo("Europe/Madrid")
MONDAY = date(2026, 11, 9)
NOW = datetime(2026, 11, 3, 10, 0, tzinfo=UTC)


def at(day: date, hour: int, minute: int = 0) -> datetime:
    return datetime.combine(day, time(hour, minute), TZ)


class Ticker:
    def __init__(self) -> None:
        self.value = 0.0

    def __call__(self) -> float:
        return self.value


@pytest.fixture
def backend() -> FakeBackend:
    fake = FakeBackend()
    fake.add_calendar("Feina", "#FF9500FF")
    fake.add_calendar("Basket", "#FF3B30FF")
    fake.add_calendar("Tareas", components=("VTODO",))
    return fake


@pytest.fixture
def service(backend: FakeBackend) -> CalendarService:
    return CalendarService(backend, TZ)


async def test_calendars_skip_task_lists_and_are_cached(backend):
    ticker = Ticker()
    service = CalendarService(backend, TZ, cache_seconds=300, monotonic=ticker)

    assert [c.name for c in await service.calendars()] == ["Basket", "Feina"]
    await service.find_calendar("feina")
    ticker.value = 299
    await service.calendars()
    assert backend.calls["calendars"] == 1
    ticker.value = 301
    await service.calendars()
    assert backend.calls["calendars"] == 2


async def test_calendar_names_match_without_case_or_accents(service):
    await service.create_calendar("Desplaçament", "gris")

    assert (await service.find_calendar("desplacament")).name == "Desplaçament"
    assert normalize("  Estudiar C++ ") == "estudiar c++"
    with pytest.raises(CalendarError, match="already exists"):
        await service.create_calendar("DESPLAÇAMENT", "azul")
    with pytest.raises(CalendarNotFoundError, match="Basket, Desplaçament, Feina"):
        await service.find_calendar("Gimnàs")


async def test_update_and_delete_calendar(service, backend):
    updated = await service.update_calendar("basket", new_name="Bàsquet", color="verde")

    assert (updated.name, color_name(updated.color)) == ("Bàsquet", "verde")
    with pytest.raises(CalendarError, match="already exists"):
        await service.update_calendar("Bàsquet", new_name="feina")
    await service.delete_calendar("Bàsquet")
    assert [c.name for c in await service.calendars()] == ["Feina"]


async def test_events_from_all_calendars_carry_ids_that_find_them(service):
    work = EventSpec("Feina", at(MONDAY, 9), at(MONDAY, 17), weekly_days=(0, 1, 2, 3, 4))
    game = EventSpec("Partit", at(MONDAY, 20), at(MONDAY, 22))
    created = await service.create_event("feina", work, NOW)
    await service.create_event("Basket", game, NOW)

    events = await service.events(at(MONDAY, 0), at(MONDAY + timedelta(days=1), 0))

    assert [(o.title, o.calendar) for o in events] == [("Feina", "Feina"), ("Partit", "Basket")]
    assert events[0].event_id == created.event_id == f"feina/{created.uid.split('@')[0]}.ics"
    found = await service.find_event(events[0].event_id)
    assert (found.calendar.name, found.spec) == ("Feina", work)
    only_basket = await service.events(at(MONDAY, 0), at(MONDAY, 23), "basket")
    assert [o.title for o in only_basket] == ["Partit"]


@pytest.mark.parametrize(
    "event_id",
    [
        "",
        "abc.ics",
        "../../etc/passwd",
        "feina/../basket/x.ics",
        "https://evil.example/feina/x.ics",
        "feina/x.txt",
        "gimnas/x.ics",
        "feina/missing.ics",
    ],
)
async def test_invalid_or_unknown_event_ids_are_rejected(service, backend, event_id):
    with pytest.raises(EventNotFoundError):
        await service.find_event(event_id)
    assert all("evil" not in url for url in backend.objects)


async def test_free_slots_ignore_all_day_events_and_the_past(service):
    await service.create_event("Feina", EventSpec("Feina", at(MONDAY, 9), at(MONDAY, 17)), NOW)
    await service.create_event("Basket", EventSpec("Partit", at(MONDAY, 19), at(MONDAY, 21)), NOW)
    await service.create_event("Feina", EventSpec("Festa", MONDAY, MONDAY + timedelta(days=1)), NOW)

    slots = await service.free_slots(
        MONDAY,
        MONDAY + timedelta(days=1),
        min_minutes=60,
        day_start=time(8),
        day_end=time(22),
        now=at(MONDAY, 8, 30),
    )

    as_text = {d: [f"{s.start:%H:%M}-{s.end:%H:%M}" for s in v] for d, v in slots.items()}
    assert as_text == {
        MONDAY: ["17:00-19:00", "21:00-22:00"],  # 08:30-09:00 is shorter than an hour
        MONDAY + timedelta(days=1): ["08:00-22:00"],
    }


async def test_conflicts_cover_four_weeks_of_a_recurring_event(service):
    tuesday_in_three_weeks = MONDAY + timedelta(days=22)
    clash = await service.create_event(
        "Basket",
        EventSpec("Partit", at(tuesday_in_three_weeks, 18), at(tuesday_in_three_weeks, 20)),
        NOW,
    )
    await service.create_event(
        "Basket", EventSpec("Lliure", MONDAY, MONDAY + timedelta(days=1)), NOW
    )
    training = EventSpec("CrossFit", at(MONDAY, 19), at(MONDAY, 20), weekly_days=(1, 3))

    (found,) = await service.conflicts(training)

    assert found.event_id == clash.event_id
    assert await service.conflicts(training, ignore_uid=clash.uid) == []
    assert await service.conflicts(EventSpec("Dia", MONDAY, MONDAY + timedelta(days=1))) == []


async def test_skip_update_and_delete_an_event(service, backend):
    work = EventSpec("Feina", at(MONDAY, 9), at(MONDAY, 17), weekly_days=(0, 1, 2, 3, 4))
    created = await service.create_event("Feina", work, NOW)
    week = (at(MONDAY, 0), at(MONDAY + timedelta(days=7), 0))

    await service.skip_occurrence(created, MONDAY + timedelta(days=2), NOW)
    with pytest.raises(EventNotFoundError, match="does not happen"):
        await service.skip_occurrence(
            await service.find_event(created.event_id), MONDAY + timedelta(days=5), NOW
        )
    assert [o.start.weekday() for o in await service.events(*week)] == [0, 1, 3, 4]

    stale = created
    fresh = await service.find_event(created.event_id)
    with pytest.raises(CalendarConflictError):
        await service.update_event(stale, work, NOW)
    shorter = EventSpec("Feina", at(MONDAY, 8), at(MONDAY, 15), "Oficina", work.weekly_days)
    updated = await service.update_event(fresh, shorter, NOW)
    assert updated.spec == shorter
    assert {o.start.hour for o in await service.events(*week)} == {8}

    await service.delete_event(updated)
    assert backend.objects == {}
    with pytest.raises(EventNotFoundError):
        await service.find_event(created.event_id)
