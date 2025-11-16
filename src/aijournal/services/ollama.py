"""LLM helpers built on top of Pydantic AI's Ollama provider."""

from __future__ import annotations

import contextlib
import copy
import json
import os
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from string import Template
from typing import Any, TypeVar, cast, get_args, get_origin

from pydantic import BaseModel, ValidationError
from pydantic.fields import PydanticUndefined
from pydantic_ai import Agent, ModelSettings, UnexpectedModelBehavior
from pydantic_ai.exceptions import UserError
from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.providers.ollama import OllamaProvider

from aijournal.common.app_config import AppConfig
from aijournal.common.constants import DEFAULT_MODEL_NAME, DEFAULT_OLLAMA_HOST
from aijournal.common.meta import LLMResult
from aijournal.utils import time as time_utils
from aijournal.utils.paths import resolve_prompt_path

_JSON_SYSTEM_PROMPT = (
    "You are part of the aijournal CLI. "
    "Respond with valid JSON only—no markdown fences, explanations, or trailing text."
)

_STRUCTURED_SYSTEM_PROMPT = (
    "You are the summarize agent for the local aijournal CLI. "
    "Read the user's prompt carefully and respond with JSON that matches the declared response schema. "
    "Do not include markdown fences or commentary."
)

DEFAULT_PROMPTS = {
    "summarize_day.md": (
        "You are a journaling summarizer. Return JSON with day, bullets, highlights, "
        "todo_candidates."
    ),
    "extract_facts.md": 'Extract atomic facts as JSON {"facts":[...]}.',
    "profile_update.md": (
        "Propose JSON with claim/facet updates grounded in entries, summaries, and microfacts."
    ),
    "advise.md": "Return an advice card JSON with recommendations citing facets and claims.",
}


class LLMResponseError(RuntimeError):
    """Raised when the LLM response cannot be parsed as valid JSON."""


@dataclass(frozen=True)
class OllamaConfig:
    """Runtime configuration for Ollama task runners."""

    model: str
    host: str | None = None
    temperature: float | None = None
    seed: int | None = None
    max_tokens: int | None = None
    timeout: float | None = None


def resolve_ollama_host(
    host: str | None = None,
    *,
    config_host: str | None = None,
) -> str:
    """Return the base Ollama host (without `/v1`) to contact."""
    if host:
        return host.rstrip("/")

    env_host = os.getenv("AIJOURNAL_OLLAMA_HOST")
    if env_host:
        return env_host.rstrip("/")

    env_base = os.getenv("OLLAMA_BASE_URL")
    if env_base:
        candidate = env_base.rstrip("/")
        if candidate.endswith("/v1"):
            candidate = candidate.removesuffix("/v1")
        return candidate

    if config_host:
        return str(config_host).rstrip("/")

    return DEFAULT_OLLAMA_HOST


def resolve_ollama_base_url(
    host: str | None = None,
    *,
    config_host: str | None = None,
) -> str:
    """Return the OpenAI-compatible base URL for the Ollama provider."""
    env_base = os.getenv("OLLAMA_BASE_URL")
    if not host and env_base:
        return env_base.rstrip("/")

    base = resolve_ollama_host(host, config_host=config_host)
    if base.endswith("/v1"):
        return base
    return f"{base}/v1"


def build_ollama_model(model_name: str, host: str | None = None) -> OpenAIChatModel:
    """Create an OpenAIChatModel configured for the target Ollama endpoint."""
    provider = OllamaProvider(base_url=resolve_ollama_base_url(host))
    return OpenAIChatModel(model_name=model_name, provider=provider)


