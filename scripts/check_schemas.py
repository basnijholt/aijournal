"""Generate and verify JSON Schemas for StrictModel subclasses."""

from __future__ import annotations

import argparse
import importlib
import inspect
import json
import os
import pkgutil
import sys
from pathlib import Path
from typing import Any

from aijournal.common.base import StrictModel

SCHEMA_DIR = Path("schemas/core")
BASE_PACKAGE = "aijournal"


def discover_modules() -> list[str]:
    package = importlib.import_module(BASE_PACKAGE)
    modules: list[str] = []
    for module_info in pkgutil.walk_packages(package.__path__, package.__name__ + "."):
        name = module_info.name
        if name.rsplit(".", 1)[-1].startswith("_"):
            continue
        try:
            importlib.import_module(name)
        except Exception as exc:  # pragma: no cover - defensive branch
            print(f"warning: failed to import {name}: {exc}", file=sys.stderr)
            continue
        modules.append(name)
    return sorted(modules)


def collect_models(modules: list[str]) -> list[type[StrictModel]]:
    models: list[type[StrictModel]] = []
    excluded = {StrictModel}
    for module_name in modules:
        module = importlib.import_module(module_name)
        for _, obj in inspect.getmembers(module, inspect.isclass):
            if obj in excluded:
                continue
            if not issubclass(obj, StrictModel):
                continue
            if obj.__module__ != module_name:
                continue
            if obj.__name__.startswith("_"):
                continue
            if inspect.isabstract(obj):
                continue
            models.append(obj)
    models.sort(key=lambda cls: f"{cls.__module__}.{cls.__name__}")
    return models


def schema_path_for(qualified_name: str) -> Path:
    return SCHEMA_DIR / f"{qualified_name}.json"


def ensure_directory(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)


def sort_json(obj: Any) -> Any:
    if isinstance(obj, dict):
        return {key: sort_json(obj[key]) for key in sorted(obj)}
    if isinstance(obj, list):
        return [sort_json(item) for item in obj]
    return obj


def render_schema(model: type[StrictModel]) -> str:
    schema = model.model_json_schema(mode="serialization")
    payload = sort_json(schema)
    return json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def write_if_changed(path: Path, content: str) -> bool:
    if path.exists() and path.read_text(encoding="utf-8") == content:
        return False
    ensure_directory(path)
    path.write_text(content, encoding="utf-8")
    return True


def load_existing_schema_files() -> set[Path]:
    if not SCHEMA_DIR.exists():
        return set()
    return {p for p in SCHEMA_DIR.rglob("*.json") if p.is_file()}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--bless",
        action="store_true",
        help="Allow writing schema changes to disk.",
    )
    args = parser.parse_args()
    should_bless = args.bless or os.environ.get("SCHEMAS_BLESS") == "1"

    modules = discover_modules()
    models = collect_models(modules)

    SCHEMA_DIR.mkdir(parents=True, exist_ok=True)
    existing = load_existing_schema_files()
    seen: set[Path] = set()

    changed: list[Path] = []
    for model in models:
        qualified = f"{model.__module__}.{model.__name__}"
        target = schema_path_for(qualified)
        seen.add(target)
        content = render_schema(model)
        if should_bless:
            if write_if_changed(target, content):
                changed.append(target)
        elif not target.exists() or target.read_text(encoding="utf-8") != content:
            changed.append(target)

    missing = sorted(existing - seen)
    if should_bless:
        for path in missing:
            path.unlink(missing_ok=True)

    if changed or missing:
        mode = "updated" if should_bless else "would change"
        for path in changed:
            print(f"{mode}: {path}")
        for path in missing:
            verb = "removed" if should_bless else "would remove"
            print(f"{verb}: {path}")

    if not should_bless and (changed or missing):
        print("schemas are out of date; run with --bless to update", file=sys.stderr)
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
