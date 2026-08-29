"""Deprecated legacy Agnes transport compatibility surface.

Active production composition must import the provider-neutral
:mod:`seven_lens.infrastructure.chat_completions_transport` instead.  This
module only re-exports the generic machinery and keeps the historical
``AgnesJsonModelTransport`` parser (exact Agnes optional metadata, provider
specific fields, and fenced-JSON normalization) available for historical
evidence replay and legacy tests.  It is not a second production route.
"""

from __future__ import annotations

import re
from collections.abc import Callable

from seven_lens.application.ports.model_transport import (
    JsonModelRequest,
    JsonModelResponse,
    ModelTransportError,
    ModelTransportErrorCode,
)
from seven_lens.config.analysis_provider import AnalysisProviderConfig
from seven_lens.config.provider import AgnesProviderConfig
from seven_lens.domain.value_objects import UtcTimestamp
from seven_lens.infrastructure.chat_completions_transport import (  # noqa: F401
    ChatCompletionsHttpExecutor,
    ChatCompletionsModelTransport,
    DnsResolver,
    HttpExecutorError,
    HttpExecutorErrorCode,
    RawHttpRequest,
    RawHttpResponse,
    ResolvedAddress,
    SpawnedDnsResolver,
    StdlibChatCompletionsHttpExecutor,
    _PreResolvedHTTPSConnection,
    _strict_json_object,
    build_chat_completions_request_body,
)
from seven_lens.security.secret_values import SecretValue

_EXACT_MODEL = "agnes-2.5-flash"
_RESPONSE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_METADATA_KEY = re.compile(r"^[a-z][a-z0-9_]{0,63}$")

AgnesHttpExecutor = ChatCompletionsHttpExecutor
StdlibAgnesHttpExecutor = StdlibChatCompletionsHttpExecutor


def _legacy_generic_route_config(config: AgnesProviderConfig) -> AnalysisProviderConfig:
    from seven_lens.config.analysis_provider import AnalysisProviderConfig, ConfigSource

    base_path = (
        config.path[: -len("/chat/completions")]
        if config.path.endswith("/chat/completions")
        else config.path
    )
    return AnalysisProviderConfig(
        config_source=ConfigSource.PACKAGE_DEFAULT,
        generation=0,
        base_url=f"https://{config.host}{base_path}",
        model_id=config.model_id,
    )


