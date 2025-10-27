"""Stateless helpers shared across CLI, services, and pipelines."""

from .coercion import coerce_float, coerce_int

__all__ = ["coerce_float", "coerce_int"]
