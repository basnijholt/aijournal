"""Compatibility shim re-exporting strict claim models from `aijournal.domain.claims`."""

from __future__ import annotations

import warnings

from aijournal.domain.claims import (
    ClaimAtom as _ClaimAtom,
)
from aijournal.domain.claims import (
    ClaimAtomsFile as _ClaimAtomsFile,
)
from aijournal.domain.claims import ClaimSource, ClaimSourceSpan
from aijournal.domain.claims import (
    Provenance as _Provenance,
)
from aijournal.domain.claims import (
    Scope as _Scope,
)
from aijournal.domain.enums import ClaimMethod, ClaimStatus, ClaimType

warnings.warn(
    "Import claim models from `aijournal.domain.claims` instead of `aijournal.models.claim_atoms`.",
    DeprecationWarning,
    stacklevel=2,
)

Scope = _Scope
Provenance = _Provenance
ClaimAtom = _ClaimAtom
ClaimAtomsFile = _ClaimAtomsFile

__all__ = [
    "ClaimAtom",
    "ClaimAtomsFile",
    "ClaimMethod",
    "ClaimSource",
    "ClaimSourceSpan",
    "ClaimStatus",
    "ClaimType",
    "Provenance",
    "Scope",
]
