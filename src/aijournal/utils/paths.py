"""Path helpers and layout constants for aijournal workspaces."""

from __future__ import annotations

from pathlib import Path
from textwrap import dedent
from typing import TYPE_CHECKING

from aijournal.common.constants import (
    DEFAULT_EMBEDDING_MODEL,
    DEFAULT_MODEL_NAME,
    DEFAULT_OLLAMA_HOST,
)

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping

    from aijournal.common.app_config import AppConfig, PathsConfig

PROJECT_ROOT = Path(__file__).resolve().parents[3]


def resolve_path(workspace: Path, config: AppConfig, rel_path: str) -> Path:
    """Resolve a relative path within a workspace using PathsConfig.

    Args:
        workspace: Workspace directory
        config: App configuration containing paths
        rel_path: Relative path like 'derived/index' or 'profile/claims.yaml'

    Returns:
        Absolute path resolved from workspace and config

    """
    # Split into base and remaining path
    parts = rel_path.split("/", 1)
    base = parts[0]
    rest = parts[1] if len(parts) > 1 else ""

    # Map base to config path
    if base == "data":
        base_path = Path(config.paths.data)
    elif base == "derived":
        base_path = Path(config.paths.derived)
    elif base == "profile":
        base_path = Path(config.paths.profile)
    elif base == "prompts":
        base_path = Path(config.paths.prompts)
    else:
        # Unknown base, treat as literal
        return workspace / rel_path

    # Make absolute if needed
    if not base_path.is_absolute():
        base_path = workspace / base_path

    # Append remaining path
    return base_path / rest if rest else base_path


AUTHORITATIVE_DIRS: tuple[str, ...] = (
    "profile",
    "data",
    "data/journal",
    "data/normalized",
    "data/raw",
    "data/manifest",
    "prompts",
)

DERIVED_DIRS: tuple[str, ...] = (
    "derived",
    "derived/summaries",
    "derived/microfacts",
    "derived/interviews",
    "derived/advice",
    "derived/persona",
    "derived/index",
    "derived/chat_sessions",
    "derived/pending",
    "derived/pending/profile_updates",
)

SEED_FILES: Mapping[str, str] = {
    "config.yaml": dedent(
        f"""
        model: "{DEFAULT_MODEL_NAME}"
        embedding_model: "{DEFAULT_EMBEDDING_MODEL}"
        host: "{DEFAULT_OLLAMA_HOST}"
        temperature: 0.2
        seed: 42
        paths:
          data: "data"
          profile: "profile"
          derived: "derived"
          prompts: "prompts"
        chat:
          max_retrieved_chunks: 12
        index:
          search_k_factor: 3.0
        llm:
          retries: 4
          timeout: 120.0
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
        # Replace the placeholder sections below with your own profile data.
        # Every key is optional—start with the pieces that matter to you.
        traits: {}
        values_motivations: {}
        goals:
          short_term: []
          long_term: []
          anti_goals: []
        decision_style: {}
        affect_energy: {}
        social: {}
        boundaries_ethics: {}
        coaching_prefs: {}
        """,
    ).strip()
    + "\n",
    "profile/claims.yaml": "claims: []\n",
}


def ensure_directories(base: Path, rel_paths: Iterable[str]) -> tuple[int, int]:
    """Ensure relative directories exist under base, returning created vs total."""
    paths = tuple(rel_paths)
    created = 0
    for rel in paths:
        target = base / rel
        existed = target.exists()
        target.mkdir(parents=True, exist_ok=True)
        if not existed:
            created += 1
    return created, len(paths)


def ensure_gitkeep_files(base: Path, rel_paths: Iterable[str]) -> tuple[int, int]:
    """Ensure each directory contains a .gitkeep marker, returning created vs total."""
    created = 0
    total = 0
    seen: set[str] = set()
    for rel in rel_paths:
        if rel in seen:
            continue
        seen.add(rel)
        total += 1
        directory = base / rel
        directory.mkdir(parents=True, exist_ok=True)
        marker = directory / ".gitkeep"
        if marker.exists():
            continue
        marker.touch()
        created += 1
    return created, total


def ensure_seed_files(base: Path, seeds: Mapping[str, str] | None = None) -> tuple[int, int]:
    """Write seed files when missing; returns created vs total."""
    payloads = seeds or SEED_FILES
    created = 0
    for rel, content in payloads.items():
        target = base / rel
        if target.exists():
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content.rstrip() + "\n", encoding="utf-8")
        created += 1
    return created, len(payloads)


def find_data_root(entry: Path) -> Path:
    """Ascend from an entry path until the workspace root is found."""
    for parent in entry.parents:
        if parent.name == "data":
            return parent.parent
    return Path.cwd()


def normalized_entry_path(
    workspace: Path,
    date_str: str,
    entry_id: str,
    *,
    paths: PathsConfig,
) -> Path:
    """Return the normalized entry path for a given day/id.

    Args:
        workspace: Workspace directory.
        date_str: Date string in YYYY-MM-DD format.
        entry_id: Entry identifier.
        paths: PathsConfig to resolve custom data directory names.

    Returns:
        Path to the normalized entry YAML file.

    """
    data_dir = Path(paths.data)
    if not data_dir.is_absolute():
        data_dir = workspace / data_dir
    return data_dir / "normalized" / date_str / f"{entry_id}.yaml"


def resolve_prompt_path(prompt_path: str, *, prompt_set: str | None = None) -> Path:
    """Resolve a prompt path relative to cwd or project scaffolding.

    Args:
        prompt_path: Relative or absolute path to the prompt file (e.g., "prompts/summarize_day.md")
        prompt_set: Optional experiment set name for A/B/N testing

    Returns:
        Resolved path, checking (in order):
        1. Absolute path if provided
        2. experiments/<set>/<kind>.md override if prompt_set is specified
        3. Default prompt in cwd
        4. Default prompt in project root

    Examples:
        >>> resolve_prompt_path("prompts/summarize_day.md", prompt_set="variant-a")
        # Returns prompts/experiments/variant-a/summarize_day.md if it exists
        # Otherwise falls back to prompts/summarize_day.md

    """
    candidate = Path(prompt_path)
    if candidate.is_absolute():
        return candidate

    # Extract prompt kind (filename without directory)
    prompt_kind = Path(prompt_path).name

    # Try experiment override first if prompt_set is specified
    if prompt_set:
        # Check in cwd first
        cwd_experiment = Path.cwd() / "prompts" / "experiments" / prompt_set / prompt_kind
        if cwd_experiment.exists():
            return cwd_experiment
        # Check in project root
        root_experiment = PROJECT_ROOT / "prompts" / "experiments" / prompt_set / prompt_kind
        if root_experiment.exists():
            return root_experiment

    # Fall back to default path resolution
    cwd_candidate = Path.cwd() / prompt_path
    if cwd_candidate.exists():
        return cwd_candidate
    return PROJECT_ROOT / prompt_path
