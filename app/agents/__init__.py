"""Tools the LLM may call, grouped by module."""

from app.agents.memory import MEMORY_TOOLS
from app.core.tools import Tool

ALL_TOOLS: tuple[Tool, ...] = (*MEMORY_TOOLS,)

__all__ = ["ALL_TOOLS"]
