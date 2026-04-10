"""
Tools package — tightly-scoped agent tools and platform dispatcher.

Agents interact with careful-memory ONLY through these tools.
The platform (ToolDispatcher) decides all belief updates.
"""

from careful_memory.tools.dispatcher import ToolDispatcher
from careful_memory.tools.schema import (
    TOOL_SCHEMAS,
    ToolCall,
    ToolName,
    ToolResult,
)

__all__ = [
    "TOOL_SCHEMAS",
    "ToolCall",
    "ToolDispatcher",
    "ToolName",
    "ToolResult",
]
