"""Microfact service utilities (indexing, consolidation, snapshots)."""

from .index import (
    MicrofactConsolidationStats,
    MicrofactIndex,
    MicrofactMatch,
    MicrofactRebuildResult,
    MicrofactRecord,
)
from .snapshot import load_consolidated_microfacts, select_recurring_facts

__all__ = [
    "MicrofactIndex",
    "MicrofactMatch",
    "MicrofactRecord",
    "MicrofactConsolidationStats",
    "MicrofactRebuildResult",
    "load_consolidated_microfacts",
    "select_recurring_facts",
]
