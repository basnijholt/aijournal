"""Chroma-backed microfact index for semantic consolidation."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from hashlib import sha256
from math import log1p
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

import chromadb
from chromadb.config import Settings
from pydantic import ValidationError

from aijournal.common.constants import DEFAULT_EMBEDDING_MODEL
from aijournal.domain.facts import MicroFact, MicroFactsFile
from aijournal.io.artifacts import load_artifact_data
from aijournal.io.yaml_io import load_yaml_model
from aijournal.services.embedding import EmbeddingBackend

if TYPE_CHECKING:
    from collections.abc import Iterable, Sequence

    from chromadb.api import ClientAPI, Collection

    from aijournal.common.app_config import AppConfig, MicrofactIndexConfig


def canonicalize_statement(text: str) -> str:
    """Normalize statements for deterministic matching."""
    return " ".join(text.lower().strip().split())


def _record_uid(
    canonical_statement: str,
    *,
    domain: str | None,
    contexts: Sequence[str],
) -> str:
    key = "|".join([canonical_statement, domain or "", "|".join(contexts)])
    return sha256(key.encode("utf-8")).hexdigest()[:16]


def _decode_list(value: Any) -> list[str]:
    if isinstance(value, str):
        try:
            decoded = json.loads(value)
            if isinstance(decoded, list):
                return [str(item) for item in decoded]
        except json.JSONDecodeError:
            return [value]
    if isinstance(value, list):
        return [str(item) for item in value]
    return []


def _fact_key(day: str, fact_id: str) -> str:
    return f"{day}:{fact_id}"


def _default_embedding_model(config: AppConfig) -> str:
    config_model = (
        config.microfacts.embedding_model or config.embedding_model or DEFAULT_EMBEDDING_MODEL
    )
    return str(config_model)


@dataclass(slots=True)
class MicrofactRecord:
    """Normalized payload stored inside the microfact index."""

    uid: str
    statement: str
    canonical_statement: str
    confidence: float
    first_seen: str
    last_seen: str
    domain: str | None = None
    contexts: list[str] = field(default_factory=list)
    observation_count: int = 1
    evidence_entries: list[str] = field(default_factory=list)
    source_fact_ids: list[str] = field(default_factory=list)
    extra: dict[str, Any] = field(default_factory=dict)

    @property
    def metadata(self) -> dict[str, str | int | float | bool | None]:
        payload: dict[str, Any] = {
            "canonical_statement": self.canonical_statement,
            "confidence": self.confidence,
            "first_seen": self.first_seen,
            "last_seen": self.last_seen,
            "observation_count": self.observation_count,
            "domain": self.domain,
            "contexts": self.contexts,
            "evidence_entries": self.evidence_entries,
            "source_fact_ids": self.source_fact_ids,
        }
        payload.update(self.extra)
        encoded: dict[str, Any] = {}
        for key, value in payload.items():
            if value in (None, [], {}):
                continue
            if isinstance(value, list):
                encoded[key] = json.dumps(value)
            else:
                encoded[key] = value
        return cast(dict[str, str | int | float | bool | None], encoded)

    @classmethod
    def from_microfact(
        cls,
        *,
        day: str,
        fact: MicroFact,
        domain: str | None,
        contexts: Sequence[str],
    ) -> MicrofactRecord:
        evidence_entry = fact.evidence.entry_id if fact.evidence else None
        canonical = canonicalize_statement(fact.statement)
        context_list = [ctx for ctx in contexts if ctx]
        uid = _record_uid(canonical, domain=domain, contexts=context_list)
        first_seen = fact.first_seen or day
        last_seen = fact.last_seen or day
        evidence_entries = [evidence_entry] if evidence_entry else []
        return cls(
            uid=uid,
            statement=fact.statement.strip(),
            canonical_statement=canonical,
            confidence=fact.confidence,
            first_seen=first_seen,
            last_seen=last_seen,
            domain=domain,
            contexts=list(context_list),
            observation_count=1,
            evidence_entries=list(evidence_entries),
            source_fact_ids=[_fact_key(day, fact.id)],
        )

    @classmethod
    def from_match(cls, match: MicrofactMatch) -> MicrofactRecord | None:
        metadata = match.metadata or {}
        canonical = metadata.get("canonical_statement") or canonicalize_statement(match.statement)
        first_seen = metadata.get("first_seen")
        last_seen = metadata.get("last_seen")
        if not first_seen or not last_seen:
            return None
        observation_count_raw = metadata.get("observation_count", 1)
        try:
            observation_count = max(1, int(observation_count_raw))
        except (TypeError, ValueError):
            observation_count = 1
        contexts = _decode_list(metadata.get("contexts"))
        evidence_entries = _decode_list(metadata.get("evidence_entries"))
        source_fact_ids = _decode_list(metadata.get("source_fact_ids"))
        domain = metadata.get("domain")
        confidence_value = metadata.get("confidence", 0.5)
        try:
            confidence = float(confidence_value)
        except (TypeError, ValueError):
            confidence = 0.5
        known_keys = {
            "canonical_statement",
            "confidence",
            "first_seen",
            "last_seen",
            "observation_count",
            "domain",
            "contexts",
            "evidence_entries",
            "source_fact_ids",
        }
        extra = {key: value for key, value in metadata.items() if key not in known_keys}
        return cls(
            uid=match.uid,
            statement=match.statement,
            canonical_statement=canonical,
            confidence=confidence,
            first_seen=first_seen,
            last_seen=last_seen,
            domain=domain,
            contexts=contexts,
            observation_count=observation_count,
            evidence_entries=evidence_entries,
            source_fact_ids=source_fact_ids,
            extra=extra,
        )

    def merge_observation(
        self,
        *,
        confidence: float,
        date: str,
        fact_id: str,
        evidence_entry: str | None,
        max_evidence_entries: int,
        fact_key: str,
    ) -> None:
        weight_existing = log1p(max(self.observation_count, 1))
        total_weight = weight_existing + 1.0
        merged_confidence = (self.confidence * weight_existing + confidence) / total_weight
        self.confidence = max(0.0, min(1.0, merged_confidence))
        self.observation_count += 1
        self.first_seen = min(self.first_seen, date)
        self.last_seen = max(self.last_seen, date)
        if evidence_entry and evidence_entry not in self.evidence_entries:
            self.evidence_entries.append(evidence_entry)
            if len(self.evidence_entries) > max(1, max_evidence_entries):
                self.evidence_entries = self.evidence_entries[-max_evidence_entries:]
        if fact_key not in self.source_fact_ids:
            self.source_fact_ids.append(fact_key)

    def apply_to_fact(self, fact: MicroFact) -> None:
        fact.first_seen = self.first_seen
        fact.last_seen = self.last_seen
        fact.confidence = self.confidence


@dataclass(slots=True)
class MicrofactMatch:
    """Result row returned by similarity queries."""

    uid: str
    statement: str
    distance: float | None
    metadata: dict[str, Any]


@dataclass(slots=True)
class MicrofactConsolidationStats:
    day: str
    processed: int
    new_records: int
    merged_records: int


@dataclass(slots=True)
class MicrofactRebuildResult:
    facts: list[MicrofactRecord]
    stats: list[MicrofactConsolidationStats]


class MicrofactIndex:
    """Persistent semantic index backed by ChromaDB."""

    def __init__(
        self,
        workspace: Path,
        config: AppConfig,
        *,
        fake_mode: bool = False,
        embedding_backend: EmbeddingBackend | None = None,
    ) -> None:
        self.workspace = workspace
        self.config = config
        self._microfacts_config = config.microfacts
        derived_root = self._resolve_path(config.paths.derived)
        self._daily_dir = derived_root / "microfacts"
        self._db_path = derived_root / self._microfacts_config.subdir
        self._db_path.mkdir(parents=True, exist_ok=True)
        self._client_settings = Settings(
            anonymized_telemetry=False,
            allow_reset=True,
        )
        self._client = self._create_client()
        self._collection = self._ensure_collection(self._client)
        model_name = _default_embedding_model(config)
        self._embedder = embedding_backend or EmbeddingBackend(
            model=model_name,
            host=config.host,
            fake_mode=fake_mode,
        )

    def _resolve_path(self, path_value: str) -> Path:
        base = Path(path_value)
        if base.is_absolute():
            return base
        return self.workspace / base

    def _create_client(self) -> ClientAPI:
        return chromadb.PersistentClient(
            path=str(self._db_path),
            settings=self._client_settings,
        )

    def _ensure_collection(self, client: ClientAPI) -> Collection:
        return client.get_or_create_collection(self._microfacts_config.collection)

    @property
    def collection(self) -> Collection:
        return self._collection

    @property
    def embedder(self) -> EmbeddingBackend:
        return self._embedder

    @property
    def settings(self) -> MicrofactIndexConfig:
        return self._microfacts_config

    def reset(self) -> None:
        """Drop the existing collection and start fresh."""
        try:
            self._client.reset()
        except Exception:  # pragma: no cover - fallback for transports without reset
            self._client = self._create_client()
        self._collection = self._ensure_collection(self._client)

    def upsert(self, records: Sequence[MicrofactRecord]) -> None:
        """Insert or update the provided microfacts in the index."""
        filtered = [record for record in records if record.statement]
        if not filtered:
            return
        documents = [record.statement for record in filtered]
        embeddings = self._embedder.embed(documents)
        if len(embeddings) != len(filtered):
            msg = "Embedding backend returned unexpected vector count"
            raise RuntimeError(msg)
        ids = [record.uid for record in filtered]
        metadatas = [record.metadata for record in filtered]
        self._collection.upsert(
            ids=ids,
            embeddings=cast(Any, embeddings),
            documents=documents,
            metadatas=cast(Any, metadatas),
        )

    def query_similar(
        self,
        statement: str,
        *,
        top_k: int | None = None,
        where: dict[str, Any] | None = None,
    ) -> list[MicrofactMatch]:
        """Return the closest matching microfacts for the supplied text."""
        normalized = statement.strip()
        if not normalized:
            return []
        k = top_k or self._microfacts_config.default_top_k
        query_embedding = self._embedder.embed_one(normalized)
        payload = self._collection.query(
            query_embeddings=cast(Any, [query_embedding]),
            n_results=k,
            where=where,
            include=["metadatas", "documents", "distances"],
        )
        matches: list[MicrofactMatch] = []
        ids = payload.get("ids") or [[]]
        metadatas = payload.get("metadatas") or [[]]
        documents = payload.get("documents") or [[]]
        distances = payload.get("distances") or [[]]
        for idx, uid in enumerate(ids[0]):
            metadata_mapping = metadatas[0][idx] if idx < len(metadatas[0]) else {}
            metadata = dict(metadata_mapping or {})
            document = documents[0][idx] if idx < len(documents[0]) else ""
            distance = distances[0][idx] if idx < len(distances[0]) else None
            matches.append(
                MicrofactMatch(
                    uid=uid,
                    statement=document,
                    distance=float(distance) if distance is not None else None,
                    metadata=metadata or {},
                ),
            )
        return matches

    def export_all_records(self) -> list[MicrofactRecord]:
        payload = self._collection.get(include=["metadatas", "documents"])
        raw_ids = payload.get("ids") or []
        raw_documents = payload.get("documents") or []
        raw_metadatas = payload.get("metadatas") or []

        def _flatten(value: Any) -> list[Any]:
            if value and isinstance(value, list) and value and isinstance(value[0], list):
                return value[0]
            return value or []

        ids = _flatten(raw_ids)
        documents = _flatten(raw_documents)
        metadatas = _flatten(raw_metadatas)

        records: list[MicrofactRecord] = []
        for idx, uid in enumerate(ids):
            statement = documents[idx] if idx < len(documents) else ""
            metadata_mapping = metadatas[idx] if idx < len(metadatas) else {}
            metadata = dict(metadata_mapping or {})
            record = MicrofactRecord.from_match(
                MicrofactMatch(uid=uid, statement=statement, distance=None, metadata=metadata),
            )
            if record:
                records.append(record)
        return records

    def rebuild_from_daily_artifacts(
        self,
        *,
        microfacts_dir: Path | None = None,
        chunk_size: int = 64,
    ) -> MicrofactRebuildResult:
        """Reset and repopulate the index from YAML artifacts."""
        location = microfacts_dir or self._daily_dir
        if not location.exists():
            self.reset()
            return MicrofactRebuildResult(facts=[], stats=[])

        aggregated: dict[str, MicrofactRecord] = {}
        stats: list[MicrofactConsolidationStats] = []
        max_evidence_entries = max(1, self._microfacts_config.max_evidence_entries)

        for path in sorted(location.glob("*.yaml")):
            day = path.stem
            if not _DAY_FILENAME.fullmatch(day):
                continue
            processed = 0
            new_records = 0
            merged_records = 0
            try:
                microfacts = load_artifact_data(path, MicroFactsFile)
            except (FileNotFoundError, ValidationError):
                try:
                    microfacts = load_yaml_model(path, MicroFactsFile)
                except FileNotFoundError:  # pragma: no cover - race condition protection
                    continue
            for fact in microfacts.facts:
                record = MicrofactRecord.from_microfact(
                    day=day,
                    fact=fact,
                    domain=None,
                    contexts=[],
                )
                if not record.statement:
                    continue
                processed += 1
                existing = aggregated.get(record.uid)
                if existing:
                    evidence_entry = fact.evidence.entry_id if fact.evidence else None
                    existing.merge_observation(
                        confidence=fact.confidence,
                        date=day,
                        fact_id=fact.id,
                        evidence_entry=evidence_entry,
                        max_evidence_entries=max_evidence_entries,
                        fact_key=_fact_key(day, fact.id),
                    )
                    merged_records += 1
                else:
                    aggregated[record.uid] = record
                    new_records += 1
            stats.append(
                MicrofactConsolidationStats(
                    day=day,
                    processed=processed,
                    new_records=new_records,
                    merged_records=merged_records,
                ),
            )

        self.reset()
        ordered_records = sorted(
            aggregated.values(),
            key=lambda record: (record.first_seen, record.uid),
        )
        for batch in _batched(ordered_records, size=chunk_size):
            self.upsert(batch)

        consolidated = self.export_all_records()
        return MicrofactRebuildResult(facts=consolidated, stats=stats)


def _batched(items: Sequence[MicrofactRecord], *, size: int) -> Iterable[list[MicrofactRecord]]:
    if size <= 0:
        size = 64
    batch: list[MicrofactRecord] = []
    for item in items:
        batch.append(item)
        if len(batch) >= size:
            yield batch
            batch = []
    if batch:
        yield batch


_DAY_FILENAME = re.compile(r"^\d{4}-\d{2}-\d{2}$")
