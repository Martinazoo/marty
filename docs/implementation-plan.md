# Implementation Plan

Each phase ends with a runnable system, passing tests, updated documentation and a
status report (what works, what was tested, what is missing, manual setup). The
next phase starts only after review.

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

## Phase 1 - Functional MARTY Core

| Milestone | Content |
| --- | --- |
| 1.1 Core data | ORM models and first migration for `users`, `user_preferences`, `conversations`, `messages`, `memories`, `agent_actions`, `audit_logs`; repositories; repository tests against PostgreSQL. |
| 1.2 Telegram bot | PTB lifecycle in the FastAPI lifespan, allowlist and private-chat filters, `/start`, `/help`, `/settings` (read-only), `/pause`, `/resume`, message persistence, global error handler. |
| 1.3 AI adapters | `OpenAICompatibleProvider` (Gemini, Groq, Ollama), error mapping, provider factory from settings, model-listing script, tests with `httpx.MockTransport`, one manual check per real provider. |
| 1.4 Orchestrator | Tool registry with policies and Pydantic validation, tool loop with iteration cap, context builder, first tools (`remember`, `list_memories`, `forget_memory` with confirmation, `current_datetime`), inline-button confirmation flow, audit logging, Spanish system prompt. |
| 1.5 Conversation upkeep | Conversation boundaries by idle time, rolling summaries (skipped when providers are saturated). |

Acceptance: you chat with MARTY in Spanish on Telegram; it remembers facts you
told it after a restart; strangers are rejected; when every provider fails it says
so and the commands still work.

## Phase 2 - Apple Calendar

| Milestone | Content |
| --- | --- |
| 2.1 Setup | CalDAV connection to iCloud with an app-specific password, calendar discovery, a controlled setup step where you choose which calendars count for availability. Create the three MARTY calendars if iCloud accepts it through CalDAV (to be verified); otherwise you create them in the Calendar app and MARTY finds them by name. |
| 2.2 Read sync | Periodic sync of a rolling window into `calendar_events`, with recurring events expanded. |
| 2.3 Availability | Free/busy computation from calendar events plus `weekly_commitments` (work, commuting) with buffers. |
| 2.4 Managed events | Create, update and delete MARTY events with deterministic UIDs, ETags and content hashes (idempotent, no duplicates). Personal events are never modified. |
| 2.5 Commands | `/calendar`, `/today`, `/week` with real data; overlap detection. |

## Phase 3 - Personal Trainer

| Milestone | Content |
| --- | --- |
| 3.1 Weekly plan | Enter the weekly template through a guided conversation (stored in the database, never in git); weekly materialisation into dated sessions. |
| 3.2 Matches | ICS feed import (idempotent) and manual registration ("El sábado que viene tengo partido"). |
| 3.3 Logging | Natural-language workout reports: duration, RPE, fatigue, unplanned workouts, cancellations. |
| 3.4 Load rules | Session load (duration × RPE), intensity per activity type, density rules (hard days in a row, intensity before a match), all explained to the user and treated as heuristics, not medical advice. |
| 3.5 Change evaluation | The "Wednesday basketball instead of running" flow: evaluate the rest of the week, adjust optional activities, request approval for coach sessions, sync approved changes to the calendar. |
| 3.6 Iron Harder (interim) | Import from pasted text, documents and screenshots into `workout_prescriptions`, original content preserved. Screenshot reading needs a multimodal model and is optional. |

## Phase 4 - C++ Professor

| Milestone | Content |
| --- | --- |
| 4.1 Import | Parse `Roadmap_CPP_60_Sessions_Expanded_Guide_EN.docx` with `python-docx` into blocks and sessions, verbatim. Idempotent by file hash. Report parsing anomalies instead of guessing. |
| 4.2 Progress | Study reports ("He estudiado C++ una hora"), session completion, `/study`, `/progress`. No quizzes. |
| 4.3 Planner | Distribute sessions over real availability with daily caps, nothing demanding during commuting, lighter load on busy days and match days. Proposed plan, then calendar sync after approval. |
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

## Open questions

These affect Phase 1 to 3 design details and can be answered at any time before
the relevant phase:

1. Is this machine (a laptop with an i7-1255U) the final server, or will MARTY run
   on a separate Ubuntu Server?
2. LLM provider: is it acceptable to send schedule and training data to the Gemini
   free tier given its data-use terms, or should Groq be primary?
3. Is your work schedule already in Apple Calendar, or should MARTY store it as
   recurring commitments?
4. Which sessions are prescribed by your physical preparation coach: CrossFit only,
   or also the running sessions?
5. How long do the Tuesday and Friday team trainings last, and where does the match
   schedule come from (ICS link, federation website, PDF)?
6. Preferred morning briefing time, weekly report time on Sunday, and quiet hours.
7. Maximum study time you consider reasonable on a free weekday and on a weekend day.
