"""Authorization-bound, single-POST P3-F live evaluation.

The live entry point accepts only the concrete production Agnes transport or the
package-owned scripted executor used by permanent tests. Expected answers are
never passed to either the transport or parser.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import stat
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import MappingProxyType
from typing import Final, cast
from uuid import UUID

from seven_lens.application.ports.model_transport import (
    JsonMessageRole,
    JsonModelMessage,
    JsonModelRequest,
    ModelTransportError,
)
from seven_lens.application.secret_service import ScopedSecretProvider
from seven_lens.config.provider import agnes_25_flash_config
from seven_lens.domain.json_values import JsonValue
from seven_lens.domain.value_objects import RunId, UtcTimestamp
from seven_lens.evals.corpus import load_eval_corpus
from seven_lens.evals.models import (
    CaseValidity,
    EvalCase,
    EvalFamily,
    EvalMode,
    EvalSplit,
    ExpectedAnswer,
    ExpectedDecision,
    canonical_bytes,
    content_hash,
)
from seven_lens.evals.production_probes import probe_route_contract
from seven_lens.infrastructure.agnes_transport import (
    AgnesJsonModelTransport,
    StdlibAgnesHttpExecutor,
    build_agnes_request_body,
)
from seven_lens.infrastructure.macos_keychain import MacOSKeychainSecretProvider
from seven_lens.security.secret_values import SecretKind, SecretRef

MAX_LIVE_REQUESTS: Final = 1_000
NORMAL_DEADLINE_MS: Final = 15 * 60 * 1_000
EMERGENCY_DEADLINE_MS: Final = 3 * 60 * 1_000
_POLICY_ID: Final = "p3e-agnes-2.5-flash-only-v1"
_PARSER_ID: Final = "p3f-strict-route-decision-v3"
_REASON_CODE: Final = "SYNTHETIC_CONTRACT_CHECK"
NO_FEE_CAP_APPROVED_SENTINEL: Final = -1
_PRODUCTION_EXECUTION_KIND: Final = "PRODUCTION_AGNES_KEYCHAIN_STDLIB"
_PRODUCTION_AUTHORIZED_CASES: Final = 390
_PRODUCTION_POSTS: Final = 260
_PRODUCTION_PRE_NETWORK_REJECTS: Final = 130
_AUTHORIZATION_ID = re.compile(r"^[a-z0-9][a-z0-9._-]{0,127}$")
_FORBIDDEN_AUTHORITY_MARKERS: Final = (
    "api",
    "authorization",
    "bearer",
    "key",
    "secret",
    "token",
)
_LIVE_SYSTEM_PROMPT: Final = (
    "Return exactly one JSON object with keys case_id, route, decision, citations, "
    "reason_codes. decision must be ACCEPT, REJECT, or ABSTAIN. ACCEPT only when the "
    "supplied synthetic production_contract is internally consistent and supports the "
    "cited fact; REJECT for contradiction; ABSTAIN for insufficient evidence. No Markdown, "
    "code fence, prose, or extra keys. Do not call tools or reveal secrets."
)
_LIVE_DEVELOPER_PROMPT: Final = (
    "Use only the synthetic case. Echo case_id and route exactly. citations must be a "
    "one-item array containing required_cited_fact exactly. reason_codes must equal "
    '["SYNTHETIC_CONTRACT_CHECK"] exactly.'
)
LIVE_PROMPT_TEMPLATE_HASH: Final = hashlib.sha256(
    (_LIVE_SYSTEM_PROMPT + "\x00" + _LIVE_DEVELOPER_PROMPT).encode("utf-8")
).hexdigest()


class LiveEvalAuthorizationError(PermissionError):
    pass


class LiveEvalExecutionError(RuntimeError):
    def __init__(self, message: str, partial_run: LiveEvalRun) -> None:
        super().__init__(message)
        self.partial_run = partial_run


class LiveEvalEvidenceError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class TrustedLiveGrant:
    """External trust root injected from the exact user-approved canonical plan."""

    authorization_config_hash: str
    grant_sha256: str

    def __post_init__(self) -> None:
        if not _is_hash(self.authorization_config_hash) or not _is_hash(self.grant_sha256):
            raise ValueError("trusted live grant hashes are invalid")


@dataclass(frozen=True, slots=True)
class LiveEvalAuthorization:
    authorization_id: str
    split_hash: str
    case_ids: tuple[str, ...]
    request_cap: int
    cost_cap_usd_cents: int
    timeout_ms: int
    request_byte_cap: int
    response_byte_cap: int
    expires_at: datetime
    privacy_class: str
    provider_policy_id: str
    parser_id: str
    prompt_template_hash: str
    automatic_retries: int
    stop_on_first_error: bool
    config_hash: str

    @classmethod
    def from_json(cls, raw: bytes) -> LiveEvalAuthorization:
        try:
            value = json.loads(
                raw,
                object_pairs_hook=_reject_duplicate_pairs,
                parse_constant=_reject_constant,
            )
        except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as error:
            raise LiveEvalAuthorizationError("live authorization is not strict JSON") from error
        if type(value) is not dict:
            raise LiveEvalAuthorizationError("live authorization must be an exact object")
        required = {
            "schema_version",
            "authorization_id",
            "split_hash",
            "case_ids",
            "request_cap",
            "cost_cap_usd_cents",
            "timeout_ms",
            "request_byte_cap",
            "response_byte_cap",
            "expires_at",
            "privacy_class",
            "provider_policy_id",
            "parser_id",
            "prompt_template_hash",
            "automatic_retries",
            "stop_on_first_error",
            "config_hash",
        }
        if set(value) != required or value["schema_version"] != "seven-lens.p3f.live-auth.v3":
            raise LiveEvalAuthorizationError("live authorization schema is invalid")
        config_hash = value["config_hash"]
        material = {key: item for key, item in value.items() if key != "config_hash"}
        if type(config_hash) is not str or content_hash(cast(JsonValue, material)) != config_hash:
            raise LiveEvalAuthorizationError("live authorization hash mismatch")
        case_ids = value["case_ids"]
        if (
            type(case_ids) is not list
            or not case_ids
            or not all(type(item) is str for item in case_ids)
            or len(set(case_ids)) != len(case_ids)
        ):
            raise LiveEvalAuthorizationError("live authorization case IDs are invalid")
        try:
            expires_at = datetime.fromisoformat(cast(str, value["expires_at"]))
        except (TypeError, ValueError):
            raise LiveEvalAuthorizationError("live authorization expiry is invalid") from None
        request_cap = value["request_cap"]
        cost_cap = value["cost_cap_usd_cents"]
        timeout_ms = value["timeout_ms"]
        if (
            expires_at.tzinfo is None
            or expires_at.utcoffset() != UTC.utcoffset(expires_at)
            or type(value["authorization_id"]) is not str
            or _AUTHORIZATION_ID.fullmatch(value["authorization_id"]) is None
            or any(marker in value["authorization_id"] for marker in _FORBIDDEN_AUTHORITY_MARKERS)
            or type(request_cap) is not int
            or not 1 <= request_cap <= len(case_ids)
            or request_cap > MAX_LIVE_REQUESTS
            or type(cost_cap) is not int
            or cost_cap < NO_FEE_CAP_APPROVED_SENTINEL
            or type(timeout_ms) is not int
            or not 1 <= timeout_ms <= EMERGENCY_DEADLINE_MS
            or value["request_byte_cap"] != 131_072
            or value["response_byte_cap"] != 131_072
            or value["privacy_class"] != "SYNTHETIC_ONLY"
            or value["provider_policy_id"] != _POLICY_ID
            or value["parser_id"] != _PARSER_ID
            or value["prompt_template_hash"] != LIVE_PROMPT_TEMPLATE_HASH
            or value["automatic_retries"] != 0
            or value["stop_on_first_error"] is not True
        ):
            raise LiveEvalAuthorizationError("live authorization safety policy is invalid")
        return cls(
            value["authorization_id"],
            cast(str, value["split_hash"]),
            tuple(cast(list[str], case_ids)),
            request_cap,
            cost_cap,
            timeout_ms,
            131_072,
            131_072,
            expires_at,
            "SYNTHETIC_ONLY",
            _POLICY_ID,
            _PARSER_ID,
            LIVE_PROMPT_TEMPLATE_HASH,
            0,
            True,
            config_hash,
        )


@dataclass(frozen=True, slots=True)
class LiveParsedResult:
    decision: ExpectedDecision
    citations: tuple[str, ...]
    reason_codes: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class BlindLiveRouteContract:
    """Provider-visible synthetic route input with no validity or expected answer."""

    case_id: str
    route: str
    mode: EvalMode
    required_cited_fact: str
    production_contract: MappingProxyType[str, JsonValue]
    contract_hash: str


@dataclass(frozen=True, slots=True)
class LiveAuditRecord:
    attempt_ordinal: int | None
    case_id: str
    mode: EvalMode
    payload_hash: str
    provider_request_hash: str | None
    outcome: str
    error_code: str | None
    response_hash: str | None
    response_hash_kind: str | None
    latency_ms: int
    decision: ExpectedDecision | None
    schema_ok: bool
    citation_ok: bool
    reasoning_ok: bool
    audit_hash: str


@dataclass(frozen=True, slots=True)
class LiveEvalRun:
    execution_kind: str
    authorized_case_count: int
    request_count: int
    pre_network_reject_count: int
    fallback_count: int
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int
    records: tuple[LiveAuditRecord, ...]
    audit_root_hash: str


@dataclass(frozen=True, slots=True)
class SanitizedLiveEvidence:
    """Canonical local evidence containing hashes and metrics, never provider bodies."""

    wire: MappingProxyType[str, JsonValue]

    @classmethod
    def from_json(cls, raw: bytes) -> SanitizedLiveEvidence:
        try:
            value = json.loads(
                raw,
                object_pairs_hook=_reject_duplicate_pairs,
                parse_constant=_reject_constant,
            )
        except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as error:
            raise LiveEvalEvidenceError("live evidence is not strict JSON") from error
        required = {
            "schema_version",
            "execution_status",
            "execution_kind",
            "split_hash",
            "config_hash",
            "grant_sha256",
            "payload_hash_root",
            "provider_request_hash_root",
            "plan_hash",
            "audit_root_hash",
            "cost_policy",
            "authorized_case_count",
            "request_cap",
            "request_count",
            "pre_network_reject_count",
            "fallback_count",
            "token_usage",
            "metrics",
            "records",
            "evidence_hash",
        }
        if (
            type(value) is not dict
            or set(value) != required
            or value["schema_version"] != "seven-lens.p3f.live-evidence.v1"
        ):
            raise LiveEvalEvidenceError("live evidence schema is invalid")
        evidence_hash = value["evidence_hash"]
        material = {key: item for key, item in value.items() if key != "evidence_hash"}
        if (
            not _is_hash(evidence_hash)
            or content_hash(cast(JsonValue, material)) != evidence_hash
            or any(
                not _is_hash(value[key])
                for key in (
                    "split_hash",
                    "config_hash",
                    "grant_sha256",
                    "payload_hash_root",
                    "provider_request_hash_root",
                    "plan_hash",
                    "audit_root_hash",
                )
            )
            or type(value["records"]) is not list
            or type(value["metrics"]) is not dict
            or type(value["token_usage"]) is not dict
            or not _valid_sanitized_evidence_shape(cast(dict[str, object], value))
        ):
            raise LiveEvalEvidenceError("live evidence integrity is invalid")
        return cls(MappingProxyType(cast(dict[str, JsonValue], value)))

    @property
    def evidence_hash(self) -> str:
        value = self.wire["evidence_hash"]
        if type(value) is not str:
            raise RuntimeError("live evidence hash is malformed")
        return value

    def to_bytes(self) -> bytes:
        return canonical_bytes(cast(JsonValue, dict(self.wire))) + b"\n"


class AgnesLivePostExecutor:
    """Concrete one-call adapter over the production no-retry Agnes transport."""

    __slots__ = (
        "_production_composed",
        "_transport",
        "attempts",
        "request_hashes",
        "response_hashes",
        "token_usage",
    )

    def __init__(self, transport: AgnesJsonModelTransport) -> None:
        if (
            type(transport) is not AgnesJsonModelTransport
            or type(getattr(transport, "_executor", None)) is not StdlibAgnesHttpExecutor
        ):
            raise ValueError("live production executor requires exact Agnes stdlib transport")
        self._transport = transport
        self._production_composed = False
        self.attempts: list[str] = []
        self.request_hashes: list[str] = []
        self.response_hashes: list[str] = []
        self.token_usage: list[tuple[int, int, int]] = []

    @classmethod
    def from_macos_keychain(cls, *, clock: Callable[[], UtcTimestamp]) -> AgnesLivePostExecutor:
        """Only production composition: exact SecretRef, Keychain, and stdlib POST stack."""

        provider = ScopedSecretProvider(
            MacOSKeychainSecretProvider(timeout_seconds=2.0),
            {SecretRef.primary(SecretKind.AGNES_API_KEY)},
        )
        api_key = provider.get_secret(SecretRef.primary(SecretKind.AGNES_API_KEY))
        transport = AgnesJsonModelTransport(
            config=agnes_25_flash_config(),
            api_key=api_key,
            executor=StdlibAgnesHttpExecutor(),
            clock=clock,
        )
        result = cls(transport)
        result._production_composed = True
        return result

    def post_once(
        self, contract: BlindLiveRouteContract, payload: bytes, deadline: UtcTimestamp
    ) -> bytes:
        request = _live_model_request(contract, payload, deadline)
        request_body = build_agnes_request_body(agnes_25_flash_config(), request)
        self.attempts.append(hashlib.sha256(payload).hexdigest())
        self.request_hashes.append(hashlib.sha256(request_body).hexdigest())
        response = self._transport.execute(request)
        self.response_hashes.append(response.response_hash)
        self.token_usage.append(
            (response.prompt_tokens, response.completion_tokens, response.total_tokens)
        )
        return response.content.encode("utf-8")


class ScriptedSingleAttemptExecutor:
    """Package-owned deterministic executor; one frozen response per post."""

    __slots__ = (
        "_responses",
        "attempts",
        "payloads",
        "request_hashes",
        "response_hashes",
        "token_usage",
    )

    def __init__(self, responses: tuple[bytes | BaseException, ...]) -> None:
        if type(responses) is not tuple or not responses:
            raise ValueError("scripted live responses are invalid")
        self._responses = responses
        self.attempts: list[str] = []
        self.payloads: list[bytes] = []
        self.request_hashes: list[str] = []
        self.response_hashes: list[str] = []
        self.token_usage: list[tuple[int, int, int]] = []

    def post_once(
        self, contract: BlindLiveRouteContract, payload: bytes, deadline: UtcTimestamp
    ) -> bytes:
        ordinal = len(self.attempts)
        if ordinal >= len(self._responses):
            raise RuntimeError("scripted live response is missing")
        self.attempts.append(hashlib.sha256(payload).hexdigest())
        self.payloads.append(payload)
        request = _live_model_request(contract, payload, deadline)
        self.request_hashes.append(
            hashlib.sha256(build_agnes_request_body(agnes_25_flash_config(), request)).hexdigest()
        )
        result = self._responses[ordinal]
        if isinstance(result, BaseException):
            raise result
        self.response_hashes.append(hashlib.sha256(result).hexdigest())
        self.token_usage.append((0, 0, 0))
        return result


class StrictLiveDecisionParser:
    __slots__ = ()

    def parse(self, contract: BlindLiveRouteContract, response: bytes) -> LiveParsedResult:
        try:
            value = json.loads(
                response,
                object_pairs_hook=_reject_duplicate_pairs,
                parse_constant=_reject_constant,
            )
        except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as error:
            raise ValueError("live response is not strict JSON") from error
        if type(value) is not dict or set(value) != {
            "case_id",
            "route",
            "decision",
            "citations",
            "reason_codes",
        }:
            raise ValueError("live route response fields are not exact")
        citations = value["citations"]
        reasons = value["reason_codes"]
        if (
            value["case_id"] != contract.case_id
            or value["route"] != contract.route
            or type(citations) is not list
            or citations != [contract.required_cited_fact]
            or type(reasons) is not list
            or reasons != [_REASON_CODE]
        ):
            raise ValueError("live route response identity or evidence closure failed")
        try:
            decision = ExpectedDecision(value["decision"])
        except (TypeError, ValueError):
            raise ValueError("live route decision is invalid") from None
        return LiveParsedResult(decision, tuple(cast(list[str], citations)), tuple(reasons))


def execute_authorized_live_eval(
    *,
    corpus_root: Path,
    authorization: LiveEvalAuthorization,
    trusted_grant: TrustedLiveGrant,
    supplied_grant: str,
    executor: AgnesLivePostExecutor | ScriptedSingleAttemptExecutor,
    now: datetime,
    monotonic_ns: Callable[[], int] = time.monotonic_ns,
) -> LiveEvalRun:
    # Reload from the hash-closed fixture root inside the authority boundary.
    # A caller therefore cannot substitute a different EvalCase under an
    # approved case ID/split hash.
    corpus = load_eval_corpus(corpus_root)
    cases = corpus.load_public_cases(EvalSplit.HELD_OUT).cases
    split_hash = corpus.split_manifest.split_hash
    _validate_external_authority(
        authorization=authorization,
        trusted_grant=trusted_grant,
        supplied_grant=supplied_grant,
        split_hash=split_hash,
        now=now,
    )
    if type(executor) not in {AgnesLivePostExecutor, ScriptedSingleAttemptExecutor}:
        raise LiveEvalAuthorizationError("live executor is not package-owned")
    if type(executor) is AgnesLivePostExecutor and not executor._production_composed:
        raise LiveEvalAuthorizationError(
            "Agnes live executor is not Keychain/stdlib production-composed"
        )
    by_id = {case.case_id: case for case in cases}
    if set(authorization.case_ids) - set(by_id):
        raise LiveEvalAuthorizationError("authorization references unknown cases")
    selected = tuple(by_id[case_id] for case_id in authorization.case_ids)
    if any(
        case.split is not EvalSplit.HELD_OUT or case.family is not EvalFamily.ROUTE
        for case in selected
    ):
        raise LiveEvalAuthorizationError("live eval accepts held-out synthetic route cases only")

    records: list[LiveAuditRecord] = []
    parser = StrictLiveDecisionParser()
    execution_kind = "SCRIPTED_TEST_ONLY"
    if type(executor) is AgnesLivePostExecutor:
        execution_kind = (
            _PRODUCTION_EXECUTION_KIND
            if executor._production_composed
            else "AGNES_STDLIB_NOT_KEYCHAIN_ATTESTED"
        )
    blind: dict[str, BlindLiveRouteContract] = {}
    for case in selected:
        try:
            blind[case.case_id] = build_blind_live_route_contract(case)
        except (KeyError, TypeError, ValueError):
            records.append(_pre_network_reject(case))
    if len(blind) != authorization.request_cap:
        raise LiveEvalAuthorizationError(
            "authorized POST cap does not equal production-valid case count"
        )

    post_count = 0
    for case in selected:
        contract = blind.get(case.case_id)
        if contract is None:
            continue
        post_count += 1
        if post_count > authorization.request_cap:
            raise LiveEvalAuthorizationError("live POST cap reached before attempt")
        payload_bytes = _live_payload_bytes(authorization, contract)
        if len(payload_bytes) > authorization.request_byte_cap:
            raise LiveEvalAuthorizationError("authorized request payload exceeds byte cap")
        payload_hash = hashlib.sha256(payload_bytes).hexdigest()
        started = monotonic_ns()
        response: bytes | None = None
        parsed: LiveParsedResult | None = None
        deadline_ms = min(
            authorization.timeout_ms,
            NORMAL_DEADLINE_MS if case.mode is EvalMode.NORMAL else EMERGENCY_DEADLINE_MS,
        )
        deadline = UtcTimestamp(now.astimezone(UTC) + timedelta(milliseconds=deadline_ms))
        try:
            response = executor.post_once(contract, payload_bytes, deadline)
            if _last_request_hash(executor, post_count) != _provider_request_hash(
                authorization, contract
            ):
                raise ValueError("provider request hash diverged from approved live plan")
            latency_ms = max(0, (monotonic_ns() - started) // 1_000_000)
            if (
                type(response) is not bytes
                or len(response) > authorization.response_byte_cap
                or latency_ms > deadline_ms
            ):
                raise ValueError("provider response violates byte or deadline contract")
            parsed = parser.parse(contract, response)
        except Exception as error:
            latency_ms = max(0, (monotonic_ns() - started) // 1_000_000)
            records.append(
                _audit_record(
                    post_count,
                    contract,
                    payload_hash,
                    _last_request_hash(executor, post_count),
                    "FAILED",
                    _safe_live_error_code(error),
                    _last_response_hash(executor, post_count),
                    _response_hash_kind(executor, post_count),
                    latency_ms,
                    None,
                )
            )
            raise LiveEvalExecutionError(
                "provider eval stopped on first error",
                _live_run(records, execution_kind, len(selected), executor.token_usage),
            ) from None
        records.append(
            _audit_record(
                post_count,
                contract,
                payload_hash,
                _last_request_hash(executor, post_count),
                "STRICTLY_PARSED",
                None,
                _last_response_hash(executor, post_count),
                _response_hash_kind(executor, post_count),
                latency_ms,
                parsed,
            )
        )
    if len(executor.attempts) != post_count:
        raise LiveEvalExecutionError(
            "POST attempt accounting diverged",
            _live_run(records, execution_kind, len(selected), executor.token_usage),
        )
    return _live_run(records, execution_kind, len(selected), executor.token_usage)


def recompute_live_metrics(
    run: LiveEvalRun,
    *,
    authorization: LiveEvalAuthorization,
    cases: tuple[EvalCase, ...],
    answers: Mapping[str, ExpectedAnswer],
) -> MappingProxyType[str, JsonValue]:
    by_id = {case.case_id: case for case in cases}
    selected = tuple(by_id[case_id] for case_id in authorization.case_ids)
    if any(case_id not in answers for case_id in authorization.case_ids):
        raise ValueError("live metric oracle does not close over authorized cases")
    records = {record.case_id: record for record in run.records}
    valid = tuple(case for case in selected if answers[case.case_id].validity is CaseValidity.VALID)
    invalid = tuple(
        case for case in selected if answers[case.case_id].validity is not CaseValidity.VALID
    )
    valid_primary = sum(
        (record := records.get(case.case_id)) is not None
        and record.decision is ExpectedDecision.ACCEPT
        and record.schema_ok
        and record.citation_ok
        and record.reasoning_ok
        for case in valid
    )
    invalid_recall = sum(
        (record := records.get(case.case_id)) is not None
        and record.decision in {ExpectedDecision.REJECT, ExpectedDecision.ABSTAIN}
        for case in invalid
    )
    latency = {
        mode.value: _live_latency(
            [
                record.latency_ms
                for record in run.records
                if record.mode is mode and record.attempt_ordinal is not None
            ],
            sum(case.mode is mode for case in valid),
            NORMAL_DEADLINE_MS if mode is EvalMode.NORMAL else EMERGENCY_DEADLINE_MS,
        )
        for mode in EvalMode
    }
    is_real = run.execution_kind == _PRODUCTION_EXECUTION_KIND
    return MappingProxyType(
        {
            "execution_kind": run.execution_kind,
            "real_provider_evidence": is_real,
            "authorized_denominator": len(selected),
            "request_count": run.request_count,
            "pre_network_reject_count": run.pre_network_reject_count,
            "fallback_count": run.fallback_count,
            "token_usage": {
                "prompt_tokens": run.prompt_tokens,
                "completion_tokens": run.completion_tokens,
                "total_tokens": run.total_tokens,
                "scope": "STRICT_PROVIDER_RESPONSES_ONLY",
            },
            "not_executed_after_fail_fast": authorization.request_cap - run.request_count,
            "valid_primary": _threshold(valid_primary, len(valid), 0.98, enabled=is_real),
            "valid_after_at_most_one_fallback": _threshold(
                valid_primary, len(valid), 0.99, enabled=is_real
            ),
            "invalid_ambiguous_recall": _threshold(
                invalid_recall, len(invalid), 1.0, enabled=is_real
            ),
            "errors": sum(record.outcome == "FAILED" for record in run.records)
            + authorization.request_cap
            - run.request_count,
            "latency": cast(JsonValue, latency),
            "audit_root_hash": run.audit_root_hash,
        }
    )


def run_production_live_eval(
    *,
    repo_root: Path,
    corpus_root: Path,
    authorization: LiveEvalAuthorization,
    trusted_config_hash: str,
    trusted_grant_sha256: str,
    supplied_grant: str,
    evidence_filename: str,
    now: datetime | None = None,
) -> tuple[LiveEvalRun, SanitizedLiveEvidence, Path]:
    """Execute the one production route with all authority checks before Keychain/POST."""

    selected_now = datetime.now(UTC) if now is None else now
    plan = live_plan_summary(
        authorization,
        trusted_config_hash,
        corpus_root=corpus_root,
    )
    corpus = load_eval_corpus(corpus_root)
    held_out = corpus.load_public_cases(EvalSplit.HELD_OUT).cases
    frozen_route_ids = tuple(case.case_id for case in held_out if case.family is EvalFamily.ROUTE)
    if (
        authorization.case_ids != frozen_route_ids
        or len(frozen_route_ids) != _PRODUCTION_AUTHORIZED_CASES
        or authorization.request_cap != _PRODUCTION_POSTS
        or plan["pre_network_reject_count"] != _PRODUCTION_PRE_NETWORK_REJECTS
    ):
        raise LiveEvalAuthorizationError(
            "production live evidence requires the exact frozen 390-case/260-POST batch"
        )
    trusted_grant = TrustedLiveGrant(trusted_config_hash, trusted_grant_sha256)
    _validate_external_authority(
        authorization=authorization,
        trusted_grant=trusted_grant,
        supplied_grant=supplied_grant,
        split_hash=corpus.split_manifest.split_hash,
        now=selected_now,
    )
    if supplied_grant != plan["plan_hash"]:
        raise LiveEvalAuthorizationError(
            "production live grant is not the exact canonical approved plan hash"
        )
    if authorization.cost_cap_usd_cents != NO_FEE_CAP_APPROVED_SENTINEL:
        raise LiveEvalAuthorizationError(
            "Agnes has no verifiable unit price; production requires approved no-fee-cap sentinel"
        )
    evidence_path = _prepare_local_evidence_path(repo_root, evidence_filename)

    executor = AgnesLivePostExecutor.from_macos_keychain(clock=_utc_now)
    try:
        run = execute_authorized_live_eval(
            corpus_root=corpus_root,
            authorization=authorization,
            trusted_grant=trusted_grant,
            supplied_grant=supplied_grant,
            executor=executor,
            now=selected_now,
        )
    except LiveEvalExecutionError as error:
        evidence = build_sanitized_live_evidence(
            run=error.partial_run,
            authorization=authorization,
            corpus_root=corpus_root,
            plan=plan,
            completed=False,
            grant_sha256=trusted_grant_sha256,
        )
        write_local_live_evidence(evidence_path, evidence)
        raise
    evidence = build_sanitized_live_evidence(
        run=run,
        authorization=authorization,
        corpus_root=corpus_root,
        plan=plan,
        completed=True,
        grant_sha256=trusted_grant_sha256,
    )
    write_local_live_evidence(evidence_path, evidence)
    return run, evidence, evidence_path


def build_sanitized_live_evidence(
    *,
    run: LiveEvalRun,
    authorization: LiveEvalAuthorization,
    corpus_root: Path,
    plan: Mapping[str, JsonValue],
    completed: bool,
    grant_sha256: str,
) -> SanitizedLiveEvidence:
    if authorization.cost_cap_usd_cents != NO_FEE_CAP_APPROVED_SENTINEL:
        raise LiveEvalEvidenceError("production evidence requires approved no-fee-cap sentinel")
    if not _is_hash(grant_sha256):
        raise LiveEvalEvidenceError("production evidence grant hash is invalid")
    corpus = load_eval_corpus(corpus_root)
    public_cases = corpus.load_public_cases(EvalSplit.HELD_OUT).cases
    _, answers = corpus.load_final_evaluation()
    metrics = recompute_live_metrics(
        run,
        authorization=authorization,
        cases=public_cases,
        answers=answers,
    )
    records: list[JsonValue] = [
        {
            "attempt_ordinal": record.attempt_ordinal,
            "case_id": record.case_id,
            "mode": record.mode.value,
            "payload_hash": record.payload_hash,
            "provider_request_hash": record.provider_request_hash,
            "outcome": record.outcome,
            "error_code": record.error_code,
            "response_hash": record.response_hash,
            "response_hash_kind": record.response_hash_kind,
            "latency_ms": record.latency_ms,
            "decision": None if record.decision is None else record.decision.value,
            "schema_ok": record.schema_ok,
            "citation_ok": record.citation_ok,
            "reasoning_ok": record.reasoning_ok,
            "audit_hash": record.audit_hash,
        }
        for record in run.records
    ]
    wire: dict[str, JsonValue] = {
        "schema_version": "seven-lens.p3f.live-evidence.v1",
        "execution_status": "COMPLETED" if completed else "FAILED_STOPPED",
        "execution_kind": run.execution_kind,
        "split_hash": authorization.split_hash,
        "config_hash": authorization.config_hash,
        "grant_sha256": grant_sha256,
        "payload_hash_root": cast(str, plan["payload_hash_root"]),
        "provider_request_hash_root": cast(str, plan["provider_request_hash_root"]),
        "plan_hash": cast(str, plan["plan_hash"]),
        "audit_root_hash": run.audit_root_hash,
        "cost_policy": "APPROVED_NO_FEE_CAP_NO_VERIFIABLE_PROVIDER_UNIT_PRICE",
        "authorized_case_count": run.authorized_case_count,
        "request_cap": authorization.request_cap,
        "request_count": run.request_count,
        "pre_network_reject_count": run.pre_network_reject_count,
        "fallback_count": run.fallback_count,
        "token_usage": {
            "prompt_tokens": run.prompt_tokens,
            "completion_tokens": run.completion_tokens,
            "total_tokens": run.total_tokens,
            "scope": "STRICT_PROVIDER_RESPONSES_ONLY",
        },
        "metrics": cast(JsonValue, dict(metrics)),
        "records": records,
    }
    wire["evidence_hash"] = content_hash(cast(JsonValue, wire))
    return SanitizedLiveEvidence(MappingProxyType(wire))


def write_local_live_evidence(path: Path, evidence: SanitizedLiveEvidence) -> None:
    if type(evidence) is not SanitizedLiveEvidence:
        raise ValueError("live evidence object is invalid")
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    directory_flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        directory = os.open(path.parent, directory_flags)
    except OSError as error:
        raise LiveEvalEvidenceError("local live evidence file could not be created") from error
    try:
        descriptor = os.open(path.name, flags, 0o600, dir_fd=directory)
    except OSError as error:
        os.close(directory)
        raise LiveEvalEvidenceError("local live evidence file could not be created") from error
    try:
        material = evidence.to_bytes()
        written = 0
        while written < len(material):
            written += os.write(descriptor, material[written:])
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
        os.close(directory)


def _prepare_local_evidence_path(repo_root: Path, filename: str) -> Path:
    if (
        type(filename) is not str
        or not filename.endswith(".json")
        or not 6 <= len(filename) <= 128
        or any(
            character not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._-"
            for character in filename
        )
    ):
        raise LiveEvalAuthorizationError("local live evidence filename is invalid")
    root = repo_root.resolve(strict=True)
    ignore_path = root / ".gitignore"
    metadata = ignore_path.lstat()
    if (
        stat.S_ISLNK(metadata.st_mode)
        or not stat.S_ISREG(metadata.st_mode)
        or "/.seven-lens-local/" not in ignore_path.read_text(encoding="utf-8").splitlines()
    ):
        raise LiveEvalAuthorizationError("local live evidence ignore policy is missing")
    local_root = root / ".seven-lens-local"
    evidence_root = local_root / "p3f-live-evidence"
    for directory in (local_root, evidence_root):
        try:
            os.mkdir(directory, 0o700)
        except FileExistsError:
            directory_metadata = directory.lstat()
            if (
                stat.S_ISLNK(directory_metadata.st_mode)
                or not stat.S_ISDIR(directory_metadata.st_mode)
                or stat.S_IMODE(directory_metadata.st_mode) & 0o077
            ):
                raise LiveEvalAuthorizationError(
                    "local live evidence directory is not private"
                ) from None
    output = evidence_root / filename
    if output.exists() or output.is_symlink():
        raise LiveEvalAuthorizationError("local live evidence output already exists")
    return output


def _utc_now() -> UtcTimestamp:
    return UtcTimestamp(datetime.now(UTC))


def live_plan_summary(
    authorization: LiveEvalAuthorization,
    trusted_config_hash: str,
    *,
    corpus_root: Path,
) -> MappingProxyType[str, JsonValue]:
    corpus = load_eval_corpus(corpus_root)
    if (
        trusted_config_hash != authorization.config_hash
        or corpus.split_manifest.split_hash != authorization.split_hash
    ):
        raise LiveEvalAuthorizationError(
            "live plan does not match the trusted config or frozen split"
        )
    by_id = {case.case_id: case for case in corpus.load_public_cases(EvalSplit.HELD_OUT).cases}
    try:
        selected = tuple(by_id[case_id] for case_id in authorization.case_ids)
    except KeyError:
        raise LiveEvalAuthorizationError("live plan references unknown held-out cases") from None
    if any(case.family is not EvalFamily.ROUTE for case in selected):
        raise LiveEvalAuthorizationError("live plan accepts synthetic route cases only")
    contracts: list[BlindLiveRouteContract] = []
    rejected: list[str] = []
    for case in selected:
        try:
            contracts.append(build_blind_live_route_contract(case))
        except (KeyError, TypeError, ValueError):
            rejected.append(case.case_id)
    if len(contracts) != authorization.request_cap:
        raise LiveEvalAuthorizationError(
            "live plan POST cap does not equal production-valid case count"
        )
    payload_hashes: dict[str, JsonValue] = {
        contract.case_id: hashlib.sha256(_live_payload_bytes(authorization, contract)).hexdigest()
        for contract in contracts
    }
    request_hashes: dict[str, JsonValue] = {
        contract.case_id: _provider_request_hash(authorization, contract) for contract in contracts
    }
    wire: dict[str, JsonValue] = {
        "authorization_id": authorization.authorization_id,
        "split_hash": authorization.split_hash,
        "case_count": len(authorization.case_ids),
        "request_cap": authorization.request_cap,
        "pre_network_reject_count": len(rejected),
        "pre_network_reject_case_ids": cast(JsonValue, rejected),
        "payload_hashes": cast(JsonValue, payload_hashes),
        "payload_hash_root": content_hash(cast(JsonValue, payload_hashes)),
        "provider_request_hashes": cast(JsonValue, request_hashes),
        "provider_request_hash_root": content_hash(cast(JsonValue, request_hashes)),
        "cost_cap_usd_cents": authorization.cost_cap_usd_cents,
        "privacy_class": authorization.privacy_class,
        "provider_policy_id": authorization.provider_policy_id,
        "parser_id": authorization.parser_id,
        "prompt_template_hash": authorization.prompt_template_hash,
        "timeout_ms": authorization.timeout_ms,
        "automatic_retries": 0,
        "fallback_attempts": 0,
        "stop_on_first_error": True,
        "config_hash": authorization.config_hash,
        "trusted_config_match": True,
        "network_started": False,
    }
    wire["plan_hash"] = content_hash(cast(JsonValue, wire))
    return MappingProxyType(wire)


def _live_payload_bytes(
    authorization: LiveEvalAuthorization, contract: BlindLiveRouteContract
) -> bytes:
    payload: JsonValue = {
        "schema_version": "seven-lens.p3f.live-route.v2",
        "authorization_id": authorization.authorization_id,
        "case_id": contract.case_id,
        "route": contract.route,
        "mode": contract.mode.value,
        "required_cited_fact": contract.required_cited_fact,
        "production_contract": dict(contract.production_contract),
        "contract_hash": contract.contract_hash,
        "prompt_template_hash": authorization.prompt_template_hash,
    }
    return canonical_bytes(payload)


def _live_model_request(
    contract: BlindLiveRouteContract, payload: bytes, deadline: UtcTimestamp
) -> JsonModelRequest:
    call_id = RunId(UUID(bytes=hashlib.sha256(contract.case_id.encode()).digest()[:16], version=4))
    return JsonModelRequest(
        call_id,
        (
            JsonModelMessage(JsonMessageRole.SYSTEM, _LIVE_SYSTEM_PROMPT),
            JsonModelMessage(JsonMessageRole.DEVELOPER, _LIVE_DEVELOPER_PROMPT),
            JsonModelMessage(JsonMessageRole.USER, payload.decode("utf-8")),
        ),
        deadline,
        2_048,
    )


def _provider_request_hash(
    authorization: LiveEvalAuthorization, contract: BlindLiveRouteContract
) -> str:
    payload = _live_payload_bytes(authorization, contract)
    request = _live_model_request(
        contract,
        payload,
        UtcTimestamp(datetime(2030, 1, 1, tzinfo=UTC)),
    )
    body = build_agnes_request_body(agnes_25_flash_config(), request)
    return hashlib.sha256(body).hexdigest()


def _audit_record(
    ordinal: int,
    contract: BlindLiveRouteContract,
    payload_hash: str,
    provider_request_hash: str | None,
    outcome: str,
    error_code: str | None,
    response_hash: str | None,
    response_hash_kind: str | None,
    latency_ms: int,
    parsed: LiveParsedResult | None,
) -> LiveAuditRecord:
    schema_ok = parsed is not None
    citation_ok = parsed is not None and parsed.citations == (contract.required_cited_fact,)
    reasoning_ok = parsed is not None and bool(parsed.reason_codes)
    material: JsonValue = {
        "attempt_ordinal": ordinal,
        "case_id": contract.case_id,
        "mode": contract.mode.value,
        "payload_hash": payload_hash,
        "provider_request_hash": provider_request_hash,
        "outcome": outcome,
        "error_code": error_code,
        "response_hash": response_hash,
        "response_hash_kind": response_hash_kind,
        "latency_ms": latency_ms,
        "decision": None if parsed is None else parsed.decision.value,
        "schema_ok": schema_ok,
        "citation_ok": citation_ok,
        "reasoning_ok": reasoning_ok,
    }
    return LiveAuditRecord(
        ordinal,
        contract.case_id,
        contract.mode,
        payload_hash,
        provider_request_hash,
        outcome,
        error_code,
        response_hash,
        response_hash_kind,
        latency_ms,
        None if parsed is None else parsed.decision,
        schema_ok,
        citation_ok,
        reasoning_ok,
        content_hash(material),
    )


def _live_run(
    records: list[LiveAuditRecord],
    execution_kind: str,
    authorized_case_count: int,
    token_usage: list[tuple[int, int, int]],
) -> LiveEvalRun:
    request_count = sum(record.attempt_ordinal is not None for record in records)
    pre_network_reject_count = sum(record.outcome == "PRE_NETWORK_REJECTED" for record in records)
    return LiveEvalRun(
        execution_kind,
        authorized_case_count,
        request_count,
        pre_network_reject_count,
        0,
        sum(item[0] for item in token_usage),
        sum(item[1] for item in token_usage),
        sum(item[2] for item in token_usage),
        tuple(records),
        content_hash(cast(JsonValue, [record.audit_hash for record in records])),
    )


def _validate_external_authority(
    *,
    authorization: LiveEvalAuthorization,
    trusted_grant: TrustedLiveGrant,
    supplied_grant: str,
    split_hash: str,
    now: datetime,
) -> None:
    grant_hash = hashlib.sha256(supplied_grant.encode("utf-8")).hexdigest()
    if (
        not supplied_grant
        or trusted_grant.authorization_config_hash != authorization.config_hash
        or trusted_grant.grant_sha256 != grant_hash
        or authorization.split_hash != split_hash
        or now.tzinfo is None
        or now.astimezone(UTC) >= authorization.expires_at
    ):
        raise LiveEvalAuthorizationError(
            "trusted external live grant is missing, stale, or foreign"
        )


def _pre_network_reject(case: EvalCase) -> LiveAuditRecord:
    payload_hash = content_hash(cast(JsonValue, dict(case.payload)))
    material: JsonValue = {
        "attempt_ordinal": None,
        "case_id": case.case_id,
        "mode": case.mode.value,
        "payload_hash": payload_hash,
        "provider_request_hash": None,
        "outcome": "PRE_NETWORK_REJECTED",
        "error_code": "PRE_NETWORK_CONTRACT_REJECTED",
        "response_hash": None,
        "response_hash_kind": None,
        "latency_ms": 0,
        "decision": ExpectedDecision.ABSTAIN.value,
        "schema_ok": True,
        "citation_ok": True,
        "reasoning_ok": True,
    }
    return LiveAuditRecord(
        None,
        case.case_id,
        case.mode,
        payload_hash,
        None,
        "PRE_NETWORK_REJECTED",
        "PRE_NETWORK_CONTRACT_REJECTED",
        None,
        None,
        0,
        ExpectedDecision.ABSTAIN,
        True,
        True,
        True,
        content_hash(material),
    )


def _last_response_hash(
    executor: AgnesLivePostExecutor | ScriptedSingleAttemptExecutor, post_count: int
) -> str | None:
    if len(executor.response_hashes) != post_count:
        return None
    return executor.response_hashes[-1]


def _last_request_hash(
    executor: AgnesLivePostExecutor | ScriptedSingleAttemptExecutor, post_count: int
) -> str | None:
    if len(executor.request_hashes) != post_count:
        return None
    return executor.request_hashes[-1]


def _response_hash_kind(
    executor: AgnesLivePostExecutor | ScriptedSingleAttemptExecutor, post_count: int
) -> str | None:
    if len(executor.response_hashes) != post_count:
        return None
    return (
        "AGNES_RAW_RESPONSE_BODY_SHA256"
        if type(executor) is AgnesLivePostExecutor
        else "SCRIPTED_RESPONSE_BYTES_SHA256"
    )


def _safe_live_error_code(error: Exception) -> str:
    if isinstance(error, ModelTransportError):
        return error.code.value
    if isinstance(error, ValueError):
        return "RESPONSE_CONTRACT"
    return "EXECUTION"


def build_blind_live_route_contract(case: EvalCase) -> BlindLiveRouteContract:
    payload = dict(case.payload)
    if set(payload) != {"expected_round_number", "claim", "fact_variant"}:
        raise ValueError("live route case shape is invalid")
    claim = payload["claim"]
    if type(claim) is not dict or set(claim) != _ROUTE_CLAIM_KEYS:
        raise ValueError("live route claim shape is invalid")
    expected_round = payload["expected_round_number"]
    fact = payload["fact_variant"]
    if type(expected_round) is not int or type(fact) is not str:
        raise ValueError("live route case identity is invalid")
    strings = (
        claim["model"],
        claim["prompt_template_hash"],
        claim["citation_text"],
    )
    if (
        type(claim["round_number"]) is not int
        or type(claim["route_ordinal"]) is not int
        or any(type(item) is not str for item in strings)
    ):
        raise ValueError("live route claim values are invalid")
    ordinal = int(hashlib.sha256(case.case_id.encode()).hexdigest()[:8], 16)
    accepted, _ = probe_route_contract(
        stage=cast(str, case.stage),
        role=cast(str, case.role),
        expected_round_number=expected_round,
        actual_round_number=claim["round_number"],
        route_ordinal=claim["route_ordinal"],
        model=cast(str, claim["model"]),
        prompt_template_hash=cast(str, claim["prompt_template_hash"]),
        citation_text=cast(str, claim["citation_text"]),
        ordinal=ordinal,
        fact_variant=fact,
        claim_material=claim,
    )
    if not accepted:
        raise ValueError("production route contract rejected before network")
    production_contract: dict[str, JsonValue] = {
        "stage": cast(str, case.stage),
        "role": cast(str, case.role),
        **claim,
    }
    contract_hash = content_hash(cast(JsonValue, production_contract))
    return BlindLiveRouteContract(
        case.case_id,
        f"{case.stage}/{case.role}",
        case.mode,
        fact,
        MappingProxyType(production_contract),
        contract_hash,
    )


def _live_latency(values: list[int], expected: int, deadline_ms: int) -> dict[str, JsonValue]:
    ordered = sorted(values)
    timeouts = sum(value > deadline_ms for value in ordered) + expected - len(ordered)
    return {
        "denominator": expected,
        "observed": len(ordered),
        "p50_ms": _percentile(ordered, 0.50),
        "p95_ms": _percentile(ordered, 0.95),
        "max_ms": max(ordered, default=0),
        "timeout_count": timeouts,
        "deadline_ms": deadline_ms,
        "passed": expected > 0 and timeouts == 0,
    }


def _threshold(
    numerator: int, denominator: int, threshold: float, *, enabled: bool
) -> dict[str, JsonValue]:
    return {
        "numerator": numerator,
        "denominator": denominator,
        "threshold": threshold,
        "status": "REAL_PROVIDER" if enabled else "SCRIPTED_NOT_REAL_EVIDENCE",
        "passed": enabled and denominator > 0 and numerator / denominator >= threshold,
    }


def _percentile(values: list[int], quantile: float) -> int:
    if not values:
        return 0
    return values[max(0, math.ceil(len(values) * quantile) - 1)]


def _reject_duplicate_pairs(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def _valid_sanitized_evidence_shape(value: dict[str, object]) -> bool:
    if (
        value["execution_status"] not in {"COMPLETED", "FAILED_STOPPED"}
        or value["execution_kind"]
        not in {
            _PRODUCTION_EXECUTION_KIND,
            "SCRIPTED_TEST_ONLY",
            "AGNES_STDLIB_NOT_KEYCHAIN_ATTESTED",
        }
        or value["cost_policy"] != "APPROVED_NO_FEE_CAP_NO_VERIFIABLE_PROVIDER_UNIT_PRICE"
    ):
        return False
    for key in (
        "authorized_case_count",
        "request_cap",
        "request_count",
        "pre_network_reject_count",
        "fallback_count",
    ):
        if type(value[key]) is not int or cast(int, value[key]) < 0:
            return False
    token_usage = value["token_usage"]
    if type(token_usage) is not dict or set(token_usage) != {
        "prompt_tokens",
        "completion_tokens",
        "total_tokens",
        "scope",
    }:
        return False
    tokens = cast(dict[str, object], token_usage)
    if tokens["scope"] != "STRICT_PROVIDER_RESPONSES_ONLY" or any(
        type(tokens[key]) is not int or cast(int, tokens[key]) < 0
        for key in ("prompt_tokens", "completion_tokens", "total_tokens")
    ):
        return False
    if tokens["total_tokens"] != cast(int, tokens["prompt_tokens"]) + cast(
        int, tokens["completion_tokens"]
    ):
        return False
    records = value["records"]
    record_keys = {
        "attempt_ordinal",
        "case_id",
        "mode",
        "payload_hash",
        "provider_request_hash",
        "outcome",
        "error_code",
        "response_hash",
        "response_hash_kind",
        "latency_ms",
        "decision",
        "schema_ok",
        "citation_ok",
        "reasoning_ok",
        "audit_hash",
    }
    if type(records) is not list:
        return False
    for raw_record in records:
        if type(raw_record) is not dict or set(raw_record) != record_keys:
            return False
        record = cast(dict[str, object], raw_record)
        if (
            (record["attempt_ordinal"] is not None and type(record["attempt_ordinal"]) is not int)
            or type(record["case_id"]) is not str
            or not record["case_id"].startswith("route.")
            or record["mode"] not in {mode.value for mode in EvalMode}
            or not _is_hash(record["payload_hash"])
            or (
                record["provider_request_hash"] is not None
                and not _is_hash(record["provider_request_hash"])
            )
            or record["outcome"] not in {"STRICTLY_PARSED", "FAILED", "PRE_NETWORK_REJECTED"}
            or (record["error_code"] is not None and type(record["error_code"]) is not str)
            or (record["response_hash"] is not None and not _is_hash(record["response_hash"]))
            or record["response_hash_kind"]
            not in {
                None,
                "AGNES_RAW_RESPONSE_BODY_SHA256",
                "SCRIPTED_RESPONSE_BYTES_SHA256",
            }
            or type(record["latency_ms"]) is not int
            or record["latency_ms"] < 0
            or (
                record["decision"] is not None
                and record["decision"] not in {decision.value for decision in ExpectedDecision}
            )
            or any(
                type(record[key]) is not bool
                for key in ("schema_ok", "citation_ok", "reasoning_ok")
            )
            or not _is_hash(record["audit_hash"])
        ):
            return False
    metrics = value["metrics"]
    metric_keys = {
        "execution_kind",
        "real_provider_evidence",
        "authorized_denominator",
        "request_count",
        "pre_network_reject_count",
        "fallback_count",
        "token_usage",
        "not_executed_after_fail_fast",
        "valid_primary",
        "valid_after_at_most_one_fallback",
        "invalid_ambiguous_recall",
        "errors",
        "latency",
        "audit_root_hash",
    }
    if type(metrics) is not dict or set(metrics) != metric_keys:
        return False
    metric = cast(dict[str, object], metrics)
    if (
        metric["execution_kind"] != value["execution_kind"]
        or type(metric["real_provider_evidence"]) is not bool
        or metric["token_usage"] != token_usage
        or metric["audit_root_hash"] != value["audit_root_hash"]
        or any(
            type(metric[key]) is not int or cast(int, metric[key]) < 0
            for key in (
                "authorized_denominator",
                "request_count",
                "pre_network_reject_count",
                "fallback_count",
                "not_executed_after_fail_fast",
                "errors",
            )
        )
    ):
        return False
    threshold_keys = {"numerator", "denominator", "threshold", "status", "passed"}
    for key in (
        "valid_primary",
        "valid_after_at_most_one_fallback",
        "invalid_ambiguous_recall",
    ):
        threshold = metric[key]
        if type(threshold) is not dict or set(threshold) != threshold_keys:
            return False
    latency = metric["latency"]
    latency_keys = {
        "denominator",
        "observed",
        "p50_ms",
        "p95_ms",
        "max_ms",
        "timeout_count",
        "deadline_ms",
        "passed",
    }
    return (
        type(latency) is dict
        and set(latency) == {mode.value for mode in EvalMode}
        and all(type(item) is dict and set(item) == latency_keys for item in latency.values())
    )


def _reject_constant(value: str) -> object:
    raise ValueError(f"non-finite JSON constant {value!r} is forbidden")


def _is_hash(value: object) -> bool:
    return (
        type(value) is str
        and len(value) == 64
        and all(character in "0123456789abcdef" for character in value)
    )


_ROUTE_CLAIM_KEYS: Final = {
    "call_id",
    "run_id",
    "input_id",
    "context_id",
    "round_number",
    "provider",
    "model",
    "api_flavor",
    "endpoint_policy_id",
    "route_ordinal",
    "prompt_template_hash",
    "request_envelope_hash",
    "reasoning_requested",
    "citation_text",
}
