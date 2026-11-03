from typing import Literal

from pydantic import Field

from app.core.tools import Tool, ToolArgs, ToolContext, ToolError
from app.database.models import Memory, MemoryCategory, MemoryKind, MemorySource, ToolPolicy
from app.services.memory import MemoryNotFoundError, MemoryService

CategoryName = Literal["training", "study", "work", "personal", "preference", "general"]


def _summary(memory: Memory) -> dict[str, object]:
    return {
        "id": memory.id,
        "kind": memory.kind.value,
        "category": memory.category.value,
        "content": memory.content,
        "importance": memory.importance,
    }


class RememberArgs(ToolArgs):
    content: str = Field(
        min_length=3,
        max_length=500,
        description="The fact, in Spanish, short and self-contained, e.g. "
        "'Su color favorito es el azul'.",
    )
    kind: Literal["profile", "long_term"] = Field(
        description="profile: stable facts and preferences about the user. "
        "long_term: objectives, decisions and context worth recalling later."
    )
    category: CategoryName
    importance: int = Field(default=3, ge=1, le=5, description="1 = trivia, 5 = essential.")


async def _remember(ctx: ToolContext, args: RememberArgs) -> dict[str, object]:
    memory, created = await MemoryService(ctx.session).remember(
        ctx.user.id,
        content=args.content,
        kind=MemoryKind(args.kind),
        category=MemoryCategory(args.category),
        importance=args.importance,
        source=MemorySource.USER_STATED,
        source_message_id=ctx.message_id,
        actor=ctx.actor,
        agent_action_id=ctx.agent_action_id,
    )
    return {"status": "stored" if created else "already_known", "memory": _summary(memory)}


class ListMemoriesArgs(ToolArgs):
    query: str | None = Field(
        default=None, max_length=200, description="Words to search for. Omit to list all."
    )
    category: CategoryName | None = None
    limit: int = Field(default=15, ge=1, le=30)


async def _list_memories(ctx: ToolContext, args: ListMemoriesArgs) -> dict[str, object]:
    memories = await MemoryService(ctx.session).find(
        ctx.user.id,
        query=args.query,
        category=MemoryCategory(args.category) if args.category else None,
        limit=args.limit,
    )
    return {"memories": [_summary(m) for m in memories]}


class UpdateMemoryArgs(ToolArgs):
    memory_id: int
    new_content: str = Field(min_length=3, max_length=500)


async def _update_memory(ctx: ToolContext, args: UpdateMemoryArgs) -> dict[str, object]:
    try:
        memory = await MemoryService(ctx.session).correct(
            ctx.user.id,
            args.memory_id,
            new_content=args.new_content,
            source_message_id=ctx.message_id,
            actor=ctx.actor,
            agent_action_id=ctx.agent_action_id,
        )
    except MemoryNotFoundError as exc:
        raise ToolError(str(exc)) from exc
    return {"status": "updated", "replaces_memory_id": args.memory_id, "memory": _summary(memory)}


class ForgetMemoryArgs(ToolArgs):
    memory_id: int


async def _forget_preview(ctx: ToolContext, args: ForgetMemoryArgs) -> str:
    try:
        memory = await MemoryService(ctx.session).get(ctx.user.id, args.memory_id)
    except MemoryNotFoundError as exc:
        raise ToolError(str(exc)) from exc
    return f"Olvidar este recuerdo: «{memory.content}»"


async def _forget_memory(ctx: ToolContext, args: ForgetMemoryArgs) -> dict[str, object]:
    try:
        await MemoryService(ctx.session).forget(
            ctx.user.id,
            args.memory_id,
            now=ctx.now,
            actor=ctx.actor,
            agent_action_id=ctx.agent_action_id,
        )
    except MemoryNotFoundError as exc:
        raise ToolError(str(exc)) from exc
    return {"status": "forgotten", "memory_id": args.memory_id}


MEMORY_TOOLS = (
    Tool(
        name="remember",
        description=(
            "Store a durable fact the user stated about themselves or explicitly asked you to "
            "remember (preferences, routines, goals, decisions). Never store guesses or "
            "inferences. If it corrects a known memory, use update_memory instead."
        ),
        args_model=RememberArgs,
        policy=ToolPolicy.WRITE,
        handler=_remember,
    ),
    Tool(
        name="list_memories",
        description="Search or list what you have stored about the user, with memory ids.",
        args_model=ListMemoriesArgs,
        policy=ToolPolicy.READ,
        handler=_list_memories,
    ),
    Tool(
        name="update_memory",
        description=(
            "Correct a stored memory when the user says it changed or was wrong. The old "
            "version is kept in history."
        ),
        args_model=UpdateMemoryArgs,
        policy=ToolPolicy.WRITE,
        handler=_update_memory,
    ),
    Tool(
        name="forget_memory",
        description=(
            "Request deleting a memory. The user must confirm with a button before anything "
            "is deleted."
        ),
        args_model=ForgetMemoryArgs,
        policy=ToolPolicy.CONFIRM,
        handler=_forget_memory,
        preview=_forget_preview,
    ),
)
