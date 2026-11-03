from collections.abc import Callable
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.agents import ALL_TOOLS
from app.core.assistant import NO_LLM_TEXT, Assistant, Sender
from app.core.context import ContextBuilder, ContextLimits
from app.core.orchestrator import (
    ALL_PROVIDERS_FAILED_TEXT,
    LOOP_EXHAUSTED_TEXT,
    Orchestrator,
)
from app.core.summaries import Summarizer
from app.core.tools import Tool, ToolArgs, ToolError, ToolExecutor, ToolRegistry
from app.database.models import (
    ActionStatus,
    Actor,
    AgentAction,
    AuditLog,
    Conversation,
    Memory,
    MemoryCategory,
    MemoryKind,
    Message,
    MessageRole,
    ToolPolicy,
)
from app.integrations.ai import (
    AllProvidersFailedError,
    ChatMessage,
    CompletionRequest,
    CompletionResult,
    Role,
    ToolCall,
    Usage,
)

pytestmark = pytest.mark.integration

SENDER = Sender(telegram_user_id=4242, chat_id=4242, display_name="Tester")
OTHER = Sender(telegram_user_id=777, chat_id=777, display_name="Otro")
START = datetime(2026, 3, 2, 8, 0, tzinfo=UTC)

Step = CompletionResult | Exception | Callable[[CompletionRequest], CompletionResult]


class ScriptedLLM:
    name = "fake"

    def __init__(self, *steps: Step) -> None:
        self.steps = list(steps)
        self.requests: list[CompletionRequest] = []

    async def complete(self, request: CompletionRequest) -> CompletionResult:
        self.requests.append(request)
        step = self.steps.pop(0)
        if isinstance(step, Exception):
            raise step
        if callable(step):
            return step(request)
        return step

    def system_prompt(self, index: int = -1) -> str:
        return self.requests[index].messages[0].content or ""


def text(content: str) -> CompletionResult:
    return CompletionResult(ChatMessage(Role.ASSISTANT, content), "fake", "fake-1", Usage(10, 5))


def calls(*tool_calls: tuple[str, dict]) -> CompletionResult:
    message = ChatMessage(
        Role.ASSISTANT,
        None,
        tool_calls=tuple(
            ToolCall(f"call_{i}", name, args) for i, (name, args) in enumerate(tool_calls)
        ),
    )
    return CompletionResult(message, "fake", "fake-1", Usage(20, 8))


class Clock:
    def __init__(self) -> None:
        self.now = START

    def __call__(self) -> datetime:
        return self.now


def make_assistant(
    session_factory: async_sessionmaker[AsyncSession],
    llm: ScriptedLLM | None,
    *,
    tools: tuple[Tool, ...] = ALL_TOOLS,
    clock: Clock | None = None,
    keep_recent: int = 16,
    max_iterations: int = 4,
) -> Assistant:
    registry = ToolRegistry(tools)
    executor = ToolExecutor(registry, pending_ttl=timedelta(hours=24))
    return Assistant(
        session_factory=session_factory,
        executor=executor,
        orchestrator=Orchestrator(
            llm, registry, executor, max_iterations=max_iterations, max_output_tokens=512
        )
        if llm
        else None,
        summarizer=Summarizer(llm, keep_recent=keep_recent, min_batch=2) if llm else None,
        context_builder=ContextBuilder(ContextLimits(recent_messages=keep_recent)),
        timezone="Europe/Madrid",
        idle_gap=timedelta(hours=6),
        clock=clock or Clock(),
    )


async def count(session: AsyncSession, model: type) -> int:
    return await session.scalar(select(func.count()).select_from(model))


REMEMBER_BLUE = (
    "remember",
    {
        "content": "Su color favorito es el azul",
        "kind": "profile",
        "category": "preference",
        "importance": 2,
    },
)


