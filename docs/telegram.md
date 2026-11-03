# Telegram Integration

Telegram is MARTY's only user interface in the MVP. The bot is private: it answers
only the Telegram user IDs listed in `TELEGRAM_ALLOWED_USER_IDS`.

## Library and transport

- **python-telegram-bot v22** (async, mature, well documented, typed). aiogram would
  also work; PTB was chosen for its documentation and built-in handling of commands,
  inline keyboards and callback queries. PTB is LGPL-3.0, which is fine for use as
  a library.
- **Long polling** (`getUpdates`). The server only makes outgoing HTTPS requests, so
  no public domain, TLS certificate or open port is needed.

The bot runs inside the FastAPI process. The lifespan handler builds the PTB
`Application` and drives it manually:

```text
startup:  application.initialize() -> application.start() -> updater.start_polling()
shutdown: updater.stop() -> application.stop() -> application.shutdown()
```

Only one poller may use a token at a time (Telegram answers a second concurrent
`getUpdates` with *409 Conflict*). Use **a separate development bot** from
@BotFather for local work, so development never interferes with the server.

Updates are processed sequentially (PTB's default), which avoids race conditions
on the single user's data.

## Authorisation

1. A gatekeeper (`TypeHandler` in group -1, `app/integrations/telegram/handlers.py`)
   runs before every other handler. Unless the update comes from an allowed user
   in a private chat, it stops processing (`ApplicationHandlerStop`): strangers
   get a fixed "Este bot es privado." (button presses get it as a toast), at most
   once every 10 minutes per user, and only their numeric ID is logged. If the bot
   is added to a group or channel, it leaves. Nothing from strangers is stored.
2. As defence in depth, every handler is also registered with
   `filters.User(TELEGRAM_ALLOWED_USER_IDS)` and `filters.ChatType.PRIVATE`.
3. Button presses are re-checked in the database: the action must belong to the
   presser and still be pending (row lock against double presses).
4. In @BotFather, disable joining groups (`/setjoingroups` -> Disable).
5. On the first message from an allowed user, MARTY creates the `users` row with
   the chat ID that proactive notifications are sent to.

## Message pipeline

1. Authorisation filter.
2. Store the incoming message (`messages`, role `user`).
3. Slash command: run the deterministic handler and reply. No LLM call.
4. Button press: resolve the pending `agent_actions` row and execute or reject it.
5. Free text: call the orchestrator (context building, tool loop), reply and store
   the reply with provider, model and token usage.
6. Photos and documents (Phase 3/4): routed to the relevant importer, such as an
   Iron Harder workout or the roadmap document. Until then MARTY answers that it
   only understands text.

While the orchestrator works, the bot refreshes the *typing* chat action every few
seconds. After the reply is sent, conversation upkeep (summaries) runs.

## Commands

Descriptions are registered in Spanish with `set_my_commands`, so they appear in
Telegram's command menu.

| Command | Purpose | Available from |
| --- | --- | --- |
| `/start` | Register the user and show a welcome message | Phase 1 |
| `/help` | What MARTY can do, with examples | Phase 1 |
| `/settings` | Show and change preferences (briefing time, quiet hours, ...) | Phase 1, extended in 5 |
| `/pause`, `/resume` | Pause or resume proactive notifications (`/pause 3d`) | Phase 1, used in 5 |
| `/calendar` | Upcoming events and conflicts | Phase 2 |
| `/today` | Today's events, workouts, study goal, free time | Phase 2, extended in 3-4 |
| `/week` | This week's plan | Phase 2, extended in 3-4 |
| `/training` | Training plan and recent workouts | Phase 3 |
| `/study` | Next roadmap session and this week's study plan | Phase 4 |
| `/progress` | Roadmap progress and training consistency | Phase 4 |

Natural language covers everything else ("¿Qué tengo hoy?", "He estudiado C++ 45
minutos", "Cancela el tiro opcional").

## Confirmation flow

Important or destructive operations (tools with the `confirm` policy) are never
executed directly by the model:

1. The tool stores an `agent_actions` row with status `pending_confirmation`, the
   validated arguments, an explanation and an expiry time (for example 24 hours).
2. The reply includes inline buttons with `callback_data` such as `act:42:yes` and
   `act:42:no`, well within Telegram's 64-byte limit.
3. On a press, the handler checks the user, that the action is still pending and
   not expired, executes it through the same tool executor, writes the audit log,
   and edits the original message to show the outcome.
4. Expired or already-decided actions answer with a short notice and do nothing.
5. The outcome is also stored as a short assistant message in the conversation,
   so the model knows on the next turn that the user confirmed or cancelled.

The confirmation message shows the tool's own preview text (for example
"Olvidar este recuerdo: «...»"), not the model's paraphrase, so the user sees
exactly what the button will do.

## Formatting and limits

- Replies use HTML parse mode, with all dynamic text escaped. MarkdownV2 requires
  escaping too many characters to be reliable with LLM output.
- Telegram messages are limited to 4096 characters. Long reports are split on
  paragraph boundaries.

## Errors

- A global PTB error handler logs the exception (with secret redaction) and replies
  with a short Spanish apology.
- If every AI provider fails, the reply says that free-form conversation is
  temporarily unavailable and lists the commands that keep working.
- Network errors while polling are retried by PTB with backoff.

## Proactive messages

Scheduled jobs (Phase 5) call a notification service, which checks pause state,
quiet hours and the `dedupe_key`, then sends through the same bot instance to the
stored chat ID.

## Privacy note

Bot conversations are not end-to-end encrypted: Telegram's servers see message
content. Avoid sending secrets to the bot. MARTY never asks for passwords in chat.
