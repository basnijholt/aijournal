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
    "MicrofactConsolidationStats",
    "MicrofactIndex",
    "MicrofactMatch",
    "MicrofactRebuildResult",
    "MicrofactRecord",
    "load_consolidated_microfacts",
    "select_recurring_facts",
]
