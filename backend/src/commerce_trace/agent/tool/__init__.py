"""Agent tool definitions and registration."""

from .base import FakeSqlExecutor, SqlExecutor, Tool, ToolContext
from .tools.args import RunSqlArgs, VisualizeDataArgs
from .registry import ToolRegistry, build_default_registry
from .tools.run_sql import RunSqlTool
from .tools.visualize_data import VisualizeDataTool

# Backward-compatible alias
ToolExecutionContext = ToolContext

__all__ = [
    "FakeSqlExecutor",
    "RunSqlArgs",
    "RunSqlTool",
    "SqlExecutor",
    "Tool",
    "ToolContext",
    "ToolExecutionContext",
    "ToolRegistry",
    "VisualizeDataArgs",
    "VisualizeDataTool",
    "build_default_registry",
]
