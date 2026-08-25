"""Exact typed contracts for the synthetic P3-F evaluation corpus."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from enum import StrEnum
from types import MappingProxyType
from typing import Final, cast

from seven_lens.domain.json_values import JsonValue

_HASH: Final = re.compile(r"^[0-9a-f]{64}$")
_ID: Final = re.compile(r"^[a-z0-9][a-z0-9._:-]{0,127}$")


class EvalSplit(StrEnum):
    GOLDEN = "golden"
    TRAINING = "training"
    DEV = "dev"
    HELD_OUT = "held_out"


class EvalFamily(StrEnum):
    SAFETY = "safety"
    SEMANTIC_TRACE = "semantic_trace"
    MEMORY = "memory"
    ROUTE = "route"


class EvalMode(StrEnum):
    NORMAL = "normal"
    EMERGENCY = "emergency"


class CaseValidity(StrEnum):
    VALID = "valid"
    INVALID = "invalid"
    AMBIGUOUS = "ambiguous"


class ExpectedDecision(StrEnum):
    ACCEPT = "ACCEPT"
    REJECT = "REJECT"
    ABSTAIN = "ABSTAIN"


def canonical_bytes(value: JsonValue) -> bytes:
    return json.dumps(
        value,
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def content_hash(value: JsonValue) -> str:
    return hashlib.sha256(canonical_bytes(value)).hexdigest()


def _exact_object(value: object, keys: frozenset[str], label: str) -> dict[str, object]:
    if type(value) is not dict:
        raise ValueError(f"{label} must be an exact object")
    obj = cast(dict[str, object], value)
    if set(obj) != keys:
        raise ValueError(f"{label} fields are not exact")
    return obj


def _exact_string(value: object, label: str, *, identifier: bool = False) -> str:
    if type(value) is not str or (identifier and _ID.fullmatch(value) is None):
        raise ValueError(f"{label} is invalid")
    return value


def _exact_enum[EnumT: StrEnum](value: object, enum_type: type[EnumT], label: str) -> EnumT:
    raw = _exact_string(value, label)
    try:
        return enum_type(raw)
    except ValueError:
        raise ValueError(f"{label} is invalid") from None


@dataclass(frozen=True, slots=True)
class EvalCase:
    case_id: str
    split: EvalSplit
    family: EvalFamily
    mode: EvalMode
    scenario: str
    stage: str | None
    role: str | None
    payload: MappingProxyType[str, JsonValue]
    fixture_hash: str

    @classmethod
    def from_wire(cls, value: object) -> EvalCase:
        obj = _exact_object(
            value,
            frozenset(
                {
                    "case_id",
                    "split",
                    "family",
                    "mode",
                    "scenario",
                    "stage",
                    "role",
                    "payload",
                    "fixture_hash",
                }
            ),
            "eval case",
        )
        payload = obj["payload"]
        if type(payload) is not dict:
            raise ValueError("eval payload must be an exact object")
        stage = obj["stage"]
        role = obj["role"]
        if stage is not None and type(stage) is not str:
            raise ValueError("eval stage is invalid")
        if role is not None and type(role) is not str:
            raise ValueError("eval role is invalid")
        fixture_hash = _exact_string(obj["fixture_hash"], "fixture hash")
        if _HASH.fullmatch(fixture_hash) is None:
            raise ValueError("fixture hash is invalid")
        material = {key: item for key, item in obj.items() if key != "fixture_hash"}
        if content_hash(cast(JsonValue, material)) != fixture_hash:
            raise ValueError("fixture hash mismatch")
        return cls(
            case_id=_exact_string(obj["case_id"], "case id", identifier=True),
            split=_exact_enum(obj["split"], EvalSplit, "case split"),
            family=_exact_enum(obj["family"], EvalFamily, "case family"),
            mode=_exact_enum(obj["mode"], EvalMode, "case mode"),
            scenario=_exact_string(obj["scenario"], "case scenario", identifier=True),
            stage=stage,
            role=role,
            payload=MappingProxyType(cast(dict[str, JsonValue], payload)),
            fixture_hash=fixture_hash,
        )


@dataclass(frozen=True, slots=True)
class ExpectedAnswer:
    case_id: str
    validity: CaseValidity
    decision: ExpectedDecision
    trace_hash: str | None
    answer_hash: str

    @classmethod
    def from_wire(cls, value: object) -> ExpectedAnswer:
        obj = _exact_object(
            value,
            frozenset({"case_id", "validity", "decision", "trace_hash", "answer_hash"}),
            "eval answer",
        )
        trace_hash = obj["trace_hash"]
        if trace_hash is not None and (
            type(trace_hash) is not str or _HASH.fullmatch(trace_hash) is None
        ):
            raise ValueError("answer trace hash is invalid")
        answer_hash = _exact_string(obj["answer_hash"], "answer hash")
        if _HASH.fullmatch(answer_hash) is None:
            raise ValueError("answer hash is invalid")
        material = {key: item for key, item in obj.items() if key != "answer_hash"}
        if content_hash(cast(JsonValue, material)) != answer_hash:
            raise ValueError("answer hash mismatch")
        return cls(
            case_id=_exact_string(obj["case_id"], "answer case id", identifier=True),
            validity=_exact_enum(obj["validity"], CaseValidity, "answer validity"),
            decision=_exact_enum(obj["decision"], ExpectedDecision, "expected decision"),
            trace_hash=trace_hash,
            answer_hash=answer_hash,
        )


@dataclass(frozen=True, slots=True)
class CaseManifest:
    manifest_id: str
    split_version: str
    split: EvalSplit
    cases: tuple[EvalCase, ...]
    content_hash: str

    @classmethod
    def from_wire(cls, value: object) -> CaseManifest:
        obj = _exact_object(
            value,
            frozenset(
                {"schema_version", "manifest_id", "split_version", "split", "cases", "content_hash"}
            ),
            "case manifest",
        )
        if obj["schema_version"] != "seven-lens.p3f.eval-cases.v1":
            raise ValueError("case manifest schema is invalid")
        manifest_hash = _exact_string(obj["content_hash"], "manifest hash")
        material = {key: item for key, item in obj.items() if key != "content_hash"}
        if (
            _HASH.fullmatch(manifest_hash) is None
            or content_hash(cast(JsonValue, material)) != manifest_hash
        ):
            raise ValueError("case manifest hash mismatch")
        raw_cases = obj["cases"]
        if type(raw_cases) is not list:
            raise ValueError("case manifest cases must be a list")
        cases = tuple(EvalCase.from_wire(item) for item in raw_cases)
        if not cases or len({case.case_id for case in cases}) != len(cases):
            raise ValueError("case manifest IDs must be non-empty and unique")
        split = _exact_enum(obj["split"], EvalSplit, "manifest split")
        if any(case.split is not split for case in cases):
            raise ValueError("case manifest contains a foreign split")
        return cls(
            manifest_id=_exact_string(obj["manifest_id"], "manifest id", identifier=True),
            split_version=_exact_string(obj["split_version"], "split version", identifier=True),
            split=split,
            cases=cases,
            content_hash=manifest_hash,
        )


@dataclass(frozen=True, slots=True)
class AnswerManifest:
    manifest_id: str
    split_version: str
    split: EvalSplit
    answers: tuple[ExpectedAnswer, ...]
    content_hash: str

    @classmethod
    def from_wire(cls, value: object) -> AnswerManifest:
        obj = _exact_object(
            value,
            frozenset(
                {
                    "schema_version",
                    "manifest_id",
                    "split_version",
                    "split",
                    "answers",
                    "content_hash",
                }
            ),
            "answer manifest",
        )
        if obj["schema_version"] != "seven-lens.p3f.eval-answers.v1":
            raise ValueError("answer manifest schema is invalid")
        manifest_hash = _exact_string(obj["content_hash"], "answer manifest hash")
        material = {key: item for key, item in obj.items() if key != "content_hash"}
        if (
            _HASH.fullmatch(manifest_hash) is None
            or content_hash(cast(JsonValue, material)) != manifest_hash
        ):
            raise ValueError("answer manifest hash mismatch")
        raw_answers = obj["answers"]
        if type(raw_answers) is not list:
            raise ValueError("answer manifest answers must be a list")
        answers = tuple(ExpectedAnswer.from_wire(item) for item in raw_answers)
        if not answers or len({answer.case_id for answer in answers}) != len(answers):
            raise ValueError("answer IDs must be non-empty and unique")
        return cls(
            manifest_id=_exact_string(obj["manifest_id"], "answer manifest id", identifier=True),
            split_version=_exact_string(
                obj["split_version"], "answer split version", identifier=True
            ),
            split=_exact_enum(obj["split"], EvalSplit, "answer split"),
            answers=answers,
            content_hash=manifest_hash,
        )


def readonly_payload(value: dict[str, JsonValue]) -> MappingProxyType[str, JsonValue]:
    return MappingProxyType(value)
