# Implementation Plan

Each phase ends with a runnable system, passing tests, updated documentation and a
status report (what works, what was tested, what is missing, manual setup). The
next phase starts only after review.

## Environments

- **Production:** the user's Ubuntu Server at home, Docker Compose.
- **Development:** macOS (Apple Silicon) with Homebrew Python 3.12 and OrbStack
  as the Docker runtime. Phase 0 was started on a temporary Ubuntu 24.04 laptop.

## Phase 0 - Architecture and project setup (done)

- Environment inspected: Ubuntu 24.04, Python 3.12.3, Docker 29.9, Compose v5.6.
- Project skeleton: FastAPI app factory, `/health` and `/health/ready`,
  pydantic-settings configuration, JSON logging with secret redaction.
- Database foundation: async SQLAlchemy 2.1 + asyncpg, naming conventions, mixins,
  Alembic wired to settings (no tables yet).
- AI provider interface and `FallbackChain` with cooldowns.
- Docker Compose: PostgreSQL 17 with a persistent volume, backend as non-root,
  health checks, restart policies, log rotation, localhost-only ports.
- Backup script, dependency lock script.
- Documentation: architecture, database schema, Telegram, AI providers, security,
  deployment.

## Phase 1 - Functional MARTY Core (implemented, awaiting acceptance on Telegram)

| Milestone | Content |
| --- | --- |
| 1.1 Core data | ORM models and first migration for `users`, `user_preferences`, `conversations`, `messages`, `memories`, `agent_actions`, `audit_logs`; repositories; repository tests against PostgreSQL. |
| 1.2 Telegram bot | PTB lifecycle in the FastAPI lifespan, allowlist and private-chat filters, `/start`, `/help`, `/settings` (read-only), `/pause`, `/resume`, message persistence, global error handler. |
| 1.3 AI adapters | `OpenAICompatibleProvider` (Gemini, Groq, Ollama), error mapping, provider factory from settings, model-listing script, tests with `httpx.MockTransport`, one manual check per real provider. |
| 1.4 Orchestrator | Tool registry with policies and Pydantic validation, tool loop with iteration cap, context builder, first tools (`remember`, `list_memories`, `update_memory`, `forget_memory` with confirmation), inline-button confirmation flow, audit logging, system prompt. |
| 1.5 Conversation upkeep | Conversation boundaries by idle time, rolling summaries (skipped when providers are saturated). |

Acceptance: you chat with MARTY in Spanish on Telegram; it remembers facts you
told it after a restart; strangers are rejected; when every provider fails it says
so and the commands still work.

Status:

- Implemented as planned, with two adjustments: the current date and time go
  into the system prompt instead of a `current_datetime` tool (saves one LLM
  round trip per message), and `update_memory` was added so corrections keep
  history instead of duplicating facts.
- Verified: 92 automated tests (unit, plus integration against PostgreSQL); a
  scripted real conversation against Gemini and Groq (remember, recall, correct,
  delete with confirmation, fallback to Groq on a Gemini 429); the Docker image
  starts the bot and applies migrations.
- Pending: acceptance on Telegram by the user.

## Phase 2 - Apple Calendar (MVP implemented, awaiting acceptance on Telegram)

| Milestone | Content |
| --- | --- |
| 2.1 Setup | CalDAV connection to iCloud with an app-specific password; calendar discovery; calendars created, renamed, recoloured and deleted (with confirmation) from the bot. |
| 2.2 Reading | Events read live from iCloud for the requested range, recurring events expanded; calendar names cached in memory for five minutes. |
| 2.3 Availability | Free time per day from timed, busy events; overlap detection when an event is created or changed (four weeks ahead for weekly events). |
| 2.4 Managed events | Create, update, skip one day of a weekly event, delete with confirmation; ETag preconditions so changes made meanwhile on another device are never overwritten. |
| 2.5 Commands | `/today` and `/week` straight from iCloud, without the LLM. |

Status:

- The design was simplified for the MVP: iCloud is the source of truth and
  nothing about events is stored in the database (no `calendar_*` tables, no
  sync job). The work schedule and commuting are weekly recurring events in the
  Feina and Desplaçament calendars instead of a `weekly_commitments` table. All
  event calendars of the account belong to MARTY, by the user's decision; task
  lists (VTODO) are ignored.
- Events are addressed by their location in iCloud (`<calendar>/<file>.ics`),
  because iCloud rejects CalDAV queries filtered by UID (HTTP 412). Ids are
  checked against the user's calendars before any request.
- Verified: 159 automated tests (iCalendar building and expansion, the CalDAV
  client against a mock server, the service with an in-memory backend, the tools
  through the assistant against PostgreSQL, the Telegram commands, tool schemas),
  an end-to-end run against real iCloud on a temporary calendar (create a weekly
  event, list, skip a day, change times, stale-ETag rejection, agenda text,
  invalid ids, delete, cleanup), and a real conversation through
  `gemini-3.5-flash-lite` doing the same in Spanish, deletion confirmed by button.
- Pending: acceptance on Telegram by the user; deployment on the home server.
- Later: the calendar in the morning briefing and conflict notifications
  (Phase 5); events changed on other devices are picked up on the next read.

## Phase 3 - Personal Trainer (specialist agent)

The Trainer is its own agent: own prompt, own tools, own stored plans. MARTY
routes a training message or document to it; it does not mix with the C++
Professor. A plan can arrive as a Word/PDF (often drafted by another AI), as
pasted text, or through conversation; the Trainer still plans the week, logs
workouts, applies load rules and writes the calendar after confirmation.

