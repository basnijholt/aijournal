"""Governance utilities for scanning provenance spans."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

import typer
import yaml
from pydantic import BaseModel

from aijournal.common.command_runner import run_command_pipeline
from aijournal.common.config_loader import load_config, use_fake_llm
from aijournal.common.context import RunContext, create_run_context
from aijournal.common.meta import Artifact, ArtifactKind
from aijournal.domain.changes import ProfileUpdateProposals
from aijournal.domain.evidence import SourceRef, redact_source_text
from aijournal.domain.persona import PersonaCore
from aijournal.io.artifacts import load_artifact, save_artifact
from aijournal.io.yaml_io import write_yaml_model
from aijournal.models.authoritative import ClaimsFile
from aijournal.models.derived import ProfileUpdateBatch
from aijournal.utils.paths import resolve_path

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable

    from aijournal.common.app_config import AppConfig


@dataclass(frozen=True)
class IssueDetail:
    """A single provenance issue discovered within a file."""

    path: str
    entry_id: str | None
    span_indices: tuple[int, ...]


@dataclass(frozen=True)
class AuditFileResult:
    """Aggregate findings for a single file."""

    path: Path
    issues: list[IssueDetail]

    @property
    def count(self) -> int:
        return sum(len(issue.span_indices) for issue in self.issues)


_AUDIT_ARTIFACT_MODELS: dict[ArtifactKind, type[BaseModel]] = {
    ArtifactKind.PROFILE_PROPOSALS: ProfileUpdateProposals,
    ArtifactKind.PROFILE_UPDATES: ProfileUpdateBatch,
    ArtifactKind.PERSONA_CORE: PersonaCore,
}


class AuditOptions(BaseModel):
    fix: bool


@dataclass(slots=True)
class AuditPrepared:
    fix: bool


@dataclass(slots=True)
class AuditResult:
    results: list[AuditFileResult]
    fix: bool


def run_audit_provenance(
    *,
    root: Path,
    workspace: Path,
    config: AppConfig,
    fix: bool,
) -> list[AuditFileResult]:
    """Scan claims and derived artifacts for provenance span text."""
    results: list[AuditFileResult] = []

    claims_path = resolve_path(workspace, config, "profile/claims.yaml")
    if claims_path.exists():
        try:
            raw_claims = yaml.safe_load(claims_path.read_text(encoding="utf-8")) or {}
        except Exception:
            raw_claims = {}
        issues = _scan_model_for_spans(raw_claims, fix=fix)
        if issues:
            if fix:
                claims_model = ClaimsFile.model_validate(raw_claims)
                write_yaml_model(claims_path, claims_model)
            results.append(
                AuditFileResult(
                    path=claims_path.relative_to(root),
                    issues=issues,
                ),
            )

    for relative_dir in ("derived/pending/profile_updates",):
        for path in _iter_artifact_files(root / relative_dir):
            artifact_entry = _load_auditable_artifact(path)
            if artifact_entry is None:
                continue
            kind, artifact = artifact_entry
            issues = _scan_model_for_spans(artifact.data, fix=fix)
            if not issues:
                continue
            if fix:
                save_artifact(
                    path,
                    Artifact[Any](
                        kind=kind,
                        meta=artifact.meta,
                        data=artifact.data,
                    ),
                    format=_artifact_format(path),
                )
            results.append(
                AuditFileResult(
                    path=path.relative_to(root),
                    issues=issues,
                ),
            )

    persona_path = resolve_path(workspace, config, "derived/persona") / "persona_core.yaml"
    if persona_path.exists():
        artifact_entry = _load_auditable_artifact(persona_path)
        if artifact_entry is not None:
            kind, artifact = artifact_entry
            issues = _scan_model_for_spans(artifact.data, fix=fix)
            if issues:
                if fix:
                    save_artifact(
                        persona_path,
                        Artifact[Any](
                            kind=kind,
                            meta=artifact.meta,
                            data=artifact.data,
                        ),
                    )
                results.append(
                    AuditFileResult(
                        path=persona_path.relative_to(root),
                        issues=issues,
                    ),
                )

    return results


def prepare_inputs(ctx: RunContext, options: AuditOptions) -> AuditPrepared:
    ctx.emit(event="prepare_summary", fix=options.fix)
    return AuditPrepared(fix=options.fix)


def invoke_pipeline(ctx: RunContext, prepared: AuditPrepared) -> AuditResult:
    findings = run_audit_provenance(
        root=ctx.workspace,
        workspace=ctx.workspace,
        config=ctx.config,
        fix=prepared.fix,
    )
    ctx.emit(
        event="pipeline_complete",
        fix=prepared.fix,
        files_with_issues=len(findings),
        total_spans=sum(result.count for result in findings),
    )
    return AuditResult(results=findings, fix=prepared.fix)


def persist_output(ctx: RunContext, result: AuditResult) -> None:
    del ctx
    if not result.results:
        typer.echo("No provenance span text detected.")
        return

    if result.fix:
        total_spans = sum(res.count for res in result.results)
        for res in result.results:
            typer.secho(
                f"Redacted {res.count} span{'s' if res.count != 1 else ''} in {res.path.as_posix()}.",
                fg=typer.colors.GREEN,
            )
        typer.echo(
            f"Redacted {total_spans} span{'s' if total_spans != 1 else ''} across {len(result.results)} file{'s' if len(result.results) != 1 else ''}.",
        )
    else:
        typer.secho("Found provenance span text in:", fg=typer.colors.YELLOW)
        for res in result.results:
            typer.echo(f"- {res.path.as_posix()}")
            for issue in res.issues:
                spans = ", ".join(str(idx) for idx in issue.span_indices)
                entry_details = f" entry_id={issue.entry_id}" if issue.entry_id else ""
                typer.echo(f"    {issue.path} spans={spans}{entry_details}")
        typer.secho("Run with --fix to redact these spans.", fg=typer.colors.YELLOW)
        raise typer.Exit(1)


def run_audit_command(ctx: RunContext, options: AuditOptions) -> None:
    run_command_pipeline(
        ctx,
        options,
        prepare_inputs=prepare_inputs,
        invoke_pipeline=invoke_pipeline,
        persist_output=persist_output,
    )


def run_audit_provenance_cli(workspace: Path | None = None, *, fix: bool) -> None:
    workspace = workspace or Path.cwd()
    config = load_config(workspace)
    ctx = create_run_context(
        command="ops.audit.provenance",
        workspace=workspace,
        config=config,
        use_fake_llm=use_fake_llm(),
        trace=False,
        verbose_json=False,
    )
    run_audit_command(ctx, AuditOptions(fix=fix))


def _artifact_format(path: Path) -> str | None:
    suffix = path.suffix.lower()
    if suffix in {".yaml", ".yml"}:
        return "yaml"
    if suffix == ".json":
        return "json"
    return None


def _iter_artifact_files(base: Path) -> Iterable[Path]:
    if not base.exists():
        return []
    return sorted(
        path
        for path in base.rglob("*")
        if path.is_file() and path.suffix.lower() in {".yaml", ".yml", ".json"}
    )


def _load_auditable_artifact(
    path: Path,
) -> tuple[ArtifactKind, Artifact[Any]] | None:
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    if not isinstance(raw, dict):
        return None
    kind_raw = raw.get("kind")
    if not isinstance(kind_raw, str):
        return None
    try:
        kind = ArtifactKind(kind_raw)
    except ValueError:
        return None
    model = _AUDIT_ARTIFACT_MODELS.get(kind)
    if model is None:
        return None
    try:
        return kind, load_artifact(path, model)
    except Exception:
        return None


def _scan_model_for_spans(value: Any, *, fix: bool) -> list[IssueDetail]:
    issues: list[IssueDetail] = []
    _scan_value(
        value,
        path=[],
        setter=lambda _: None,
        issues=issues,
        fix=fix,
    )
    return issues


def _scan_value(
    value: Any,
    *,
    path: list[str],
    setter: Callable[[Any], None],
    issues: list[IssueDetail],
    fix: bool,
) -> int:
    count = 0
    if isinstance(value, SourceRef):
        spans_with_text = tuple(
            idx for idx, span in enumerate(value.spans) if span.text not in (None, "")
        )
        if spans_with_text:
            issues.append(
                IssueDetail(
                    path=_format_path(path),
                    entry_id=value.entry_id,
                    span_indices=spans_with_text,
                ),
            )
            count += len(spans_with_text)
            if fix:
                setter(redact_source_text(value))
        return count

    if isinstance(value, BaseModel):
        for field_name in value.__class__.model_fields:
            field_value = getattr(value, field_name)

            def _set_field(
                new_value: Any,
                *,
                field: str = field_name,
                owner: BaseModel = value,
            ) -> None:
                setattr(owner, field, new_value)

            count += _scan_value(
                field_value,
                path=[*path, field_name],
                setter=_set_field,
                issues=issues,
                fix=fix,
            )
        return count

    if isinstance(value, list):
        for idx, item in enumerate(value):

            def _set_index(new_value: Any, *, index: int = idx, target: list[Any] = value) -> None:
                target[index] = new_value

            count += _scan_value(
                item,
                path=[*path, f"[{idx}]"],
                setter=_set_index,
                issues=issues,
                fix=fix,
            )
        return count

    if isinstance(value, tuple):
        mutable = list(value)
        tuple_changed = False
        for idx, item in enumerate(mutable):

            def _set_tuple_item(new_value: Any, *, index: int = idx) -> None:
                mutable[index] = new_value

            child_count = _scan_value(
                item,
                path=[*path, f"[{idx}]"],
                setter=_set_tuple_item,
                issues=issues,
                fix=fix,
            )
            if child_count and fix:
                tuple_changed = True
            count += child_count
        if tuple_changed and fix:
            setter(tuple(mutable))
        return count

    if isinstance(value, dict):
        if "entry_id" in value and "spans" in value:
            try:
                source = SourceRef.model_validate(value)
            except Exception:
                source = None
            if source is not None:

                def _assign_source(
                    new_source: SourceRef,
                    *,
                    parent_setter: Callable[[Any], None] = setter,
                ) -> None:
                    parent_setter(new_source.model_dump(mode="python"))

                count += _scan_value(
                    source,
                    path=path,
                    setter=_assign_source,
                    issues=issues,
                    fix=fix,
                )
                return count
        for key, item in list(value.items()):

            def _set_dict_item(
                new_value: Any,
                *,
                dict_key: Any = key,
                target: dict[Any, Any] = value,
            ) -> None:
                target[dict_key] = new_value

            count += _scan_value(
                item,
                path=[*path, str(key)],
                setter=_set_dict_item,
                issues=issues,
                fix=fix,
            )
        return count

    return count


def _format_path(segments: list[str]) -> str:
    parts: list[str] = []
    for segment in segments:
        if not segment:
            continue
        if segment.startswith("["):
            if parts:
                parts[-1] = parts[-1] + segment
            else:
                parts.append(segment)
        else:
            parts.append(segment)
    return ".".join(parts)
