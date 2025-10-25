"""Typer CLI entrypoint for aijournal."""

from __future__ import annotations

import json
import os
import re
from datetime import datetime, timedelta, timezone
from hashlib import sha256
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

from agno.agent import Agent
import typer
import yaml

from aijournal.ingest_agent import (
    AgentSettings,
    IngestResult,
    IngestSection,
    build_ingest_agent,
    ingest_with_agent,
)


app = typer.Typer(help="Local-first personal journal utilities.")
profile_app = typer.Typer(help="Profile utilities.")
ollama_app = typer.Typer(help="Ollama helpers (fake mode only).")
app.add_typer(profile_app, name="profile")
app.add_typer(ollama_app, name="ollama")


@app.callback()
def main() -> None:
    """aijournal command-line interface."""

    # Intentionally empty; commands provide functionality.
    return None


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
    "derived/index",
)

SEED_FILES = {
    "config/config.yaml": """model: \"llama3.1:8b-instruct\"\ntemperature: 0.2\nseed: 42\npaths:\n  data: \"data\"\n  profile: \"profile\"\n  derived: \"derived\"\n  prompts: \"prompts\"\nimpact_weights:\n  values_goals: 1.5\n  decision_style: 1.3\n  affect_energy: 1.2\n  traits: 1.0\n  social: 0.9\nadvisor:\n  max_recos: 3\n  include_risks: true\n""",
    "profile/self_profile.yaml": """traits:\n  big_five:\n    openness: {score: 0.74, method: self_report, user_verified: true}\n    conscientiousness: {score: 0.68, method: inferred}\n    extraversion: {score: 0.42, method: self_report}\n    agreeableness: {score: 0.61, method: inferred}\n    neuroticism: {score: 0.33, method: self_report}\n  regulatory_focus: {promotion: 0.7, prevention: 0.3}\n  risk_tolerance: {domain: \"career\", level: \"medium-high\"}\n  time_horizon: {preferred: \"long\", evidence: [\"2024_l2_...\"]}\n  review_after_days: 180\n\nvalues_motivations:\n  schwartz_top5: [\"Self-Direction\", \"Achievement\", \"Universalism\", \"Benevolence\", \"Security\"]\n  sdt: {autonomy: 0.8, competence: 0.7, relatedness: 0.6}\n  drivers:\n    - value: \"Mastery over tools & systems\"\n      method: inferred\n      confidence: 0.8\n  review_after_days: 120\n\ngoals:\n  short_term:\n    - value: \"Ship personal agent MVP\"\n      why: \"reduce friction\"\n      krs: [\"CLI usable\", \"context pack <1800t\"]\n      review_after_days: 30\n  long_term:\n    - value: \"Work-life consistency with twins\"\n      krs: [\"2 evenings/week protected\"]\n      review_after_days: 90\n  anti_goals:\n    - value: \"No late-night production firefighting as a norm\"\n      reason: \"family/health\"\n\ndecision_style:\n  default: {speed_vs_quality: \"quality\", satisficer_vs_maximizer: \"bounded_maximizer\"}\n  implementation_intentions:\n    - if: \"Feeling anxious before presentations\"\n      then: \"Run checklist + 10-min rehearsal\"\n      evidence: [\"2021-04-12_l1\"]\n\naffect_energy:\n  energy_map: {morning: \"high\", afternoon: \"medium\", evening: \"low\"}\n  stressors: [\"ambiguous deadlines\", \"noisy environment\"]\n  coping_strategies: [\"walks\", \"time-boxing\", \"no email after 18:00\"]\n\nsocial:\n  relationships:\n    - person: \"Jess\"\n      role: \"coworker\"\n      notes: \"great feedback partner\"\n      boundary: \"no pings after 18:00\"\n\nboundaries_ethics:\n  red_lines: [\"No sharing private family data\", \"No health advice beyond guidelines\"]\n\ncoaching_prefs:\n  tone: \"direct, warm\"\n  depth: \"concrete first, theory second\"\n  probing: {max_questions: 2, prefer: \"yes/no + one short open follow-up\"}\n""",
    "profile/claims.yaml": """claims: []\n""",
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

MARKDOWN_SUFFIXES = {".md", ".markdown"}


def _now() -> datetime:
    """Return the current UTC time; separated for easy monkeypatching in tests."""

    return datetime.now(tz=timezone.utc)


def _slugify_title(title: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")
    return slug or "entry"


def _format_timestamp(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


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
        raise ValueError("Markdown entry missing YAML/TOML frontmatter delimiter")

    parts = text.split(delimiter, 2)
    if len(parts) < 3:
        raise ValueError("Incomplete YAML/TOML frontmatter block")

    frontmatter_raw = parts[1].strip()
    body = parts[2]
    return frontmatter_raw, body


def _scan_headings(text: str) -> List[dict[str, Any]]:
    sections: List[dict[str, Any]] = []
    for line in text.splitlines():
        heading_match = re.match(r"^(#{1,6})\s+(.*)$", line.strip())
        if heading_match:
            sections.append(
                {
                    "heading": heading_match.group(2).strip(),
                    "level": len(heading_match.group(1)),
                }
            )
    return sections


def _parse_entry(entry_path: Path) -> tuple[dict[str, Any], List[dict[str, Any]]]:
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


def _load_existing_yaml(path: Path) -> Optional[dict[str, Any]]:
    if not path.exists():
        return None
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def _write_yaml_if_changed(path: Path, data: dict[str, Any]) -> bool:
    existing = _load_existing_yaml(path)
    if existing == data:
        return False

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
    return True


def _normalize_created_at(value: Any) -> str:
    if isinstance(value, datetime):
        dt = value.astimezone(timezone.utc)
        return _format_timestamp(dt)

    if isinstance(value, str):
        candidate = value.replace("Z", "+00:00") if value.endswith("Z") else value
        try:
            dt = datetime.fromisoformat(candidate)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
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


def _coerce_float(value: Any) -> Optional[float]:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _coerce_int(value: Any) -> Optional[int]:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _manifest_path(root: Path) -> Path:
    return root / "data" / "manifest" / "ingested.yaml"


def _load_manifest(path: Path) -> List[dict[str, Any]]:
    if not path.exists():
        return []
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or []
    if isinstance(data, list):
        return data
    return []


def _write_manifest(path: Path, entries: List[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(entries, sort_keys=False), encoding="utf-8")


def _discover_markdown_files(inputs: Iterable[Path]) -> List[Path]:
    files: List[Path] = []
    for source in inputs:
        resolved = source.expanduser().resolve()
        if resolved.is_dir():
            for candidate in sorted(resolved.rglob("*")):
                if candidate.is_file() and candidate.suffix.lower() in MARKDOWN_SUFFIXES:
                    files.append(candidate)
        elif resolved.is_file():
            files.append(resolved)

    unique: List[Path] = []
    seen: set[Path] = set()
    for file in files:
        if file not in seen:
            seen.add(file)
            unique.append(file)
    return unique


def _normalize_tags(raw: Iterable[Any]) -> List[str]:
    tags: List[str] = []
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


def _clean_summary(text: Optional[str], fallback: Optional[str] = None) -> Optional[str]:
    candidate = (text or "").strip()
    if candidate:
        for marker in (',"entry_id"', ',"tags"', ',"sections"'):
            idx = candidate.find(marker)
            if idx != -1:
                candidate = candidate[:idx]
                break
        candidate = candidate.replace("\n", " ").strip().strip('\"')
        sentences = re.split(r"(?<=[.!?])\s+", candidate)
        sentences = [sentence.strip() for sentence in sentences if sentence.strip()]
        if sentences:
            candidate = " ".join(sentences[:2])
        else:
            candidate = ""

    if not candidate and fallback:
        candidate = fallback.strip()

    return candidate or None


def _merge_sections(
    primary: Iterable[IngestSection],
    fallback: Iterable[dict[str, Any]],
    *,
    title: str,
    limit: int = 6,
) -> List[dict[str, Any]]:
    entries: List[dict[str, Any]] = []
    seen: set[str] = set()

    def add_section(heading: str, level: int, summary: Optional[str] = None) -> None:
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

    for section in primary:
        add_section(section.heading, section.level, section.summary)
        if len(entries) >= limit:
            return entries

    for section in fallback:
        add_section(str(section.get("heading") or title), int(section.get("level", 2)))
        if len(entries) >= limit:
            return entries

    if not entries:
        add_section(title or "entry", 1)
    return entries


def _sanitize_entry_id(candidate: Optional[str], title: str, date_str: str, digest: str) -> str:
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


def _extract_frontmatter_tags(frontmatter: dict[str, Any]) -> List[str]:
    values: List[str] = []
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
    fallback_sections: Optional[List[dict[str, Any]]] = None,
    fallback_tags: Optional[List[str]] = None,
    fallback_summary: Optional[str] = None,
) -> tuple[dict[str, Any], str]:
    created_at = structured.created_at
    if isinstance(created_at, datetime):
        created_str = _format_timestamp(created_at.astimezone(timezone.utc))
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


def _load_normalized_entries(root: Path, day: str) -> List[dict[str, Any]]:
    folder = root / "data" / "normalized" / day
    if not folder.exists():
        return []
    entries: List[dict[str, Any]] = []
    for file in sorted(folder.glob("*.yaml")):
        entries.append(_load_yaml(file))
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


def _hash_prompt(prompt_path: str) -> Optional[str]:
    path = Path(prompt_path)
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


def _fake_summarize(entries: List[dict[str, Any]]) -> List[str]:
    bullets: List[str] = []
    for entry in entries:
        title = entry.get("title", entry.get("id", "entry"))
        sections = entry.get("sections") or []
        section_titles = ", ".join(sec.get("heading", "") for sec in sections[:2] if sec)
        if section_titles:
            bullets.append(f"{title}: {section_titles}")
        else:
            bullets.append(f"{title}: no sections")
    return bullets or ["No content available"]


def _fake_microfacts(entries: List[dict[str, Any]]) -> List[dict[str, Any]]:
    facts: List[dict[str, Any]] = []
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
            }
        )
    return facts or [
        {
            "id": "fact-empty",
            "statement": "No normalized entries available",
            "confidence": 0.0,
            "evidence": {},
        }
    ]


def _fake_advise(question: str, profile: dict[str, Any], claims: List[dict[str, Any]]) -> dict[str, Any]:
    claim = claims[0] if claims else {}
    boundaries = profile.get("boundaries_ethics", {}).get("red_lines", [])
    values = profile.get("values_motivations", {}).get("schwartz_top5", [])

    recommendations = [
        {
            "title": claim.get("statement", "Reflect on priorities"),
            "actions": [
                "Review morning deep-work blocks",
                f"Question posed: {question}",
            ],
            "respecting": boundaries,
        }
    ]

    alignment = {
        "claims": [claim.get("id")] if claim.get("id") else [],
        "values": values,
    }

    return {"recommendations": recommendations, "alignment": alignment}


def _fake_profile_suggestions(
    entries: List[dict[str, Any]],
    profile: dict[str, Any],
    claims: List[dict[str, Any]],
) -> dict[str, Any]:
    upserts = []
    updates = []

    for entry in entries[:1]:
        upserts.append(
            {
                "target": "claims",
                "operation": "upsert",
                "value": {
                    "id": f"auto_{entry.get('id', 'entry')}",
                    "statement": entry.get("title", "New observation"),
                    "confidence": 0.6,
                },
            }
        )

    if profile:
        updates.append(
            {
                "target": "values_motivations.schwartz_top5",
                "operation": "update",
                "value": profile.get("values_motivations", {}).get("schwartz_top5", []),
            }
        )

    return {"upserts": upserts, "updates": updates}


@app.command()
def init(
    path: Optional[Path] = typer.Option(
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
    title: str = typer.Argument(..., help="Title for the journal entry."),
    tags: Optional[List[str]] = typer.Option(
        None,
        "--tags",
        "-t",
        help="Tag to attach to the entry (repeatable).",
    ),
) -> None:
    """Create a new journal entry with YAML frontmatter."""

    now = _now()
    slug = f"{now.strftime('%Y-%m-%d')}-{_slugify_title(title)}"
    entry_path = _journal_path(Path.cwd(), now, slug)

    if entry_path.exists():
        typer.echo(f"Entry exists: {entry_path}")
        raise typer.Exit(1)

    entry_path.parent.mkdir(parents=True, exist_ok=True)

    frontmatter = {
        "id": slug,
        "created_at": _format_timestamp(now),
        "title": title,
        "tags": tags or [],
    }

    yaml_block = yaml.safe_dump(frontmatter, sort_keys=False).strip()
    body = "---\n" + yaml_block + "\n---\n\n"
    entry_path.write_text(body, encoding="utf-8")

    typer.echo(str(entry_path))


@app.command()
def ingest(
    sources: List[Path] = typer.Argument(
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
    limit: Optional[int] = typer.Option(
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
        typer.secho("No Markdown files found in the provided sources.", fg=typer.colors.RED, err=True)
        raise typer.Exit(1)
    if limit is not None:
        files = files[:limit]

    config = _load_config(root)
    model_name = _resolve_model_name(config)
    is_fake = _use_fake_llm()
    agent: Optional[Agent] = None
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
        _write_yaml_if_changed(normalized_path, normalized)

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
    entry: Path = typer.Argument(..., exists=True, readable=True, help="Path to journal Markdown entry."),
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
        typer.secho("Frontmatter must include id, created_at, title.", fg=typer.colors.RED, err=True)
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
    _write_yaml_if_changed(output_path, normalized_data)
    typer.echo(str(output_path))


@app.command()
def summarize(
    date: str = typer.Option(..., "--date", "-d", help="Date (YYYY-MM-DD) to summarize."),
) -> None:
    """Generate a daily summary from normalized entries (fake LLM mode)."""

    root = Path.cwd()
    entries = _load_normalized_entries(root, date)
    if not entries:
        typer.secho(f"No normalized entries for {date}", fg=typer.colors.RED, err=True)
        raise typer.Exit(1)

    if os.getenv("AIJOURNAL_FAKE_OLLAMA") != "1":
        typer.secho(
            "Only fake Ollama mode is implemented for summarize.",
            fg=typer.colors.RED,
            err=True,
        )
        raise typer.Exit(1)

    bullets = _fake_summarize(entries)
    summary_data = {
        "day": date,
        "bullets": bullets,
        "meta": _build_meta("prompts/summarize_day.md"),
    }

    summary_path = _derived_summary_path(root, date)
    _write_yaml_if_changed(summary_path, summary_data)
    typer.echo(str(summary_path))


@app.command()
def facts(
    date: str = typer.Option(..., "--date", "-d", help="Date (YYYY-MM-DD) to analyze."),
) -> None:
    """Generate micro-facts from normalized entries (fake LLM mode)."""

    root = Path.cwd()
    entries = _load_normalized_entries(root, date)
    if not entries:
        typer.secho(f"No normalized entries for {date}", fg=typer.colors.RED, err=True)
        raise typer.Exit(1)

    if os.getenv("AIJOURNAL_FAKE_OLLAMA") != "1":
        typer.secho(
            "Only fake Ollama mode is implemented for facts.",
            fg=typer.colors.RED,
            err=True,
        )
        raise typer.Exit(1)

    facts_data = {
        "facts": _fake_microfacts(entries),
        "meta": _build_meta("prompts/extract_facts.md"),
    }

    facts_path = _derived_microfacts_path(root, date)
    _write_yaml_if_changed(facts_path, facts_data)
    typer.echo(str(facts_path))


@profile_app.command("suggest")
def profile_suggest(
    date: str = typer.Option(..., "--date", "-d", help="Date (YYYY-MM-DD) to analyze."),
) -> None:
    """Suggest profile updates based on normalized entries (fake LLM)."""

    if os.getenv("AIJOURNAL_FAKE_OLLAMA") != "1":
        typer.secho(
            "Only fake Ollama mode is implemented for profile suggest.",
            fg=typer.colors.RED,
            err=True,
        )
        raise typer.Exit(1)

    root = Path.cwd()
    entries = _load_normalized_entries(root, date)
    if not entries:
        typer.secho(f"No normalized entries for {date}", fg=typer.colors.RED, err=True)
        raise typer.Exit(1)

    profile, claims = _load_profile_components(root)
    if not profile and not claims:
        typer.secho("No profile data", fg=typer.colors.RED, err=True)
        raise typer.Exit(1)

    suggestions = _fake_profile_suggestions(entries, profile, claims)
    suggestions["meta"] = _build_meta("prompts/profile_suggest.md")

    path = _derived_profile_suggestions_path(root, date)
    _write_yaml_if_changed(path, suggestions)
    typer.echo(str(path))


@profile_app.command("apply")
def profile_apply(
    date: str = typer.Option(..., "--date", "-d", help="Date (YYYY-MM-DD) to apply."),
    file: Optional[Path] = typer.Option(None, "--file", help="Path to suggestions YAML."),
    yes: bool = typer.Option(False, "--yes", help="Apply without prompting."),
) -> None:
    """Apply profile suggestions to authoritative files (offline)."""

    root = Path.cwd()
    suggestions_path = file or (
        root / "derived" / "profile_suggestions" / f"{date}.yaml"
    )

    if not suggestions_path.exists():
        typer.secho(f"Suggestions file not found: {suggestions_path}", fg=typer.colors.RED, err=True)
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

    _atomic_write(root / "profile" / "self_profile.yaml", profile)
    _atomic_write(root / "profile" / "claims.yaml", {"claims": claims})
    typer.echo("Applied 1 suggestions file")


@app.command()
def advise(
    question: str = typer.Argument(..., help="Question for the advisor to answer."),
) -> None:
    """Generate advice from the current profile (fake LLM mode)."""

    if os.getenv("AIJOURNAL_FAKE_OLLAMA") != "1":
        typer.secho(
            "Only fake Ollama mode is implemented for advise.",
            fg=typer.colors.RED,
            err=True,
        )
        raise typer.Exit(1)

    root = Path.cwd()
    profile, claims = _load_profile_components(root)
    if not profile and not claims:
        typer.secho("No profile data", fg=typer.colors.RED, err=True)
        raise typer.Exit(1)

    advice_content = _fake_advise(question, profile, claims)
    advice_content["question"] = question
    advice_content["meta"] = _build_meta("prompts/advise.md")

    day = _created_date(_format_timestamp(_now()))
    advice_path = _derived_advice_path(root, day, question)
    _write_yaml_if_changed(advice_path, advice_content)
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


def _parse_datetime(value: str) -> Optional[datetime]:
    try:
        candidate = value.replace("Z", "+00:00") if value.endswith("Z") else value
        dt = datetime.fromisoformat(candidate)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except ValueError:
        return None


def _days_between(now: datetime, past: Optional[str]) -> Optional[float]:
    if not past:
        return None
    dt = _parse_datetime(past)
    if not dt:
        return None
    delta = now - dt
    return delta.total_seconds() / 86400.0


def _flatten_facets(node: Any, prefix: str = "") -> List[tuple[str, Dict[str, Any]]]:
    items: List[tuple[str, Dict[str, Any]]] = []
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


def _load_profile_components(root: Path) -> tuple[dict[str, Any], List[dict[str, Any]]]:
    profile_path = root / "profile" / "self_profile.yaml"
    claims_path = root / "profile" / "claims.yaml"

    profile = _load_yaml(profile_path) if profile_path.exists() else {}
    claims_data = _load_yaml(claims_path).get("claims", []) if claims_path.exists() else []
    return profile, claims_data


def _atomic_write(path: Path, payload: dict[str, Any]) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")
    tmp.replace(path)


def _impact_for(path: str, weights: Dict[str, float]) -> float:
    key = path.split(".", 1)[0]
    return float(weights.get(key, 1.0))


def _compute_rankings(
    profile: dict[str, Any],
    claims: List[dict[str, Any]],
    weights: Dict[str, float],
    now: datetime,
) -> List[tuple[str, float]]:
    ranked: List[tuple[str, float]] = []

    for path, facet in _flatten_facets(profile):
        days = _days_between(now, str(facet.get("last_updated", "")))
        review = facet.get("review_after_days") or 90
        if days is None:
            continue
        staleness = days / float(review)
        ranked.append((path, staleness * _impact_for(path, weights)))

    for claim in claims:
        path = claim.get("id", "claim")
        days = _days_between(now, str(claim.get("last_updated", "")))
        review = claim.get("review_after_days") or 90
        if days is None:
            continue
        staleness = days / float(review)
        ranked.append((f"claim:{path}", staleness * float(weights.get("claims", 1.0))))

    ranked.sort(key=lambda item: (-item[1], item[0]))
    return ranked


def _build_targeted_probes(
    rankings: List[tuple[str, float]],
    entries: List[dict[str, Any]],
    *,
    max_items: int = 4,
) -> List[str]:
    title = entries[0].get("title", "recent notes") if entries else "recent notes"
    probes: List[str] = []
    for path, score in rankings:
        probes.append(
            f"- {path}: What new observations from {title} should update this area? (score {score:.2f})"
        )
        if len(probes) >= max_items:
            break
    if len(probes) < 2:
        return []
    return probes


def _print_rankings(ranked: List[tuple[str, float]]) -> None:
    if not ranked:
        typer.echo("No profile data")
        return
    typer.echo("Profile review priority:")
    for idx, (path, score) in enumerate(ranked, start=1):
        typer.echo(f"{idx}. {path} (score {score:.2f})")


def _apply_claim_upsert(claims: List[dict[str, Any]], value: dict[str, Any], timestamp: str) -> bool:
    new_value = dict(value)
    new_value["last_updated"] = timestamp
    for idx, claim in enumerate(claims):
        if claim.get("id") == new_value.get("id"):
            if claim == new_value:
                return False
            claims[idx] = new_value
            return True
    claims.append(new_value)
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
    entries: List[dict[str, Any]],
    budget: int,
    trimmed: List[dict[str, str]],
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
) -> List[tuple[str, Path]]:
    level = level.upper()
    entries: List[tuple[str, Path, int]] = []

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
            raise typer.Exit(f"Missing required file {path}")

    def add_dir(
        role: str,
        directory: Path,
        *,
        required: bool = False,
        pattern: Optional[str] = None,
        recursive: bool = False,
        day_index: int = 0,
    ) -> None:
        if not directory.exists():
            if required:
                raise typer.Exit(f"Missing required files under {directory}")
            return
        if recursive:
            files = sorted(p for p in directory.rglob("*") if p.is_file())
        elif pattern:
            files = sorted(directory.glob(pattern))
        else:
            files = sorted(p for p in directory.iterdir() if p.is_file())
        if not files and required:
            raise typer.Exit(f"Missing required files under {directory}")
        for file in files:
            entries.append((role, file, day_index))

    def add_day_artifacts(day: str, day_index: int, *, include_raw: bool, required_core: bool) -> None:
        normalized_dir = root / "data" / "normalized" / day
        add_dir("normalized", normalized_dir, required=required_core, pattern="*.yaml", day_index=day_index)
        summary_path = root / "derived" / "summaries" / f"{day}.yaml"
        add_path("summaries", summary_path, day_index=day_index)
        microfacts_path = root / "derived" / "microfacts" / f"{day}.yaml"
        add_path("microfacts", microfacts_path, day_index=day_index)
        if include_raw:
            year, month, day_part = day.split("-")
            journal_dir = root / "data" / "journal" / year / month / day_part
            add_dir("journal_raw", journal_dir, pattern="*.md", day_index=day_index)

    if level not in {"L1", "L2", "L3", "L4"}:
        raise typer.Exit(f"Unsupported level {level}")

    add_path("profile", root / "profile" / "self_profile.yaml", required=True)
    add_path("claims", root / "profile" / "claims.yaml", required=True)

    include_history = level == "L4"
    if level in {"L2", "L3", "L4"}:
        day_offsets: List[Tuple[str, int]] = [(date, 0)]
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


def _latest_normalized_day(root: Path) -> Optional[str]:
    base = root / "data" / "normalized"
    if not base.exists():
        return None
    candidates = sorted(p.name for p in base.iterdir() if p.is_dir())
    return candidates[-1] if candidates else None


def _resolve_pack_date(level: str, requested: Optional[str], root: Path) -> str:
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
    entries: List[dict[str, Any]],
    level: str,
    date: str,
    trimmed: List[dict[str, str]],
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
    date: Optional[str] = typer.Option(
        None,
        "--date",
        "-d",
        help="Date (YYYY-MM-DD); auto-detected for L2 when omitted.",
    ),
    output: Optional[Path] = typer.Option(None, "--output", "-o"),
    max_tokens: Optional[int] = typer.Option(None, "--max-tokens"),
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
    entries_info = _collect_pack_entries(root, level, resolved_date, history_days if level == "L4" else 0)

    entries_payload: List[dict[str, Any]] = []
    for role, path in entries_info:
        text = path.read_text(encoding="utf-8")
        rel = _relative_source_path(path, root)
        entries_payload.append(
            {
                "role": role,
                "path": rel,
                "tokens": _pack_token_count(text),
                "content": text,
            }
        )

    total_tokens = sum(entry["tokens"] for entry in entries_payload)
    trimmed: List[dict[str, str]] = []
    if total_tokens > budget:
        _pack_trim_entries(entries_payload, budget, trimmed)
        total_tokens = sum(entry["tokens"] for entry in entries_payload)

    payload = _build_pack_payload(entries_payload, level, resolved_date, trimmed, total_tokens, budget)

    if dry_run:
        typer.echo("Planned files:")
        for entry in entries_payload:
            typer.echo(f"- {entry['path']} ({entry['tokens']} tokens)")
        if trimmed:
            typer.echo("trimmed: " + ", ".join(trimmed))
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
