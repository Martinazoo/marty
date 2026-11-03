import json
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import select

from app.agents import ALL_TOOLS
from app.agents.calendar import build_calendar_tools
from app.core.assistant import Assistant
from app.core.context import ContextBuilder
from app.core.orchestrator import Orchestrator
from app.core.summaries import Summarizer
from app.core.tools import ToolExecutor, ToolRegistry
from app.database.models import ActionStatus, AuditLog
from app.integrations.apple_calendar import CalendarUnavailableError, EventSpec
from app.services.calendar import CalendarService
from tests.calendar_fakes import FakeBackend
from tests.test_assistant import SENDER, Clock, ScriptedLLM, calls, text

pytestmark = pytest.mark.integration

TZ = ZoneInfo("Europe/Madrid")
MONDAY = date(2026, 3, 9)


def make_assistant(session_factory, llm: ScriptedLLM, service: CalendarService) -> Assistant:
    registry = ToolRegistry((*ALL_TOOLS, *build_calendar_tools(service)))
    executor = ToolExecutor(registry, pending_ttl=timedelta(hours=24))
    return Assistant(
        session_factory=session_factory,
        executor=executor,
        orchestrator=Orchestrator(llm, registry, executor, max_iterations=4, max_output_tokens=512),
        summarizer=Summarizer(llm, keep_recent=16, min_batch=2),
        context_builder=ContextBuilder(calendar=service),
        timezone="Europe/Madrid",
        idle_gap=timedelta(hours=6),
        clock=Clock(),
    )


def tool_result(llm: ScriptedLLM, request: int = 1, index: int = -1) -> dict:
    return json.loads(llm.requests[request].messages[index].content or "{}")


@pytest.fixture
def backend() -> FakeBackend:
    fake = FakeBackend()
    fake.add_calendar("Feina", "#FF9500FF")
    fake.add_calendar("Basket", "#FF3B30FF")
    return fake


@pytest.fixture
def service(backend) -> CalendarService:
    return CalendarService(backend, TZ)


WORK = (
    "create_event",
    {
        "calendar": "feina",
        "title": "Feina",
        "start": f"{MONDAY}T09:00",
        "end": f"{MONDAY}T17:00",
        "repeat_weekly_on": ["monday", "tuesday", "wednesday", "thursday", "friday"],
    },
)


async def test_create_a_weekly_event_and_list_it(session_factory, session, service, backend):
    llm = ScriptedLLM(
        calls(WORK),
        text("Hecho."),
        calls(("list_events", {"start_date": str(MONDAY)})),
        text("Trabajas de 9 a 17."),
    )
    assistant = make_assistant(session_factory, llm, service)

    await assistant.handle_text(SENDER, "Trabajo de lunes a viernes de 9 a 17", 1)
    await assistant.handle_text(SENDER, "¿Qué tengo el lunes?", 2)

    assert "The user's calendars: Basket, Feina." in llm.system_prompt(0)
    created = tool_result(llm, 1)
    assert created["status"] == "created"
    assert created["event"]["repeats_weekly_on"] == WORK[1]["repeat_weekly_on"]
    assert "overlaps_with" not in created
    (listed,) = tool_result(llm, 3)["events"]
    assert listed["event_id"] == created["event"]["event_id"]
    assert (listed["start"], listed["end"], listed["recurring"]) == (
        f"{MONDAY}T09:00",
        f"{MONDAY}T17:00",
        True,
    )
    audit = await session.scalar(select(AuditLog).where(AuditLog.entity_type == "calendar_event"))
    assert audit.changes["title"] == "Feina"
    assert len(backend.objects) == 1


async def test_overlaps_are_reported_to_the_model(session_factory, service):
    await service.create_event(
        "Basket",
        EventSpec(
            "Partit",
            datetime(2026, 3, 11, 16, 0, tzinfo=TZ),
            datetime(2026, 3, 11, 18, 0, tzinfo=TZ),
        ),
        datetime(2026, 3, 1, tzinfo=TZ),
    )
    llm = ScriptedLLM(calls(WORK), text("Ojo, se solapa con el partido."))

    await make_assistant(session_factory, llm, service).handle_text(SENDER, "Trabajo...", 1)

    (overlap,) = tool_result(llm)["overlaps_with"]
    assert (overlap["title"], overlap["calendar"]) == ("Partit", "Basket")


async def test_deleting_an_event_waits_for_the_button(session_factory, session, service, backend):
    created = await service.create_event(
        "Feina",
        EventSpec(
            "Feina",
            datetime(2026, 3, 9, 9, tzinfo=TZ),
            datetime(2026, 3, 9, 17, tzinfo=TZ),
            weekly_days=(0, 2),
        ),
        datetime(2026, 3, 1, tzinfo=TZ),
    )
    llm = ScriptedLLM(calls(("delete_event", {"event_id": created.event_id})), text("Confírmalo."))
    assistant = make_assistant(session_factory, llm, service)

    reply = await assistant.handle_text(SENDER, "Borra el horario de trabajo", 1)

    (confirmation,) = reply.confirmations
    assert confirmation.explanation == (
        "Borrar todas las repeticiones de «Feina» del calendario Feina "
        "(los lunes y miércoles, 09:00-17:00)"
    )
    assert len(backend.objects) == 1
    decision = await assistant.decide(SENDER.telegram_user_id, confirmation.action_id, approve=True)
    assert decision.status is ActionStatus.EXECUTED
    assert backend.objects == {}
    audit = await session.scalar(
        select(AuditLog).where(
            AuditLog.entity_type == "calendar_event", AuditLog.action == "delete"
        )
    )
    assert audit.agent_action_id == confirmation.action_id


async def test_calendar_management_and_delete_preview(session_factory, service, backend):
    await service.create_event(
        "Basket",
        EventSpec(
            "Partit", datetime(2026, 3, 9, 20, tzinfo=TZ), datetime(2026, 3, 9, 22, tzinfo=TZ)
        ),
        datetime(2026, 3, 1, tzinfo=TZ),
    )
    llm = ScriptedLLM(
        calls(
            ("create_calendar", {"name": "CrossFit", "color": "verde"}),
            ("update_calendar", {"name": "basket", "color": "azul"}),
            ("delete_calendar", {"name": "Basket"}),
        ),
        text("Hecho; confirma el borrado."),
    )

    reply = await make_assistant(session_factory, llm, service).handle_text(SENDER, "...", 1)

    created, updated, _ = (tool_result(llm, 1, i) for i in (-3, -2, -1))
    assert created == {"status": "created", "calendar": "CrossFit", "color": "verde"}
    assert updated == {"status": "updated", "calendar": "Basket", "color": "azul"}
    (confirmation,) = reply.confirmations
    assert confirmation.explanation == "Borrar el calendario «Basket» con su único evento"
    assert [c.name for c in backend.calendar_list] == ["Feina", "Basket", "CrossFit"]


async def test_unreachable_calendar_is_explained_not_crashed(session_factory, service, backend):
    async def offline(*args, **kwargs):
        raise CalendarUnavailableError("CalDAV REPORT: ConnectError")

    backend.events_between = offline
    llm = ScriptedLLM(
        calls(("list_events", {"start_date": str(MONDAY)})),
        text("No puedo acceder al calendario ahora."),
    )

    reply = await make_assistant(session_factory, llm, service).handle_text(SENDER, "¿Hoy?", 1)

    assert reply.text == "No puedo acceder al calendario ahora."
    assert "unreachable" in (llm.requests[1].messages[-1].content or "")