def build_ollama_config_from_mapping(
    config: AppConfig | None = None,
    *,
    model: str | None = None,
    host: str | None = None,
    timeout: float | None = None,
    temperature: float | None = None,
    seed: int | None = None,
    max_tokens: int | None = None,
) -> OllamaConfig:
    """Construct an OllamaConfig from a loose mapping of settings."""
    cfg = config or AppConfig()
    raw_config_model = cfg.model
    raw_config_host = cfg.host

    env_model = os.getenv("AIJOURNAL_MODEL")
    resolved_model = (
        model
        or (env_model if env_model else None)
        or (str(raw_config_model) if raw_config_model else None)
        or DEFAULT_MODEL_NAME
    )

    config_host_value = str(raw_config_host) if raw_config_host else None
    resolved_host = resolve_ollama_host(host, config_host=config_host_value)
    effective_temperature = temperature if temperature is not None else cfg.temperature
    effective_seed = seed if seed is not None else cfg.seed
    effective_max_tokens = max_tokens if max_tokens is not None else cfg.max_tokens
    effective_timeout = timeout if timeout is not None else cfg.timeout
    return OllamaConfig(
        model=resolved_model,
        host=resolved_host,
        temperature=effective_temperature,
        seed=effective_seed,
        max_tokens=effective_max_tokens,
        timeout=effective_timeout,
    )


def resolve_model_name(
    config: AppConfig | None,
    *,
    use_fake_llm: bool,
    fake_label: str = "fake-ollama",
) -> str:
    """Return the effective model name, accounting for fake-LLM mode."""
    if use_fake_llm:
        return fake_label
    return build_ollama_config_from_mapping(config).model


def _model_settings_from_config(config: OllamaConfig) -> ModelSettings | None:
    kwargs: dict[str, Any] = {}
    if config.temperature is not None:
        kwargs["temperature"] = float(config.temperature)
    if config.seed is not None:
        kwargs["seed"] = int(config.seed)
    if config.max_tokens is not None:
        kwargs["max_tokens"] = int(config.max_tokens)
    if config.timeout is not None:
        kwargs["timeout"] = float(config.timeout)
    return ModelSettings(**cast(Any, kwargs)) if kwargs else None


def build_ollama_agent(
    config: OllamaConfig,
    *,
    system_prompt: str = _JSON_SYSTEM_PROMPT,
    output_type: type[Any] | None = None,
    name: str = "aijournal-json-runner",
    retries: int | None = None,
) -> Agent:
    """Create a Pydantic AI agent for the given configuration."""
    model_settings = _model_settings_from_config(config)
    agent_kwargs: dict[str, Any] = {
        "name": name,
        "system_prompt": system_prompt,
        "model_settings": model_settings,
    }
    if output_type is not None:
        agent_kwargs["output_type"] = output_type
    if retries is not None:
        agent_kwargs["retries"] = retries
    return Agent(build_ollama_model(config.model, config.host), **agent_kwargs)


_PayloadT = TypeVar("_PayloadT", bound=Any)


def _to_json(data: Any) -> str:
    try:
        return json.dumps(data, indent=2, ensure_ascii=False)
    except TypeError:
        return str(data)


def _clip(text: str, *, limit: int = 4000) -> str:
    if len(text) <= limit:
        return text
    return f"{text[:limit]}..."


def _unwrap_optional(annotation: Any) -> Any:
    origin = get_origin(annotation)
    if origin is None:
        return annotation
    args = get_args(annotation)
    if not args:
        return annotation
    non_none = [arg for arg in args if arg is not type(None)]
    if len(non_none) == 1 and len(non_none) < len(args):
        return non_none[0]
    return annotation


def _field_skeleton(annotation: Any) -> Any:
    annotation = _unwrap_optional(annotation)
    origin = get_origin(annotation)

    if isinstance(annotation, type) and issubclass(annotation, BaseModel):
        return _model_skeleton(annotation)

    if origin in {list, tuple, set}:
        args = get_args(annotation)
        element_annotation = args[0] if args else Any
        return [_field_skeleton(element_annotation)]

    if origin in {dict, Mapping}:
        args = get_args(annotation)
        value_annotation = args[1] if len(args) == 2 else Any
        return {"key": _field_skeleton(value_annotation)}

    if annotation in {str, bytes}:
        return ""
    if annotation is int:
        return 0
    if annotation is float:
        return 0.0
    if annotation is bool:
        return False

    return None