async def test_remember_flow_stores_memory_action_audit_and_messages(session_factory, session):
    llm = ScriptedLLM(calls(REMEMBER_BLUE), text("Apuntado: tu color favorito es el azul."))
    assistant = make_assistant(session_factory, llm)

    reply = await assistant.handle_text(SENDER, "Mi color favorito es el azul", 11)

    assert reply.text == "Apuntado: tu color favorito es el azul."
    assert reply.confirmations == []
    memory = await session.scalar(select(Memory))
    assert (memory.content, memory.kind, memory.category) == (
        "Su color favorito es el azul",
        MemoryKind.PROFILE,
        MemoryCategory.PREFERENCE,
    )
    action = await session.scalar(select(AgentAction))
    assert (action.tool_name, action.status) == ("remember", ActionStatus.EXECUTED)
    audit = await session.scalar(select(AuditLog).where(AuditLog.entity_type == "memory"))
    assert (audit.actor, audit.agent_action_id, audit.entity_id) == (
        Actor.MARTY,
        action.id,
        memory.id,
    )
    messages = (await session.scalars(select(Message).order_by(Message.id))).all()
    assert [m.role for m in messages] == [MessageRole.USER, MessageRole.TOOL, MessageRole.ASSISTANT]
    assert memory.source_message_id == messages[0].id
    assert messages[0].telegram_message_id == 11
    assert (messages[2].llm_provider, messages[2].input_tokens, messages[2].output_tokens) == (
        "fake",
        30,
        13,
    )
    # The second request carried the tool result back to the model.
    tool_message = llm.requests[1].messages[-1]
    assert tool_message.role is Role.TOOL and '"stored"' in (tool_message.content or "")


async def test_memories_are_in_the_context_of_later_turns(session_factory):
    llm = ScriptedLLM(calls(REMEMBER_BLUE), text("Hecho."), text("Azul."))
    assistant = make_assistant(session_factory, llm)

    await assistant.handle_text(SENDER, "Mi color favorito es el azul", 1)
    await assistant.handle_text(SENDER, "¿Cuál es mi color favorito?", 2)

    assert "Su color favorito es el azul" in llm.system_prompt()
    assert "lunes 2 de marzo de 2026, 09:00" in llm.system_prompt()
    history = [(m.role, m.content) for m in llm.requests[-1].messages[1:]]
    assert history == [
        (Role.USER, "Mi color favorito es el azul"),
        (Role.ASSISTANT, "Hecho."),
        (Role.USER, "¿Cuál es mi color favorito?"),
    ]


async def test_invalid_arguments_are_reported_to_the_model(session_factory, session):
    bad = ("remember", {"content": "x", "kind": "gossip", "category": "general"})
    llm = ScriptedLLM(calls(bad), text("Perdona, no lo he guardado."))

    await make_assistant(session_factory, llm).handle_text(SENDER, "Recuerda x", 1)

    tool_result = llm.requests[1].messages[-1].content or ""
    assert "invalid arguments" in tool_result and "kind" in tool_result
    assert await count(session, Memory) == 0
    assert await count(session, AgentAction) == 0


async def test_unknown_tool_is_reported_to_the_model(session_factory):
    llm = ScriptedLLM(calls(("run_sql", {"q": "DROP TABLE users"})), text("No puedo."))

    await make_assistant(session_factory, llm).handle_text(SENDER, "hola", 1)

    assert "unknown tool" in (llm.requests[1].messages[-1].content or "")


async def remember_then_forget(session_factory, clock: Clock) -> tuple[Assistant, int, int]:
    llm = ScriptedLLM(
        calls(REMEMBER_BLUE),
        text("Hecho."),
        calls(("forget_memory", {"memory_id": 1})),
        text("Confírmalo con el botón."),
    )
    assistant = make_assistant(session_factory, llm, clock=clock)
    await assistant.handle_text(SENDER, "Mi color favorito es el azul", 1)
    reply = await assistant.handle_text(SENDER, "Olvida mi color favorito", 2)
    (confirmation,) = reply.confirmations
    assert confirmation.explanation == "Olvidar este recuerdo: «Su color favorito es el azul»"
    return assistant, confirmation.action_id, 1


