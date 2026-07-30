"""Single-agent request execution.

Only ``Agent`` is public. Request state and answer synthesis remain internal to
the Agent Core module.
"""

from .service import AgentService

__all__ = ["AgentService"]
