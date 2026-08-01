"""System prompt components: instruction, schema catalog, and business knowledge."""

from .instruction import SYSTEM_PROMPT
from .knowledge import METRICS, RULES
from .schema import SCHEMA_CATALOG, compact_catalog, schema_fingerprint

__all__ = [
    "METRICS",
    "RULES",
    "SCHEMA_CATALOG",
    "SYSTEM_PROMPT",
    "compact_catalog",
    "schema_fingerprint",
]
