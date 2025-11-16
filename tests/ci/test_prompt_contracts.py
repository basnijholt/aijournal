"""Ensure Typer commands keep response_model wired to prompt DTOs."""

from __future__ import annotations

import ast
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
COMMANDS_DIR = PROJECT_ROOT / "src" / "aijournal" / "commands"

ALLOWED_PREFIXES = ("aijournal.domain.prompts.",)
ALLOWED_EXTRAS = {
    "aijournal.domain.facts.DailySummary",
    "aijournal.models.derived.AdviceCard",
}


def _gather_aliases(tree: ast.AST) -> dict[str, str]:
    aliases: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                target = alias.name
                asname = alias.asname or target.split(".")[-1]
                aliases[asname] = target
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            for alias in node.names:
                if alias.name == "*":
                    continue
                target = f"{module}.{alias.name}" if module else alias.name
                asname = alias.asname or alias.name
                aliases[asname] = target
    return aliases


def _resolve_name(node: ast.AST, aliases: dict[str, str]) -> str | None:
    if isinstance(node, ast.Name):
        return aliases.get(node.id, node.id)
    if isinstance(node, ast.Attribute):
        base = _resolve_name(node.value, aliases)
        if base is None:
            return None
        return f"{base}.{node.attr}"
    if isinstance(node, ast.Subscript):  # typing.Annotated[PromptDTO, ...]
        return _resolve_name(node.value, aliases)
    return None


def _is_allowed(path: str | None) -> bool:
    if path is None:
        return False
    return path.startswith(ALLOWED_PREFIXES) or path in ALLOWED_EXTRAS


def test_response_models_use_prompt_dtos() -> None:
    violations: list[str] = []

    for file_path in sorted(COMMANDS_DIR.rglob("*.py")):
        tree = ast.parse(file_path.read_text(encoding="utf-8"))
        aliases = _gather_aliases(tree)
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            for keyword in node.keywords or []:
                if keyword.arg != "response_model":
                    continue
                resolved = _resolve_name(keyword.value, aliases)
                if _is_allowed(resolved):
                    continue
                name = ast.unparse(keyword.value) if hasattr(ast, "unparse") else str(keyword.value)
                violations.append(
                    f"{file_path.relative_to(PROJECT_ROOT)}:{keyword.value.lineno} "
                    f"uses disallowed response_model `{name}` (resolved to {resolved!r}).",
                )

    assert not violations, "\n".join(["Found response_model violations:", *violations])