| Milestone | Content |
| --- | --- |
| 3.1 Weekly plan | Enter the weekly template through a guided conversation (stored in the database, never in git); weekly materialisation into dated sessions. |
| 3.2 Document ingest | Also accept `.docx`, `.pdf` and pasted text (for example a plan drafted by another AI). Keep the original. Extract sessions without inventing missing fields; ask if a date or time is unclear. The extracted plan feeds the same template as 3.1. |
| 3.3 Matches | ICS feed import (idempotent) and manual registration ("El sábado que viene tengo partido"). |
| 3.4 Logging | Natural-language workout reports: duration, RPE, fatigue, unplanned workouts, cancellations. |
| 3.5 Load rules | Session load (duration × RPE), intensity per activity type, density rules (hard days in a row, intensity before a match), all explained to the user and treated as heuristics, not medical advice. |
| 3.6 Change evaluation | The "Wednesday basketball instead of running" flow: evaluate the rest of the week, adjust optional activities, request approval for coach sessions, sync approved changes to the calendar. |
| 3.7 Calendar | Propose events on the right calendars (CrossFit, Basket, …); write them only after the user confirms. Recurring trainings are one weekly event; a match is a one-off. |
| 3.8 Iron Harder (interim) | Import from pasted text, documents and screenshots into `workout_prescriptions`, original content preserved. Screenshot reading needs a multimodal model and is optional. |

## Phase 4 - C++ Professor (specialist agent)

Same shape as the Trainer, for study: own prompt, own tools, own stored
roadmap. The 60-session guide, a Word/PDF drafted elsewhere, or a
conversation can all feed the same plan; the Professor still tracks
progress, places sessions in free time and reschedules what was not done.

| Milestone | Content |
| --- | --- |
| 4.1 Import | Parse `Roadmap_CPP_60_Sessions_Expanded_Guide_EN.docx` (and other `.docx` / `.pdf`) with `python-docx` into blocks and sessions, verbatim. Idempotent by file hash. Report parsing anomalies instead of guessing. Keep the original. |
| 4.2 Progress | Study reports ("He estudiado C++ una hora"), session completion, `/study`, `/progress`. No quizzes. |
| 4.3 Planner | Distribute sessions over real availability with daily caps, nothing demanding during commuting, lighter load on busy days and match days. Proposed plan, then calendar sync after approval (Estudiar C++). |
| 4.4 Rescheduling | Detect unfinished planned blocks and propose moving them. |

## Phase 5 - Proactive features

APScheduler jobs driven by `user_preferences`, with notification deduplication,
pause and quiet hours: morning briefing, study inactivity reminder (once per
inactivity streak, never assuming that silence means no study), Sunday weekly report
with statistics computed from records only, conflict notifications, and complete
`/settings`.

## Phase 6 - Future integrations

Iron Harder investigation (official API, exports, share links), Apple Health via an
iOS companion app (HealthKit permissions, signing requirements, secure ingestion
endpoint), further agents.

## Decisions

- MARTY runs on the user's Ubuntu Server at home; development happens on macOS.
- LLM providers: the user accepts the free tiers. Gemini is primary
  (`gemini-3.5-flash`, then `gemini-3.5-flash-lite` when the first one's daily
  quota is used up), Groq is the last fallback (`openai/gpt-oss-120b`). All three
  were checked with real tool-calling requests. `gemini-3.8-flash` was overloaded
  (HTTP 503) when tested and can be retried later; Gemini Pro models and
  `gemini-4-argon` return 429 on the free tier.
- The Trainer and the C++ Professor are specialised agents, not extra Telegram
  bots: same chat with MARTY, a router chooses which agent's tools and prompt
  to use. They share memory and the calendar; they do not share domain logic.
  They keep every responsibility already planned (weekly template, matches,
  workout logs, load rules, change evaluation, roadmap import, study
  progress, planner, rescheduling). In addition they accept a Word/PDF
  (often drafted by another AI): they read it, keep the original, merge it
  into the stored plan, and put the resulting events on the calendar after
  confirmation.
- The work schedule is not in any calendar yet: the user enters it through the
  bot, which stores it as weekly recurring events in Feina (and commuting in
  Desplaçament).
- Calendar starts from scratch (2026-11-03): the user's previous iCloud calendars
  were backed up to `.ics` outside the repository and deleted. iCloud accepts
  `MKCALENDAR` and `PROPPATCH` (name, `calendar-color`) over CalDAV, so MARTY's
  calendars were created that way, with names and colours chosen by the user:
  CrossFit (green), Basket (red), Estudiar C++ (blue), Desplaçament (grey) and
  Feina (orange). iCloud refuses (HTTP 403) to delete the last event calendar
  and the legacy "Recordatorios ⚠️" task list, which stays.

## Open questions

These affect Phase 2 to 5 design details and can be answered at any time before
the relevant phase:

1. Which sessions are prescribed by your physical preparation coach: CrossFit only,
   or also the running sessions?
2. How long do the Tuesday and Friday team trainings last, and where does the match
   schedule come from (ICS link, federation website, PDF)?
3. Preferred morning briefing time, weekly report time on Sunday, and quiet hours
   (the database defaults, 07:30 and Sunday 20:00, are placeholders).
4. Maximum study time you consider reasonable on a free weekday and on a weekend day.
