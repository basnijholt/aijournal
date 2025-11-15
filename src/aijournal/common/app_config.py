"""Typed application configuration model."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class PathsConfig(BaseModel):
    """Path configuration for workspace subdirectories.

    All paths are relative to the workspace root directory.
    """

    model_config = ConfigDict(extra="ignore")

    data: str = "data"
    profile: str = "profile"
    derived: str = "derived"
    prompts: str = "prompts"


class PromptsConfig(BaseModel):
    """Prompts configuration for A/B/N testing."""

    model_config = ConfigDict(extra="ignore")

    active_set: str | None = None


class ChatConfig(BaseModel):
    """Chat service configuration."""

    model_config = ConfigDict(extra="ignore")

    model: str | None = None
    host: str | None = None
    timeout: float | None = None
    temperature: float | None = None
    seed: int | None = None
    max_tokens: int | None = None
    max_retrieved_chunks: int | None = None


class IndexConfig(BaseModel):
    """Vector index configuration."""

    model_config = ConfigDict(extra="ignore")

    search_k_factor: float = 3.0
    include_summaries: bool = True
    include_microfacts: bool = True


class PersonaConfig(BaseModel):
    """Persona generation configuration."""

    model_config = ConfigDict(extra="ignore")

    token_budget: int = 1200
    max_claims: int = 24
    min_claims: int = 8


class TokenEstimatorConfig(BaseModel):
    """Token estimation configuration."""

    model_config = ConfigDict(extra="ignore")

    char_per_token: float = 4.2


class ClaimTypesWeights(BaseModel):
    """Weights for different claim types."""

    model_config = ConfigDict(extra="ignore")

    value: float = 1.4
    goal: float = 1.4
    boundary: float = 1.3
    trait: float = 1.2
    preference: float = 1.0
    habit: float = 0.9
    aversion: float = 1.1
    skill: float = 1.0


class ImpactWeightsConfig(BaseModel):
    """Impact weights for persona characteristics."""

    model_config = ConfigDict(extra="ignore")

    values_goals: float = 1.5
    decision_style: float = 1.3
    affect_energy: float = 1.2
    traits: float = 1.0
    social: float = 0.9
    claims: float = 1.0
    claim_types: ClaimTypesWeights = Field(default_factory=ClaimTypesWeights)


class AdvisorConfig(BaseModel):
    """Advisor service configuration."""

    model_config = ConfigDict(extra="ignore")

    max_recos: int = 3
    include_risks: bool = True


class CaptureConfig(BaseModel):
    """Capture service configuration."""

    model_config = ConfigDict(extra="ignore")


class MicrofactIndexConfig(BaseModel):
    """Configuration for the Chroma-backed microfact index."""

    model_config = ConfigDict(extra="ignore")

    subdir: str = "microfacts/index"
    collection: str = "microfacts"
    default_top_k: int = 5
    embedding_model: str | None = None
    merge_distance: float = 0.12
    max_evidence_entries: int = 5
    min_token_overlap: float = 0.6


class LLMConfig(BaseModel):
    """LLM runtime configuration."""

    model_config = ConfigDict(extra="ignore")

    retries: int = Field(
        default=4,
        ge=1,
        le=10,
        description="Number of retry attempts for failed LLM requests",
    )
    timeout: float = Field(
        default=120.0,
        ge=10.0,
        le=600.0,
        description="Maximum seconds to wait for LLM response before timeout",
    )


class AppConfig(BaseModel):
    """Project configuration backed by Pydantic validation."""

    model_config = ConfigDict(extra="ignore")

    # Global LLM settings
    model: str | None = None
    host: str | None = None
    temperature: float | None = None
    seed: int | None = None
    max_tokens: int | None = None
    timeout: float | None = None
    embedding_model: str | None = None

    # Nested configurations with typed models and defaults
    paths: PathsConfig = Field(default_factory=PathsConfig)
    prompts: PromptsConfig = Field(default_factory=PromptsConfig)
    chat: ChatConfig = Field(default_factory=ChatConfig)
    index: IndexConfig = Field(default_factory=IndexConfig)
    persona: PersonaConfig = Field(default_factory=PersonaConfig)
    token_estimator: TokenEstimatorConfig = Field(default_factory=TokenEstimatorConfig)
    impact_weights: ImpactWeightsConfig = Field(default_factory=ImpactWeightsConfig)
    advisor: AdvisorConfig = Field(default_factory=AdvisorConfig)
    capture: CaptureConfig = Field(default_factory=CaptureConfig)
    microfacts: MicrofactIndexConfig = Field(default_factory=MicrofactIndexConfig)
    llm: LLMConfig = Field(default_factory=LLMConfig)

    def to_dict(self, *, exclude_none: bool = False) -> dict[str, Any]:
        """Return the configuration as a plain dictionary."""
        return self.model_dump(mode="python", exclude_none=exclude_none)