def build_agnes_request_body(config: AgnesProviderConfig, request: JsonModelRequest) -> bytes:
    """Legacy exact Agnes POST body builder kept for historical evidence replay."""

    import json as _json

    if type(config) is not AgnesProviderConfig or type(request) is not JsonModelRequest:
        raise ModelTransportError(ModelTransportErrorCode.CONFIG)
    if request.max_output_tokens > config.max_output_tokens:
        raise ModelTransportError(ModelTransportErrorCode.CONFIG)
    wire = {
        "max_tokens": request.max_output_tokens,
        "messages": [
            {"content": message.content, "role": message.role.value} for message in request.messages
        ],
        "model": config.model_id,
        "stream": False,
        "temperature": config.temperature,
    }
    if request.response_format is not None:
        wire["response_format"] = dict(request.response_format)
    try:
        body = _json.dumps(
            wire,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    except (TypeError, ValueError, UnicodeError):
        raise ModelTransportError(ModelTransportErrorCode.SCHEMA) from None
    if not body or len(body) > config.request_byte_cap:
        raise ModelTransportError(ModelTransportErrorCode.OVERSIZE)
    return body


class AgnesJsonModelTransport(ChatCompletionsModelTransport):
    """Deprecated single-attempt exact Agnes adapter for historical replay only."""

    def __init__(
        self,
        *,
        config: AgnesProviderConfig,
        api_key: SecretValue,
        executor: ChatCompletionsHttpExecutor,
        clock: Callable[[], UtcTimestamp],
    ) -> None:
        if type(config) is not AgnesProviderConfig or config.model_id != _EXACT_MODEL:
            raise ModelTransportError(ModelTransportErrorCode.CONFIG)
        super().__init__(
            config=_legacy_generic_route_config(config),
            api_key=api_key,
            executor=executor,
            clock=clock,
        )

    def _parse_response(self, payload: dict[str, object], raw_body: bytes) -> JsonModelResponse:
        return _parse_legacy_agnes_response(self, payload, raw_body)


def _parse_legacy_agnes_response(
    transport: ChatCompletionsModelTransport,
    payload: dict[str, object],
    raw_body: bytes,
) -> JsonModelResponse:
    import hashlib

    from seven_lens.application.ports.model_transport import JsonModelResponse

    required_outer = {"id", "object", "created", "model", "choices", "usage"}
    if not required_outer.issubset(payload) or not set(payload).issubset(
        required_outer | {"metadata"}
    ):
        raise ModelTransportError(ModelTransportErrorCode.PROTOCOL)
    if "metadata" in payload:
        _validate_agnes_metadata(payload["metadata"])
    response_id = payload["id"]
    if type(response_id) is not str or _RESPONSE_ID.fullmatch(response_id) is None:
        raise ModelTransportError(ModelTransportErrorCode.PROTOCOL)
    if payload["object"] != "chat.completion":
        raise ModelTransportError(ModelTransportErrorCode.PROTOCOL)
    if type(payload["created"]) is not int or payload["created"] < 0:
        raise ModelTransportError(ModelTransportErrorCode.PROTOCOL)
    if payload["model"] != _EXACT_MODEL:
        raise ModelTransportError(ModelTransportErrorCode.PROTOCOL)
    choices = payload["choices"]
    if type(choices) is not list or len(choices) != 1 or type(choices[0]) is not dict:
        raise ModelTransportError(ModelTransportErrorCode.PROTOCOL)
    choice = choices[0]
    required_choice = {"index", "message", "finish_reason"}
    if not required_choice.issubset(choice) or not set(choice).issubset(
        required_choice | {"provider_specific_fields"}
    ):
        raise ModelTransportError(ModelTransportErrorCode.PROTOCOL)
    if "provider_specific_fields" in choice:
        _validate_provider_specific_fields(choice["provider_specific_fields"])
    if type(choice["index"]) is not int or choice["index"] != 0:
        raise ModelTransportError(ModelTransportErrorCode.PROTOCOL)
    if choice["finish_reason"] != "stop":
        raise ModelTransportError(ModelTransportErrorCode.SCHEMA)
    message = choice["message"]
    if type(message) is not dict:
        raise ModelTransportError(ModelTransportErrorCode.PROTOCOL)
    required_message = {"role", "content"}
    if not required_message.issubset(message) or not set(message).issubset(
        required_message | {"reasoning_content"}
    ):
        raise ModelTransportError(ModelTransportErrorCode.PROTOCOL)
    if "reasoning_content" in message:
        reasoning_content = message["reasoning_content"]
        if type(reasoning_content) is not str:
            raise ModelTransportError(ModelTransportErrorCode.PROTOCOL)
    if message["role"] != "assistant" or type(message["content"]) is not str:
        raise ModelTransportError(ModelTransportErrorCode.PROTOCOL)
    content = _normalize_model_content(message["content"])
    usage = payload["usage"]
    if type(usage) is not dict:
        raise ModelTransportError(ModelTransportErrorCode.PROTOCOL)
    required_usage = {"prompt_tokens", "completion_tokens", "total_tokens"}
    optional_usage = {"prompt_tokens_details", "completion_tokens_details"}
    if not required_usage.issubset(usage) or not set(usage).issubset(
        required_usage | optional_usage
    ):
        raise ModelTransportError(ModelTransportErrorCode.PROTOCOL)
    for details_name in sorted(optional_usage & set(usage)):
        _validate_token_details(usage[details_name])
    prompt_tokens = usage["prompt_tokens"]
    completion_tokens = usage["completion_tokens"]
    total_tokens = usage["total_tokens"]
    if (
        any(
            type(value) is not int or value < 0 or value > 10_000_000
            for value in (prompt_tokens, completion_tokens, total_tokens)
        )
        or total_tokens != prompt_tokens + completion_tokens
    ):
        raise ModelTransportError(ModelTransportErrorCode.PROTOCOL)
    return JsonModelResponse(
        provider_response_id=response_id,
        model_id=_EXACT_MODEL,
        content=content,
        response_hash=hashlib.sha256(raw_body).hexdigest(),
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        total_tokens=total_tokens,
    )


def _validate_agnes_metadata(value: object) -> None:
    if type(value) is not dict or set(value) != {"weight_version"}:
        raise ModelTransportError(ModelTransportErrorCode.PROTOCOL)
    weight_version = value["weight_version"]
    if type(weight_version) is not str or _RESPONSE_ID.fullmatch(weight_version) is None:
        raise ModelTransportError(ModelTransportErrorCode.PROTOCOL)


def _validate_token_details(value: object) -> None:
    if type(value) is not dict or len(value) > 16:
        raise ModelTransportError(ModelTransportErrorCode.PROTOCOL)
    for key, count in value.items():
        if (
            type(key) is not str
            or _METADATA_KEY.fullmatch(key) is None
            or type(count) is not int
            or not 0 <= count <= 10_000_000
        ):
            raise ModelTransportError(ModelTransportErrorCode.PROTOCOL)


def _validate_provider_specific_fields(value: object) -> None:
    if type(value) is not dict or set(value) != {"matched_stop"}:
        raise ModelTransportError(ModelTransportErrorCode.PROTOCOL)
    matched_stop = value["matched_stop"]
    if type(matched_stop) is not int or not -1 <= matched_stop <= 1_000_000:
        raise ModelTransportError(ModelTransportErrorCode.PROTOCOL)


def _normalize_model_content(content: str) -> str:
    """Historical Agnes fenced-JSON normalization; legacy parse path only."""

    if not content:
        raise ModelTransportError(ModelTransportErrorCode.SCHEMA)
    if "```" not in content:
        _strict_json_object(content.encode("utf-8"), ModelTransportErrorCode.SCHEMA)
        return content
    stripped = content.strip()
    prefix = "```json\n"
    suffix = "\n```"
    if (
        stripped.count("```") != 2
        or not stripped.startswith(prefix)
        or not stripped.endswith(suffix)
    ):
        raise ModelTransportError(ModelTransportErrorCode.SCHEMA)
    normalized = stripped[len(prefix) : -len(suffix)]
    if not normalized.startswith("{") or not normalized.endswith("}"):
        raise ModelTransportError(ModelTransportErrorCode.SCHEMA)
    _strict_json_object(normalized.encode("utf-8"), ModelTransportErrorCode.SCHEMA)
    return normalized
