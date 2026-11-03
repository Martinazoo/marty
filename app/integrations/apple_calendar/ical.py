"""Building, editing and expanding iCalendar (RFC 5545) objects.

One CalDAV object holds one event: its master VEVENT plus, for recurring events
edited on another device, override VEVENTs with a RECURRENCE-ID. Edits here
change the master and keep everything else (alarms, overrides, unknown
properties) as it was.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

import recurring_ical_events
from icalendar import Calendar, Event

WEEKDAY_CODES = ("MO", "TU", "WE", "TH", "FR", "SA", "SU")
PRODID = "-//MARTY//Personal assistant//ES"


class ICalError(ValueError):
    """The object cannot be parsed or the change makes no sense."""


@dataclass(frozen=True)
class EventSpec:
    """What MARTY wants an event to be. Timed events use aware datetimes; all-day
    events use dates with an exclusive end, as iCalendar does."""

    title: str
    start: datetime | date
    end: datetime | date
    location: str | None = None
    weekly_days: tuple[int, ...] = ()  # 0 = Monday; empty = no repetition
    repeat_until: date | None = None


@dataclass(frozen=True)
class Occurrence:
    uid: str
    title: str
    start: datetime | date
    end: datetime | date
    all_day: bool
    location: str | None
    recurring: bool
    busy: bool
    calendar: str = ""
    event_id: str = ""


def _is_all_day(value: datetime | date) -> bool:
    return not isinstance(value, datetime)


def _rrule(spec: EventSpec) -> dict | None:
    if not spec.weekly_days:
        return None
    rule: dict = {"freq": "weekly", "byday": [WEEKDAY_CODES[d] for d in sorted(spec.weekly_days)]}
    if spec.repeat_until is not None:
        if isinstance(spec.start, datetime):
            # With a TZID start, UNTIL must be UTC; the whole local day is included.
            last = datetime.combine(spec.repeat_until, time(23, 59, 59), spec.start.tzinfo)
            rule["until"] = last.astimezone(UTC)
        else:
            rule["until"] = spec.repeat_until
    return rule


def first_matching_start(spec: EventSpec) -> EventSpec:
    """A recurring event's DTSTART must be one of its occurrences: move it forward
    to the first selected weekday, keeping time of day and duration."""
    if not spec.weekly_days:
        return spec
    start_day = spec.start.date() if isinstance(spec.start, datetime) else spec.start
    shift = min((d - start_day.weekday()) % 7 for d in spec.weekly_days)
    if shift == 0:
        return spec
    delta = timedelta(days=shift)
    return EventSpec(
        spec.title,
        spec.start + delta,
        spec.end + delta,
        spec.location,
        spec.weekly_days,
        spec.repeat_until,
    )


def _validate(spec: EventSpec) -> None:
    if _is_all_day(spec.start) != _is_all_day(spec.end):
        raise ICalError("start and end must both be dates or both be date-times")
    if spec.end <= spec.start:  # type: ignore[operator]
        raise ICalError("the end must be after the start")
    first = spec.start.date() if isinstance(spec.start, datetime) else spec.start
    if spec.repeat_until is not None and spec.repeat_until < first:
        raise ICalError("repeat_until is before the first occurrence")


def new_event(uid: str, spec: EventSpec, now: datetime) -> str:
    spec = first_matching_start(spec)
    _validate(spec)
    event = Event.new(
        uid=uid,
        summary=spec.title,
        start=spec.start,
        end=spec.end,
        location=spec.location or None,
        stamp=now.astimezone(UTC),
        created=now.astimezone(UTC),
    )
    if (rule := _rrule(spec)) is not None:
        event.add("rrule", rule)
    calendar = Calendar.new(prodid=PRODID, subcomponents=[event])
    calendar.pop("UID", None)
    calendar.add_missing_timezones()
    return calendar.to_ical().decode()


def _parse(ics: str) -> tuple[Calendar, Event]:
    try:
        calendar = Calendar.from_ical(ics)
    except ValueError as exc:
        raise ICalError(f"invalid iCalendar data: {exc}") from exc
    masters = [e for e in calendar.walk("VEVENT") if "RECURRENCE-ID" not in e]
    if not masters:
        raise ICalError("no VEVENT without RECURRENCE-ID")
    return calendar, masters[0]


def describe(ics: str) -> EventSpec:
    """The master event as an `EventSpec` (repetition rules beyond weekly
    BYDAY are reported as no weekdays)."""
    _, master = _parse(ics)
    start, end = master.start, master.end
    weekly_days: tuple[int, ...] = ()
    until = None
    for rule in master.rrules:
        if rule.get("FREQ") == ["WEEKLY"]:
            weekly_days = tuple(
                WEEKDAY_CODES.index(code[-2:]) for code in rule.get("BYDAY", []) if code
            )
            if not weekly_days:
                weekly_days = (start.weekday(),)
            if rule.get("UNTIL"):
                raw = rule["UNTIL"][0]
                until = raw.astimezone(start.tzinfo).date() if isinstance(raw, datetime) else raw
    return EventSpec(
        title=str(master.get("SUMMARY", "")),
        start=start,
        end=end,
        location=str(master["LOCATION"]) if master.get("LOCATION") else None,
        weekly_days=weekly_days,
        repeat_until=until,
    )


def uid_of(ics: str) -> str:
    _, master = _parse(ics)
    return str(master.get("UID", ""))


def is_recurring(ics: str) -> bool:
    _, master = _parse(ics)
    return bool(master.get("RRULE") or master.get("RDATE"))


def update_event(ics: str, spec: EventSpec, now: datetime) -> str:
    """Replace title, times, location and weekly repetition of the master event."""
    spec = first_matching_start(spec)
    _validate(spec)
    calendar, master = _parse(ics)
    for key in ("SUMMARY", "DTSTART", "DTEND", "DURATION", "LOCATION", "RRULE"):
        master.pop(key, None)
    master.add("summary", spec.title)
    master.add("dtstart", spec.start)
    master.add("dtend", spec.end)
    if spec.location:
        master.add("location", spec.location)
    if (rule := _rrule(spec)) is not None:
        master.add("rrule", rule)
    _touch(master, now)
    calendar.add_missing_timezones()
    return calendar.to_ical().decode()


def add_exception(ics: str, occurrence_start: datetime | date, now: datetime) -> str:
    """Skip one occurrence of a recurring event (EXDATE)."""
    calendar, master = _parse(ics)
    if not (master.get("RRULE") or master.get("RDATE")):
        raise ICalError("the event does not repeat")
    master.add("exdate", occurrence_start)
    _touch(master, now)
    return calendar.to_ical().decode()


def _touch(master: Event, now: datetime) -> None:
    for key in ("DTSTAMP", "LAST-MODIFIED"):
        master.pop(key, None)
    master.add("dtstamp", now.astimezone(UTC))
    master.add("last-modified", now.astimezone(UTC))
    master["SEQUENCE"] = int(master.get("SEQUENCE", 0)) + 1


def expand(
    objects: Sequence[str],
    start: datetime,
    end: datetime,
    tz: ZoneInfo,
    calendar: str = "",
    event_ids: Sequence[str] = (),
) -> list[Occurrence]:
    """Occurrences overlapping [start, end), recurring events expanded, cancelled
    ones dropped. Timed occurrences are returned in `tz`. `event_ids`, if given,
    runs parallel to `objects`."""
    occurrences = []
    for index, ics in enumerate(objects):
        event_id = event_ids[index] if index < len(event_ids) else ""
        try:
            parsed = Calendar.from_ical(ics)
            recurring = any(e.get("RRULE") or e.get("RDATE") for e in parsed.walk("VEVENT"))
            events = recurring_ical_events.of(parsed).between(start, end)
        except ValueError:
            continue  # one malformed object must not hide the rest of the agenda
        for event in events:
            if str(event.get("STATUS", "")).upper() == "CANCELLED":
                continue
            ev_start, ev_end = event.start, event.end
            all_day = _is_all_day(ev_start)
            if not all_day:
                ev_start, ev_end = ev_start.astimezone(tz), ev_end.astimezone(tz)
            occurrences.append(
                Occurrence(
                    uid=str(event.get("UID", "")),
                    title=str(event.get("SUMMARY", "")),
                    start=ev_start,
                    end=ev_end,
                    all_day=all_day,
                    location=str(event["LOCATION"]) if event.get("LOCATION") else None,
                    recurring=recurring,
                    busy=str(event.get("TRANSP", "OPAQUE")).upper() != "TRANSPARENT",
                    calendar=calendar,
                    event_id=event_id,
                )
            )
    return sorted(occurrences, key=lambda o: sort_key(o.start, tz))


def sort_key(value: datetime | date, tz: ZoneInfo) -> datetime:
    if isinstance(value, datetime):
        return value
    return datetime.combine(value, time.min, tz)