def _model_skeleton(model: type[BaseModel]) -> dict[str, Any]:
    skeleton: dict[str, Any] = {}
    for name, field in model.model_fields.items():
        if field.default is not PydanticUndefined:
            skeleton[name] = field.default
            continue
        if field.default_factory is not None:
            try:
                factory = cast(Callable[[], Any], field.default_factory)
                skeleton[name] = factory()
                continue
            except TypeError:  # pragma: no cover - defensive guard
                skeleton[name] = None
                continue
        skeleton[name] = _field_skeleton(field.annotation)
    return skeleton


def _compose_attempt_prompt(
    base_prompt: str,
    *,
    skeleton: str | None,
    previous_payload: str | None,
    validation_summary: str | None,
) -> str:
    parts: list[str] = [base_prompt.rstrip()]
    if skeleton:
        parts.append("\nJSON_SKELETON (fill without removing keys):\n" + skeleton.strip())
    if previous_payload or validation_summary:
        parts.append("\nThe prior JSON failed validation. Correct it and return only valid JSON.")
        if previous_payload:
            parts.append("\nPrevious JSON candidate:\n" + _clip(previous_payload))
        if validation_summary:
            parts.append("\nValidation errors (JSON):\n" + _clip(validation_summary))
    return "\n".join(part for part in parts if part)


def _coerce_value_for_type(
    value: Any,
    annotation: Any,
    *,
    field_path: str,
) -> tuple[Any, list[dict[str, str]]]:
    annotation = _unwrap_optional(annotation)
    origin = get_origin(annotation)
    changes: list[dict[str, str]] = []

    if isinstance(annotation, type) and issubclass(annotation, BaseModel):
        if hasattr(value, "model_dump"):
            value = value.model_dump(mode="python")
        if isinstance(value, dict):
            coerced, nested = _coerce_payload_for_model(value, annotation)
            changes.extend(nested)
            return coerced, changes
        return value, changes

    if origin in {list, tuple, set}:
        args = get_args(annotation)
        element_annotation = args[0] if args else Any
        if value is None:
            value = []
        if not isinstance(value, list):
            coerced_value = [value]
            changes.append(
                {
                    "field": field_path,
                    "rule": "wrap_scalar_in_list",
                    "from": repr(value),
                    "to": repr(coerced_value),
                },
            )
            value = coerced_value
        coerced_items: list[Any] = []
        for index, item in enumerate(value):
            coerced_item, nested = _coerce_value_for_type(
                item,
                element_annotation,
                field_path=f"{field_path}[{index}]",
            )
            coerced_items.append(coerced_item)
            changes.extend(nested)
        return coerced_items, changes

    if origin in {dict, Mapping} and isinstance(value, dict):
        args = get_args(annotation)
        value_annotation = args[1] if len(args) == 2 else Any
        coerced_dict: dict[str, Any] = {}
        for key, item in value.items():
            coerced_item, nested = _coerce_value_for_type(
                item,
                value_annotation,
                field_path=f"{field_path}.{key}",
            )
            coerced_dict[key] = coerced_item
            changes.extend(nested)
        return coerced_dict, changes

    return value, changes


def _coerce_payload_for_model(
    payload: dict[str, Any],
    model: type[BaseModel],
) -> tuple[dict[str, Any], list[dict[str, str]]]:
    coerced = copy.deepcopy(payload)
    changes: list[dict[str, str]] = []
    for name, field in model.model_fields.items():
        if name not in coerced:
            continue
        coerced_value, field_changes = _coerce_value_for_type(
            coerced[name],
            field.annotation,
            field_path=name,
        )
        coerced[name] = coerced_value
        changes.extend(field_changes)
    return coerced, changes


def _attempt_model_validation(
    payload: dict[str, Any],
    model: type[BaseModel],
) -> tuple[BaseModel | None, list[dict[str, str]], list[dict[str, Any]]]:
    try:
        validated = model.model_validate(payload)
        return validated, [], []
    except ValidationError as exc:
        errors = cast(list[dict[str, Any]], exc.errors())
        coerced_payload, coercions = _coerce_payload_for_model(payload, model)
        if coercions:
            try:
                validated = model.model_validate(coerced_payload)
                return validated, coercions, []
            except ValidationError as coercion_exc:
                coerced_errors = cast(list[dict[str, Any]], coercion_exc.errors())
                return None, coercions, coerced_errors
        return None, [], errors


