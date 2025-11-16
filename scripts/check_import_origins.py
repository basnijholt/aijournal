"""Check that re-exported imports are avoided within the project.

This script enforces the local style rule:

    If a file executes ``from X import Y`` then ``Y`` must be *defined*
    inside module ``X`` (rather than only being imported there and re-exported).

The checker scans Python files, builds an index of module-level definitions,
and then inspects every ``ImportFrom`` to verify that imported symbols come
straight from the module where they are declared.

Usage (from the repository root):

    uv run python scripts/check_import_origins.py              # default: src/
    uv run python scripts/check_import_origins.py src tests    # custom roots

Use ``--allow-module`` or ``--allow-prefix`` to silence intentional
re-exports (for example package ``__init__`` files that exist solely to
provide a public interface).
"""

from __future__ import annotations

import argparse
import ast
import sys
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterable, Sequence


@dataclass(slots=True)
class ImportRecord:
    """Concrete ``from module import symbol`` usage inside a file."""

    importer_module: str
    importer_path: Path
    target_module: str
    symbol: str
    lineno: int


@dataclass(slots=True)
class ModuleInfo:
    """Metadata collected for a single Python module."""

    module: str
    path: Path
    package_parts: tuple[str, ...]
    defined_names: set[str] = field(default_factory=set)
    imports: list[ImportRecord] = field(default_factory=list)


def parse_args(argv: Sequence[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "paths",
        nargs="*",
        default=["src"],
        help="Directory roots to treat as importable modules (default: src)",
    )
    parser.add_argument(
        "--allow-module",
        action="append",
        default=[],
        help="Exact module names that may re-export symbols.",
    )
    parser.add_argument(
        "--allow-prefix",
        action="append",
        default=[],
        help="Module name prefixes that may re-export symbols (e.g. 'pkg.api').",
    )
    return parser.parse_args(argv)


def iter_python_files(base_dirs: Sequence[Path]) -> Iterable[tuple[Path, Path]]:
    """Yield ``(base_dir, file_path)`` pairs for each Python file."""
    for base in base_dirs:
        if not base.exists():
            msg = f"Base directory does not exist: {base}"
            raise SystemExit(msg)
        if not base.is_dir():
            msg = f"Base path is not a directory: {base}"
            raise SystemExit(msg)

        for path in base.rglob("*.py"):
            if "__pycache__" in path.parts:
                continue
            yield base, path


def module_metadata(path: Path, base: Path) -> tuple[str, tuple[str, ...]]:
    """Return the module name and package parts for ``path`` relative to ``base``."""
    rel = path.relative_to(base).with_suffix("")
    package_parts = tuple(rel.parts)
    module_parts = list(package_parts)
    if module_parts and module_parts[-1] == "__init__":
        module_parts = module_parts[:-1]
    module_name = ".".join(module_parts) if module_parts else path.stem
    return module_name, package_parts


def record_definitions(tree: ast.AST, info: ModuleInfo) -> None:
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            info.defined_names.add(node.name)
        elif isinstance(node, ast.Assign):
            for target in node.targets:
                info.defined_names.update(_assigned_names(target))
        elif isinstance(node, ast.AnnAssign):
            info.defined_names.update(_assigned_names(node.target))


def _assigned_names(node: ast.AST) -> Iterable[str]:
    if isinstance(node, ast.Name):
        yield node.id
    elif isinstance(node, (ast.Tuple, ast.List)):
        for element in node.elts:
            yield from _assigned_names(element)


def resolve_import_module(info: ModuleInfo, node: ast.ImportFrom) -> str | None:
    level = node.level or 0
    module_hint = node.module or ""

    if level == 0:
        return module_hint or None

    if not info.package_parts:
        return None

    parts = list(info.package_parts)
    if len(parts) < level:
        return None

    base_parts = parts[: len(parts) - level]
    if module_hint:
        base_parts.extend(module_hint.split("."))

    if not base_parts:
        return None

    return ".".join(base_parts)


def record_imports(tree: ast.AST, info: ModuleInfo) -> None:
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            if any(alias.name == "*" for alias in node.names):
                continue

            target_module = resolve_import_module(info, node)
            if not target_module:
                continue

            for alias in node.names:
                symbol = alias.name
                if not symbol or symbol == "*":
                    continue

                record = ImportRecord(
                    importer_module=info.module,
                    importer_path=info.path,
                    target_module=target_module,
                    symbol=symbol,
                    lineno=node.lineno,
                )
                info.imports.append(record)


def collect_modules(base_dirs: Sequence[Path]) -> dict[str, ModuleInfo]:
    module_infos: dict[str, ModuleInfo] = {}

    files: list[tuple[Path, Path]] = list(iter_python_files(base_dirs))
    for base, path in files:
        module, package_parts = module_metadata(path, base)
        if not module:
            continue

        if module in module_infos:
            continue

        info = ModuleInfo(module=module, path=path, package_parts=package_parts)
        module_infos[module] = info

    for info in module_infos.values():
        source = info.path.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=str(info.path))
        record_definitions(tree, info)
        record_imports(tree, info)

    return module_infos


def build_definition_index(module_infos: dict[str, ModuleInfo]) -> dict[str, set[str]]:
    index: dict[str, set[str]] = defaultdict(set)
    for module, info in module_infos.items():
        for name in info.defined_names:
            index[name].add(module)
    return index


def module_allowed(module: str, exact: set[str], prefixes: Sequence[str]) -> bool:
    if module in exact:
        return True
    return any(module == prefix or module.startswith(prefix + ".") for prefix in prefixes)


def find_problems(
    module_infos: dict[str, ModuleInfo],
    definition_index: dict[str, set[str]],
    allow_exact: set[str],
    allow_prefixes: Sequence[str],
) -> list[str]:
    problems: list[str] = []
    cwd = Path.cwd()

    for info in module_infos.values():
        for record in info.imports:
            target_info = module_infos.get(record.target_module)
            if target_info is None:
                continue

            if module_allowed(record.target_module, allow_exact, allow_prefixes):
                continue

            submodule_name = f"{record.target_module}.{record.symbol}"
            if submodule_name in module_infos:
                continue

            if record.symbol in target_info.defined_names:
                continue

            defining_modules = definition_index.get(record.symbol, set())
            defined_hint = ", ".join(sorted(defining_modules)) if defining_modules else "unknown"

            try:
                rel_path = record.importer_path.relative_to(cwd)
            except ValueError:
                rel_path = record.importer_path

            problems.append(
                f"{rel_path}:{record.lineno}: "
                f"{record.symbol!r} is imported from {record.target_module} but not defined there."
                f" Defined inside: {defined_hint}",
            )

    return sorted(problems)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv or sys.argv[1:])
    base_dirs = [Path(p).resolve() for p in args.paths]
    allow_exact = set(args.allow_module or [])
    allow_prefixes = list(args.allow_prefix or [])

    module_infos = collect_modules(base_dirs)
    definition_index = build_definition_index(module_infos)
    problems = find_problems(module_infos, definition_index, allow_exact, allow_prefixes)

    if problems:
        print("Found import origin violations:")
        for line in problems:
            print(f"  - {line}")
        return 1

    print("All from-imports resolve to locally defined symbols.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
