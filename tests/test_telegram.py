from datetime import datetime, timedelta
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest
from telegram.constants import ChatType
from telegram.ext import ApplicationHandlerStop

from app.core.assistant import Reply
from app.core.orchestrator import PendingConfirmation
from app.core.tools import Decision
from app.database.models import ActionStatus
from app.integrations.ai import RateLimitedError
from app.integrations.apple_calendar import CalendarUnavailableError, Occurrence
from app.integrations.telegram import texts
from app.integrations.telegram.formatting import escape, parse_duration, split_message
from app.integrations.telegram.handlers import TelegramHandlers

ALLOWED = 1001


class FakeChat:
    def __init__(self, chat_id: int, chat_type: str = ChatType.PRIVATE) -> None:
        self.id = chat_id
        self.type = chat_type
        self.sent: list[tuple[str, dict]] = []
        self.left = False

    async def send_message(self, text: str, **kwargs) -> None:
        self.sent.append((text, kwargs))

    async def send_action(self, action) -> None:
        pass

    async def leave(self) -> None:
        self.left = True


class FakeQuery:
    def __init__(self, user_id: int, data: str) -> None:
        self.from_user = SimpleNamespace(id=user_id)
        self.data = data
        self.answers: list[str | None] = []
        self.edited: list[str] = []

    async def answer(self, text: str | None = None) -> None:
        self.answers.append(text)

    async def edit_message_text(self, text: str, **kwargs) -> None:
        self.edited.append(text)

    async def edit_message_reply_markup(self, markup) -> None:
        self.edited.append("<markup removed>")


def make_update(user_id: int | None, chat: FakeChat | None, *, text=None, query=None, voice=None):
    user = SimpleNamespace(id=user_id, first_name="Tester") if user_id is not None else None
    message = (
        SimpleNamespace(text=text, voice=voice, audio=None, message_id=55)
        if text is not None or voice is not None
        else None
    )
    return SimpleNamespace(
        effective_user=user,
        effective_chat=chat,
        effective_message=message,
        callback_query=query,
    )


class FakeAssistant:
    def __init__(self, reply: Reply | None = None, decision: Decision | None = None) -> None:
        self.reply = reply
        self.decision_result = decision
        self.calls: list[tuple] = []

    async def handle_text(self, sender, text, telegram_message_id):
        self.calls.append(("handle_text", sender.telegram_user_id, text, telegram_message_id))
        return self.reply

    async def upkeep(self, reply):
        self.calls.append(("upkeep",))

    async def decide(self, telegram_user_id, action_id, *, approve):
        self.calls.append(("decide", telegram_user_id, action_id, approve))
        return self.decision_result


class FakeTranscriber:
    def __init__(self, result: str | Exception) -> None:
        self.result = result
        self.calls: list[tuple[bytes, str]] = []

    async def transcribe(self, audio: bytes, filename: str) -> str:
        self.calls.append((audio, filename))
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


def fake_voice(duration: int = 5) -> SimpleNamespace:
    async def download_as_bytearray() -> bytearray:
        return bytearray(b"OggS...")

    async def get_file() -> SimpleNamespace:
        return SimpleNamespace(download_as_bytearray=download_as_bytearray)

    return SimpleNamespace(duration=duration, mime_type="audio/ogg", get_file=get_file)


class FakeCalendar:
    """Answers with one event at 18:00 on the first day asked for."""

    def __init__(self, error: Exception | None = None) -> None:
        self.error = error
        self.ranges: list[tuple[datetime, datetime]] = []

    async def events(self, start: datetime, end: datetime) -> list[Occurrence]:
        self.ranges.append((start, end))
        if self.error is not None:
            raise self.error
        return [
            Occurrence(
                uid="x@marty",
                title="Basket <entreno>",
                start=start.replace(hour=18),
                end=start.replace(hour=20),
                all_day=False,
                location=None,
                recurring=True,
                busy=True,
                calendar="Basket",
            )
        ]


def make_handlers(
    assistant: FakeAssistant | None = None,
    transcriber: FakeTranscriber | None = None,
    calendar: FakeCalendar | None = None,
) -> TelegramHandlers:
    return TelegramHandlers(
        assistant or FakeAssistant(),
        allowed_user_ids=[ALLOWED],
        timezone=ZoneInfo("Europe/Madrid"),
        transcriber=transcriber,
        voice_max_seconds=60,
        calendar=calendar,
    )