def _failure_log_dir(label: str | None) -> Path:
    base = Path.cwd() / "derived" / "logs" / "structured_failures"
    if label:
        base = base / label
    return base


def _metrics_log_path() -> Path:
    return Path.cwd() / "derived" / "logs" / "structured_metrics.jsonl"


def _append_metrics_record(record: dict[str, Any]) -> None:
    path = _metrics_log_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(record, ensure_ascii=False)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(payload + "\n")


def _write_failure_log(
    *,
    label: str | None,
    prompt: str,
    prompt_path: str | None,
    retries: int,
    error: Exception,
    raw_payload: str | None,
) -> None:
    folder = _failure_log_dir(label)
    with contextlib.suppress(FileExistsError):  # pragma: no cover - minor race safety
        folder.mkdir(parents=True, exist_ok=True)

    timestamp = time_utils.format_timestamp(time_utils.now()).replace(":", "-")
    payload: dict[str, Any] = {
        "retries": retries,
        "prompt_path": prompt_path,
        "prompt": prompt,
        "error": str(error),
        "raw_payload": raw_payload,
        "recorded_at": time_utils.format_timestamp(time_utils.now()),
    }
    log_path = folder / f"{timestamp}.json"
    log_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def _extract_raw_payload(error: UnexpectedModelBehavior) -> str | None:
    for attr in ("response_text", "raw_response_text", "output"):
        candidate = getattr(error, attr, None)
        if isinstance(candidate, str) and candidate.strip():
            return candidate
    if error.args:
        summary = error.args[0]
        if isinstance(summary, str) and summary.strip():
            return summary
    return None


def run_ollama_agent(
    config: OllamaConfig,
    prompt: str,
    *,
    system_prompt: str = _JSON_SYSTEM_PROMPT,
    output_type: type[Any] | None = None,
    retries: int = 0,
    prompt_path: str | None = None,
    prompt_hash: str | None = None,
    prompt_kind: str | None = None,
    prompt_set: str | None = None,
    log_label: str | None = None,
) -> LLMResult[_PayloadT]:
    """Run a Pydantic AI agent and return the validated payload with metadata."""
    target_model: type[BaseModel] | None = None
    if isinstance(output_type, type) and issubclass(output_type, BaseModel):
        target_model = output_type
        resolved_output: type[Any] = dict
    else:
        resolved_output = output_type or dict

    agent = build_ollama_agent(
        config,
        system_prompt=system_prompt,
        output_type=resolved_output,
        retries=retries,
    )

    skeleton_json: str | None = None
    if target_model is not None:
        skeleton_json = _to_json(_model_skeleton(target_model))

    base_prompt = _compose_attempt_prompt(
        prompt.rstrip(),
        skeleton=skeleton_json,
        previous_payload=None,
        validation_summary=None,
    )

    try:
        result = agent.run_sync(base_prompt, output_type=resolved_output)
    except UnexpectedModelBehavior as exc:
        raw_payload_text = _extract_raw_payload(exc)
        _write_failure_log(
            label=log_label or getattr(agent, "name", None),
            prompt=base_prompt,
            prompt_path=prompt_path,
            retries=retries,
            error=exc,
            raw_payload=raw_payload_text,
        )
        msg = f"Model returned invalid JSON: {exc}"
        raise LLMResponseError(msg) from exc
    except UserError as exc:
        msg = f"Ollama provider error: {exc}"
        raise LLMResponseError(msg) from exc
    except Exception as exc:  # pragma: no cover - dependent on runtime env
        msg = f"Ollama request failed: {exc}"
        raise LLMResponseError(msg) from exc

    payload: Any = result.output
    coercions_applied: list[dict[str, str]] = []

    if target_model is not None:
        raw_payload_dict: dict[str, Any]
        if isinstance(payload, BaseModel):
            raw_payload_dict = payload.model_dump(mode="python")
        elif isinstance(payload, dict):
            raw_payload_dict = cast(dict[str, Any], payload)
        else:
            try:
                raw_payload_dict = cast(dict[str, Any], json.loads(str(payload)))
            except json.JSONDecodeError as exc:  # pragma: no cover - defensive
                msg = f"Model returned non-JSON payload: {payload!r}"
                raise LLMResponseError(msg) from exc

        validated, coercions, errors = _attempt_model_validation(raw_payload_dict, target_model)
        if validated is None:
            error_display = _to_json(errors) if errors else "validation failed"
            raw_display = _to_json(raw_payload_dict)
            _write_failure_log(
                label=log_label or getattr(agent, "name", None),
                prompt=base_prompt,
                prompt_path=prompt_path,
                retries=retries,
                error=ValueError(f"Validation failed for {target_model.__name__}"),
                raw_payload=raw_display,
            )
            msg = f"Model response failed validation after retries. Errors: {error_display}"
            raise LLMResponseError(msg)

        payload = validated
        coercions_applied.extend(coercions)

    created_at = time_utils.format_timestamp(time_utils.now())

    attempts = 1
    usage = result.usage()
    attempts = usage.requests

    result_payload = LLMResult[_PayloadT](
        model=config.model,
        prompt_path=prompt_path or "<inline>",
        prompt_hash=prompt_hash,
        prompt_kind=prompt_kind,
        prompt_set=prompt_set,
        created_at=created_at,
        payload=cast(_PayloadT, payload),
        attempts=attempts,
        coercions_applied=coercions_applied,
    )

    metrics_record = {
        "prompt_path": result_payload.prompt_path,
        "prompt_kind": result_payload.prompt_kind,
        "prompt_set": result_payload.prompt_set,
        "model": result_payload.model,
        "label": log_label or getattr(agent, "name", None),
        "attempts": result_payload.attempts,
        "coercion_count": len(result_payload.coercions_applied),
        "created_at": result_payload.created_at,
    }
    _append_metrics_record(metrics_record)

    return result_payload


