from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from app.integrations.apple_calendar import EventSpec, ICalError, ical

TZ = ZoneInfo("Europe/Madrid")
NOW = datetime(2026, 11, 3, 10, 0, tzinfo=UTC)
MONDAY = date(2026, 11, 9)


def at(day: date, hour: int, minute: int = 0) -> datetime:
    return datetime(day.year, day.month, day.day, hour, minute, tzinfo=TZ)


def week_of(day: date) -> tuple[datetime, datetime]:
    return at(day, 0), at(day + timedelta(days=7), 0)


WORK = EventSpec(
    "Feina",
    at(MONDAY, 9),
    at(MONDAY, 17),
    location="Oficina",
    weekly_days=(0, 1, 2, 3, 4),
    repeat_until=MONDAY + timedelta(days=11),
)


def test_single_event_round_trips():
    spec = EventSpec("Dentista", at(MONDAY, 18), at(MONDAY, 18, 45), location="Clínica")
    ics = ical.new_event("abc@marty", spec, NOW)

    assert ical.uid_of(ics) == "abc@marty"
    assert not ical.is_recurring(ics)
    assert ical.describe(ics) == spec
    assert "BEGIN:VTIMEZONE" in ics


def test_weekly_event_expands_with_until_inclusive_and_in_local_time():
    ics = ical.new_event("work@marty", WORK, NOW)
    assert "UNTIL=20261120T225959Z" in ics  # 23:59:59 local, in UTC

    first = ical.expand([ics], *week_of(MONDAY), TZ, "Feina", event_ids=["feina/work.ics"])
    assert [o.start.weekday() for o in first] == [0, 1, 2, 3, 4]
    assert {(o.start.hour, o.end.hour) for o in first} == {(9, 17)}
    assert all(o.recurring and o.busy and o.calendar == "Feina" for o in first)
    assert {o.event_id for o in first} == {"feina/work.ics"}

    second = ical.expand([ics], *week_of(MONDAY + timedelta(days=7)), TZ)
    assert [o.start.day for o in second] == [16, 17, 18, 19, 20]  # Friday 20 is the last day
    assert ical.expand([ics], *week_of(MONDAY + timedelta(days=14)), TZ) == []
    assert ical.describe(ics) == WORK


def test_recurring_start_moves_to_the_first_selected_weekday():
    sunday = MONDAY - timedelta(days=1)
    spec = EventSpec("Basket", at(sunday, 19), at(sunday, 21), weekly_days=(1, 3))

    moved = ical.first_matching_start(spec)

    assert moved.start == at(MONDAY + timedelta(days=1), 19)
    assert moved.end - moved.start == timedelta(hours=2)
    assert ical.describe(ical.new_event("b@marty", spec, NOW)).start == moved.start


def test_skipping_an_occurrence_keeps_the_rest_and_survives_an_update():
    ics = ical.new_event("work@marty", WORK, NOW)
    wednesday = at(MONDAY + timedelta(days=2), 9)

    skipped = ical.add_exception(ics, wednesday, NOW)
    days = [o.start.weekday() for o in ical.expand([skipped], *week_of(MONDAY), TZ)]
    assert days == [0, 1, 3, 4]

    later = EventSpec("Feina", at(MONDAY, 9), at(MONDAY, 15), None, WORK.weekly_days)
    updated = ical.update_event(skipped, later, NOW)
    occurrences = ical.expand([updated], *week_of(MONDAY), TZ)
    assert [o.start.weekday() for o in occurrences] == [0, 1, 3, 4]
    assert {o.end.hour for o in occurrences} == {15}
    assert all(o.location is None for o in occurrences)
    assert "SEQUENCE:2" in updated


def test_skipping_requires_a_recurring_event():
    ics = ical.new_event("one@marty", EventSpec("Cena", at(MONDAY, 21), at(MONDAY, 23)), NOW)
    with pytest.raises(ICalError, match="does not repeat"):
        ical.add_exception(ics, at(MONDAY, 21), NOW)


def test_update_keeps_alarms_and_unknown_properties():
    ics = ical.new_event("one@marty", EventSpec("Cena", at(MONDAY, 21), at(MONDAY, 23)), NOW)
    ics = ics.replace(
        "END:VEVENT",
        "X-APPLE-TRAVEL-ADVISORY-BEHAVIOR:AUTOMATIC\r\nBEGIN:VALARM\r\nACTION:DISPLAY\r\n"
        "DESCRIPTION:Aviso\r\nTRIGGER:-PT15M\r\nEND:VALARM\r\nEND:VEVENT",
    )

    updated = ical.update_event(ics, EventSpec("Cena", at(MONDAY, 20), at(MONDAY, 22)), NOW)

    assert "BEGIN:VALARM" in updated
    assert "X-APPLE-TRAVEL-ADVISORY-BEHAVIOR" in updated
    assert ical.describe(updated).start == at(MONDAY, 20)


def test_all_day_events_use_exclusive_end_dates():
    spec = EventSpec("Vacaciones", MONDAY, MONDAY + timedelta(days=3))
    ics = ical.new_event("v@marty", spec, NOW)

    (occurrence,) = ical.expand([ics], *week_of(MONDAY), TZ)

    assert occurrence.all_day
    assert (occurrence.start, occurrence.end) == (MONDAY, MONDAY + timedelta(days=3))
    assert ical.describe(ics) == spec


@pytest.mark.parametrize(
    "spec",
    [
        EventSpec("Mal", at(MONDAY, 10), at(MONDAY, 9)),
        EventSpec("Mal", at(MONDAY, 10), MONDAY + timedelta(days=1)),
        EventSpec("Mal", at(MONDAY, 10), at(MONDAY, 11), None, (0,), MONDAY - timedelta(days=1)),
    ],
)
def test_invalid_specs_are_rejected(spec):
    with pytest.raises(ICalError):
        ical.new_event("x@marty", spec, NOW)


def test_cancelled_transparent_and_malformed_objects():
    base = EventSpec("Algo", at(MONDAY, 10), at(MONDAY, 11))
    cancelled = ical.new_event("c@marty", base, NOW).replace(
        "END:VEVENT", "STATUS:CANCELLED\r\nEND:VEVENT"
    )
    free = ical.new_event("f@marty", base, NOW).replace(
        "END:VEVENT", "TRANSP:TRANSPARENT\r\nEND:VEVENT"
    )

    (occurrence,) = ical.expand([cancelled, free, "not ical"], *week_of(MONDAY), TZ)

    assert occurrence.uid == "f@marty"
    assert not occurrence.busy
    with pytest.raises(ICalError):
        ical.describe("not ical")
