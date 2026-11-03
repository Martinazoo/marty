"""Tool registry, policies and executor.

The LLM can only act through tools registered here. Arguments are validated
with each tool's Pydantic model before anything runs. Policies:

- read: runs immediately.
- write: runs immediately, recorded in `agent_actions`, audited by the service.
- confirm: stored as a pending action; runs only when the user presses the
  Telegram button (`ToolExecutor.decide`), which the model cannot do.
"""

import json
import logging
from collections.abc import Awaitable, Callable, Iterable
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from typing import Any

from pydantic import BaseModel, ConfigDict, ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import ActionStatus, Actor, AgentAction, ToolPolicy, User
from app.database.repositories import ActionRepository, ConversationRepository
from app.integrations.ai import ToolCall, ToolSpec

logger = logging.getLogger(__name__)


class ToolArgs(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class ToolError(Exception):
    """An expected failure, reported back to the model so it can react."""


@dataclass(frozen=True)
class ToolContext:
    session: AsyncSession
    user: User
    now: datetime
    actor: Actor = Actor.MARTY
    message_id: int | None = None
    agent_action_id: int | None = None


Handler = Callable[[ToolContext, Any], Awaitable[dict[str, Any]]]
Preview = Callable[[ToolContext, Any], Awaitable[str]]


@dataclass(frozen=True)
class Tool:
    name: str
    description: str
    args_model: type[ToolArgs]
    policy: ToolPolicy
    handler: Handler
    # Confirm tools: a Spanish sentence telling the user exactly what will happen.
    preview: Preview | None = None

    def __post_init__(self) -> None:
        if self.policy is ToolPolicy.CONFIRM and self.preview is None:
            raise ValueError(f"confirm tool {self.name!r} needs a preview")

    def spec(self) -> ToolSpec:
        return ToolSpec(self.name, self.description, _clean_schema(self.args_model))


def _clean_schema(model: type[BaseModel]) -> dict[str, Any]:
    """JSON Schema without `title` noise. Argument models use `Literal`, not
    `Enum`, so the schema has no `$ref`s that some providers reject."""

    def strip(node: Any) -> Any:
        if isinstance(node, dict):
            return {k: strip(v) for k, v in node.items() if k != "title"}
        if isinstance(node, list):
            return [strip(v) for v in node]
        return node

    return strip(model.model_json_schema())


class ToolRegistry:
    def __init__(self, tools: Iterable[Tool] = ()) -> None:
        self._tools: dict[str, Tool] = {}
        for tool in tools:
            self.register(tool)

    def register(self, tool: Tool) -> None:
        if tool.name in self._tools:
            raise ValueError(f"duplicate tool {tool.name!r}")
        self._tools[tool.name] = tool

    def get(self, name: str) -> Tool | None:
        return self._tools.get(name)

    def specs(self) -> tuple[ToolSpec, ...]:
        return tuple(tool.spec() for tool in self._tools.values())


@dataclass(frozen=True)
class ToolOutcome:
    content: str
    pending_action: AgentAction | None = None


@dataclass(frozen=True)
class Decision:
    """`status` is None when the action does not exist for this user. `changed` is
    False when the action had already been decided before this press."""

    status: ActionStatus | None
    explanation: str | None
    changed: bool = False


def _json(payload: dict[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False, default=str)


def _error(message: str) -> str:
    return _json({"error": message})


class ToolExecutor:
    def __init__(self, registry: ToolRegistry, *, pending_ttl: timedelta) -> None:
        self._registry = registry
        self._pending_ttl = pending_ttl

    async def run(self, ctx: ToolContext, call: ToolCall) -> ToolOutcome:
        tool = self._registry.get(call.name)
        if tool is None:
            return ToolOutcome(_error(f"unknown tool {call.name!r}"))
        try:
            args = tool.args_model.model_validate(call.arguments)
        except ValidationError as exc:
            problems = [
                {"field": ".".join(map(str, e["loc"])), "problem": e["msg"]} for e in exc.errors()
            ]
            return ToolOutcome(_json({"error": "invalid arguments", "details": problems}))

        if tool.policy is ToolPolicy.READ:
            try:
                return ToolOutcome(_json(await tool.handler(ctx, args)))
            except ToolError as exc:
                return ToolOutcome(_error(str(exc)))
            except Exception:
                logger.exception("Read tool failed", extra={"tool": tool.name})
                return ToolOutcome(_error("internal error"))

        actions = ActionRepository(ctx.session)
        if tool.policy is ToolPolicy.CONFIRM:
            try:
                explanation = await tool.preview(ctx, args)  # type: ignore[misc]
            except ToolError as exc:
                return ToolOutcome(_error(str(exc)))
            arguments = args.model_dump(mode="json")
            previous = await actions.latest_identical(ctx.user.id, tool.name, arguments)
            if previous is not None and self._still_pending(previous, ctx.now):
                content = _json(
                    {
                        "status": "pending_confirmation",
                        "action_id": previous.id,
                        "will_do": previous.explanation,
                        "note": "This exact request was already waiting for the user; its "
                        "button is shown again. Nothing has changed yet.",
                    }
                )
                return ToolOutcome(content, pending_action=previous)
            if previous is not None and await self._just_rejected(ctx, previous):
                content = _json(
                    {
                        "status": "not_requested",
                        "reason": "The user pressed Cancel on this exact request after their "
                        "previous message, so it stays as it is. Do not request it again and "
                        "do not ask about it. If the user's latest message clearly insists on "
                        "it, tell them to ask once more.",
                    }
                )
                return ToolOutcome(content)
            action = await actions.add(
                AgentAction(
                    user_id=ctx.user.id,
                    message_id=ctx.message_id,
                    tool_name=tool.name,
                    arguments=arguments,
                    policy=ToolPolicy.CONFIRM,
                    status=ActionStatus.PENDING_CONFIRMATION,
                    explanation=explanation,
                    expires_at=ctx.now + self._pending_ttl,
                )
            )
            content = _json(
                {
                    "status": "pending_confirmation",
                    "action_id": action.id,
                    "will_do": explanation,
                    "note": "Nothing has changed yet. A confirmation button is shown to the "
                    "user; do not say it is done.",
                }
            )
            return ToolOutcome(content, pending_action=action)

        action = await actions.add(
            AgentAction(
                user_id=ctx.user.id,
                message_id=ctx.message_id,
                tool_name=tool.name,
                arguments=args.model_dump(mode="json"),
                policy=ToolPolicy.WRITE,
                status=ActionStatus.EXECUTED,
                executed_at=ctx.now,
            )
        )
        result = await self._execute(tool, replace(ctx, agent_action_id=action.id), args, action)
        return ToolOutcome(_json(result))

    async def decide(self, ctx: ToolContext, action_id: int, *, approve: bool) -> Decision:
        """Apply the user's button press to a pending action."""
        action = await ActionRepository(ctx.session).get_for_update(ctx.user.id, action_id)
        if action is None:
            return Decision(None, None)
        if action.status is not ActionStatus.PENDING_CONFIRMATION:
            return Decision(action.status, action.explanation)
        action.decided_at = ctx.now
        if action.expires_at is not None and action.expires_at <= ctx.now:
            action.status = ActionStatus.EXPIRED
        elif not approve:
            action.status = ActionStatus.REJECTED
        elif (tool := self._registry.get(action.tool_name)) is None:
            action.status = ActionStatus.FAILED
            action.error = "tool no longer exists"
        else:
            args = tool.args_model.model_validate(action.arguments)
            action.status = ActionStatus.EXECUTED
            action.executed_at = ctx.now
            exec_ctx = replace(ctx, actor=Actor.USER, agent_action_id=action.id)
            await self._execute(tool, exec_ctx, args, action)
        return Decision(action.status, action.explanation, changed=True)

    @staticmethod
    def _still_pending(action: AgentAction, now: datetime) -> bool:
        return action.status is ActionStatus.PENDING_CONFIRMATION and (
            action.expires_at is None or action.expires_at > now
        )

    @staticmethod
    async def _just_rejected(ctx: ToolContext, action: AgentAction) -> bool:
        """Rejected since the user's previous message, i.e. the current message is
        the first one after pressing Cancel."""
        if action.status is not ActionStatus.REJECTED or action.decided_at is None:
            return False
        if ctx.message_id is None:
            return True
        previous = await ConversationRepository(ctx.session).previous_user_message(
            ctx.user.id, ctx.message_id
        )
        return previous is None or action.decided_at >= previous.created_at

    async def _execute(
        self, tool: Tool, ctx: ToolContext, args: ToolArgs, action: AgentAction
    ) -> dict[str, Any]:
        """Run the handler in a savepoint, so a failure undoes its partial changes
        while the action row keeps the failure."""
        try:
            async with ctx.session.begin_nested():
                result = await tool.handler(ctx, args)
        except ToolError as exc:
            action.status, action.error = ActionStatus.FAILED, str(exc)
            return {"error": str(exc)}
        except Exception as exc:
            logger.exception("Tool failed", extra={"tool": tool.name})
            action.status, action.error = ActionStatus.FAILED, type(exc).__name__
            return {"error": "internal error"}
        action.result = result
        return result
