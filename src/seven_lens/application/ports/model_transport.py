"""Application-neutral typed JSON model transport contract."""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol

from seven_lens.domain.value_objects import RunId, UtcTimestamp

_HASH = re.compile(r"^[0-9a-f]{64}$")
_RESPONSE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_MAX_MESSAGE_BYTES = 131_072
_MAX_RESPONSE_CONTENT_BYTES = 131_072
_MAX_RESPONSE_FORMAT_NODES = 128
_MAX_RESPONSE_FORMAT_DEPTH = 8
_MAX_RESPONSE_FORMAT_STRING_BYTES = 512


class JsonMessageRole(StrEnum):
    SYSTEM = "system"
    DEVELOPER = "developer"
    USER = "user"


@dataclass(frozen=True, slots=True, repr=False)
class JsonModelMessage:
    role: JsonMessageRole
    content: str

    def __post_init__(self) -> None:
        if type(self.role) is not JsonMessageRole:
            raise ValueError("model message role is invalid")
        if (
            type(self.content) is not str
            or not self.content
            or len(self.content.encode("utf-8")) > _MAX_MESSAGE_BYTES
            or "\x00" in self.content
        ):
            raise ValueError("model message content is invalid")

    def __repr__(self) -> str:
        return (
            "JsonModelMessage("
            f"role={self.role.value!r}, content=[REDACTED], "
            f"content_bytes={len(self.content.encode('utf-8'))})"
        )


@dataclass(frozen=True, slots=True, repr=False)
class JsonModelRequest:
    call_id: RunId
    messages: tuple[JsonModelMessage, ...]
    deadline: UtcTimestamp
    max_output_tokens: int
    response_format: Mapping[str, object] | None = None

    def __post_init__(self) -> None:
        if type(self.call_id) is not RunId:
            raise ValueError("model request call identity is invalid")
        if (
            type(self.messages) is not tuple
            or len(self.messages) not in {2, 3}
            or any(type(message) is not JsonModelMessage for message in self.messages)
        ):
            raise ValueError("model request messages are invalid")
        expected_roles = (
            (JsonMessageRole.SYSTEM, JsonMessageRole.USER)
            if len(self.messages) == 2
            else (
                JsonMessageRole.SYSTEM,
                JsonMessageRole.DEVELOPER,
                JsonMessageRole.USER,
            )
        )
        if tuple(message.role for message in self.messages) != expected_roles:
            raise ValueError("model request message order is invalid")
        if type(self.deadline) is not UtcTimestamp:
            raise ValueError("model request deadline is invalid")
        if type(self.max_output_tokens) is not int or not 1 <= self.max_output_tokens <= 65_536:
            raise ValueError("model request output token limit is invalid")
        if self.response_format is not None and not _valid_response_format(
            self.response_format, depth=0
        ):
            raise ValueError("model request response format is invalid")

    def __repr__(self) -> str:
        return (
            "JsonModelRequest("
            f"call_id={str(self.call_id)!r}, messages=[REDACTED], "
            f"message_count={len(self.messages)}, deadline={str(self.deadline)!r}, "
            f"max_output_tokens={self.max_output_tokens}, "
            f"response_format={'SET' if self.response_format is not None else 'NONE'})"
        )


def _valid_response_format(value: object, *, depth: int) -> bool:
    if depth > _MAX_RESPONSE_FORMAT_DEPTH:
        return False
    if value is None or type(value) is bool or type(value) is int:
        return True
    if type(value) is float:
        return value == value and value not in (float("inf"), float("-inf"))
    if type(value) is str:
        return 0 < len(value.encode("utf-8")) <= _MAX_RESPONSE_FORMAT_STRING_BYTES
    if type(value) is list:
        return len(value) <= _MAX_RESPONSE_FORMAT_NODES and all(
            _valid_response_format(item, depth=depth + 1) for item in value
        )
    if isinstance(value, Mapping):
        return (
            len(value) <= _MAX_RESPONSE_FORMAT_NODES
            and all(type(key) is str and 0 < len(key) <= 128 for key in value)
            and all(_valid_response_format(item, depth=depth + 1) for item in value.values())
        )
    return False


