"""Single-agent request execution.

Only ``Agent`` is public. Request state and answer synthesis remain internal to
the Agent Core module.
"""

from .core import Agent

__all__ = ["Agent"]
