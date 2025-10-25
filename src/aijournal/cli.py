"""Typer CLI entrypoint for aijournal."""

from __future__ import annotations

import os
import re
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

import typer
import yaml


app = typer.Typer(help="Local-first personal journal utilities.")
profile_app = typer.Typer(help="Profile utilities.")
app.add_typer(profile_app, name="profile")


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
    if not text.startswith("---"):
        raise ValueError("Markdown entry missing YAML frontmatter delimiter")

    parts = text.split("---", 2)
    if len(parts) < 3:
        raise ValueError("Incomplete YAML frontmatter block")

    frontmatter_raw = parts[1].strip()
    body = parts[2]
    return frontmatter_raw, body


def _parse_entry(entry_path: Path) -> tuple[dict[str, Any], List[dict[str, Any]]]:
    text = entry_path.read_text(encoding="utf-8")
    frontmatter_raw, body = _split_frontmatter(text)
    data = yaml.safe_load(frontmatter_raw) or {}

    sections: List[dict[str, Any]] = []
    for line in body.splitlines():
        heading_match = re.match(r"^(#{1,6})\s+(.*)$", line.strip())
        if heading_match:
            sections.append(
                {
                    "heading": heading_match.group(2).strip(),
                    "level": len(heading_match.group(1)),
                }
            )

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


def _hash_prompt(prompt_path: str) -> str:
    return sha256(prompt_path.encode("utf-8")).hexdigest()


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
        if "review_after_days" in node and "last_updated" in node:
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


def _print_rankings(ranked: List[tuple[str, float]]) -> None:
    if not ranked:
        typer.echo("No profile data")
        return
    typer.echo("Profile review priority:")
    for idx, (path, score) in enumerate(ranked, start=1):
        typer.echo(f"{idx}. {path} (score {score:.2f})")


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