@dataclass(frozen=True, slots=True, repr=False)
class JsonModelResponse:
    provider_response_id: str
    model_id: str
    content: str
    response_hash: str
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int

    def __post_init__(self) -> None:
        if (
            type(self.provider_response_id) is not str
            or _RESPONSE_ID.fullmatch(self.provider_response_id) is None
        ):
            raise ValueError("model response identity is invalid")
        if type(self.model_id) is not str or not self.model_id:
            raise ValueError("model response model identity is invalid")
        if (
            type(self.content) is not str
            or not self.content
            or len(self.content.encode("utf-8")) > _MAX_RESPONSE_CONTENT_BYTES
        ):
            raise ValueError("model response content is invalid")
        if type(self.response_hash) is not str or _HASH.fullmatch(self.response_hash) is None:
            raise ValueError("model response hash is invalid")
        for count in (self.prompt_tokens, self.completion_tokens, self.total_tokens):
            if type(count) is not int or count < 0 or count > 10_000_000:
                raise ValueError("model response token counts are invalid")
        if self.total_tokens != self.prompt_tokens + self.completion_tokens:
            raise ValueError("model response token counts are inconsistent")

    def __repr__(self) -> str:
        return (
            "JsonModelResponse("
            "provider_response_id=[REDACTED], "
            f"model_id={self.model_id!r}, content=[REDACTED], "
            f"content_bytes={len(self.content.encode('utf-8'))}, "
            f"response_hash={self.response_hash!r}, prompt_tokens={self.prompt_tokens}, "
            f"completion_tokens={self.completion_tokens}, total_tokens={self.total_tokens})"
        )


class ModelTransportErrorCode(StrEnum):
    CONFIG = "CONFIG"
    AUTH = "AUTH"
    PERMANENT = "PERMANENT"
    RATE_LIMIT = "RATE_LIMIT"
    TRANSIENT = "TRANSIENT"
    TIMEOUT = "TIMEOUT"
    PROTOCOL = "PROTOCOL"
    SCHEMA = "SCHEMA"
    OVERSIZE = "OVERSIZE"
    # Kept for decoding older callers; ModelTransportError normalizes it to TIMEOUT.
    DEADLINE = "DEADLINE"
    AUDIT = "AUDIT"


_ERROR_MESSAGES = {
    ModelTransportErrorCode.CONFIG: "model transport configuration is invalid",
    ModelTransportErrorCode.AUTH: "model provider authentication failed",
    ModelTransportErrorCode.PERMANENT: "model provider rejected the request",
    ModelTransportErrorCode.RATE_LIMIT: "model provider rate limit was reached",
    ModelTransportErrorCode.TRANSIENT: "model provider transport failed",
    ModelTransportErrorCode.TIMEOUT: "model provider request timed out",
    ModelTransportErrorCode.PROTOCOL: "model provider protocol response is invalid",
    ModelTransportErrorCode.SCHEMA: "model provider output schema is invalid",
    ModelTransportErrorCode.OVERSIZE: "model provider payload exceeds the fixed limit",
    ModelTransportErrorCode.DEADLINE: "model provider deadline was exceeded",
    ModelTransportErrorCode.AUDIT: "model call audit failed",
}


class ModelTransportError(RuntimeError):
    """Fixed non-disclosing failure safe for audit classification."""

    __slots__ = ("code",)

    def __init__(self, code: ModelTransportErrorCode) -> None:
        if type(code) is not ModelTransportErrorCode:
            raise ValueError("model transport error code is invalid")
        normalized = (
            ModelTransportErrorCode.TIMEOUT if code is ModelTransportErrorCode.DEADLINE else code
        )
        self.code = normalized
        super().__init__(_ERROR_MESSAGES[normalized])

    def __repr__(self) -> str:
        return f"ModelTransportError(code={self.code.value!r})"


class JsonModelTransport(Protocol):
    def execute(self, request: JsonModelRequest) -> JsonModelResponse: ...
