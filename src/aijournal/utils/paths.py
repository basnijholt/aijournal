"""Path helpers and layout constants for aijournal workspaces."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from pathlib import Path
from textwrap import dedent

PROJECT_ROOT = Path(__file__).resolve().parents[3]

AUTHORITATIVE_DIRS: tuple[str, ...] = (
    "config",
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
    "derived/profile_suggestions",
    "derived/interviews",
    "derived/advice",
    "derived/persona",
    "derived/index",
    "derived/chat_sessions",
    "derived/pending",
    "derived/pending/profile_updates",
)

SEED_FILES: Mapping[str, str] = {
    "config/config.yaml": dedent(
        """
        model: "llama3.1:8b-instruct"
        host: "http://127.0.0.1:11434"
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


def normalized_entry_path(root: Path, date_str: str, entry_id: str) -> Path:
    """Return the normalized entry path for a given day/id."""
    return root / "data" / "normalized" / date_str / f"{entry_id}.yaml"


def resolve_prompt_path(prompt_path: str) -> Path:
    """Resolve a prompt path relative to cwd or project scaffolding."""
    candidate = Path(prompt_path)
    if candidate.is_absolute():
        return candidate
    cwd_candidate = Path.cwd() / prompt_path
    if cwd_candidate.exists():
        return cwd_candidate
    return PROJECT_ROOT / prompt_path


__all__ = [
    "AUTHORITATIVE_DIRS",
    "DERIVED_DIRS",
    "SEED_FILES",
    "PROJECT_ROOT",
    "ensure_directories",
    "ensure_seed_files",
    "find_data_root",
    "normalized_entry_path",
    "resolve_prompt_path",
]