# --- Formatting -------------------------------------------------------------


def test_escape_only_html_special_characters():
    assert escape('<b>"Tom" & Jerry</b>') == '&lt;b&gt;"Tom" &amp; Jerry&lt;/b&gt;'


def test_split_message_keeps_short_text_and_drops_empty():
    assert split_message("  hola  ") == ["hola"]
    assert split_message("   ") == []


def test_split_message_prefers_paragraph_boundaries():
    text = "a" * 30 + "\n\n" + "b" * 30 + "\n\n" + "c" * 30

    assert split_message(text, limit=70) == ["a" * 30 + "\n\n" + "b" * 30, "c" * 30]


def test_split_message_breaks_long_paragraphs_on_spaces_or_hard():
    words = " ".join(["palabra"] * 20)
    chunks = split_message(words, limit=50)
    assert all(len(c) <= 50 for c in chunks)
    assert " ".join(chunks) == words

    assert split_message("x" * 120, limit=50) == ["x" * 50, "x" * 50, "x" * 20]


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("30m", timedelta(minutes=30)),
        ("12h", timedelta(hours=12)),
        ("3d", timedelta(days=3)),
        ("2 D", timedelta(days=2)),
        ("90d", timedelta(days=90)),
        ("91d", None),
        ("0h", None),
        ("mañana", None),
        ("3", None),
    ],
)
def test_parse_duration(value, expected):
    assert parse_duration(value) == expected


# --- Gatekeeper -------------------------------------------------------------


async def test_gatekeeper_lets_the_allowed_user_through_in_private():
    await make_handlers().gatekeeper(make_update(ALLOWED, FakeChat(ALLOWED)), None)


async def test_gatekeeper_blocks_strangers_and_rate_limits_the_notice():
    handlers = make_handlers()
    chat = FakeChat(99)

    for _ in range(3):
        with pytest.raises(ApplicationHandlerStop):
            await handlers.gatekeeper(make_update(99, chat, text="hola"), None)

    assert chat.sent == [(texts.PRIVATE_BOT, {})]


async def test_gatekeeper_answers_strangers_button_presses():
    query = FakeQuery(99, "act:1:yes")

    with pytest.raises(ApplicationHandlerStop):
        await make_handlers().gatekeeper(make_update(99, FakeChat(99), query=query), None)

    assert query.answers == [texts.PRIVATE_BOT]


async def test_gatekeeper_leaves_groups_even_for_the_allowed_user():
    group = FakeChat(-500, ChatType.GROUP)

    with pytest.raises(ApplicationHandlerStop):
        await make_handlers().gatekeeper(make_update(ALLOWED, group, text="hola"), None)

    assert group.left and group.sent == []


async def test_gatekeeper_blocks_updates_without_a_user():
    with pytest.raises(ApplicationHandlerStop):
        await make_handlers().gatekeeper(make_update(None, FakeChat(-1, ChatType.CHANNEL)), None)


# --- Conversation and confirmations -------------------------------------------


async def test_text_sends_escaped_reply_then_confirmation_buttons_then_upkeep():
    reply = Reply(
        text="Vale <ok>",
        confirmations=[PendingConfirmation(7, "Olvidar este recuerdo: «x & y»")],
    )
    assistant = FakeAssistant(reply=reply)
    chat = FakeChat(ALLOWED)

    await make_handlers(assistant).text(make_update(ALLOWED, chat, text="Olvida x"), None)

    assert assistant.calls == [("handle_text", ALLOWED, "Olvida x", 55), ("upkeep",)]
    (first, _), (second, second_kwargs) = chat.sent
    assert first == "Vale &lt;ok&gt;"
    assert "Olvidar este recuerdo: «x &amp; y»" in second
    buttons = second_kwargs["reply_markup"].inline_keyboard[0]
    assert [b.callback_data for b in buttons] == ["act:7:yes", "act:7:no"]