async def test_forget_waits_for_confirmation_then_executes(session_factory, session):
    clock = Clock()
    assistant, action_id, memory_id = await remember_then_forget(session_factory, clock)
    memory = await session.get(Memory, memory_id)
    assert memory.deleted_at is None

    decision = await assistant.decide(SENDER.telegram_user_id, action_id, approve=True)

    assert (decision.status, decision.changed) == (ActionStatus.EXECUTED, True)
    await session.refresh(memory)
    assert memory.deleted_at == START
    audit = await session.scalar(select(AuditLog).where(AuditLog.action == "delete"))
    assert (audit.actor, audit.agent_action_id) == (Actor.USER, action_id)
    again = await assistant.decide(SENDER.telegram_user_id, action_id, approve=True)
    assert (again.status, again.changed) == (ActionStatus.EXECUTED, False)
    assert await count(session, AuditLog) == 3  # user created, memory created, memory deleted


async def test_button_outcome_is_visible_to_the_model_on_the_next_turn(session_factory):
    clock = Clock()
    assistant, action_id, _ = await remember_then_forget(session_factory, clock)
    await assistant.decide(SENDER.telegram_user_id, action_id, approve=True)
    llm = ScriptedLLM(text("De nada."))
    assistant._orchestrator._llm = llm  # same assistant, fresh script

    await assistant.handle_text(SENDER, "Gracias", 3)

    history = [m.content for m in llm.requests[0].messages[1:]]
    assert history[-2] == (
        "(Solicitud «Olvidar este recuerdo: «Su color favorito es el azul»»: "
        "el usuario la ha confirmado y ya está hecha.)"
    )


async def test_rejected_and_expired_confirmations_change_nothing(session_factory, session):
    clock = Clock()
    assistant, action_id, memory_id = await remember_then_forget(session_factory, clock)

    rejected = await assistant.decide(SENDER.telegram_user_id, action_id, approve=False)
    assert rejected.status is ActionStatus.REJECTED

    session.add(
        AgentAction(
            user_id=1,
            tool_name="forget_memory",
            arguments={"memory_id": memory_id},
            policy=ToolPolicy.CONFIRM,
            status=ActionStatus.PENDING_CONFIRMATION,
            expires_at=START + timedelta(hours=1),
        )
    )
    await session.commit()
    clock.now = START + timedelta(hours=2)
    expired = await assistant.decide(SENDER.telegram_user_id, action_id + 1, approve=True)

    assert expired.status is ActionStatus.EXPIRED
    assert (await session.get(Memory, memory_id)).deleted_at is None


async def test_other_users_cannot_decide_an_action(session_factory):
    assistant, action_id, _ = await remember_then_forget(session_factory, Clock())
    await assistant.register(OTHER)

    decision = await assistant.decide(OTHER.telegram_user_id, action_id, approve=True)

    assert decision.status is None


async def test_failed_write_tool_rolls_back_its_changes(session_factory, session):
    class Args(ToolArgs):
        content: str

    async def half_done(ctx, args):
        ctx.session.add(
            Memory(
                user_id=ctx.user.id,
                kind=MemoryKind.LONG_TERM,
                category=MemoryCategory.GENERAL,
                content=args.content,
                source="user_stated",
            )
        )
        await ctx.session.flush()
        raise ToolError("calendar unreachable")

    tool = Tool("flaky", "Fails halfway.", Args, ToolPolicy.WRITE, half_done)
    llm = ScriptedLLM(calls(("flaky", {"content": "algo"})), text("No he podido."))

    await make_assistant(session_factory, llm, tools=(tool,)).handle_text(SENDER, "hazlo", 1)

    assert await count(session, Memory) == 0
    action = await session.scalar(select(AgentAction))
    assert (action.status, action.error) == (ActionStatus.FAILED, "calendar unreachable")
    assert "calendar unreachable" in (llm.requests[1].messages[-1].content or "")