def _hash_prompt(prompt_path: str, *, prompt_set: str | None = None) -> str | None:
    path = resolve_prompt_path(prompt_path, prompt_set=prompt_set)
    try:
        data = path.read_bytes()
    except FileNotFoundError:
        return None
    return sha256(data).hexdigest()


def _load_prompt_template(prompt_path: str, *, prompt_set: str | None = None) -> str:
    path = resolve_prompt_path(prompt_path, prompt_set=prompt_set)
    if path.exists():
        return path.read_text(encoding="utf-8")
    key = Path(prompt_path).name
    return DEFAULT_PROMPTS.get(prompt_path) or DEFAULT_PROMPTS.get(key, "")


def _render_prompt(
    prompt_path: str,
    variables: dict[str, str],
    *,
    prompt_set: str | None = None,
) -> str:
    template = Template(_load_prompt_template(prompt_path, prompt_set=prompt_set))
    return template.safe_substitute(**variables)


StructuredModelT = TypeVar("StructuredModelT", bound=BaseModel)


def invoke_structured_llm(
    prompt_path: str,
    variables: dict[str, str],
    *,
    response_model: type[StructuredModelT],
    agent_name: str,
    config: AppConfig,
    prompt_set: str | None = None,
) -> StructuredModelT:
    prompt = _render_prompt(prompt_path, variables, prompt_set=prompt_set)
    prompt_hash = _hash_prompt(prompt_path, prompt_set=prompt_set)
    prompt_kind = Path(prompt_path).stem
    try:
        ollama_config = build_ollama_config_from_mapping(config)

        result: LLMResult[BaseModel] = run_ollama_agent(
            ollama_config,
            prompt,
            system_prompt=_STRUCTURED_SYSTEM_PROMPT,
            output_type=response_model,
            retries=config.llm.retries,
            prompt_path=prompt_path,
            prompt_hash=prompt_hash,
            prompt_kind=prompt_kind,
            prompt_set=prompt_set,
            log_label=agent_name,
        )
        return cast(StructuredModelT, result.payload)
    except Exception as exc:  # pragma: no cover - runtime dependent
        msg = f"Structured output generation failed for {prompt_path}: {exc}"
        raise LLMResponseError(msg) from exc
