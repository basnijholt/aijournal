"""String utilities shared across capture/ingest flows."""

from __future__ import annotations

import unicodedata

INVISIBLE_PREFIX_CHARACTERS = {
    "\ufeff",  # UTF-8 BOM / zero-width no-break space
    "\u200b",  # zero-width space
    "\u200c",  # zero-width non-joiner
    "\u200d",  # zero-width joiner
    "\u2060",  # word joiner
    "\u2061",  # function application
    "\u2062",  # invisible times
    "\u2063",  # invisible separator
    "\u2064",  # invisible plus
    "\u202a",  # left-to-right embedding
    "\u202b",  # right-to-left embedding
    "\u202c",  # pop directional formatting
    "\u202d",  # left-to-right override
    "\u202e",  # right-to-left override
}


def strip_invisible_prefix(text: str) -> str:
    """Remove invisible control characters that precede visible content."""
    index = 0
    length = len(text)
    while index < length:
        char = text[index]
        if char == "\x00":  # stray NULL bytes from some exports
            index += 1
            continue
        if char in INVISIBLE_PREFIX_CHARACTERS:
            index += 1
            continue
        if unicodedata.category(char) == "Cf":
            index += 1
            continue
        break
    if index:
        return text[index:]
    return text


__all__ = ["strip_invisible_prefix"]