async def test_voice_is_transcribed_shown_and_handled_like_text():
    assistant = FakeAssistant(reply=Reply(text="Apuntado."))
    transcriber = FakeTranscriber("Mi color favorito es el <verde>")
    chat = FakeChat(ALLOWED)

    await make_handlers(assistant, transcriber).voice(
        make_update(ALLOWED, chat, voice=fake_voice()), None
    )

    assert transcriber.calls == [(b"OggS...", "audio.ogg")]
    assert [text for text, _ in chat.sent] == [
        "<i>«Mi color favorito es el &lt;verde&gt;»</i>",
        "Apuntado.",
    ]
    assert assistant.calls == [
        ("handle_text", ALLOWED, "Mi color favorito es el <verde>", 55),
        ("upkeep",),
    ]


@pytest.mark.parametrize(
    ("transcriber", "duration", "expected"),
    [
        (None, 5, texts.VOICE_DISABLED),
        (FakeTranscriber("hola"), 61, texts.VOICE_TOO_LONG.format(minutes=1)),
        (FakeTranscriber(RateLimitedError("429")), 5, texts.VOICE_FAILED),
        (FakeTranscriber(""), 5, texts.VOICE_EMPTY),
    ],
)
async def test_voice_problems_get_a_short_answer_and_reach_no_llm(transcriber, duration, expected):
    assistant = FakeAssistant()
    chat = FakeChat(ALLOWED)

    await make_handlers(assistant, transcriber).voice(
        make_update(ALLOWED, chat, voice=fake_voice(duration)), None
    )

    assert [text for text, _ in chat.sent] == [escape(expected)]
    assert assistant.calls == []


async def test_today_and_week_render_the_agenda_without_the_llm():
    assistant, calendar, chat = FakeAssistant(), FakeCalendar(), FakeChat(ALLOWED)
    handlers = make_handlers(assistant, calendar=calendar)

    await handlers.today(make_update(ALLOWED, chat, text="/today"), None)
    await handlers.week(make_update(ALLOWED, chat, text="/week"), None)

    (today, _), (week, _) = chat.sent
    day_start, day_end = calendar.ranges[0]
    assert (day_start.hour, day_end - day_start) == (0, timedelta(days=1))
    assert calendar.ranges[1][1] - calendar.ranges[1][0] == timedelta(days=7)
    heading, line = today.split("\n")
    assert heading.startswith("<b>") and heading.endswith("</b>")
    assert line == "18:00-20:00 Basket &lt;entreno&gt; (Basket)"
    assert week.count("<b>") == 7 and week.count("Nada") == 6
    assert assistant.calls == []


@pytest.mark.parametrize(
    ("calendar", "expected"),
    [
        (None, texts.CALENDAR_DISABLED),
        (FakeCalendar(CalendarUnavailableError("down")), texts.CALENDAR_UNAVAILABLE),
    ],
)
async def test_agenda_without_calendar_explains_why(calendar, expected):
    chat = FakeChat(ALLOWED)

    await make_handlers(calendar=calendar).today(make_update(ALLOWED, chat, text="/today"), None)

    assert [text for text, _ in chat.sent] == [escape(expected)]


async def test_decision_edits_the_message_with_the_outcome():
    decision = Decision(ActionStatus.EXECUTED, "Olvidar este recuerdo: «x»", changed=True)
    assistant = FakeAssistant(decision=decision)
    query = FakeQuery(ALLOWED, "act:7:yes")

    await make_handlers(assistant).decision(
        make_update(ALLOWED, FakeChat(ALLOWED), query=query), None
    )

    assert assistant.calls == [("decide", ALLOWED, 7, True)]
    assert query.answers == ["Hecho."]
    assert query.edited == ["Olvidar este recuerdo: «x»\n\n<b>Hecho.</b>"]


async def test_repeated_press_says_already_decided():
    decision = Decision(ActionStatus.REJECTED, "Olvidar", changed=False)
    query = FakeQuery(ALLOWED, "act:7:yes")

    await make_handlers(FakeAssistant(decision=decision)).decision(
        make_update(ALLOWED, FakeChat(ALLOWED), query=query), None
    )

    assert query.answers == [texts.ALREADY_DECIDED]


async def test_unknown_action_removes_the_buttons():
    query = FakeQuery(ALLOWED, "act:7:no")

    await make_handlers(FakeAssistant(decision=Decision(None, None))).decision(
        make_update(ALLOWED, FakeChat(ALLOWED), query=query), None
    )

    assert query.answers == [texts.ACTION_NOT_FOUND]
    assert query.edited == ["<markup removed>"]
