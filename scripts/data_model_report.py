"""Inventory of aijournal data models.

This script imports every module inside the ``aijournal`` package and builds a
textual report of all classes that are either Pydantic ``BaseModel``
subclasses or ``dataclass`` definitions. The output groups models by their
field signatures so we can spot overlapping structures quickly.

Run it from the repository root:

    uv run python scripts/data_model_report.py

"""

from __future__ import annotations

import contextlib
import dataclasses
import importlib
import inspect
import pkgutil
import sys
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel

if TYPE_CHECKING:
    from collections.abc import Iterable


@dataclass(slots=True)
class FieldSummary:
    """Flat representation of a model field."""

    name: str
    type_repr: str
    default_repr: str


@dataclass(slots=True)
class ClassSummary:
    """Metadata captured for either a Pydantic model or dataclass."""

    qualified_name: str
    module: str
    kind: str
    fields: list[FieldSummary]
    file_path: str

    @property
    def signature(self) -> tuple[tuple[str, str], ...]:
        """Similarity signature used for grouping (field name + type)."""
        return tuple((field.name, field.type_repr) for field in self.fields)


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    src_dir = root / "src"
    if str(src_dir) not in sys.path:
        sys.path.insert(0, str(src_dir))

    package = importlib.import_module("aijournal")
    module_names = sorted(
        name for _, name, _ in pkgutil.walk_packages(package.__path__, package.__name__ + ".")
    )

    classes: list[ClassSummary] = []
    for module_name in module_names:
        module = importlib.import_module(module_name)
        for cls_name, cls in inspect.getmembers(module, inspect.isclass):
            if cls.__module__ != module_name:
                continue
            if cls is BaseModel:
                continue
            # Get the file path where the class is defined
            try:
                file_path = inspect.getfile(cls)
                # Make path relative to root for cleaner output
                with contextlib.suppress(ValueError):
                    file_path = str(Path(file_path).relative_to(root))
            except (TypeError, OSError):
                file_path = "<unknown>"

            if issubclass(cls, BaseModel):
                classes.append(
                    ClassSummary(
                        qualified_name=f"{module_name}.{cls_name}",
                        module=module_name,
                        kind="pydantic",
                        fields=list(_iter_pydantic_fields(cls)),
                        file_path=file_path,
                    ),
                )
            elif dataclasses.is_dataclass(cls):
                classes.append(
                    ClassSummary(
                        qualified_name=f"{module_name}.{cls_name}",
                        module=module_name,
                        kind="dataclass",
                        fields=list(_iter_dataclass_fields(cls)),
                        file_path=file_path,
                    ),
                )

    pydantic_classes = [c for c in classes if c.kind == "pydantic"]
    dataclass_classes = [c for c in classes if c.kind == "dataclass"]

    print("# Data Structure Inventory\n")
    print(
        f"Discovered {len(pydantic_classes)} Pydantic models and "
        f"{len(dataclass_classes)} dataclasses across {len(module_names)} modules.\n",
    )

    _print_group_report("Pydantic Models", pydantic_classes)
    _print_group_report("Dataclasses", dataclass_classes)


def _print_group_report(title: str, summaries: Iterable[ClassSummary]) -> None:
    grouped: dict[tuple[tuple[str, str], ...], list[ClassSummary]] = defaultdict(list)
    for summary in summaries:
        grouped[summary.signature].append(summary)

    print(f"## {title}\n")
    print(
        f"{len(summaries)} total · {len(grouped)} unique field signatures\n",
    )
    for index, (signature, items) in enumerate(
        sorted(grouped.items(), key=lambda item: (-len(item[1]), item[0])),
        start=1,
    ):
        fields_display = ", ".join(f"{name}: {type_repr}" for name, type_repr in signature)
        print(f"### Group {index} ({len(items)} classes)\n")
        print(f"Fields: {fields_display or '<no fields>'}\n")
        for summary in sorted(items, key=lambda c: c.qualified_name):
            print(f"- {summary.qualified_name} ({summary.file_path})")
        print()


def _iter_pydantic_fields(cls: type[BaseModel]) -> Iterable[FieldSummary]:
    for name, field in cls.model_fields.items():
        type_repr = _repr_annotation(field.annotation)
        if field.is_required():
            default_repr = "<required>"
        elif field.default_factory is not None:
            factory_name = getattr(field.default_factory, "__name__", repr(field.default_factory))
            default_repr = f"factory({factory_name})"
        else:
            default_repr = repr(field.default)
        yield FieldSummary(name=name, type_repr=type_repr, default_repr=default_repr)


def _iter_dataclass_fields(cls: type[Any]) -> Iterable[FieldSummary]:
    for field in dataclasses.fields(cls):
        type_repr = _repr_annotation(field.type)
        if field.default is not dataclasses.MISSING:
            default_repr = repr(field.default)
        elif field.default_factory is not dataclasses.MISSING:  # type: ignore[attr-defined]
            factory = field.default_factory  # type: ignore[attr-defined]
            factory_name = getattr(factory, "__name__", repr(factory))
            default_repr = f"factory({factory_name})"
        else:
            default_repr = "<required>"
        yield FieldSummary(name=field.name, type_repr=type_repr, default_repr=default_repr)


def _repr_annotation(annotation: Any) -> str:
    if annotation is None:
        return "None"
    if isinstance(annotation, type):
        return annotation.__name__
    return repr(annotation)


if __name__ == "__main__":
    main()
