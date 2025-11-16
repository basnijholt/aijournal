from __future__ import annotations

from aijournal.commands import profile as profile_cmd
from aijournal.domain.changes import ClaimAtomInput, ClaimProposal
from aijournal.domain.claims import Scope
from aijournal.domain.enums import ClaimMethod, ClaimStatus, ClaimType
from aijournal.domain.evidence import SourceRef
from aijournal.utils import time as time_utils


def _make_proposal(
    statement: str,
    *,
    normalized_id: str = "entry-2006-12-01",
    predicate: str = "prefers",
) -> ClaimProposal:
    claim_input = ClaimAtomInput(
        type=ClaimType.PREFERENCE,
        subject="work",
        predicate=predicate,
        value=statement,
        statement=statement,
        scope=Scope(),
        strength=0.6,
        status=ClaimStatus.ACCEPTED,
        method=ClaimMethod.BEHAVIORAL,
        user_verified=False,
        review_after_days=120,
    )
    return ClaimProposal(
        type=claim_input.type,
        subject=claim_input.subject,
        predicate=claim_input.predicate,
        value=claim_input.value,
        statement=claim_input.statement,
        scope=claim_input.scope,
        strength=claim_input.strength,
        status=claim_input.status,
        method=claim_input.method,
        user_verified=claim_input.user_verified,
        review_after_days=claim_input.review_after_days,
        normalized_ids=[normalized_id],
        evidence=[SourceRef(entry_id=normalized_id, spans=[])],
    )


def test_claim_ids_include_hash_suffix_for_uniqueness() -> None:
    proposal_a = _make_proposal("Prefers morning planning sessions")
    proposal_b = _make_proposal("Prefers evening reflection rituals")

    id_a = profile_cmd._proposal_claim_id(
        proposal_a,
        proposal_a.statement,
        set(),
    )
    id_b = profile_cmd._proposal_claim_id(
        proposal_b,
        proposal_b.statement,
        {id_a},
    )

    assert id_a != id_b
    assert id_a.startswith("entry-2006-12-01-")
    assert id_b.startswith("entry-2006-12-01-")


def test_apply_claim_proposal_keeps_every_statement() -> None:
    timestamp = time_utils.format_timestamp(time_utils.now())
    claims: list = []
    proposals = [
        _make_proposal("Blocks mornings for planning", predicate="plans_mornings"),
        _make_proposal("Schedules evening retros", predicate="retros_evenings"),
    ]

    for proposal in proposals:
        profile_cmd._apply_claim_proposal(claims, proposal, timestamp)

    assert len(claims) == len(proposals)
    assert len({claim.id for claim in claims}) == len(proposals)
