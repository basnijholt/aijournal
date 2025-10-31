"""Shared enum types used across aijournal domain models."""

from __future__ import annotations

from enum import StrEnum


class ClaimType(StrEnum):
    PREFERENCE = "preference"
    VALUE = "value"
    GOAL = "goal"
    BOUNDARY = "boundary"
    TRAIT = "trait"
    HABIT = "habit"
    AVERSION = "aversion"
    SKILL = "skill"


class ClaimStatus(StrEnum):
    ACCEPTED = "accepted"
    TENTATIVE = "tentative"
    REJECTED = "rejected"


class ClaimMethod(StrEnum):
    SELF_REPORT = "self_report"
    INFERRED = "inferred"
    BEHAVIORAL = "behavioral"


class FacetOperation(StrEnum):
    SET = "set"
    REMOVE = "remove"
    MERGE = "merge"


class ClaimEventAction(StrEnum):
    UPSERT = "upsert"
    UPDATE = "update"
    DELETE = "delete"
    CONFLICT = "conflict"
    STRENGTH_DELTA = "strength_delta"


class FeedbackDirection(StrEnum):
    UP = "up"
    DOWN = "down"
