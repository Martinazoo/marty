"""Calendar tools. Times from and to the model are local (the user's timezone)
and without offset, e.g. "2026-11-05T20:00"."""

from collections.abc import Awaitable
from datetime import date, datetime, time, timedelta
from typing import Any, Literal
from zoneinfo import ZoneInfo

from pydantic import Field, model_validator

from app.core.agenda import when_es
from app.core.tools import Tool, ToolArgs, ToolContext, ToolError
from app.database.models import AuditAction, ToolPolicy
from app.database.repositories import AuditRepository
from app.integrations.apple_calendar import (
    CalendarConflictError,
    CalendarError,
    CalendarRequestError,
    CalendarUnavailableError,
    EventSpec,
    ICalError,
    Occurrence,
)
from app.services.calendar import CalendarService, FoundEvent, color_name

Weekday = Literal["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
WEEKDAYS: tuple[str, ...] = Weekday.__args__  # type: ignore[attr-defined]
ColorName = Literal[
    "rojo", "naranja", "amarillo", "verde", "turquesa", "azul", "morado", "rosa", "marrón", "gris"
]
MAX_RANGE_DAYS = 62
DEFAULT_DURATION = timedelta(hours=1)


async def _guard[T](call: Awaitable[T]) -> T:
    """Calendar failures become tool errors the model can explain to the user."""
    try:
        return await call
    except CalendarUnavailableError as exc:
        raise ToolError(f"iCloud calendar is unreachable right now ({exc})") from exc
    except CalendarConflictError as exc:
        raise ToolError("the event changed on another device meanwhile; read it again") from exc
    except CalendarRequestError as exc:
        raise ToolError(f"iCloud refused the change (HTTP {exc.status})") from exc
    except (CalendarError, ICalError) as exc:
        raise ToolError(str(exc)) from exc


def _local(value: datetime, tz: ZoneInfo) -> datetime:
    return value.replace(tzinfo=tz) if value.tzinfo is None else value.astimezone(tz)


def _iso(value: datetime | date) -> str:
    return f"{value:%Y-%m-%dT%H:%M}" if isinstance(value, datetime) else value.isoformat()


def _occurrence(o: Occurrence) -> dict[str, Any]:
    item: dict[str, Any] = {
        "event_id": o.event_id,
        "calendar": o.calendar,
        "title": o.title,
        "weekday": WEEKDAYS[o.start.weekday()],
        "start": _iso(o.start),
    }
    if o.all_day:
        item["all_day"] = True
        item["last_day"] = (o.end - timedelta(days=1)).isoformat()  # type: ignore[operator]
    else:
        item["end"] = _iso(o.end)
    if o.recurring:
        item["recurring"] = True
    if o.location:
        item["location"] = o.location
    return item


def _event(found: FoundEvent) -> dict[str, Any]:
    spec = found.spec
    item: dict[str, Any] = {
        "event_id": found.event_id,
        "calendar": found.calendar.name,
        "title": spec.title,
        "start": _iso(spec.start),
    }
    if isinstance(spec.start, datetime):
        item["end"] = _iso(spec.end)
    else:
        item["all_day"] = True
        item["last_day"] = (spec.end - timedelta(days=1)).isoformat()  # type: ignore[operator]
    if spec.weekly_days:
        item["repeats_weekly_on"] = [WEEKDAYS[d] for d in spec.weekly_days]
        if spec.repeat_until:
            item["repeat_until"] = spec.repeat_until.isoformat()
    if spec.location:
        item["location"] = spec.location
    return item


def _days(names: list[Weekday]) -> tuple[int, ...]:
    return tuple(sorted({WEEKDAYS.index(n) for n in names}))


async def _audit(
    ctx: ToolContext, action: AuditAction, entity_type: str, changes: dict[str, Any]
) -> None:
    await AuditRepository(ctx.session).record(
        user_id=ctx.user.id,
        actor=ctx.actor,
        action=action,
        entity_type=entity_type,
        entity_id=None,
        changes=changes,
        agent_action_id=ctx.agent_action_id,
    )


# --- Arguments ---------------------------------------------------------------------


class DateRange(ToolArgs):
    start_date: date
    end_date: date | None = Field(default=None, description="Inclusive; defaults to start_date.")

    @model_validator(mode="after")
    def _check_range(self) -> "DateRange":
        end = self.end_date or self.start_date
        if end < self.start_date:
            raise ValueError("end_date is before start_date")
        if (end - self.start_date).days >= MAX_RANGE_DAYS:
            raise ValueError(f"at most {MAX_RANGE_DAYS} days at a time")
        return self

    @property
    def last(self) -> date:
        return self.end_date or self.start_date


class ListCalendarsArgs(ToolArgs):
    pass


class ListEventsArgs(DateRange):
    calendar: str | None = Field(default=None, description="Only this calendar.")


class FindFreeTimeArgs(DateRange):
    min_minutes: int = Field(default=60, ge=15, le=600)
    day_start: time = Field(default=time(8, 0), description="Earliest time of day to consider.")
    day_end: time = Field(default=time(22, 0), description="Latest time of day to consider.")


class Timing(ToolArgs):
    end: datetime | None = Field(
        default=None, description="Local end. For all-day events, the last day."
    )
    duration_minutes: int | None = Field(
        default=None, ge=5, le=1440, description="Alternative to end."
    )
    location: str | None = Field(default=None, max_length=200)
    repeat_weekly_on: list[Weekday] | None = Field(
        default=None, description="Weekdays the event repeats on, every week."
    )
    repeat_until: date | None = Field(default=None, description="Last day of the repetition.")


class CreateEventArgs(Timing):
    calendar: str
    title: str = Field(min_length=1, max_length=200)
    start: datetime = Field(description="Local start, e.g. 2026-11-05T20:00.")
    all_day: bool = False


class UpdateEventArgs(Timing):
    event_id: str
    title: str | None = Field(default=None, min_length=1, max_length=200)
    start: datetime | None = Field(
        default=None, description="New local start; for a recurring event, the whole series."
    )


class EventIdArgs(ToolArgs):
    event_id: str


class SkipOccurrenceArgs(ToolArgs):
    event_id: str
    day: date = Field(description="The date of the occurrence to cancel.")


class CreateCalendarArgs(ToolArgs):
    name: str = Field(min_length=1, max_length=60)
    color: ColorName


class UpdateCalendarArgs(ToolArgs):
    name: str
    new_name: str | None = Field(default=None, min_length=1, max_length=60)
    color: ColorName | None = None


class CalendarNameArgs(ToolArgs):
    name: str


# --- Tools --------------------------------------------------------------------------


def build_calendar_tools(service: CalendarService) -> tuple[Tool, ...]:
    tz = service.tz

    def new_spec(args: CreateEventArgs) -> EventSpec:
        start = _local(args.start, tz)
        days = _days(args.repeat_weekly_on or [])
        if args.all_day:
            last = _local(args.end, tz).date() if args.end else start.date()
            return EventSpec(
                args.title,
                start.date(),
                last + timedelta(days=1),
                args.location,
                days,
                args.repeat_until,
            )
        if args.end is not None:
            end = _local(args.end, tz)
        elif args.duration_minutes is not None:
            end = start + timedelta(minutes=args.duration_minutes)
        else:
            end = start + DEFAULT_DURATION
        return EventSpec(args.title, start, end, args.location, days, args.repeat_until)

    def changed_spec(current: EventSpec, args: UpdateEventArgs) -> EventSpec:
        all_day = not isinstance(current.start, datetime)
        duration = current.end - current.start  # type: ignore[operator]
        if args.start is None:
            start = current.start
        else:
            start = _local(args.start, tz).date() if all_day else _local(args.start, tz)
        if args.end is not None:
            end = (
                _local(args.end, tz).date() + timedelta(days=1) if all_day else _local(args.end, tz)
            )
        elif args.duration_minutes is not None and not all_day:
            end = start + timedelta(minutes=args.duration_minutes)
        else:
            end = start + duration
        if args.repeat_weekly_on is None:
            days = current.weekly_days
        else:
            days = _days(args.repeat_weekly_on)
        if args.location is None:
            location = current.location
        else:
            location = args.location or None
        until = args.repeat_until if args.repeat_until is not None else current.repeat_until
        return EventSpec(
            args.title or current.title, start, end, location, days, until if days else None
        )

    async def list_calendars(ctx: ToolContext, args: ListCalendarsArgs) -> dict[str, Any]:
        calendars = await _guard(service.calendars(refresh=True))
        return {"calendars": [{"name": c.name, "color": color_name(c.color)} for c in calendars]}

    async def list_events(ctx: ToolContext, args: ListEventsArgs) -> dict[str, Any]:
        start = datetime.combine(args.start_date, time.min, tz)
        end = datetime.combine(args.last + timedelta(days=1), time.min, tz)
        occurrences = await _guard(service.events(start, end, args.calendar))
        return {"events": [_occurrence(o) for o in occurrences[:100]]}

    async def find_free_time(ctx: ToolContext, args: FindFreeTimeArgs) -> dict[str, Any]:
        if args.day_end <= args.day_start:
            raise ToolError("day_end must be after day_start")
        slots = await _guard(
            service.free_slots(
                args.start_date,
                args.last,
                min_minutes=args.min_minutes,
                day_start=args.day_start,
                day_end=args.day_end,
                now=ctx.now,
            )
        )
        return {
            "free": [
                {
                    "date": day.isoformat(),
                    "weekday": WEEKDAYS[day.weekday()],
                    "slots": [f"{s.start:%H:%M}-{s.end:%H:%M}" for s in day_slots],
                }
                for day, day_slots in slots.items()
            ]
        }

    async def create_event(ctx: ToolContext, args: CreateEventArgs) -> dict[str, Any]:
        spec = new_spec(args)
        clashes = await _guard(service.conflicts(spec))
        found = await _guard(service.create_event(args.calendar, spec, ctx.now))
        result = {"status": "created", "event": _event(found)}
        await _audit(ctx, AuditAction.CREATE, "calendar_event", result["event"])
        return _with_conflicts(result, clashes)

    async def update_event(ctx: ToolContext, args: UpdateEventArgs) -> dict[str, Any]:
        found = await _guard(service.find_event(args.event_id))
        spec = changed_spec(found.spec, args)
        clashes = await _guard(service.conflicts(spec, ignore_uid=found.uid))
        updated = await _guard(service.update_event(found, spec, ctx.now))
        result = {"status": "updated", "event": _event(updated)}
        await _audit(
            ctx,
            AuditAction.UPDATE,
            "calendar_event",
            {"before": _event(found), "after": result["event"]},
        )
        return _with_conflicts(result, clashes)

    async def skip_occurrence(ctx: ToolContext, args: SkipOccurrenceArgs) -> dict[str, Any]:
        found = await _guard(service.find_event(args.event_id))
        await _guard(service.skip_occurrence(found, args.day, ctx.now))
        await _audit(
            ctx,
            AuditAction.UPDATE,
            "calendar_event",
            {"event": _event(found), "skipped": args.day.isoformat()},
        )
        return {"status": "skipped", "title": found.spec.title, "day": args.day.isoformat()}

    async def delete_event_preview(ctx: ToolContext, args: EventIdArgs) -> str:
        found = await _guard(service.find_event(args.event_id))
        scope = "todas las repeticiones de " if found.spec.weekly_days else ""
        return (
            f"Borrar {scope}«{found.spec.title}» del calendario {found.calendar.name} "
            f"({when_es(found.spec)})"
        )

    async def delete_event(ctx: ToolContext, args: EventIdArgs) -> dict[str, Any]:
        found = await _guard(service.find_event(args.event_id))
        await _guard(service.delete_event(found))
        await _audit(ctx, AuditAction.DELETE, "calendar_event", _event(found))
        return {"status": "deleted", "title": found.spec.title}

    async def create_calendar(ctx: ToolContext, args: CreateCalendarArgs) -> dict[str, Any]:
        calendar = await _guard(service.create_calendar(args.name, args.color))
        await _audit(ctx, AuditAction.CREATE, "calendar", {"name": calendar.name})
        return {"status": "created", "calendar": calendar.name, "color": args.color}

    async def update_calendar(ctx: ToolContext, args: UpdateCalendarArgs) -> dict[str, Any]:
        if args.new_name is None and args.color is None:
            raise ToolError("nothing to change: give new_name and/or color")
        calendar = await _guard(
            service.update_calendar(args.name, new_name=args.new_name, color=args.color)
        )
        changes = {"name": args.name, "new_name": args.new_name, "color": args.color}
        await _audit(ctx, AuditAction.UPDATE, "calendar", changes)
        return {"status": "updated", "calendar": calendar.name, "color": color_name(calendar.color)}

    async def delete_calendar_preview(ctx: ToolContext, args: CalendarNameArgs) -> str:
        calendar = await _guard(service.find_calendar(args.name))
        count = await _guard(service.count_events(calendar.name))
        events = {0: "sin eventos", 1: "con su único evento"}.get(count, f"con sus {count} eventos")
        return f"Borrar el calendario «{calendar.name}» {events}"

    async def delete_calendar(ctx: ToolContext, args: CalendarNameArgs) -> dict[str, Any]:
        calendar = await _guard(service.delete_calendar(args.name))
        await _audit(ctx, AuditAction.DELETE, "calendar", {"name": calendar.name})
        return {"status": "deleted", "calendar": calendar.name}

    return (
        Tool(
            name="list_calendars",
            description="List the user's calendars with their colours.",
            args_model=ListCalendarsArgs,
            policy=ToolPolicy.READ,
            handler=list_calendars,
        ),
        Tool(
            name="list_events",
            description=(
                "List events between two dates (inclusive), with their event_id. Use it before "
                "answering anything about the agenda and before changing an event."
            ),
            args_model=ListEventsArgs,
            policy=ToolPolicy.READ,
            handler=list_events,
        ),
        Tool(
            name="find_free_time",
            description=(
                "Free time slots per day, between day_start and day_end, of at least "
                "min_minutes. All-day events do not block time."
            ),
            args_model=FindFreeTimeArgs,
            policy=ToolPolicy.READ,
            handler=find_free_time,
        ),
        Tool(
            name="create_event",
            description=(
                "Create an event. For a weekly routine (work, commuting, trainings) create ONE "
                "event with repeat_weekly_on instead of several. Without end or duration it "
                "lasts one hour. The result lists overlapping events: tell the user about them."
            ),
            args_model=CreateEventArgs,
            policy=ToolPolicy.WRITE,
            handler=create_event,
        ),
        Tool(
            name="update_event",
            description=(
                "Change an event: title, times, location or weekly repetition (an empty "
                "repeat_weekly_on list stops repeating, an empty location removes it). For a "
                "recurring event it changes the whole series; to drop a single day use "
                "skip_occurrence."
            ),
            args_model=UpdateEventArgs,
            policy=ToolPolicy.WRITE,
            handler=update_event,
        ),
        Tool(
            name="skip_occurrence",
            description="Cancel one day of a recurring event, keeping the rest of the series.",
            args_model=SkipOccurrenceArgs,
            policy=ToolPolicy.WRITE,
            handler=skip_occurrence,
        ),
        Tool(
            name="delete_event",
            description=(
                "Request deleting an event (all its repetitions). The user must confirm with a "
                "button first."
            ),
            args_model=EventIdArgs,
            policy=ToolPolicy.CONFIRM,
            handler=delete_event,
            preview=delete_event_preview,
        ),
        Tool(
            name="create_calendar",
            description="Create a new calendar with a colour.",
            args_model=CreateCalendarArgs,
            policy=ToolPolicy.WRITE,
            handler=create_calendar,
        ),
        Tool(
            name="update_calendar",
            description="Rename a calendar and/or change its colour.",
            args_model=UpdateCalendarArgs,
            policy=ToolPolicy.WRITE,
            handler=update_calendar,
        ),
        Tool(
            name="delete_calendar",
            description=(
                "Request deleting a calendar and all its events. The user must confirm with a "
                "button first."
            ),
            args_model=CalendarNameArgs,
            policy=ToolPolicy.CONFIRM,
            handler=delete_calendar,
            preview=delete_calendar_preview,
        ),
    )


def _with_conflicts(result: dict[str, Any], clashes: list[Occurrence]) -> dict[str, Any]:
    if clashes:
        result["overlaps_with"] = [_occurrence(o) for o in clashes]
    return result
