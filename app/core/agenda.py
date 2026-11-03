"""Spanish wording of events and agendas, shared by confirmations and commands."""

from datetime import date, datetime, timedelta

from app.core.context import MONTHS_ES, WEEKDAYS_ES
from app.integrations.apple_calendar import EventSpec, Occurrence


def day_es(day: date) -> str:
    return f"{WEEKDAYS_ES[day.weekday()]} {day.day} de {MONTHS_ES[day.month - 1]}"


def _days_es(weekdays: tuple[int, ...]) -> str:
    names = [WEEKDAYS_ES[d] for d in sorted(weekdays)]
    plural = [n if n.endswith("s") else n + "s" for n in names]  # "sábados", "domingos"
    return plural[0] if len(plural) == 1 else ", ".join(plural[:-1]) + " y " + plural[-1]


def when_es(spec: EventSpec) -> str:
    """For example "jueves 5 de noviembre, 20:00-21:00" or
    "los lunes y miércoles, 09:00-17:00, hasta el 30 de junio"."""
    if isinstance(spec.start, datetime) and isinstance(spec.end, datetime):
        hours = f"{spec.start:%H:%M}-{spec.end:%H:%M}"
    else:
        hours = "todo el día"
    if spec.weekly_days:
        text = f"los {_days_es(spec.weekly_days)}, {hours}"
        if spec.repeat_until:
            until = spec.repeat_until
            text += f", hasta el {until.day} de {MONTHS_ES[until.month - 1]}"
        return text
    start_day = spec.start.date() if isinstance(spec.start, datetime) else spec.start
    return f"{day_es(start_day)}, {hours}"


def occurrence_line(occurrence: Occurrence) -> str:
    if occurrence.all_day:
        hours = "Todo el día"
    else:
        hours = f"{occurrence.start:%H:%M}-{occurrence.end:%H:%M}"
    line = f"{hours} {occurrence.title}"
    if occurrence.calendar and occurrence.calendar != occurrence.title:
        line += f" ({occurrence.calendar})"
    return line


def agenda_es(occurrences: list[Occurrence], first_day: date, last_day: date) -> list[str]:
    """One block per day: a heading and its events (or "Nada")."""
    blocks = []
    day = first_day
    while day <= last_day:
        todays = [o for o in occurrences if _covers(o, day)]
        lines = [occurrence_line(o) for o in todays] or ["Nada"]
        blocks.append("\n".join([day_es(day).capitalize(), *lines]))
        day += timedelta(days=1)
    return blocks


def _covers(occurrence: Occurrence, day: date) -> bool:
    if occurrence.all_day:
        return occurrence.start <= day < occurrence.end  # type: ignore[operator]
    start, end = occurrence.start, occurrence.end
    return start.date() <= day <= (end - timedelta(microseconds=1)).date()  # type: ignore[union-attr,operator]
