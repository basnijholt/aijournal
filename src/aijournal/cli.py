"""Typer CLI entrypoint for aijournal."""

from __future__ import annotations

import copy
import json
import os
import random
import re
import sqlite3
from array import array
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from math import ceil, exp
from pathlib import Path
from string import Template
from textwrap import dedent
from typing import TYPE_CHECKING, Any, Literal

import numpy as np
import typer
import yaml
from annoy import AnnoyIndex

from aijournal.ingest_agent import (
    AgentSettings,
    IngestResult,
    IngestSection,
    build_ingest_agent,
    ingest_with_agent,
)
from aijournal.schema import SchemaValidationError, validate_schema
from aijournal.services import LLMResponseError, OllamaConfig, OllamaTaskRunner
from aijournal.services.embedding import EmbeddingBackend

if TYPE_CHECKING:
    from collections.abc import Callable

    from agno.agent import Agent

app = typer.Typer(help="Local-first personal journal utilities.")
profile_app = typer.Typer(help="Profile utilities.")
ollama_app = typer.Typer(help="Ollama helpers (fake mode only).")
app.add_typer(profile_app, name="profile")
app.add_typer(ollama_app, name="ollama")
index_app = typer.Typer(help="Retrieval index utilities.")
app.add_typer(index_app, name="index")
persona_app = typer.Typer(help="Persona utilities.")
app.add_typer(persona_app, name="persona")


@app.callback()
def main() -> None:
    """Aijournal command-line interface."""
    # Intentionally empty; commands provide functionality.
    return


AUTHORITATIVE_DIRS = (
    "config",
    "profile",
    "data",
    "data/journal",
    "data/normalized",
    "data/raw",
    "data/manifest",
    "prompts",
)

DERIVED_DIRS = (
    "derived",
    "derived/summaries",
    "derived/microfacts",
    "derived/profile_suggestions",
    "derived/interviews",
    "derived/advice",
    "derived/persona",
    "derived/index",
    "derived/pending",
    "derived/pending/profile_updates",
)

SEED_FILES = {
    "config/config.yaml": dedent(
        """
        model: "llama3.1:8b-instruct"
        temperature: 0.2
        seed: 42
        paths:
          data: "data"
          profile: "profile"
          derived: "derived"
          prompts: "prompts"
        impact_weights:
          values_goals: 1.5
          decision_style: 1.3
          affect_energy: 1.2
          traits: 1.0
          social: 0.9
          claims: 1.0
          claim_types:
            value: 1.4
            goal: 1.4
            boundary: 1.3
            trait: 1.2
            preference: 1.0
            habit: 0.9
            aversion: 1.1
            skill: 1.0
        advisor:
          max_recos: 3
          include_risks: true
        token_estimator:
          char_per_token: 4.2
        persona:
          token_budget: 1200
          max_claims: 24
          min_claims: 8
        """,
    ).strip()
    + "\n",
    "profile/self_profile.yaml": dedent(
        """
        traits:
          big_five:
            openness: {score: 0.74, method: self_report, user_verified: true}
            conscientiousness: {score: 0.68, method: inferred}
            extraversion: {score: 0.42, method: self_report}
            agreeableness: {score: 0.61, method: inferred}
            neuroticism: {score: 0.33, method: self_report}
          regulatory_focus: {promotion: 0.7, prevention: 0.3}
          risk_tolerance: {domain: "career", level: "medium-high"}
          time_horizon: {preferred: "long", evidence: ["2024_l2_..."]}
          review_after_days: 180

        values_motivations:
          schwartz_top5:
            - "Self-Direction"
            - "Achievement"
            - "Universalism"
            - "Benevolence"
            - "Security"
          sdt: {autonomy: 0.8, competence: 0.7, relatedness: 0.6}
          drivers:
            - value: "Mastery over tools & systems"
              method: inferred
              confidence: 0.8
          review_after_days: 120

        goals:
          short_term:
            - value: "Ship personal agent MVP"
              why: "reduce friction"
              krs: ["CLI usable", "context pack <1800t"]
              review_after_days: 30
          long_term:
            - value: "Work-life consistency with twins"
              krs: ["2 evenings/week protected"]
              review_after_days: 90
          anti_goals:
            - value: "No late-night production firefighting as a norm"
              reason: "family/health"

        decision_style:
          default: {speed_vs_quality: "quality", satisficer_vs_maximizer: "bounded_maximizer"}
          implementation_intentions:
            - if: "Feeling anxious before presentations"
              then: "Run checklist + 10-min rehearsal"
              evidence: ["2021-04-12_l1"]

        affect_energy:
          energy_map: {morning: "high", afternoon: "medium", evening: "low"}
          stressors: ["ambiguous deadlines", "noisy environment"]
          coping_strategies: ["walks", "time-boxing", "no email after 18:00"]

        social:
          relationships:
            - person: "Jess"
              role: "coworker"
              notes: "great feedback partner"
              boundary: "no pings after 18:00"

        boundaries_ethics:
          red_lines: ["No sharing private family data", "No health advice beyond guidelines"]

        coaching_prefs:
          tone: "direct, warm"
          depth: "concrete first, theory second"
          probing: {max_questions: 2, prefer: "yes/no + one short open follow-up"}
        """,
    ).strip()
    + "\n",
    "profile/claims.yaml": "claims: []\n",
}

ROLE_ORDER = [
    "profile",
    "claims",
    "config",
    "prompt",
    "normalized",
    "summaries",
    "microfacts",
    "advice",
    "profile_suggestions",
    "journal_raw",
]

TRIM_PRIORITY = [
    "journal_raw",
    "prompt",
    "config",
    "advice",
    "profile_suggestions",
    "microfacts",
    "summaries",
    "normalized",
    "claims",
    "profile",
]

PERSONA_PROFILE_KEYS = (
    "values_motivations",
    "goals",
    "boundaries_ethics",
    "coaching_prefs",
    "affect_energy",
    "decision_style",
    "traits",
    "social",
)

CLAIM_TYPE_IMPACT_DEFAULTS: dict[str, float] = {
    "value": 1.4,
    "goal": 1.4,
    "boundary": 1.3,
    "trait": 1.2,
    "preference": 1.0,
    "habit": 0.9,
    "aversion": 1.1,
    "skill": 1.0,
}

PERSONA_DEFAULTS = {
    "token_budget": 1200,
    "max_claims": 24,
    "min_claims": 8,
}

DEFAULT_CHAR_PER_TOKEN = 4.2

HIGH_IMPACT_PROBES = [
    "- Top 3 values you refuse to trade off—rank them.",
    "- One long-term goal that matters most this year—and why now?",
    "- When speed and quality conflict, what do you choose by default?",
    "- List 2 anti-goals (things you want to avoid) and the reasons.",
    "- Your risk posture in career moves: low / medium / high—why?",
    "- Energy map: when are you best for deep work vs admin?",
    "- Feedback style you prefer when you’re wrong?",
    "- Three coping strategies that reliably help under stress.",
]

CHUNK_TARGET_CHARS = 900
CHUNK_MAX_CHARS = 1200
ANN_METRIC: Literal["angular", "euclidean", "manhattan", "hamming", "dot"] = "angular"
INDEX_DB_FILENAME = "index.db"
ANNOY_FILENAME = "annoy.index"
INDEX_META_FILENAME = "meta.json"


@dataclass
class ChunkRecord:
    """Normalized chunk + embedding payload stored in SQLite."""

    chunk_id: str
    normalized_id: str
    normalized_path: str
    chunk_index: int
    chunk_text: str
    date: str
    tags: list[str]
    source_type: str | None
    source_path: str
    tokens: int
    source_hash: str | None
    manifest_hash: str | None
    embedding: list[float] | None = None


@dataclass
class SourceRecord:
    """Bookkeeping entry for indexed normalized files."""

    normalized_path: str
    normalized_id: str
    date: str
    source_hash: str | None
    manifest_hash: str | None
    chunk_count: int
    updated_at: str


@dataclass
class IndexTask:
    """Prepared normalized entry ready for chunking/indexing."""

    day: str
    path: Path
    normalized_path: str
    normalized_id: str
    entry: dict[str, Any]
    source_hash: str | None
    manifest: dict[str, Any] | None


FAKE_TIME_BLOCKS = [
    ("Morning focus", 9),
    ("Midday review", 12),
    ("Afternoon systems", 15),
    ("Evening reflection", 20),
]

FAKE_THEMES = [
    "deep work sprint",
    "planning checkpoint",
    "family logistics",
    "energy reset",
    "coaching prep",
    "writing sprint",
    "health baseline",
]

FAKE_PROJECTS = [
    "aijournal",
    "infra cleanup",
    "garden automation",
    "parenting playbook",
    "focus playlist",
    "writing pipeline",
]

FAKE_ACTIONS = [
    "Mapped blockers and sketched next three steps",
    "Clarified success criteria before touching code",
    "Reconciled notes from last retro",
    "Documented one insight per paragraph",
    "Turned vague worries into explicit tasks",
]

FAKE_REFLECTIONS = [
    "Noticed recurring tension around context switching",
    "Energy dipped after lunch but came back with a walk",
    "Family logistics feel smoother when blocked on Sundays",
    "Confidence spikes once the first win lands",
    "Need to protect two uninterrupted mornings",
]

FAKE_NEXT_STEPS = [
    "Block next session on the calendar",
    "Ping Jess for async review notes",
    "Move open todos into Things inbox",
    "Write a two-paragraph recap for future me",
    "Tidy prompt library before shipping",
]

FAKE_MOODS = ["steady", "energized", "calm", "curious", "stretched"]

FAKE_TAG_SETS = [
    ["focus", "planning"],
    ["reflection", "family"],
    ["health", "habits"],
    ["shipping", "systems"],
    ["writing", "learning"],
]

FAKE_MINUTES = [0, 5, 10, 15, 20, 30, 35, 40, 45, 50]

MARKDOWN_SUFFIXES = {".md", ".markdown"}

PROJECT_ROOT = Path(__file__).resolve().parents[2]
PENDING_UPDATES_SUBDIR = "derived/pending/profile_updates"

DEFAULT_PROMPTS = {
    "summarize_day.md": (
        "You are a journaling summarizer. Return JSON with day, bullets, highlights, "
        "todo_candidates."
    ),
    "extract_facts.md": 'Extract atomic facts as JSON {"facts":[...]}.',
    "profile_suggest.md": (
        "Propose JSON with upserts and updates grounded in the entries and profile."
    ),
    "advise.md": "Return an advice card JSON with recommendations citing facets and claims.",
    "characterize.md": ("Return JSON with claims and facets describing pending profile updates."),
}


def _now() -> datetime:
    """Return the current UTC time; separated for easy monkeypatching in tests."""
    return datetime.now(tz=UTC)