async def test_all_providers_failing_gives_a_fixed_reply_and_keeps_the_message(
    session_factory, session
):
    llm = ScriptedLLM(AllProvidersFailedError(["gemini: RateLimitedError"]))

    reply = await make_assistant(session_factory, llm).handle_text(SENDER, "hola", 1)

    assert reply.text == ALL_PROVIDERS_FAILED_TEXT
    roles = (await session.scalars(select(Message.role).order_by(Message.id))).all()
    assert roles == [MessageRole.USER, MessageRole.ASSISTANT]


async def test_without_llm_commands_still_work(session_factory):
    assistant = make_assistant(session_factory, None)

    reply = await assistant.handle_text(SENDER, "hola", 1)
    until = await assistant.pause(SENDER, timedelta(days=3))
    paused = await assistant.preferences(SENDER)

    assert reply.text == NO_LLM_TEXT
    assert until == START + timedelta(days=3)
    assert (paused.notifications_paused, paused.paused_until) == (True, until)
    await assistant.resume(SENDER)
    assert (await assistant.preferences(SENDER)).notifications_paused is False


async def test_tool_loop_is_capped(session_factory):
    llm = ScriptedLLM(*[calls(("list_memories", {}))] * 3)

    reply = await make_assistant(session_factory, llm, max_iterations=3).handle_text(
        SENDER, "hola", 1
    )

    assert reply.text == LOOP_EXHAUSTED_TEXT
    assert len(llm.requests) == 3


async def test_new_conversation_after_idle_gap_with_summary_of_the_previous(
    session_factory, session
):
    clock = Clock()

    def summary(request: CompletionRequest) -> CompletionResult:
        transcript = request.messages[1].content or ""
        assert "Usuario: Mañana juego un partido" in transcript
        return text("El usuario juega un partido mañana.")

    llm = ScriptedLLM(text("¡Suerte!"), text("Hola de nuevo."), summary, text("¿Qué tal fue?"))
    assistant = make_assistant(session_factory, llm, clock=clock)

    await assistant.handle_text(SENDER, "Mañana juego un partido", 1)
    clock.now += timedelta(hours=7)
    reply = await assistant.handle_text(SENDER, "Buenos días", 2)
    assert reply.new_conversation
    await assistant.upkeep(reply)
    await assistant.handle_text(SENDER, "¿Te acuerdas de lo de ayer?", 3)

    assert await count(session, Conversation) == 2
    assert "El usuario juega un partido mañana." in llm.system_prompt()
    assert "Mañana juego un partido" not in str(llm.requests[-1].messages[1:])


async def test_rolling_summary_folds_messages_outside_the_window(session_factory, session):
    replies = [text(f"respuesta {i}") for i in range(3)]
    llm = ScriptedLLM(*replies, text("Resumen de los primeros mensajes."), text("vale"))
    assistant = make_assistant(session_factory, llm, keep_recent=4)

    for i in range(3):
        reply = await assistant.handle_text(SENDER, f"mensaje {i}", i)
    await assistant.upkeep(reply)  # 6 messages, window 4: the oldest 2 get folded
    await assistant.handle_text(SENDER, "otro", 9)

    conversation = await session.scalar(select(Conversation))
    assert conversation.summary == "Resumen de los primeros mensajes."
    assert conversation.summarized_through_message_id == 2
    assert "Resumen de los primeros mensajes." in llm.system_prompt()


async def test_summary_failure_is_harmless(session_factory, session):
    llm = ScriptedLLM(*[text("ok")] * 3, AllProvidersFailedError(["gemini: RateLimitedError"]))
    assistant = make_assistant(session_factory, llm, keep_recent=4)
    for i in range(3):
        reply = await assistant.handle_text(SENDER, f"m{i}", i)

    await assistant.upkeep(reply)

    assert (await session.scalar(select(Conversation))).summary is None