def _slugify_title(title: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")
    return slug or "entry"


def _format_timestamp(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def _resolve_prompt_path(prompt_path: str) -> Path:
    candidate = Path(prompt_path)
    if candidate.is_absolute():
        return candidate
    cwd_candidate = Path.cwd() / prompt_path
    if cwd_candidate.exists():
        return cwd_candidate
    return PROJECT_ROOT / prompt_path


def _load_prompt_template(prompt_path: str) -> str:
    path = _resolve_prompt_path(prompt_path)
    if path.exists():
        return path.read_text(encoding="utf-8")
    key = Path(prompt_path).name
    return DEFAULT_PROMPTS.get(prompt_path) or DEFAULT_PROMPTS.get(key, "")


def _render_prompt(prompt_path: str, variables: dict[str, str]) -> str:
    template = Template(_load_prompt_template(prompt_path))
    return template.safe_substitute(**variables)


def _json_block(data: Any) -> str:
    return json.dumps(data, indent=2, ensure_ascii=False)


def _build_ollama_runner(config: dict[str, Any]) -> OllamaTaskRunner:
    runner_config = OllamaConfig(
        model=_resolve_model_name(config),
        host=os.getenv("AIJOURNAL_OLLAMA_HOST"),
        temperature=_coerce_float(config.get("temperature")),
        seed=_coerce_int(config.get("seed")),
    )
    return OllamaTaskRunner(runner_config)


def _safe_llm_json(
    prompt_path: str,
    variables: dict[str, str],
    runner: OllamaTaskRunner,
    fallback: Callable[[], dict[str, Any]],
) -> dict[str, Any]:
    prompt = _render_prompt(prompt_path, variables)
    try:
        return runner.generate_json(prompt)
    except (
        LLMResponseError,
        Exception,
    ) as exc:  # pragma: no cover - network errors hard to simulate
        typer.secho(
            f"Falling back to offline heuristics for {prompt_path}: {exc}",
            fg=typer.colors.YELLOW,
            err=True,
        )
        return fallback()


def _journal_path(base: Path, dt: datetime, slug: str) -> Path:
    return (
        base
        / "data"
        / "journal"
        / dt.strftime("%Y")
        / dt.strftime("%m")
        / dt.strftime("%d")
        / f"{slug}.md"
    )


def _write_markdown_entry(path: Path, frontmatter: dict[str, Any], body: str = "") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    yaml_block = yaml.safe_dump(frontmatter, sort_keys=False).strip()
    content = f"---\n{yaml_block}\n---\n"
    if body:
        content += f"\n{body.strip()}\n"
    else:
        content += "\n"
    path.write_text(content, encoding="utf-8")


def _generate_fake_entries(
    count: int,
    override_tags: list[str] | None,
    seed: int | None,
    base: Path,
) -> tuple[int, int]:
    if count <= 0:
        return (0, 0)

    rng_seed = seed if seed is not None else int(_now().timestamp())
    rng = random.Random(rng_seed)
    base_dt = _now()
    base_day = base_dt.date()
    enforced_tags = list(override_tags or [])

    created = 0
    skipped = 0

    for idx in range(count):
        day = base_day - timedelta(days=idx)
        label, hour = rng.choice(FAKE_TIME_BLOCKS)
        minute = rng.choice(FAKE_MINUTES)
        created_dt = datetime(
            day.year,
            day.month,
            day.day,
            hour,
            minute,
            tzinfo=UTC,
        )
        theme = rng.choice(FAKE_THEMES)
        project = rng.choice(FAKE_PROJECTS)
        mood = rng.choice(FAKE_MOODS)
        action = rng.choice(FAKE_ACTIONS)
        reflection = rng.choice(FAKE_REFLECTIONS)
        next_step = rng.choice(FAKE_NEXT_STEPS)
        slug = (
            f"{created_dt.strftime('%Y-%m-%d')}-{_slugify_title(theme)}-{_slugify_title(project)}"
        )
        title = f"{label}: {theme.title()} ({project})"

        entry_path = _journal_path(base, created_dt, slug)
        if entry_path.exists():
            typer.echo(f"Skipping {entry_path} (already exists)")
            skipped += 1
            continue

        if enforced_tags:
            tags = enforced_tags
        else:
            auto_tags = set(rng.choice(FAKE_TAG_SETS))
            auto_tags.add(project.split()[0].lower())
            auto_tags.add(theme.split()[0].lower())
            tags = sorted(auto_tags)

        frontmatter = {
            "id": slug,
            "created_at": _format_timestamp(created_dt),
            "title": title,
            "tags": tags,
            "mood": mood,
            "projects": [project],
        }
        body = "\n\n".join(
            [
                f"{label} block stayed on {project}: {action}.",
                f"Felt {mood}; {reflection}.",
                f"Next: {next_step}.",
            ],
        )
        _write_markdown_entry(entry_path, frontmatter, body)
        typer.echo(str(entry_path))
        created += 1

    return created, skipped


def _find_data_root(entry: Path) -> Path:
    for parent in entry.parents:
        if parent.name == "data":
            return parent.parent
    return Path.cwd()


def _normalized_path(root: Path, date_str: str, entry_id: str) -> Path:
    return root / "data" / "normalized" / date_str / f"{entry_id}.yaml"


def _ensure_dirs(base: Path, rel_paths: Iterable[str]) -> tuple[int, int]:
    paths = tuple(rel_paths)
    created = 0
    for rel in paths:
        target = base / rel
        if not target.exists():
            target.mkdir(parents=True, exist_ok=True)
            created += 1
        else:
            target.mkdir(parents=True, exist_ok=True)
    return created, len(paths)


def _ensure_files(base: Path) -> tuple[int, int]:
    created = 0
    for rel, content in SEED_FILES.items():
        target = base / rel
        if target.exists():
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content.rstrip() + "\n", encoding="utf-8")
        created += 1
    return created, len(SEED_FILES)


def _split_frontmatter(text: str) -> tuple[str, str]:
    delimiter = None
    if text.startswith("---"):
        delimiter = "---"
    elif text.startswith("+++"):
        delimiter = "+++"
    if delimiter is None:
        msg = "Markdown entry missing YAML/TOML frontmatter delimiter"
        raise ValueError(msg)

    parts = text.split(delimiter, 2)
    if len(parts) < 3:
        msg = "Incomplete YAML/TOML frontmatter block"
        raise ValueError(msg)

    frontmatter_raw = parts[1].strip()
    body = parts[2]
    return frontmatter_raw, body


def _scan_headings(text: str) -> list[dict[str, Any]]:
    sections: list[dict[str, Any]] = []
    for line in text.splitlines():
        heading_match = re.match(r"^(#{1,6})\s+(.*)$", line.strip())
        if heading_match:
            sections.append(
                {
                    "heading": heading_match.group(2).strip(),
                    "level": len(heading_match.group(1)),
                },
            )
    return sections


def _parse_entry(entry_path: Path) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    text = entry_path.read_text(encoding="utf-8")
    frontmatter_raw, body = _split_frontmatter(text)
    data = yaml.safe_load(frontmatter_raw) or {}
    sections = _scan_headings(body)
    return data, sections


def _relative_source_path(entry_path: Path, root: Path) -> str:
    try:
        return str(entry_path.relative_to(root))
    except ValueError:
        return str(entry_path)


def _load_existing_yaml(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def _write_yaml_if_changed(
    path: Path,
    data: dict[str, Any],
    *,
    schema: str | None = None,
) -> bool:
    if schema:
        try:
            validate_schema(schema, data)
        except SchemaValidationError as exc:
            typer.secho(str(exc), fg=typer.colors.RED, err=True)
            raise typer.Exit(1)

    existing = _load_existing_yaml(path)
    if existing == data:
        return False

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
    return True


def _normalize_created_at(value: Any) -> str:
    if isinstance(value, datetime):
        dt = value.astimezone(UTC)
        return _format_timestamp(dt)

    if isinstance(value, str):
        candidate = value.replace("Z", "+00:00") if value.endswith("Z") else value
        try:
            dt = datetime.fromisoformat(candidate)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=UTC)
            return _format_timestamp(dt)
        except ValueError:
            return value

    return str(value)


def _created_date(created_at: str) -> str:
    if "T" in created_at:
        return created_at.split("T", 1)[0]
    return created_at


def _load_yaml(path: Path) -> dict[str, Any]:
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


def _load_config(root: Path) -> dict[str, Any]:
    config_path = root / "config" / "config.yaml"
    if not config_path.exists():
        return {}
    return _load_yaml(config_path)


def _use_fake_llm() -> bool:
    return os.getenv("AIJOURNAL_FAKE_OLLAMA") == "1"


def _resolve_model_name(config: dict[str, Any]) -> str:
    return os.getenv("AIJOURNAL_MODEL") or str(config.get("model") or "llama3.1:8b-instruct")


def _coerce_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _coerce_int(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _manifest_path(root: Path) -> Path:
    return root / "data" / "manifest" / "ingested.yaml"


def _load_manifest(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or []
    if isinstance(data, list):
        return data
    return []


def _write_manifest(path: Path, entries: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(entries, sort_keys=False), encoding="utf-8")


def _manifest_by_id(entries: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    index: dict[str, dict[str, Any]] = {}
    for entry in entries:
        entry_id = entry.get("id")
        if not entry_id:
            continue
        index[str(entry_id)] = entry
    return index


def _discover_markdown_files(inputs: Iterable[Path]) -> list[Path]:
    files: list[Path] = []
    for source in inputs:
        resolved = source.expanduser().resolve()
        if resolved.is_dir():
            for candidate in sorted(resolved.rglob("*")):
                if candidate.is_file() and candidate.suffix.lower() in MARKDOWN_SUFFIXES:
                    files.append(candidate)
        elif resolved.is_file():
            files.append(resolved)

    unique: list[Path] = []
    seen: set[Path] = set()
    for file in files:
        if file not in seen:
            seen.add(file)
            unique.append(file)
    return unique


def _normalize_tags(raw: Iterable[Any]) -> list[str]:
    tags: list[str] = []
    seen: set[str] = set()
    for value in raw:
        if value is None:
            continue
        text = str(value).strip()
        if not text:
            continue
        slug = _slugify_title(text)
        if slug and slug not in seen:
            seen.add(slug)
            tags.append(slug)
    return tags


def _clamp01(value: Any) -> float:
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        numeric = 0.0
    return max(0.0, min(1.0, numeric))


def _coerce_timestamp(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        dt = value.astimezone(UTC)
        return dt.strftime("%Y-%m-%dT%H:%M:%SZ")
    text = str(value)
    return text if text else None


def _ensure_scope_dict(raw: Any) -> dict[str, Any]:
    scope = raw if isinstance(raw, dict) else {}
    domain = scope.get("domain")
    scope_dict: dict[str, Any] = {"domain": str(domain) if domain else None}
    context_raw = scope.get("context") if isinstance(scope, dict) else None
    context = context_raw if isinstance(context_raw, list) else []
    scope_dict["context"] = [str(item).strip() for item in context if str(item).strip()]
    conditions_raw = scope.get("conditions") if isinstance(scope, dict) else None
    conditions = conditions_raw if isinstance(conditions_raw, list) else []
    scope_dict["conditions"] = [str(item).strip() for item in conditions if str(item).strip()]
    return scope_dict


def _ensure_provenance_dict(
    raw: Any,
    timestamp: str,
    default_sources: list[dict[str, Any]] | None,
) -> dict[str, Any]:
    provenance = raw if isinstance(raw, dict) else {}
    sources_raw = provenance.get("sources") if isinstance(provenance, dict) else None
    normalized_sources: list[dict[str, Any]] = []
    if isinstance(sources_raw, list):
        for source in sources_raw:
            if not isinstance(source, dict):
                continue
            entry_id = source.get("entry_id")
            if not entry_id:
                continue
            spans = source.get("spans") if isinstance(source.get("spans"), list) else []
            normalized_sources.append(
                {
                    "entry_id": str(entry_id),
                    "spans": spans,
                },
            )
    if not normalized_sources and default_sources:
        normalized_sources = [dict(src) for src in default_sources]
    provenance = dict(provenance)
    provenance["sources"] = normalized_sources
    first_seen_raw = provenance.get("first_seen")
    first_seen = _coerce_timestamp(first_seen_raw)
    if first_seen:
        provenance["first_seen"] = _created_date(first_seen)
    else:
        provenance["first_seen"] = _created_date(timestamp)
    last_updated = _coerce_timestamp(provenance.get("last_updated")) or timestamp
    provenance["last_updated"] = last_updated
    return provenance


def _ensure_claim_atom_dict(
    data: dict[str, Any],
    *,
    timestamp: str,
    default_sources: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    atom = dict(data)
    statement = str(atom.get("statement") or "").strip()
    if not statement:
        msg = "Claim statement is required"
        raise ValueError(msg)
    atom["statement"] = statement

    claim_type = str(atom.get("type") or "preference").strip() or "preference"
    atom["type"] = claim_type

    subject = atom.get("subject") or atom.get("id") or _slugify_title(statement) or "observation"
    atom["subject"] = str(subject)

    predicate = str(atom.get("predicate") or "statement")
    atom["predicate"] = predicate

    value_text = atom.get("value")
    if value_text is None or not str(value_text).strip():
        value_text = statement
    atom["value"] = str(value_text)

    claim_id = atom.get("id")
    if not claim_id:
        subject_slug = _slugify_title(str(subject)) or "subject"
        predicate_slug = _slugify_title(predicate) or "predicate"
        claim_id = f"{claim_type}.{subject_slug}.{predicate_slug}"
    atom["id"] = str(claim_id)[:96]

    atom["scope"] = _ensure_scope_dict(atom.get("scope"))

    strength_value = atom.get("strength")
    if strength_value is None:
        strength_value = atom.get("confidence")
    base_strength = _coerce_float(strength_value)
    atom["strength"] = _clamp01(base_strength if base_strength is not None else 0.6)

    atom["status"] = str(atom.get("status") or "tentative")
    atom["method"] = str(atom.get("method") or "inferred")
    atom["user_verified"] = bool(atom.get("user_verified", False))
    atom["review_after_days"] = _coerce_int(atom.get("review_after_days")) or 120

    atom["provenance"] = _ensure_provenance_dict(
        atom.get("provenance"),
        timestamp,
        default_sources,
    )
    return atom


def _build_claim_atom_from_entry(
    entry: dict[str, Any],
    *,
    claim_id: str,
    statement: str,
    strength: float,
    status: str,
) -> dict[str, Any]:
    timestamp = _format_timestamp(_now())
    default_sources = [
        {
            "entry_id": str(entry.get("id") or claim_id),
            "spans": [],
        },
    ]
    raw = {
        "id": claim_id,
        "type": "preference",
        "subject": entry.get("title") or claim_id,
        "predicate": "insight",
        "value": statement,
        "statement": statement,
        "scope": {
            "domain": None,
            "context": entry.get("tags", [])[:2],
            "conditions": [],
        },
        "strength": strength,
        "status": status,
        "method": "inferred",
        "user_verified": False,
        "review_after_days": 120,
        "provenance": {
            "sources": default_sources,
            "first_seen": entry.get("created_at") or timestamp,
        },
    }
    return _ensure_claim_atom_dict(raw, timestamp=timestamp, default_sources=default_sources)


def _clean_summary(text: str | None, fallback: str | None = None) -> str | None:
    candidate = (text or "").strip()
    if candidate:
        for marker in (',"entry_id"', ',"tags"', ',"sections"'):
            idx = candidate.find(marker)
            if idx != -1:
                candidate = candidate[:idx]
                break
        candidate = candidate.replace("\n", " ").strip().strip('"')
        sentences = re.split(r"(?<=[.!?])\s+", candidate)
        sentences = [sentence.strip() for sentence in sentences if sentence.strip()]
        candidate = " ".join(sentences[:2]) if sentences else ""

    if not candidate and fallback:
        candidate = fallback.strip()

    return candidate or None


def _merge_sections(
    primary: Iterable[IngestSection],
    fallback: Iterable[dict[str, Any]],
    *,
    title: str,
    limit: int = 6,
) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    seen: set[str] = set()

    def add_section(heading: str, level: int, summary: str | None = None) -> None:
        heading = heading.strip()
        if not heading:
            return
        key = heading.lower()
        if key in seen:
            return
        seen.add(key)
        entry: dict[str, Any] = {
            "heading": heading,
            "level": max(1, min(6, int(level or 1))),
        }
        if summary:
            entry["summary"] = summary.strip()
        entries.append(entry)

    for primary_section in primary:
        add_section(primary_section.heading, primary_section.level, primary_section.summary)
        if len(entries) >= limit:
            return entries

    for fallback_section in fallback:
        heading = str(fallback_section.get("heading") or title)
        level = int(fallback_section.get("level", 2))
        add_section(heading, level)
        if len(entries) >= limit:
            return entries

    if not entries:
        add_section(title or "entry", 1)
    return entries


def _sanitize_entry_id(candidate: str | None, title: str, date_str: str, digest: str) -> str:
    slug = ""
    if candidate and candidate.strip():
        slug = _slugify_title(candidate)
    elif title.strip():
        slug = _slugify_title(title)

    if slug:
        if not slug.startswith(date_str):
            slug = f"{date_str}-{slug}"
    else:
        slug = f"{date_str}-{digest[:8]}"

    return slug[:96]


def _extract_frontmatter_tags(frontmatter: dict[str, Any]) -> list[str]:
    values: list[str] = []
    for key in ("tags", "categories", "keywords", "topics", "projects"):
        raw = frontmatter.get(key)
        if raw is None:
            continue
        if isinstance(raw, str):
            values.append(raw)
        elif isinstance(raw, list):
            for item in raw:
                values.append(str(item))
    return values


def _fake_structured_entry(entry_path: Path) -> IngestResult:
    try:
        frontmatter, sections_raw = _parse_entry(entry_path)
    except ValueError:
        frontmatter = {}
        sections_raw = []

    created_value = (
        frontmatter.get("created_at")
        or frontmatter.get("date")
        or frontmatter.get("published")
        or _format_timestamp(_now())
    )
    created_dt = _parse_datetime(str(created_value)) or _now()
    title = str(frontmatter.get("title") or entry_path.stem)
    section_models = [
        IngestSection(
            heading=str(section.get("heading", title)),
            level=int(section.get("level", 2) or 2),
        )
        for section in sections_raw
    ]
    if not section_models:
        section_models = [IngestSection(heading=title, level=1)]

    summary = frontmatter.get("summary")
    tags = _extract_frontmatter_tags(frontmatter)
    entry_id = frontmatter.get("id") or frontmatter.get("slug")

    return IngestResult(
        entry_id=str(entry_id) if entry_id else None,
        created_at=created_dt,
        title=title,
        tags=tags,
        sections=section_models,
        summary=str(summary) if isinstance(summary, str) else None,
    )


def _normalized_from_structured(
    structured: IngestResult,
    *,
    source_path: Path,
    root: Path,
    digest: str,
    source_type: str,
    fallback_sections: list[dict[str, Any]] | None = None,
    fallback_tags: list[str] | None = None,
    fallback_summary: str | None = None,
) -> tuple[dict[str, Any], str]:
    created_at = structured.created_at
    if isinstance(created_at, datetime):
        created_str = _format_timestamp(created_at.astimezone(UTC))
    else:
        created_str = _normalize_created_at(created_at)

    date_str = _created_date(created_str)
    entry_id = _sanitize_entry_id(structured.entry_id, structured.title, date_str, digest)
    tags = _normalize_tags(list(structured.tags or []) + list(fallback_tags or []))

    merged_sections = _merge_sections(
        structured.sections or [],
        fallback_sections or [],
        title=structured.title.strip() or entry_id,
    )

    normalized = {
        "id": entry_id,
        "created_at": created_str,
        "source_path": _relative_source_path(source_path, root),
        "title": structured.title.strip() or entry_id,
        "tags": tags,
        "sections": merged_sections,
        "source_hash": digest,
        "source_type": source_type,
    }
    summary = _clean_summary(structured.summary, fallback_summary)
    if summary:
        normalized["summary"] = summary

    return normalized, date_str


def _load_normalized_entries(root: Path, day: str) -> list[dict[str, Any]]:
    folder = root / "data" / "normalized" / day
    if not folder.exists():
        return []
    entries: list[dict[str, Any]] = []
    for file in sorted(folder.glob("*.yaml")):
        entries.append(_load_yaml(file))
    return entries


def _load_normalized_entries_with_paths(root: Path, day: str) -> list[tuple[dict[str, Any], Path]]:
    folder = root / "data" / "normalized" / day
    if not folder.exists():
        return []
    entries: list[tuple[dict[str, Any], Path]] = []
    for file in sorted(folder.glob("*.yaml")):
        entries.append((_load_yaml(file), file))
    return entries


def _derived_summary_path(root: Path, day: str) -> Path:
    return root / "derived" / "summaries" / f"{day}.yaml"


def _derived_microfacts_path(root: Path, day: str) -> Path:
    return root / "derived" / "microfacts" / f"{day}.yaml"


def _derived_advice_path(root: Path, day: str, question: str) -> Path:
    slug = _slugify_title(question)
    return root / "derived" / "advice" / day / f"{slug}.yaml"


def _derived_profile_suggestions_path(root: Path, day: str) -> Path:
    return root / "derived" / "profile_suggestions" / f"{day}.yaml"


def _pending_updates_dir(root: Path) -> Path:
    return root / PENDING_UPDATES_SUBDIR


def _pending_updates_path(root: Path, batch_id: str) -> Path:
    safe_id = batch_id.replace(":", "-")
    return _pending_updates_dir(root) / f"{safe_id}.yaml"


def _latest_pending_batch(root: Path) -> Path | None:
    directory = _pending_updates_dir(root)
    if not directory.exists():
        return None
    files = sorted(p for p in directory.glob("*.yaml") if p.is_file())
    return files[-1] if files else None


def _hash_prompt(prompt_path: str) -> str | None:
    path = _resolve_prompt_path(prompt_path)
    try:
        data = path.read_bytes()
    except FileNotFoundError:
        return None
    return sha256(data).hexdigest()


def _build_meta(prompt_path: str, model: str = "fake-ollama") -> dict[str, Any]:
    return {
        "llm_model": model,
        "prompt_path": prompt_path,
        "prompt_hash": _hash_prompt(prompt_path),
        "created_at": _format_timestamp(_now()),
    }


def _fake_summarize(entries: list[dict[str, Any]]) -> list[str]:
    bullets: list[str] = []
    for entry in entries:
        title = entry.get("title", entry.get("id", "entry"))
        sections = entry.get("sections") or []
        section_titles = ", ".join(sec.get("heading", "") for sec in sections[:2] if sec)
        if section_titles:
            bullets.append(f"{title}: {section_titles}")
        else:
            bullets.append(f"{title}: no sections")
    return bullets or ["No content available"]


def _fake_microfacts(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    facts: list[dict[str, Any]] = []
    for entry in entries:
        entry_id = entry.get("id", "entry")
        title = entry.get("title", entry_id)
        sections = entry.get("sections") or []
        statement = f"{title} covers {len(sections)} sections"
        facts.append(
            {
                "id": f"fact-{entry_id}",
                "statement": statement,
                "confidence": 0.8,
                "evidence": {"entry_id": entry_id},
            },
        )
    return facts or [
        {
            "id": "fact-empty",
            "statement": "No normalized entries available",
            "confidence": 0.0,
            "evidence": {"entry_id": "unknown"},
        },
    ]


def _fake_advise(
    question: str,
    profile: dict[str, Any],
    claims: list[dict[str, Any]],
) -> dict[str, Any]:
    claim = claims[0] if claims else {}
    advice_id = _advice_identifier(question)
    claim_statement = claim.get("statement") or "Reflect on priorities"
    claim_id = claim.get("id")

    facets: list[str] = []
    if profile.get("affect_energy"):
        facets.append("affect_energy.energy_map")
    if profile.get("goals"):
        facets.append("goals.short_term")
    if profile.get("values_motivations"):
        facets.append("values_motivations.schwartz_top5")

    reference = {
        "facets": facets,
        "claims": [claim_id] if claim_id else [],
    }

    assumption = (
        f"Reference claim: {claim_statement}" if claim_statement else "No verified claims available"
    )

    recommendation = {
        "title": claim_statement,
        "why_this_fits_you": {
            "facets": list(reference["facets"]),
            "claims": list(reference["claims"]),
        },
        "steps": [
            "Protect two deep-work mornings for focused execution.",
            f"Question under review: {question}",
        ],
        "risks": ["Schedule collisions", "Unclear stakeholder updates"],
        "mitigations": [
            "Share the plan with collaborators early.",
            "Add end-of-day shutdown reminders to honor boundaries.",
        ],
    }

    style = profile.get("coaching_prefs") or {"tone": "direct", "depth": "concrete-first"}

    return {
        "id": advice_id,
        "query": question,
        "assumptions": [assumption],
        "recommendations": [recommendation],
        "tradeoffs": ["Shipping speed may dip slightly while routines stabilize."],
        "next_actions": [
            "Block two 3-hour focus windows next week.",
            "Schedule a 10-minute Friday review with yourself.",
        ],
        "confidence": 0.5,
        "alignment": reference,
        "style": style,
    }


def _fake_profile_suggestions(
    entries: list[dict[str, Any]],
    profile: dict[str, Any],
    claims: list[dict[str, Any]],
) -> dict[str, Any]:
    upserts = []
    updates = []

    for entry in entries[:1]:
        statement = entry.get("title", "New observation")
        claim_id = f"auto_{entry.get('id', 'entry')}"
        upserts.append(
            {
                "target": "claims",
                "operation": "upsert",
                "value": _build_claim_atom_from_entry(
                    entry,
                    claim_id=claim_id,
                    statement=statement,
                    strength=0.6,
                    status="tentative",
                ),
            },
        )

    if profile:
        updates.append(
            {
                "target": "values_motivations.schwartz_top5",
                "operation": "update",
                "value": profile.get("values_motivations", {}).get("schwartz_top5", []),
            },
        )

    return {"upserts": upserts, "updates": updates}


def _fake_characterize(
    entries: list[dict[str, Any]],
    profile: dict[str, Any],
    claims: list[dict[str, Any]],
) -> dict[str, Any]:
    if not entries:
        return {"claims": [], "facets": []}

    seed = entries[0]
    date = _created_date(str(seed.get("created_at") or _format_timestamp(_now())))
    heading = ""
    sections = seed.get("sections") or []
    if sections:
        heading = sections[0].get("heading") or ""
    title = seed.get("title") or seed.get("id") or "entry"
    theme = heading or title
    tag = (seed.get("tags") or [theme])[0]
    claim_id = f"{_slugify_title(theme) or 'entry'}-{date.replace('-', '')}-claim"
    claim = _build_claim_atom_from_entry(
        seed,
        claim_id=claim_id[:64],
        statement=f"{theme} remains top-of-mind on {date}.",
        strength=0.64,
        status="tentative",
    )

    facet = {
        "path": "values_motivations.recurring_theme",
        "operation": "set",
        "value": {
            "label": theme,
            "tag_hint": tag,
            "last_seen": date,
        },
        "method": "inferred",
        "confidence": 0.55,
        "review_after_days": 90,
        "user_verified": False,
    }

    return {"claims": [claim], "facets": [facet]}


def _coerce_str_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    if isinstance(value, str):
        candidate = value.strip()
        return [candidate] if candidate else []
    return []


def _todo_from_entries(entries: list[dict[str, Any]]) -> list[str]:
    todos: list[str] = []
    for entry in entries[:3]:
        title = entry.get("title") or entry.get("id") or "entry"
        todos.append(f"Review follow-ups from {title}")
    return todos or ["Capture explicit next actions in tomorrow's entry."]


def _summarize_day_payload(
    entries: list[dict[str, Any]],
    date: str,
    config: dict[str, Any],
) -> dict[str, Any]:
    def fallback() -> dict[str, Any]:
        bullets = _fake_summarize(entries)
        return {
            "day": date,
            "bullets": bullets,
            "highlights": bullets[:3],
            "todo_candidates": _todo_from_entries(entries),
        }

    if _use_fake_llm():
        return fallback()

    try:
        runner = _build_ollama_runner(config)
    except Exception as exc:  # pragma: no cover - dependent on runtime env
        typer.secho(
            f"Unable to initialize Ollama for summarize: {exc}",
            fg=typer.colors.YELLOW,
            err=True,
        )
        return fallback()

    payload = _safe_llm_json(
        "prompts/summarize_day.md",
        {"date": date, "entries_json": _json_block(entries)},
        runner,
        fallback,
    )
    return {
        "day": str(payload.get("day") or date),
        "bullets": _coerce_str_list(payload.get("bullets")),
        "highlights": _coerce_str_list(payload.get("highlights"))
        or _coerce_str_list(payload.get("bullets"))[:3],
        "todo_candidates": _coerce_str_list(payload.get("todo_candidates"))
        or _todo_from_entries(entries),
    }


def _microfacts_payload(
    entries: list[dict[str, Any]],
    date: str,
    config: dict[str, Any],
) -> dict[str, Any]:
    def fallback() -> dict[str, Any]:
        return {"facts": _fake_microfacts(entries)}

    if _use_fake_llm():
        return fallback()

    try:
        runner = _build_ollama_runner(config)
    except Exception as exc:  # pragma: no cover
        typer.secho(
            f"Unable to initialize Ollama for facts: {exc}",
            fg=typer.colors.YELLOW,
            err=True,
        )
        return fallback()

    payload = _safe_llm_json(
        "prompts/extract_facts.md",
        {"date": date, "entries_json": _json_block(entries)},
        runner,
        fallback,
    )
    facts = payload.get("facts")
    if not isinstance(facts, list):
        return fallback()
    return {"facts": facts}


def _profile_suggestions_payload(
    entries: list[dict[str, Any]],
    profile: dict[str, Any],
    claims: list[dict[str, Any]],
    date: str,
    config: dict[str, Any],
) -> dict[str, Any]:
    def fallback() -> dict[str, Any]:
        return _fake_profile_suggestions(entries, profile, claims)

    if _use_fake_llm():
        return fallback()

    try:
        runner = _build_ollama_runner(config)
    except Exception as exc:  # pragma: no cover
        typer.secho(
            f"Unable to initialize Ollama for profile suggestions: {exc}",
            fg=typer.colors.YELLOW,
            err=True,
        )
        return fallback()

    payload = _safe_llm_json(
        "prompts/profile_suggest.md",
        {
            "date": date,
            "entries_json": _json_block(entries),
            "profile_json": _json_block(profile),
            "claims_json": _json_block({"claims": claims}),
        },
        runner,
        fallback,
    )

    upserts_obj = payload.get("upserts")
    updates_obj = payload.get("updates")
    raw_upserts = upserts_obj if isinstance(upserts_obj, list) else []
    updates = updates_obj if isinstance(updates_obj, list) else []
    timestamp = _format_timestamp(_now())
    normalized_upserts: list[dict[str, Any]] = []
    for upsert in raw_upserts:
        if not isinstance(upsert, dict):
            continue
        value = upsert.get("value")
        if not isinstance(value, dict):
            continue
        try:
            normalized_value = _ensure_claim_atom_dict(value, timestamp=timestamp)
        except ValueError:
            continue
        entry = dict(upsert)
        entry["value"] = normalized_value
        normalized_upserts.append(entry)

    return {"upserts": normalized_upserts, "updates": updates}


def _characterization_context(
    entries: list[dict[str, Any]],
    manifest_index: dict[str, dict[str, Any]],
) -> tuple[list[str], list[str], list[str], list[dict[str, Any]]]:
    normalized_ids: list[str] = []
    source_hashes: set[str] = set()
    manifest_hashes: set[str] = set()
    default_sources: list[dict[str, Any]] = []

    for idx, entry in enumerate(entries):
        entry_id = str(entry.get("id") or f"entry-{idx + 1}")
        normalized_ids.append(entry_id)
        source_hash = entry.get("source_hash")
        if isinstance(source_hash, str) and source_hash:
            source_hashes.add(source_hash)
        manifest_entry = manifest_index.get(entry_id)
        manifest_hash = manifest_entry.get("hash") if isinstance(manifest_entry, dict) else None
        if manifest_hash:
            manifest_hashes.add(str(manifest_hash))
        default_sources.append({"entry_id": entry_id, "spans": []})

    return (
        normalized_ids,
        sorted(source_hashes),
        sorted(manifest_hashes),
        default_sources,
    )


def _normalize_claim_proposals(
    raw_claims: Iterable[dict[str, Any]],
    *,
    normalized_ids: list[str],
    evidence_hashes: list[str],
    manifest_hashes: list[str],
    default_sources: list[dict[str, Any]],
    timestamp: str,
) -> list[dict[str, Any]]:
    proposals: list[dict[str, Any]] = []
    for raw in raw_claims:
        if not isinstance(raw, dict):
            continue
        raw_claim = raw.get("claim")
        candidate = raw_claim if isinstance(raw_claim, dict) else raw
        try:
            claim = _ensure_claim_atom_dict(
                candidate,
                timestamp=timestamp,
                default_sources=default_sources,
            )
        except ValueError:
            continue

        proposals.append(
            {
                "claim": claim,
                "normalized_ids": list(normalized_ids),
                "evidence_hashes": list(evidence_hashes),
                "manifest_hashes": list(manifest_hashes),
                "rationale": raw.get("rationale") or raw.get("reason"),
            },
        )
    return proposals


def _normalize_facet_proposals(
    raw_facets: Iterable[dict[str, Any]],
    *,
    normalized_ids: list[str],
    evidence_hashes: list[str],
) -> list[dict[str, Any]]:
    proposals: list[dict[str, Any]] = []
    for raw in raw_facets:
        if not isinstance(raw, dict):
            continue
        path = raw.get("path") or raw.get("target")
        if not path:
            continue
        value = raw.get("value")
        proposal = {
            "path": str(path),
            "value": value,
            "operation": raw.get("operation") or "set",
            "method": raw.get("method") or "inferred",
            "confidence": _coerce_float(raw.get("confidence")) or 0.55,
            "review_after_days": _coerce_int(raw.get("review_after_days")) or 90,
            "user_verified": bool(raw.get("user_verified", False)),
            "normalized_ids": list(normalized_ids),
            "evidence_hashes": list(evidence_hashes),
            "rationale": raw.get("rationale") or raw.get("reason"),
        }
        proposals.append(proposal)
    return proposals


def _characterize_payload(
    entries: list[dict[str, Any]],
    profile: dict[str, Any],
    claims: list[dict[str, Any]],
    manifest_index: dict[str, dict[str, Any]],
    config: dict[str, Any],
) -> dict[str, Any]:
    claim_timestamp = _format_timestamp(_now())
    (
        normalized_ids,
        evidence_hashes,
        manifest_hashes,
        default_sources,
    ) = _characterization_context(entries, manifest_index)

    def fallback() -> dict[str, Any]:
        return _fake_characterize(entries, profile, claims)

    if _use_fake_llm():
        raw = fallback()
    else:
        try:
            runner = _build_ollama_runner(config)
        except Exception as exc:  # pragma: no cover
            typer.secho(
                f"Unable to initialize Ollama for characterize: {exc}",
                fg=typer.colors.YELLOW,
                err=True,
            )
            raw = fallback()
        else:
            manifest_payload = _json_block(manifest_index)
            raw = _safe_llm_json(
                "prompts/characterize.md",
                {
                    "date": _created_date(claim_timestamp),
                    "entries_json": _json_block(entries),
                    "profile_json": _json_block(profile),
                    "claims_json": _json_block({"claims": claims}),
                    "manifest_json": manifest_payload,
                },
                runner,
                fallback,
            )

    raw_claim_candidates = raw.get("claims")
    raw_claims = raw_claim_candidates if isinstance(raw_claim_candidates, list) else []
    raw_facet_candidates = raw.get("facets")
    raw_facets = raw_facet_candidates if isinstance(raw_facet_candidates, list) else []

    claims_payload = _normalize_claim_proposals(
        raw_claims,
        normalized_ids=normalized_ids,
        evidence_hashes=evidence_hashes,
        manifest_hashes=manifest_hashes,
        default_sources=default_sources,
        timestamp=claim_timestamp,
    )
    facets_payload = _normalize_facet_proposals(
        raw_facets,
        normalized_ids=normalized_ids,
        evidence_hashes=evidence_hashes,
    )
    return {"claims": claims_payload, "facets": facets_payload}


def _advice_identifier(question: str) -> str:
    day = _created_date(_format_timestamp(_now()))
    digest = sha256(question.encode("utf-8")).hexdigest()[:8]
    return f"adv_{day}_{digest}"


def _advice_payload(
    question: str,
    profile: dict[str, Any],
    claims: list[dict[str, Any]],
    config: dict[str, Any],
) -> dict[str, Any]:
    def fallback() -> dict[str, Any]:
        return _fake_advise(question, profile, claims)

    if _use_fake_llm():
        return fallback()

    try:
        runner = _build_ollama_runner(config)
    except Exception as exc:  # pragma: no cover
        typer.secho(
            f"Unable to initialize Ollama for advise: {exc}",
            fg=typer.colors.YELLOW,
            err=True,
        )
        return fallback()

    payload = _safe_llm_json(
        "prompts/advise.md",
        {
            "date": _created_date(_format_timestamp(_now())),
            "question": question,
            "profile_json": _json_block(profile),
            "claims_json": _json_block({"claims": claims}),
        },
        runner,
        fallback,
    )

    fallback_defaults = fallback()
    advice = dict(payload)
    advice.setdefault("id", _advice_identifier(question))
    advice.setdefault("query", question)
    advice.setdefault("assumptions", ["Grounded in supplied profile data"])
    advice.setdefault("recommendations", fallback_defaults.get("recommendations", []))
    advice.setdefault("tradeoffs", [])
    advice.setdefault("next_actions", [])
    advice.setdefault("confidence", 0.6)
    advice.setdefault("alignment", {"facets": [], "claims": []})
    advice.setdefault("style", profile.get("coaching_prefs", {}))
    return advice


@app.command()
def init(
    path: Path | None = typer.Option(
        None,
        "--path",
        "-p",
        help="Directory to initialize (defaults to current working directory).",
    ),
) -> None:
    """Initialize the local aijournal layout."""
    base = path or Path.cwd()
    base.mkdir(parents=True, exist_ok=True)

    dir_sets = (AUTHORITATIVE_DIRS, DERIVED_DIRS)
    created_dirs = 0
    total_dirs = 0
    for rels in dir_sets:
        created, total = _ensure_dirs(base, rels)
        created_dirs += created
        total_dirs += total

    created_files, total_files = _ensure_files(base)

    already_dirs = total_dirs - created_dirs
    already_files = total_files - created_files

    summary = (
        f"Created {created_dirs} directories and {created_files} files under {base}. "
        f"Already present: {already_dirs} directories and {already_files} files."
    )
    typer.echo(summary)


@app.command()
def new(
    title: str | None = typer.Argument(
        None,
        help="Title for the journal entry; omit when using --fake.",
    ),
    tags: list[str] | None = typer.Option(
        None,
        "--tags",
        "-t",
        help="Tag to attach to the entry (repeatable).",
    ),
    fake: int = typer.Option(
        0,
        "--fake",
        min=0,
        help="Generate N fake entries with deterministic metadata (no LLM).",
    ),
    seed: int | None = typer.Option(
        None,
        "--seed",
        help="Optional RNG seed for --fake generation.",
    ),
) -> None:
    """Create a new journal entry or synthesize fake entries for testing."""
    base = Path.cwd()

    if fake > 0:
        if title is not None:
            typer.secho(
                "Provide either a title or --fake, not both.",
                fg=typer.colors.RED,
                err=True,
            )
            raise typer.Exit(1)
        created, skipped = _generate_fake_entries(fake, tags, seed, base)
        summary = f"Generated {created} fake entr{'y' if created == 1 else 'ies'}"
        if skipped:
            summary += f" ({skipped} skipped)"
        typer.echo(summary)
        return

    if seed is not None:
        typer.secho("--seed is only valid together with --fake.", fg=typer.colors.RED, err=True)
        raise typer.Exit(1)

    if not title:
        typer.secho("Title is required unless --fake is provided.", fg=typer.colors.RED, err=True)
        raise typer.Exit(1)

    now = _now()
    slug = f"{now.strftime('%Y-%m-%d')}-{_slugify_title(title)}"
    entry_path = _journal_path(base, now, slug)

    if entry_path.exists():
        typer.echo(f"Entry exists: {entry_path}")
        raise typer.Exit(1)

    frontmatter = {
        "id": slug,
        "created_at": _format_timestamp(now),
        "title": title,
        "tags": tags or [],
    }

    _write_markdown_entry(entry_path, frontmatter)
    typer.echo(str(entry_path))


@app.command()
def ingest(
    sources: list[Path] = typer.Argument(
        ...,
        exists=True,
        dir_okay=True,
        file_okay=True,
        readable=True,
        resolve_path=True,
        help="Markdown files or directories to ingest.",
    ),
    source_type: str = typer.Option(
        "external",
        "--source-type",
        help="Label recorded in the manifest for these sources.",
    ),
    limit: int | None = typer.Option(
        None,
        "--limit",
        help="Maximum number of files to ingest.",
    ),
    snapshot: bool = typer.Option(
        True,
        "--snapshot/--no-snapshot",
        help="Store raw copies under data/raw/<hash>.md.",
    ),
) -> None:
    """Ingest Markdown posts into normalized YAML via Ollama."""
    if limit is not None and limit <= 0:
        typer.secho("--limit must be positive when provided.", fg=typer.colors.RED, err=True)
        raise typer.Exit(1)

    root = Path.cwd()
    files = _discover_markdown_files(sources)
    if not files:
        typer.secho(
            "No Markdown files found in the provided sources.",
            fg=typer.colors.RED,
            err=True,
        )
        raise typer.Exit(1)
    if limit is not None:
        files = files[:limit]

    config = _load_config(root)
    model_name = _resolve_model_name(config)
    is_fake = _use_fake_llm()
    agent: Agent | None = None
    if not is_fake:
        settings = AgentSettings(
            model=model_name,
            host=os.getenv("AIJOURNAL_OLLAMA_HOST"),
            temperature=_coerce_float(config.get("temperature")),
            seed=_coerce_int(config.get("seed")),
        )
        try:
            agent = build_ingest_agent(settings)
        except Exception as exc:  # pragma: no cover - initialization errors are rare
            typer.secho(
                f"Unable to initialize Ollama ingestion agent: {exc}",
                fg=typer.colors.RED,
                err=True,
            )
            raise typer.Exit(1)

    manifest_path = _manifest_path(root)
    manifest_entries = _load_manifest(manifest_path)
    known_hashes = {entry.get("hash"): entry for entry in manifest_entries if entry.get("hash")}

    ingested = 0
    skipped = 0
    errors = 0
    raw_dir = root / "data" / "raw"

    for file in files:
        try:
            raw_bytes = file.read_bytes()
        except OSError as exc:
            errors += 1
            typer.secho(f"Failed to read {file}: {exc}", fg=typer.colors.RED, err=True)
            continue

        digest = sha256(raw_bytes).hexdigest()
        if digest in known_hashes:
            skipped += 1
            typer.echo(f"Skipping {file} (already ingested)")
            continue

        try:
            text = raw_bytes.decode("utf-8")
        except UnicodeDecodeError as exc:
            errors += 1
            typer.secho(f"Failed to decode {file}: {exc}", fg=typer.colors.RED, err=True)
            continue

        try:
            frontmatter_data, fallback_sections = _parse_entry(file)
        except ValueError:
            frontmatter_data = {}
            fallback_sections = _scan_headings(text)
        fallback_tags = _extract_frontmatter_tags(frontmatter_data)
        fallback_summary = frontmatter_data.get("summary")
        if fallback_summary is not None:
            fallback_summary = str(fallback_summary)

        try:
            if is_fake:
                structured = _fake_structured_entry(file)
            else:
                assert agent is not None
                structured = ingest_with_agent(agent, source_path=file, markdown=text)
            normalized, date_str = _normalized_from_structured(
                structured,
                source_path=file,
                root=root,
                digest=digest,
                source_type=source_type,
                fallback_sections=fallback_sections,
                fallback_tags=fallback_tags,
                fallback_summary=fallback_summary,
            )
        except Exception as exc:
            errors += 1
            typer.secho(f"Failed to ingest {file}: {exc}", fg=typer.colors.RED, err=True)
            continue

        normalized_path = _normalized_path(root, date_str, normalized["id"])
        _write_yaml_if_changed(
            normalized_path,
            normalized,
            schema="normalized_entry",
        )

        if snapshot:
            raw_dir.mkdir(parents=True, exist_ok=True)
            (raw_dir / f"{digest}.md").write_bytes(raw_bytes)

        manifest_entry = {
            "hash": digest,
            "path": _relative_source_path(file, root),
            "normalized": _relative_source_path(normalized_path, root),
            "source_type": source_type,
            "ingested_at": _format_timestamp(_now()),
            "created_at": normalized["created_at"],
            "id": normalized["id"],
            "tags": normalized.get("tags", []),
            "model": model_name if not is_fake else "fake-ollama",
        }
        manifest_entries.append(manifest_entry)
        known_hashes[digest] = manifest_entry

        typer.echo(f"Ingested {file} -> {normalized_path}")
        ingested += 1

    if ingested:
        _write_manifest(manifest_path, manifest_entries)

    typer.echo(f"Ingest summary: {ingested} new, {skipped} skipped, {errors} errors.")
    if errors:
        raise typer.Exit(1)


@app.command()
def normalize(
    entry: Path = typer.Argument(
        ...,
        exists=True,
        readable=True,
        help="Path to journal Markdown entry.",
    ),
) -> None:
    """Normalize a Markdown journal entry into structured YAML."""
    entry = entry.resolve()
    try:
        frontmatter, sections = _parse_entry(entry)
    except ValueError as err:
        typer.secho(str(err), fg=typer.colors.RED, err=True)
        raise typer.Exit(1)

    entry_id_value = frontmatter.get("id")
    created_value = frontmatter.get("created_at")
    title_value = frontmatter.get("title")
    tags = frontmatter.get("tags", []) or []

    if not all([entry_id_value, created_value, title_value]):
        typer.secho(
            "Frontmatter must include id, created_at, title.",
            fg=typer.colors.RED,
            err=True,
        )
        raise typer.Exit(1)

    entry_id = str(entry_id_value)
    title = str(title_value)
    created_str = _normalize_created_at(created_value)
    date_str = _created_date(created_str)
    root = _find_data_root(entry)
    normalized_data = {
        "id": entry_id,
        "created_at": created_str,
        "source_path": _relative_source_path(entry, root),
        "title": title,
        "tags": tags,
        "sections": sections,
    }

    output_path = _normalized_path(root, date_str, entry_id)
    _write_yaml_if_changed(
        output_path,
        normalized_data,
        schema="normalized_entry",
    )
    typer.echo(str(output_path))


@app.command()
def summarize(
    date: str = typer.Option(..., "--date", "-d", help="Date (YYYY-MM-DD) to summarize."),
) -> None:
    """Generate a daily summary from normalized entries."""
    root = Path.cwd()
    entries = _load_normalized_entries(root, date)
    if not entries:
        typer.secho(f"No normalized entries for {date}", fg=typer.colors.RED, err=True)
        raise typer.Exit(1)

    config = _load_config(root)
    summary_data = _summarize_day_payload(entries, date, config)
    summary_data["meta"] = _build_meta("prompts/summarize_day.md")

    summary_path = _derived_summary_path(root, date)
    _write_yaml_if_changed(summary_path, summary_data, schema="summary")
    typer.echo(str(summary_path))


@app.command()
def facts(
    date: str = typer.Option(..., "--date", "-d", help="Date (YYYY-MM-DD) to analyze."),
) -> None:
    """Generate micro-facts from normalized entries."""
    root = Path.cwd()
    entries = _load_normalized_entries(root, date)
    if not entries:
        typer.secho(f"No normalized entries for {date}", fg=typer.colors.RED, err=True)
        raise typer.Exit(1)

    config = _load_config(root)
    facts_data = _microfacts_payload(entries, date, config)
    facts_data["meta"] = _build_meta("prompts/extract_facts.md")

    facts_path = _derived_microfacts_path(root, date)
    _write_yaml_if_changed(facts_path, facts_data, schema="microfacts")
    typer.echo(str(facts_path))


@profile_app.command("suggest")
def profile_suggest(
    date: str = typer.Option(..., "--date", "-d", help="Date (YYYY-MM-DD) to analyze."),
) -> None:
    """Suggest profile updates based on normalized entries."""
    root = Path.cwd()
    entries = _load_normalized_entries(root, date)
    if not entries:
        typer.secho(f"No normalized entries for {date}", fg=typer.colors.RED, err=True)
        raise typer.Exit(1)

    profile, claims = _load_profile_components(root)
    if not profile and not claims:
        typer.secho("No profile data", fg=typer.colors.RED, err=True)
        raise typer.Exit(1)

    config = _load_config(root)
    suggestions = _profile_suggestions_payload(entries, profile, claims, date, config)
    suggestions["meta"] = _build_meta("prompts/profile_suggest.md")

    path = _derived_profile_suggestions_path(root, date)
    _write_yaml_if_changed(path, suggestions, schema="profile_suggestions")
    typer.echo(str(path))


@profile_app.command("apply")
def profile_apply(
    date: str = typer.Option(..., "--date", "-d", help="Date (YYYY-MM-DD) to apply."),
    file: Path | None = typer.Option(None, "--file", help="Path to suggestions YAML."),
    yes: bool = typer.Option(False, "--yes", help="Apply without prompting."),
) -> None:
    """Apply profile suggestions to authoritative files (offline)."""
    root = Path.cwd()
    suggestions_path = file or (root / "derived" / "profile_suggestions" / f"{date}.yaml")

    if not suggestions_path.exists():
        typer.secho(
            f"Suggestions file not found: {suggestions_path}",
            fg=typer.colors.RED,
            err=True,
        )
        raise typer.Exit(1)

    suggestions = _load_yaml(suggestions_path)
    profile, claims = _load_profile_components(root)
    timestamp = _format_timestamp(_now())
    changed = False

    for upsert in suggestions.get("upserts", []):
        if upsert.get("target") == "claims":
            if _apply_claim_upsert(claims, upsert.get("value", {}), timestamp):
                changed = True

    for update in suggestions.get("updates", []):
        target = update.get("target")
        if not target:
            continue
        if _apply_profile_update(profile, target, update.get("value"), timestamp):
            changed = True

    if not changed:
        typer.echo("No changes to apply")
        raise typer.Exit(0)

    _atomic_write(
        root / "profile" / "self_profile.yaml",
        profile,
        schema="self_profile",
    )
    _atomic_write(
        root / "profile" / "claims.yaml",
        {"claims": claims},
        schema="claims",
    )
    typer.echo("Applied 1 suggestions file")


@app.command()
def characterize(
    date: str = typer.Option(..., "--date", "-d", help="Date (YYYY-MM-DD) to analyze."),
) -> None:
    """Derive pending profile updates from normalized entries."""
    root = Path.cwd()
    entries_with_paths = _load_normalized_entries_with_paths(root, date)
    if not entries_with_paths:
        typer.secho(f"No normalized entries for {date}", fg=typer.colors.RED, err=True)
        raise typer.Exit(1)

    manifest_entries = _load_manifest(_manifest_path(root))
    manifest_index = _manifest_by_id(manifest_entries)
    profile, claims = _load_profile_components(root)
    config = _load_config(root)

    entries = [entry for entry, _ in entries_with_paths]
    proposals = _characterize_payload(entries, profile, claims, manifest_index, config)

    timestamp = _format_timestamp(_now())
    batch_id = f"{date}-{timestamp}"

    inputs: list[dict[str, Any]] = []
    for data, path in entries_with_paths:
        entry_id = str(data.get("id") or path.stem)
        manifest_entry = manifest_index.get(entry_id, {})
        inputs.append(
            {
                "id": entry_id,
                "normalized_path": _relative_source_path(path, root),
                "source_hash": data.get("source_hash") or manifest_entry.get("hash"),
                "manifest_hash": manifest_entry.get("hash"),
                "tags": data.get("tags", []),
            },
        )

    batch = {
        "batch_id": batch_id,
        "created_at": timestamp,
        "date": date,
        "inputs": inputs,
        "proposals": proposals,
        "meta": _build_meta("prompts/characterize.md"),
    }

    pending_dir = _pending_updates_dir(root)
    pending_dir.mkdir(parents=True, exist_ok=True)
    batch_path = _pending_updates_path(root, batch_id)
    _write_yaml_if_changed(batch_path, batch, schema="profile_updates")
    typer.echo(str(batch_path))


@app.command("review-updates")
def review_updates(
    file: Path | None = typer.Option(
        None,
        "--file",
        help="Specific pending batch to review (defaults to latest).",
    ),
    apply: bool = typer.Option(False, "--apply", help="Apply the proposed updates."),
) -> None:
    """Review or apply pending profile update batches."""
    root = Path.cwd()
    batch_path = file or _latest_pending_batch(root)
    if batch_path is None:
        typer.secho("No pending profile update batches found.", fg=typer.colors.RED, err=True)
        raise typer.Exit(1)
    if not batch_path.exists():
        typer.secho(f"Batch file not found: {batch_path}", fg=typer.colors.RED, err=True)
        raise typer.Exit(1)

    batch = _load_yaml(batch_path)
    try:
        validate_schema("profile_updates", batch)
    except SchemaValidationError as exc:
        typer.secho(str(exc), fg=typer.colors.RED, err=True)
        raise typer.Exit(1)

    proposals = batch.get("proposals", {})
    claim_proposals = proposals.get("claims", []) or []
    facet_proposals = proposals.get("facets", []) or []

    batch_id = batch.get("batch_id") or batch_path.stem
    typer.echo(
        f"Batch {batch_id}: {len(claim_proposals)} claim(s), {len(facet_proposals)} facet(s)",
    )

    for proposal in claim_proposals:
        claim = proposal.get("claim") if isinstance(proposal, dict) else None
        if not isinstance(claim, dict):
            continue
        typer.echo(f"- claim {claim.get('id')}: {claim.get('statement')}")

    for proposal in facet_proposals:
        if not isinstance(proposal, dict):
            continue
        path = proposal.get("path")
        if not path:
            continue
        typer.echo(f"- facet {path}: {proposal.get('value')}")

    if not apply:
        return

    profile, claims_data = _load_profile_components(root)
    timestamp = _format_timestamp(_now())
    applied = 0

    for proposal in claim_proposals:
        claim = proposal.get("claim") if isinstance(proposal, dict) else None
        if not isinstance(claim, dict):
            continue
        if _apply_claim_upsert(claims_data, claim, timestamp):
            applied += 1

    for proposal in facet_proposals:
        if not isinstance(proposal, dict):
            continue
        path = proposal.get("path") or proposal.get("target")
        if not path:
            continue
        if _apply_profile_update(profile, str(path), proposal.get("value"), timestamp):
            applied += 1

    if not applied:
        typer.echo("No changes applied")
        return

    _atomic_write(
        root / "profile" / "self_profile.yaml",
        profile,
        schema="self_profile",
    )
    _atomic_write(
        root / "profile" / "claims.yaml",
        {"claims": claims_data},
        schema="claims",
    )
    typer.echo(f"Applied {applied} updates from {batch_path}")


@app.command()
def advise(
    question: str = typer.Argument(..., help="Question for the advisor to answer."),
) -> None:
    """Generate advice from the current profile."""
    root = Path.cwd()
    profile, claims = _load_profile_components(root)
    if not profile and not claims:
        typer.secho("No profile data", fg=typer.colors.RED, err=True)
        raise typer.Exit(1)

    config = _load_config(root)
    advice_content = _advice_payload(question, profile, claims, config)
    advice_content["meta"] = _build_meta("prompts/advise.md")

    day = _created_date(_format_timestamp(_now()))
    advice_path = _derived_advice_path(root, day, question)
    _write_yaml_if_changed(advice_path, advice_content, schema="advice")
    typer.echo(str(advice_path))


@ollama_app.command("health")
def ollama_health() -> None:
    """Show fake Ollama model availability in offline mode."""
    if os.getenv("AIJOURNAL_FAKE_OLLAMA") != "1":
        typer.secho(
            "Set AIJOURNAL_FAKE_OLLAMA=1 to use the offline health probe.",
            fg=typer.colors.RED,
            err=True,
        )
        raise typer.Exit(1)

    models = [
        {"name": "llama3.1:8b-instruct", "size": "8B", "quant": "Q4_K_M"},
        {"name": "llama3.1:70b-instruct", "size": "70B", "quant": "Q4_K_M"},
    ]
    payload = {
        "endpoint": "fake://ollama",
        "default": models[0]["name"],
        "models": models,
    }
    typer.echo(yaml.safe_dump(payload, sort_keys=False).rstrip())


def _parse_datetime(value: str) -> datetime | None:
    try:
        candidate = value.replace("Z", "+00:00") if value.endswith("Z") else value
        dt = datetime.fromisoformat(candidate)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=UTC)
        return dt
    except ValueError:
        return None


def _days_between(now: datetime, past: str | None) -> float | None:
    if not past:
        return None
    dt = _parse_datetime(past)
    if not dt:
        return None
    delta = now - dt
    return delta.total_seconds() / 86400.0


def _flatten_facets(node: Any, prefix: str = "") -> list[tuple[str, dict[str, Any]]]:
    items: list[tuple[str, dict[str, Any]]] = []
    if isinstance(node, dict):
        if "last_updated" in node:
            items.append((prefix or "root", node))
        for key, value in node.items():
            child_prefix = f"{prefix}.{key}" if prefix else str(key)
            items.extend(_flatten_facets(value, child_prefix))
    elif isinstance(node, list):
        for idx, value in enumerate(node):
            child_prefix = f"{prefix}[{idx}]"
            items.extend(_flatten_facets(value, child_prefix))
    return items


def _load_profile_components(root: Path) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    profile_path = root / "profile" / "self_profile.yaml"
    claims_path = root / "profile" / "claims.yaml"

    profile = _load_yaml(profile_path) if profile_path.exists() else {}
    claims_data = _load_yaml(claims_path).get("claims", []) if claims_path.exists() else []
    return profile, claims_data


def _persona_profile_slice(profile: dict[str, Any]) -> dict[str, Any]:
    """Return the subset of profile facets that feed persona core."""
    if not profile:
        return {}
    subset: dict[str, Any] = {}
    for key in PERSONA_PROFILE_KEYS:
        value = profile.get(key)
        if value is None:
            continue
        subset[key] = copy.deepcopy(value)
    if not subset:
        return copy.deepcopy(profile)
    return subset


def _claim_weight(claim: dict[str, Any], weights: dict[str, Any]) -> float:
    """Resolve the impact weight for a claim based on config overrides."""
    claim_type = str(claim.get("type") or "preference")
    claim_types_raw = weights.get("claim_types")
    claim_weights = claim_types_raw if isinstance(claim_types_raw, dict) else {}
    if claim_type in claim_weights:
        return _coerce_float(claim_weights[claim_type]) or 1.0
    if "default" in claim_weights:
        return _coerce_float(claim_weights["default"]) or 1.0
    if "claims" in weights:
        return _coerce_float(weights["claims"]) or 1.0
    return CLAIM_TYPE_IMPACT_DEFAULTS.get(claim_type, 1.0)


def _claim_effective_strength(
    claim: dict[str, Any],
    *,
    weights: dict[str, Any],
    now: datetime,
) -> float:
    strength = _clamp01(claim.get("strength", 0.5))
    weight = _claim_weight(claim, weights)
    last_updated = _claim_last_updated(claim)
    review_after = _coerce_int(claim.get("review_after_days")) or 120
    days_since = _days_between(now, last_updated) or 0.0
    staleness = min(2.0, max(0.0, days_since / max(review_after, 1)))
    decay = exp(-0.2 * staleness)
    status = str(claim.get("status") or "tentative").lower()
    status_bonus = 0.05 if status == "accepted" else 0.0
    return (strength * weight * decay) + status_bonus


def _rank_claims_for_persona(
    claims: list[dict[str, Any]],
    weights: dict[str, Any],
    now: datetime,
) -> list[dict[str, Any]]:
    if not claims:
        return []
    ranked: list[tuple[int, float, dict[str, Any]]] = []
    status_priority = {"accepted": 0, "tentative": 1, "rejected": 2}
    for claim in claims:
        score = _claim_effective_strength(claim, weights=weights, now=now)
        status = str(claim.get("status") or "tentative").lower()
        priority = status_priority.get(status, 1)
        ranked.append((priority, -score, copy.deepcopy(claim)))
    ranked.sort(key=lambda item: (item[0], item[1], item[2].get("id", "")))
    return [item[2] for item in ranked]


def _estimate_persona_tokens(persona_block: dict[str, Any], char_per_token: float) -> int:
    width = max(char_per_token, 0.01)
    text = yaml.safe_dump(persona_block, sort_keys=False)
    return max(1, ceil(len(text) / width))


def _select_persona_claims(
    claims: list[dict[str, Any]],
    profile_slice: dict[str, Any],
    *,
    token_budget: int,
    char_per_token: float,
    min_claims: int,
    max_claims: int,
) -> tuple[list[dict[str, Any]], list[str], int, bool]:
    selected = claims[:max_claims]
    persona_block = {"profile": profile_slice, "claims": selected}
    tokens = _estimate_persona_tokens(persona_block, char_per_token)
    trimmed_ids: list[str] = []
    while tokens > token_budget and len(selected) > min_claims and selected:
        removed = selected.pop()
        trimmed_ids.append(str(removed.get("id", f"claim-{len(trimmed_ids)}")))
        persona_block = {"profile": profile_slice, "claims": selected}
        tokens = _estimate_persona_tokens(persona_block, char_per_token)
    budget_exceeded = tokens > token_budget
    return selected, trimmed_ids, tokens, budget_exceeded


def _relative_to_root(path: Path, root: Path) -> str:
    try:
        return str(path.relative_to(root))
    except ValueError:
        return str(path)


def _persona_build_impl(
    *,
    token_budget_override: int | None = None,
    max_claims_override: int | None = None,
    min_claims_override: int | None = None,
) -> tuple[Path, bool]:
    root = Path.cwd()
    profile, claims = _load_profile_components(root)
    if not profile and not claims:
        typer.secho(
            "No profile data or claims available; run `aijournal init` or add entries first.",
            fg=typer.colors.RED,
            err=True,
        )
        raise typer.Exit(1)

    config = _load_config(root)
    persona_cfg_raw = config.get("persona")
    persona_cfg = persona_cfg_raw if isinstance(persona_cfg_raw, dict) else {}
    token_budget = int(
        token_budget_override
        if token_budget_override is not None
        else persona_cfg.get("token_budget") or PERSONA_DEFAULTS["token_budget"],
    )
    max_claims = int(
        max_claims_override
        if max_claims_override is not None
        else persona_cfg.get("max_claims") or PERSONA_DEFAULTS["max_claims"],
    )
    min_claims = int(
        min_claims_override
        if min_claims_override is not None
        else persona_cfg.get("min_claims") or PERSONA_DEFAULTS["min_claims"],
    )
    if token_budget <= 0:
        typer.secho("Token budget must be positive", fg=typer.colors.RED, err=True)
        raise typer.Exit(1)
    if max_claims <= 0:
        typer.secho("max-claims must be positive", fg=typer.colors.RED, err=True)
        raise typer.Exit(1)
    if min_claims < 0 or min_claims > max_claims:
        typer.secho("min-claims must be between 0 and max-claims", fg=typer.colors.RED, err=True)
        raise typer.Exit(1)

    token_estimator_raw = config.get("token_estimator")
    token_estimator = token_estimator_raw if isinstance(token_estimator_raw, dict) else {}
    char_per_token = _coerce_float(token_estimator.get("char_per_token")) or DEFAULT_CHAR_PER_TOKEN

    profile_slice = _persona_profile_slice(profile)
    now_dt = _now()
    impact_weights_raw = config.get("impact_weights")
    impact_weights = impact_weights_raw if isinstance(impact_weights_raw, dict) else {}
    ranked_claims = _rank_claims_for_persona(claims, impact_weights, now_dt)

    if not profile_slice and not ranked_claims:
        typer.secho(
            "Nothing to include in persona core; add profile facets or claims first.",
            fg=typer.colors.RED,
            err=True,
        )
        raise typer.Exit(1)

    selected_claims, trimmed_ids, planned_tokens, budget_exceeded = _select_persona_claims(
        ranked_claims,
        profile_slice,
        token_budget=token_budget,
        char_per_token=char_per_token,
        min_claims=min_claims,
        max_claims=max_claims,
    )

    persona_block = {
        "profile": profile_slice,
        "claims": selected_claims,
    }
    now = _format_timestamp(now_dt)
    meta: dict[str, Any] = {
        "generated_at": now,
        "token_budget": token_budget,
        "planned_tokens": planned_tokens,
        "char_per_token": char_per_token,
        "selection_strategy": "strength*impact*decay",
        "claim_pool": len(ranked_claims),
        "claim_count": len(selected_claims),
        "max_claims": max_claims,
        "min_claims": min_claims,
        "budget_exceeded": budget_exceeded,
    }
    if trimmed_ids:
        meta["trimmed"] = [{"type": "claim", "id": cid} for cid in trimmed_ids]

    sources: dict[str, str] = {}
    profile_path = root / "profile" / "self_profile.yaml"
    claims_path = root / "profile" / "claims.yaml"
    if profile_path.exists():
        sources["profile"] = _relative_to_root(profile_path, root)
    if claims_path.exists():
        sources["claims"] = _relative_to_root(claims_path, root)
    if sources:
        meta["sources"] = sources

    payload = {
        "persona": persona_block,
        "meta": meta,
    }
    persona_path = root / "derived" / "persona" / "persona_core.yaml"
    changed = _write_yaml_if_changed(persona_path, payload, schema="persona_core")
    return persona_path, changed


@persona_app.command("build")
def persona_build(
    token_budget: int | None = typer.Option(
        None,
        help="Override the persona_core token budget (default 1200).",
    ),
    max_claims: int | None = typer.Option(
        None,
        help="Limit the number of claims considered for persona core.",
    ),
    min_claims: int | None = typer.Option(
        None,
        help="Guarantee at least this many claims remain even if over budget.",
    ),
) -> None:
    """Regenerate derived/persona/persona_core.yaml."""
    path, changed = _persona_build_impl(
        token_budget_override=token_budget,
        max_claims_override=max_claims,
        min_claims_override=min_claims,
    )
    status = "Wrote" if changed else "Persona core already up to date"
    typer.echo(f"{status}: {path}")


def _atomic_write(
    path: Path,
    payload: dict[str, Any],
    *,
    schema: str | None = None,
) -> None:
    if schema:
        try:
            validate_schema(schema, payload)
        except SchemaValidationError as exc:
            typer.secho(str(exc), fg=typer.colors.RED, err=True)
            raise typer.Exit(1)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")
    tmp.replace(path)


def _impact_for(path: str, weights: dict[str, float]) -> float:
    key = path.split(".", 1)[0]
    return float(weights.get(key, 1.0))


def _compute_rankings(
    profile: dict[str, Any],
    claims: list[dict[str, Any]],
    weights: dict[str, float],
    now: datetime,
) -> list[tuple[str, float]]:
    ranked: list[tuple[str, float]] = []

    for path, facet in _flatten_facets(profile):
        days = _days_between(now, str(facet.get("last_updated", "")))
        review = facet.get("review_after_days") or 90
        if days is None:
            continue
        staleness = days / float(review)
        ranked.append((path, staleness * _impact_for(path, weights)))

    for claim in claims:
        path = claim.get("id", "claim")
        days = _days_between(now, _claim_last_updated(claim))
        review = claim.get("review_after_days") or 90
        if days is None:
            continue
        staleness = days / float(review)
        ranked.append((f"claim:{path}", staleness * float(weights.get("claims", 1.0))))

    ranked.sort(key=lambda item: (-item[1], item[0]))
    return ranked


def _build_targeted_probes(
    rankings: list[tuple[str, float]],
    entries: list[dict[str, Any]],
    *,
    max_items: int = 4,
) -> list[str]:
    title = entries[0].get("title", "recent notes") if entries else "recent notes"
    probes: list[str] = []
    for path, score in rankings:
        probes.append(
            (
                f"- {path}: What new observations from {title} should update this area? "
                f"(score {score:.2f})"
            ),
        )
        if len(probes) >= max_items:
            break
    if len(probes) < 2:
        return []
    return probes


def _print_rankings(ranked: list[tuple[str, float]]) -> None:
    if not ranked:
        typer.echo("No profile data")
        return
    typer.echo("Profile review priority:")
    for idx, (path, score) in enumerate(ranked, start=1):
        typer.echo(f"{idx}. {path} (score {score:.2f})")


def _apply_claim_upsert(
    claims: list[dict[str, Any]],
    value: dict[str, Any],
    timestamp: str,
) -> bool:
    try:
        normalized = _ensure_claim_atom_dict(
            value,
            timestamp=timestamp,
            default_sources=None,
        )
    except ValueError:
        return False

    for idx, claim in enumerate(claims):
        if claim.get("id") == normalized.get("id"):
            provenance = normalized.get("provenance", {})
            prev_provenance = claim.get("provenance", {})
            if prev_provenance.get("first_seen"):
                provenance["first_seen"] = prev_provenance["first_seen"]
            if not provenance.get("sources") and prev_provenance.get("sources"):
                provenance["sources"] = prev_provenance["sources"]
            normalized["provenance"] = provenance
            if claim == normalized:
                return False
            claims[idx] = normalized
            return True
    claims.append(normalized)
    return True


def _apply_profile_update(profile: dict[str, Any], target: str, value: Any, timestamp: str) -> bool:
    parts = target.split(".")
    current = profile
    for part in parts[:-1]:
        current = current.setdefault(part, {})
    key = parts[-1]
    previous = current.get(key)
    if previous == value:
        return False
    current[key] = value
    current["last_updated"] = timestamp
    return True


def _claim_last_updated(claim: dict[str, Any]) -> str | None:
    provenance = claim.get("provenance")
    if isinstance(provenance, dict):
        return _coerce_timestamp(provenance.get("last_updated"))
    return _coerce_timestamp(claim.get("last_updated"))


def _profile_status_impl() -> None:
    root = Path.cwd()
    profile, claims = _load_profile_components(root)
    config_path = root / "config" / "config.yaml"
    config = _load_yaml(config_path) if config_path.exists() else {}
    weights = config.get("impact_weights", {})

    if not profile and not claims:
        typer.echo("No profile data")
        raise typer.Exit(0)

    rankings = _compute_rankings(profile, claims, weights, _now())
    if not rankings:
        typer.echo("No profile data")
        raise typer.Exit(0)
    _print_rankings(rankings)


@profile_app.command("status")
def profile_status() -> None:
    """Show ranked facets/claims needing review."""
    _profile_status_impl()


@app.command("profile-status")
def profile_status_alias() -> None:
    """Alias command for profile status (for backwards compatibility)."""
    _profile_status_impl()


@app.command("interview")
def interview(
    date: str = typer.Option(..., "--date", "-d", help="Date (YYYY-MM-DD) to review."),
) -> None:
    """Surface targeted interview probes based on stale facets (fake LLM)."""
    if os.getenv("AIJOURNAL_FAKE_OLLAMA") != "1":
        typer.secho(
            "Only fake Ollama mode is implemented for interview.",
            fg=typer.colors.RED,
            err=True,
        )
        raise typer.Exit(1)

    root = Path.cwd()
    profile, claims = _load_profile_components(root)
    if not profile and not claims:
        typer.secho("No profile data", fg=typer.colors.RED, err=True)
        raise typer.Exit(1)

    entries = _load_normalized_entries(root, date)
    if not entries:
        typer.secho(f"No normalized entries for {date}", fg=typer.colors.RED, err=True)
        raise typer.Exit(1)

    config_path = root / "config" / "config.yaml"
    config = _load_yaml(config_path) if config_path.exists() else {}
    weights = config.get("impact_weights", {})

    rankings = _compute_rankings(profile, claims, weights, _now())
    probes = _build_targeted_probes(rankings, entries)
    if not probes:
        probes = HIGH_IMPACT_PROBES

    typer.echo("Interview probes:")
    for probe in probes:
        typer.echo(probe)


def _write_json_if_changed(path: Path, payload: dict[str, Any]) -> bool:
    existing = None
    if path.exists():
        try:
            existing = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            existing = None
    if existing == payload:
        return False
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    tmp.replace(path)
    return True


def _pack_token_count(text: str) -> int:
    return len(text.split())


def _pack_trim_entries(
    entries: list[dict[str, Any]],
    budget: int,
    trimmed: list[dict[str, str]],
) -> None:
    priority_roles = TRIM_PRIORITY

    def total_tokens() -> int:
        return sum(entry["tokens"] for entry in entries)

    if total_tokens() <= budget:
        return

    for role in priority_roles:
        for entry in entries:
            if entry["role"] == role and entry["tokens"] > 0:
                trimmed.append({"role": role, "path": entry["path"]})
                entry["content"] = "(trimmed due to token budget)"
                entry["tokens"] = 0
                if total_tokens() <= budget:
                    return


def _collect_pack_entries(
    root: Path,
    level: str,
    date: str,
    history_days: int,
) -> list[tuple[str, Path]]:
    level = level.upper()
    entries: list[tuple[str, Path, int]] = []

    def add_path(
        role: str,
        path: Path,
        *,
        required: bool = False,
        day_index: int = 0,
    ) -> None:
        if path.is_file():
            entries.append((role, path, day_index))
        elif required:
            msg = f"Missing required file {path}"
            typer.secho(msg, fg=typer.colors.RED, err=True)
            raise typer.Exit(1)

    def add_dir(
        role: str,
        directory: Path,
        *,
        required: bool = False,
        pattern: str | None = None,
        recursive: bool = False,
        day_index: int = 0,
    ) -> None:
        if not directory.exists():
            if required:
                msg = f"Missing required files under {directory}"
                typer.secho(msg, fg=typer.colors.RED, err=True)
                raise typer.Exit(1)
            return
        if recursive:
            files = sorted(p for p in directory.rglob("*") if p.is_file())
        elif pattern:
            files = sorted(directory.glob(pattern))
        else:
            files = sorted(p for p in directory.iterdir() if p.is_file())
        if not files and required:
            msg = f"Missing required files under {directory}"
            typer.secho(msg, fg=typer.colors.RED, err=True)
            raise typer.Exit(1)
        for file in files:
            entries.append((role, file, day_index))

    def add_day_artifacts(
        day: str,
        day_index: int,
        *,
        include_raw: bool,
        required_core: bool,
    ) -> None:
        normalized_dir = root / "data" / "normalized" / day
        add_dir(
            "normalized",
            normalized_dir,
            required=required_core,
            pattern="*.yaml",
            day_index=day_index,
        )
        summary_path = root / "derived" / "summaries" / f"{day}.yaml"
        add_path("summaries", summary_path, day_index=day_index)
        microfacts_path = root / "derived" / "microfacts" / f"{day}.yaml"
        add_path("microfacts", microfacts_path, day_index=day_index)
        if include_raw:
            year, month, day_part = day.split("-")
            journal_dir = root / "data" / "journal" / year / month / day_part
            add_dir("journal_raw", journal_dir, pattern="*.md", day_index=day_index)

    if level not in {"L1", "L2", "L3", "L4"}:
        msg = f"Unsupported level {level}"
        typer.secho(msg, fg=typer.colors.RED, err=True)
        raise typer.Exit(1)

    add_path("profile", root / "profile" / "self_profile.yaml", required=True)
    add_path("claims", root / "profile" / "claims.yaml", required=True)

    include_history = level == "L4"
    if level in {"L2", "L3", "L4"}:
        day_offsets: list[tuple[str, int]] = [(date, 0)]
        if include_history and history_days > 0:
            anchor = datetime.fromisoformat(date)
            for offset in range(1, history_days + 1):
                prior = (anchor - timedelta(days=offset)).strftime("%Y-%m-%d")
                day_offsets.append((prior, offset))

        for day_value, idx in day_offsets:
            add_day_artifacts(
                day_value,
                idx,
                include_raw=include_history,
                required_core=idx == 0,
            )

    if level in {"L3", "L4"}:
        advice_dir = root / "derived" / "advice" / date
        add_dir("advice", advice_dir, pattern="*.yaml")
        profile_suggestions = root / "derived" / "profile_suggestions" / f"{date}.yaml"
        add_path("profile_suggestions", profile_suggestions)

    if level == "L4":
        prompts_dir = root / "prompts"
        add_dir("prompt", prompts_dir, pattern="*.md", recursive=True)
        add_path("config", root / "config" / "config.yaml")

    role_rank = {role: idx for idx, role in enumerate(ROLE_ORDER)}
    entries.sort(key=lambda item: (role_rank.get(item[0], len(ROLE_ORDER)), item[2], str(item[1])))
    return [(role, path) for role, path, _ in entries]


def _latest_normalized_day(root: Path) -> str | None:
    base = root / "data" / "normalized"
    if not base.exists():
        return None
    candidates = sorted(p.name for p in base.iterdir() if p.is_dir())
    return candidates[-1] if candidates else None


def _resolve_pack_date(level: str, requested: str | None, root: Path) -> str:
    if requested:
        return requested
    if level == "L1":
        return _now().strftime("%Y-%m-%d")
    latest = _latest_normalized_day(root)
    if latest:
        return latest
    typer.secho("No normalized entries available; provide --date.", fg=typer.colors.RED, err=True)
    raise typer.Exit(1)


def _build_pack_payload(
    entries: list[dict[str, Any]],
    level: str,
    date: str,
    trimmed: list[dict[str, str]],
    total_tokens: int,
    max_tokens: int,
) -> dict[str, Any]:
    return {
        "level": level,
        "date": date,
        "files": entries,
        "meta": {
            "total_tokens": total_tokens,
            "max_tokens": max_tokens,
            "trimmed": trimmed,
            "generated_at": _format_timestamp(_now()),
        },
    }


@app.command("pack")
def pack(
    level: str = typer.Option("L2", "--level", "-l", help="Context depth (L1 or L2)."),
    date: str | None = typer.Option(
        None,
        "--date",
        "-d",
        help="Date (YYYY-MM-DD); auto-detected for L2 when omitted.",
    ),
    output: Path | None = typer.Option(None, "--output", "-o"),
    max_tokens: int | None = typer.Option(None, "--max-tokens"),
    fmt: str = typer.Option("yaml", "--format", help="Output format: yaml or json."),
    history_days: int = typer.Option(
        0,
        "--history-days",
        help="Number of previous days to include (L4 packs only).",
    ),
    dry_run: bool = typer.Option(False, "--dry-run", help="Show plan without emitting payload."),
) -> None:
    """Assemble a context bundle for prompting."""
    level = level.upper()
    fmt_value = fmt.lower()
    if fmt_value not in {"yaml", "json"}:
        typer.secho(f"Unsupported format: {fmt}", fg=typer.colors.RED, err=True)
        raise typer.Exit(1)
    fmt = fmt_value
    if history_days < 0:
        typer.secho("--history-days must be zero or positive.", fg=typer.colors.RED, err=True)
        raise typer.Exit(1)
    if level != "L4" and history_days:
        typer.secho("--history-days is only supported for L4 packs.", fg=typer.colors.RED, err=True)
        raise typer.Exit(1)

    default_budget = {"L1": 1200, "L2": 2000, "L3": 2600, "L4": 3200}
    budget = max_tokens or default_budget.get(level, 2000)

    root = Path.cwd()
    resolved_date = _resolve_pack_date(level, date, root)
    entries_info = _collect_pack_entries(
        root,
        level,
        resolved_date,
        history_days if level == "L4" else 0,
    )

    entries_payload: list[dict[str, Any]] = []
    for role, path in entries_info:
        text = path.read_text(encoding="utf-8")
        rel = _relative_source_path(path, root)
        entries_payload.append(
            {
                "role": role,
                "path": rel,
                "tokens": _pack_token_count(text),
                "content": text,
            },
        )

    total_tokens = sum(entry["tokens"] for entry in entries_payload)
    trimmed: list[dict[str, str]] = []
    if total_tokens > budget:
        _pack_trim_entries(entries_payload, budget, trimmed)
        total_tokens = sum(entry["tokens"] for entry in entries_payload)

    payload = _build_pack_payload(
        entries_payload,
        level,
        resolved_date,
        trimmed,
        total_tokens,
        budget,
    )

    if dry_run:
        typer.echo("Planned files:")
        for entry in entries_payload:
            typer.echo(f"- {entry['path']} ({entry['tokens']} tokens)")
        if trimmed:
            trimmed_display = ", ".join(f"{item['role']}:{item['path']}" for item in trimmed)
            typer.echo(f"trimmed: {trimmed_display}")
        return

    if output:
        output.parent.mkdir(parents=True, exist_ok=True)
        changed = False
        if fmt == "json":
            changed = _write_json_if_changed(output, payload)
        else:
            changed = _write_yaml_if_changed(output, payload)
        if changed:
            typer.echo(str(output))
        else:
            typer.echo("No changes")
        return

    if fmt == "json":
        typer.echo(json.dumps(payload, indent=2))
    else:
        typer.echo(yaml.safe_dump(payload, sort_keys=False))


@index_app.command("rebuild")
def index_rebuild(
    since: str | None = typer.Option(
        None,
        "--since",
        help="Earliest date (YYYY-MM-DD or Nd) to include when rebuilding.",
    ),
    limit: int | None = typer.Option(
        None,
        "--limit",
        help="Maximum number of normalized files to index (debug/testing).",
    ),
) -> None:
    """Rebuild the Annoy+SQLite retrieval index from normalized YAML."""
    root = Path.cwd()
    if limit is not None and limit <= 0:
        typer.secho("--limit must be positive when provided.", fg=typer.colors.RED, err=True)
        raise typer.Exit(1)

    since_filter = _resolve_since_filter(since)
    entries = _collect_normalized_files(root, since_filter)
    if limit is not None:
        entries = entries[:limit]
    if not entries:
        typer.secho(
            "No normalized entries available for indexing.",
            fg=typer.colors.RED,
            err=True,
        )
        raise typer.Exit(1)

    manifest_index = _manifest_by_id(_load_manifest(_manifest_path(root)))
    tasks = _prepare_index_tasks(entries, root, manifest_index)
    if not tasks:
        typer.secho("No normalized entries with valid IDs found.", fg=typer.colors.RED, err=True)
        raise typer.Exit(1)

    config = _load_config(root)
    embedder = _build_embedding_backend(config)
    ann_trees, search_k_factor, char_per_token = _index_settings(config)

    db_path = _index_db_path(root)
    index_dir = _index_dir(root)
    index_dir.mkdir(parents=True, exist_ok=True)
    conn = _connect_index_db(db_path, overwrite=True)
    with conn:
        _prepare_index_schema(conn)
        stats = _index_entries(conn, tasks, embedder, char_per_token)

    chunk_total, entry_total = _gather_index_stats(conn)
    _rebuild_annoy_index(conn, embedder.dim, ann_trees, _annoy_index_path(root))
    conn.commit()
    if stats["dates"]:
        _write_chunk_manifests(conn, root, stats["dates"], embedder)
    conn.close()

    _write_index_meta(
        root,
        embedder=embedder,
        chunk_total=chunk_total,
        entry_total=entry_total,
        mode="rebuild",
        fake_mode=_use_fake_llm(),
        ann_trees=ann_trees,
        search_k_factor=search_k_factor,
        char_per_token=char_per_token,
        since=since_filter,
        limit=limit,
        touched_dates=stats["dates"],
    )

    typer.echo(
        f"Indexed {chunk_total} chunks across {entry_total} entries (mode: rebuild).",
    )


@index_app.command("tail")
def index_tail(
    since: str | None = typer.Option(
        None,
        "--since",
        help="Earliest date (YYYY-MM-DD or Nd) to scan for new normalized files.",
    ),
    days: int = typer.Option(
        7,
        "--days",
        help="Rolling window (days) when --since is omitted.",
    ),
    limit: int | None = typer.Option(
        None,
        "--limit",
        help="Maximum number of normalized files to inspect.",
    ),
) -> None:
    """Incrementally ingest new normalized entries into the retrieval index."""
    if days <= 0:
        typer.secho("--days must be positive.", fg=typer.colors.RED, err=True)
        raise typer.Exit(1)
    if limit is not None and limit <= 0:
        typer.secho("--limit must be positive when provided.", fg=typer.colors.RED, err=True)
        raise typer.Exit(1)

    root = Path.cwd()
    db_path = _index_db_path(root)
    if not db_path.exists():
        typer.secho(
            "Index database not found. Run `aijournal index rebuild` first.",
            fg=typer.colors.RED,
            err=True,
        )
        raise typer.Exit(1)

    since_filter = _resolve_since_filter(since, fallback_days=days)
    entries = _collect_normalized_files(root, since_filter)
    if limit is not None:
        entries = entries[:limit]
    if not entries:
        typer.secho(
            "No normalized entries matched the requested window.",
            fg=typer.colors.RED,
            err=True,
        )
        raise typer.Exit(1)

    manifest_index = _manifest_by_id(_load_manifest(_manifest_path(root)))
    tasks = _prepare_index_tasks(entries, root, manifest_index)
    conn = _connect_index_db(db_path)
    try:
        pending = _filter_tasks_for_tail(conn, tasks)
        if not pending:
            typer.echo("Index already up to date for requested window.")
            return

        config = _load_config(root)
        embedder = _build_embedding_backend(config)
        ann_trees, search_k_factor, char_per_token = _index_settings(config)

        with conn:
            _prepare_index_schema(conn)
            stats = _index_entries(conn, pending, embedder, char_per_token)

        chunk_total, entry_total = _gather_index_stats(conn)
        _rebuild_annoy_index(conn, embedder.dim, ann_trees, _annoy_index_path(root))
        conn.commit()
        if stats["dates"]:
            _write_chunk_manifests(conn, root, stats["dates"], embedder)

        _write_index_meta(
            root,
            embedder=embedder,
            chunk_total=chunk_total,
            entry_total=entry_total,
            mode="tail",
            fake_mode=_use_fake_llm(),
            ann_trees=ann_trees,
            search_k_factor=search_k_factor,
            char_per_token=char_per_token,
            since=since_filter,
            limit=limit,
            touched_dates=stats["dates"],
        )

        typer.echo(
            f"Indexed {stats['chunks']} chunks across {stats['entries']} entries (mode: tail).",
        )
    finally:
        conn.close()


def _index_dir(root: Path) -> Path:
    return root / "derived" / "index"


def _index_db_path(root: Path) -> Path:
    return _index_dir(root) / INDEX_DB_FILENAME


def _annoy_index_path(root: Path) -> Path:
    return _index_dir(root) / ANNOY_FILENAME


def _chunk_manifest_dir(root: Path) -> Path:
    return _index_dir(root) / "chunks"


def _index_meta_path(root: Path) -> Path:
    return _index_dir(root) / INDEX_META_FILENAME


def _collect_normalized_files(
    root: Path,
    since: str | None,
) -> list[tuple[str, Path]]:
    normalized_root = root / "data" / "normalized"
    if not normalized_root.exists():
        return []
    entries: list[tuple[str, Path]] = []
    for day_dir in sorted(p for p in normalized_root.iterdir() if p.is_dir()):
        day = day_dir.name
        if since and day < since:
            continue
        for file in sorted(day_dir.glob("*.yaml")):
            entries.append((day, file))
    return entries


def _resolve_since_filter(value: str | None, fallback_days: int | None = None) -> str | None:
    if value:
        text = value.strip()
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", text):
            return text
        if text.endswith("d") and text[:-1].isdigit():
            window = int(text[:-1])
            return (_now() - timedelta(days=window)).strftime("%Y-%m-%d")
        typer.secho(
            "--since must be YYYY-MM-DD or Nd (e.g., 7d)",
            fg=typer.colors.RED,
            err=True,
        )
        raise typer.Exit(1)
    if fallback_days is not None:
        return (_now() - timedelta(days=fallback_days)).strftime("%Y-%m-%d")
    return None


def _build_embedding_backend(config: dict[str, Any]) -> EmbeddingBackend:
    model = str(config.get("embedding_model") or "nomic-embed-text")
    host = os.getenv("AIJOURNAL_OLLAMA_HOST")
    return EmbeddingBackend(model, host=host, fake_mode=_use_fake_llm())


def _index_settings(config: dict[str, Any]) -> tuple[int, float, float]:
    index_cfg_raw = config.get("index")
    index_cfg = index_cfg_raw if isinstance(index_cfg_raw, dict) else {}
    ann_trees = int(index_cfg.get("ann_trees") or 50)
    search_k_factor = float(index_cfg.get("search_k_factor") or 3.0)
    token_cfg_raw = config.get("token_estimator")
    token_cfg = token_cfg_raw if isinstance(token_cfg_raw, dict) else {}
    char_per_token = float(token_cfg.get("char_per_token") or 4.2)
    return ann_trees, search_k_factor, char_per_token


def _prepare_index_tasks(
    entries: Sequence[tuple[str, Path]],
    root: Path,
    manifest_index: dict[str, dict[str, Any]],
) -> list[IndexTask]:
    tasks: list[IndexTask] = []
    for day, path in entries:
        entry = _load_yaml(path)
        normalized_id = str(entry.get("id") or "").strip()
        if not normalized_id:
            continue
        normalized_path = _relative_source_path(path, root)
        manifest = manifest_index.get(normalized_id)
        source_hash = _select_source_hash(entry, path)
        if manifest and not source_hash:
            manifest_hash = manifest.get("hash")
            source_hash = str(manifest_hash) if manifest_hash else None
        tasks.append(
            IndexTask(
                day=day,
                path=path,
                normalized_path=normalized_path,
                normalized_id=normalized_id,
                entry=entry,
                source_hash=source_hash,
                manifest=manifest,
            ),
        )
    return tasks


def _filter_tasks_for_tail(conn: sqlite3.Connection, tasks: Sequence[IndexTask]) -> list[IndexTask]:
    pending: list[IndexTask] = []
    for task in tasks:
        stored = conn.execute(
            "SELECT source_hash FROM sources WHERE normalized_path = ?",
            (task.normalized_path,),
        ).fetchone()
        stored_hash = stored[0] if stored else None
        if stored_hash and task.source_hash and stored_hash == task.source_hash:
            continue
        pending.append(task)
    return pending


def _select_source_hash(entry: dict[str, Any], path: Path) -> str | None:
    source_hash = entry.get("source_hash")
    if isinstance(source_hash, str) and source_hash.strip():
        return source_hash.strip()
    return _hash_file(path)


def _hash_file(path: Path) -> str | None:
    try:
        return sha256(path.read_bytes()).hexdigest()
    except OSError:
        return None


def _index_entries(
    conn: sqlite3.Connection,
    tasks: Sequence[IndexTask],
    embedder: EmbeddingBackend,
    char_per_token: float,
) -> dict[str, Any]:
    touched_dates: set[str] = set()
    processed_entries = 0
    processed_chunks = 0
    timestamp = _format_timestamp(_now())

    for task in tasks:
        chunk_records = _build_chunk_records(
            task.entry,
            task.normalized_path,
            char_per_token=char_per_token,
            manifest=task.manifest,
            source_hash=task.source_hash,
        )
        if not chunk_records:
            continue
        vectors = embedder.embed([chunk.chunk_text for chunk in chunk_records])
        _delete_chunks_for_entry(conn, task.normalized_id)
        for chunk, vector in zip(chunk_records, vectors, strict=False):
            chunk.embedding = vector
            _insert_chunk_record(conn, chunk)
        _upsert_source_record(
            conn,
            SourceRecord(
                normalized_path=task.normalized_path,
                normalized_id=task.normalized_id,
                date=chunk_records[0].date,
                source_hash=task.source_hash,
                manifest_hash=chunk_records[0].manifest_hash,
                chunk_count=len(chunk_records),
                updated_at=timestamp,
            ),
        )
        touched_dates.add(task.day)
        processed_entries += 1
        processed_chunks += len(chunk_records)

    return {"entries": processed_entries, "chunks": processed_chunks, "dates": touched_dates}


def _build_chunk_records(
    entry: dict[str, Any],
    normalized_path: str,
    *,
    char_per_token: float,
    manifest: dict[str, Any] | None,
    source_hash: str | None,
) -> list[ChunkRecord]:
    entry_id = str(entry.get("id") or "").strip()
    if not entry_id:
        return []
    created_at = _normalize_created_at(entry.get("created_at") or _format_timestamp(_now()))
    date_value = _created_date(created_at)
    tags = entry.get("tags") or []
    scope_tags = [str(tag) for tag in tags]
    paragraphs = _entry_paragraphs(entry)
    chunk_texts = _chunk_paragraphs(paragraphs)
    if not chunk_texts:
        chunk_texts = [entry.get("title") or entry_id]

    chunk_records: list[ChunkRecord] = []
    manifest_hash = manifest.get("hash") if manifest else None
    source_type = entry.get("source_type") or (manifest.get("source_type") if manifest else None)

    for idx, text in enumerate(chunk_texts):
        chunk_records.append(
            ChunkRecord(
                chunk_id=f"{entry_id}#c{idx}",
                normalized_id=entry_id,
                normalized_path=normalized_path,
                chunk_index=idx,
                chunk_text=text,
                date=date_value,
                tags=scope_tags,
                source_type=source_type,
                source_path=entry.get("source_path") or normalized_path,
                tokens=_token_estimate(text, char_per_token),
                source_hash=source_hash,
                manifest_hash=str(manifest_hash) if manifest_hash else None,
            ),
        )

    return chunk_records


def _entry_paragraphs(entry: dict[str, Any]) -> list[str]:
    paragraphs: list[str] = []
    summary = entry.get("summary")
    if isinstance(summary, str) and summary.strip():
        paragraphs.append(summary.strip())
    for section in entry.get("sections") or []:
        heading = str(section.get("heading") or "").strip()
        snippet = str(section.get("summary") or "").strip()
        if heading and snippet:
            paragraphs.append(f"{heading}: {snippet}")
        elif heading:
            paragraphs.append(heading)
        elif snippet:
            paragraphs.append(snippet)
    if not paragraphs:
        title = str(entry.get("title") or entry.get("id") or "entry").strip()
        if title:
            paragraphs.append(title)
    return paragraphs


def _chunk_paragraphs(paragraphs: Iterable[str]) -> list[str]:
    chunks: list[str] = []
    current: list[str] = []
    length = 0
    for paragraph in paragraphs:
        text = paragraph.strip()
        if not text:
            continue
        if current and length + len(text) + 2 > CHUNK_MAX_CHARS:
            chunks.append("\n\n".join(current))
            current = [text]
            length = len(text)
            continue
        current.append(text)
        length += len(text) + (2 if length else 0)
        if length >= CHUNK_TARGET_CHARS:
            chunks.append("\n\n".join(current))
            current = []
            length = 0
    if current:
        chunks.append("\n\n".join(current))
    return chunks


def _token_estimate(text: str, char_per_token: float) -> int:
    divisor = char_per_token if char_per_token > 0 else 4.2
    return max(1, ceil(len(text) / divisor))


def _connect_index_db(path: Path, *, overwrite: bool = False) -> sqlite3.Connection:
    if overwrite and path.exists():
        path.unlink()
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA foreign_keys=ON;")
    return conn


def _prepare_index_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS chunks (
            chunk_id TEXT PRIMARY KEY,
            normalized_id TEXT NOT NULL,
            normalized_path TEXT NOT NULL,
            chunk_index INTEGER NOT NULL,
            chunk_text TEXT NOT NULL,
            date TEXT NOT NULL,
            tags TEXT NOT NULL,
            source_type TEXT,
            source_path TEXT,
            tokens INTEGER NOT NULL,
            source_hash TEXT,
            manifest_hash TEXT,
            embedding BLOB NOT NULL
        );
        CREATE TABLE IF NOT EXISTS chunk_fts (
            chunk_id TEXT PRIMARY KEY,
            chunk_text TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS sources (
            normalized_path TEXT PRIMARY KEY,
            normalized_id TEXT NOT NULL,
            date TEXT NOT NULL,
            source_hash TEXT,
            manifest_hash TEXT,
            chunk_count INTEGER NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS annoy_map (
            annoy_idx INTEGER PRIMARY KEY,
            chunk_id TEXT NOT NULL
        );
        """,
    )


def _delete_chunks_for_entry(conn: sqlite3.Connection, normalized_id: str) -> None:
    rows = conn.execute(
        "SELECT chunk_id FROM chunks WHERE normalized_id = ?",
        (normalized_id,),
    ).fetchall()
    if rows:
        conn.executemany(
            "DELETE FROM chunk_fts WHERE chunk_id = ?",
            ((row[0],) for row in rows),
        )
    conn.execute("DELETE FROM chunks WHERE normalized_id = ?", (normalized_id,))


def _insert_chunk_record(conn: sqlite3.Connection, chunk: ChunkRecord) -> None:
    if chunk.embedding is None:
        msg = "Chunk embedding missing"
        raise RuntimeError(msg)
    tags_json = json.dumps(chunk.tags, sort_keys=True)
    conn.execute(
        """
        INSERT INTO chunks (
            chunk_id, normalized_id, normalized_path, chunk_index, chunk_text,
            date, tags, source_type, source_path, tokens, source_hash,
            manifest_hash, embedding
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            chunk.chunk_id,
            chunk.normalized_id,
            chunk.normalized_path,
            chunk.chunk_index,
            chunk.chunk_text,
            chunk.date,
            tags_json,
            chunk.source_type,
            chunk.source_path,
            chunk.tokens,
            chunk.source_hash,
            chunk.manifest_hash,
            _vector_to_blob(chunk.embedding),
        ),
    )
    conn.execute(
        "INSERT INTO chunk_fts (chunk_id, chunk_text) VALUES (?, ?)",
        (chunk.chunk_id, chunk.chunk_text),
    )


def _vector_to_blob(vector: Sequence[float]) -> memoryview:
    arr = array("f", vector)
    return memoryview(arr.tobytes())


def _blob_to_vector(blob: bytes) -> list[float]:
    arr = array("f")
    arr.frombytes(blob)
    return list(arr)


def _upsert_source_record(conn: sqlite3.Connection, record: SourceRecord) -> None:
    conn.execute(
        """
        INSERT INTO sources (
            normalized_path, normalized_id, date, source_hash, manifest_hash,
            chunk_count, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(normalized_path) DO UPDATE SET
            normalized_id=excluded.normalized_id,
            date=excluded.date,
            source_hash=excluded.source_hash,
            manifest_hash=excluded.manifest_hash,
            chunk_count=excluded.chunk_count,
            updated_at=excluded.updated_at
        """,
        (
            record.normalized_path,
            record.normalized_id,
            record.date,
            record.source_hash,
            record.manifest_hash,
            record.chunk_count,
            record.updated_at,
        ),
    )


def _rebuild_annoy_index(
    conn: sqlite3.Connection,
    dimension: int,
    ann_trees: int,
    output_path: Path,
) -> None:
    rows = conn.execute(
        "SELECT chunk_id, embedding FROM chunks ORDER BY chunk_id",
    ).fetchall()
    if not rows:
        if output_path.exists():
            output_path.unlink()
        conn.execute("DELETE FROM annoy_map")
        return

    index = AnnoyIndex(dimension, metric=ANN_METRIC)
    for idx, row in enumerate(rows):
        vector = _blob_to_vector(row["embedding"])
        index.add_item(idx, vector)
    index.build(ann_trees)
    index.save(str(output_path))

    conn.execute("DELETE FROM annoy_map")
    conn.executemany(
        "INSERT INTO annoy_map (annoy_idx, chunk_id) VALUES (?, ?)",
        ((idx, row["chunk_id"]) for idx, row in enumerate(rows)),
    )


def _write_chunk_manifests(
    conn: sqlite3.Connection,
    root: Path,
    days: Iterable[str],
    embedder: EmbeddingBackend,
) -> None:
    chunk_dir = _chunk_manifest_dir(root)
    chunk_dir.mkdir(parents=True, exist_ok=True)
    for day in sorted(set(days)):
        rows = conn.execute(
            """
            SELECT chunk_id, normalized_id, chunk_index, chunk_text, tags,
                   source_type, source_path, tokens, source_hash, manifest_hash, embedding
            FROM chunks
            WHERE date = ?
            ORDER BY normalized_id, chunk_index
            """,
            (day,),
        ).fetchall()
        chunks: list[dict[str, Any]] = []
        payload = {
            "day": day,
            "chunks": chunks,
            "meta": {
                "embedding_model": embedder.model,
                "vector_dimension": embedder.dim,
                "generated_at": _format_timestamp(_now()),
            },
        }
        vectors: list[list[float]] = []
        for row in rows:
            tags = json.loads(row["tags"] or "[]")
            chunks.append(
                {
                    "chunk_id": row["chunk_id"],
                    "normalized_id": row["normalized_id"],
                    "chunk_index": row["chunk_index"],
                    "chunk_text": row["chunk_text"],
                    "tags": tags,
                    "source_type": row["source_type"],
                    "source_path": row["source_path"],
                    "tokens": row["tokens"],
                    "source_hash": row["source_hash"],
                    "manifest_hash": row["manifest_hash"],
                },
            )
            vectors.append(_blob_to_vector(row["embedding"]))

        manifest_path = chunk_dir / f"{day}.yaml"
        manifest_path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")
        vector_array = (
            np.array(vectors, dtype="float32")
            if vectors
            else np.zeros((0, embedder.dim), dtype="float32")
        )
        np.save(chunk_dir / f"{day}.npy", vector_array)


def _gather_index_stats(conn: sqlite3.Connection) -> tuple[int, int]:
    chunk_total = conn.execute("SELECT COUNT(*) FROM chunks").fetchone()[0]
    entry_total = conn.execute("SELECT COUNT(*) FROM sources").fetchone()[0]
    return int(chunk_total), int(entry_total)


def _write_index_meta(
    root: Path,
    *,
    embedder: EmbeddingBackend,
    chunk_total: int,
    entry_total: int,
    mode: str,
    fake_mode: bool,
    ann_trees: int,
    search_k_factor: float,
    char_per_token: float,
    since: str | None,
    limit: int | None,
    touched_dates: Iterable[str],
) -> None:
    payload = {
        "embedding_model": embedder.model,
        "vector_dimension": embedder.dimension,
        "chunk_count": chunk_total,
        "entry_count": entry_total,
        "mode": mode,
        "fake_mode": fake_mode,
        "annoy_trees": ann_trees,
        "search_k_factor": search_k_factor,
        "char_per_token": char_per_token,
        "since": since,
        "limit": limit,
        "touched_dates": sorted(set(touched_dates)),
        "updated_at": _format_timestamp(_now()),
    }
    _index_meta_path(root).write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
