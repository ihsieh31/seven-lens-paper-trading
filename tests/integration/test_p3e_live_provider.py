# mypy: ignore-errors
"""Authorized six-case current-route conformance with durable, payload-free evidence.

The harness is route-neutral: every identity, assertion, and evidence field is
derived from the immutable :class:`AnalysisProviderConfig` snapshot.  Offline
tests in this file never touch the Keychain, the network, or PostgreSQL; the
live case is separately flag-guarded and runs against the exact configured
route with a hard six-request cap, zero retries, and zero fallbacks.
"""

from __future__ import annotations

import json
import math
import os
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Final

import psycopg
import pytest

from seven_lens.analysis.contracts import (
    SCHEMA_VERSION,
    ContractMeta,
    build_analysis_input,
    build_portfolio_snapshot,
)
from seven_lens.analysis.model_audit import (
    CanonicalModelCallResult,
    ModelCallOutcome,
    ModelCallResultKind,
)
from seven_lens.analysis.model_envelope import (
    EnvelopeRole,
    EnvelopeStage,
    SanitizedProviderEnvelope,
)
from seven_lens.analysis.pipeline import AnalysisPipeline
from seven_lens.analysis.ports import ProviderRequest
from seven_lens.analysis.prompt_builder import OutputContract
from seven_lens.analysis.proposal_contracts import PortfolioProposal
from seven_lens.analysis.proposal_pipeline import (
    ProposalProducerVersions,
    ResearchBatchCoordinator,
)
from seven_lens.analysis.proposal_ports import ProposalRequest
from seven_lens.application.analysis_provider_composition import (
    analysis_provider_secret_refs,
    build_analysis_provider_stack,
    default_operator_config_root,
)
from seven_lens.application.model_invoker import (
    ModelInvocationError,
    _parse_output,
    _validate_output,
)
from seven_lens.application.ports.analysis import InMemoryAnalysisStateRepository
from seven_lens.application.ports.model_audit import ModelCallAuditError
from seven_lens.application.ports.model_transport import ModelTransportErrorCode
from seven_lens.config.analysis_provider import (
    AnalysisProviderConfig,
    load_analysis_provider_config,
    package_default_analysis_provider_config,
)
from seven_lens.domain.value_objects import UtcTimestamp
from seven_lens.infrastructure.chat_completions_transport import (
    ChatCompletionsHttpExecutor,
    HttpExecutorError,
    HttpExecutorErrorCode,
    RawHttpRequest,
    RawHttpResponse,
    StdlibChatCompletionsHttpExecutor,
)
from seven_lens.infrastructure.macos_keychain import MacOSKeychainSecretProvider
from seven_lens.infrastructure.postgres_model_audit import PostgresModelCallAuditRepository
from seven_lens.security.secret_values import SecretKind, SecretRef, SecretValue
from seven_lens.sources.contracts import build_evidence_packet
from test_p3bc_evidence_and_infrastructure import evidence_packet as fixture_packet
from test_p3d_proposal_contracts import parent_input as fixture_parent
from test_p3d_research_and_proposal_pipeline import make_proposal_pipeline
from test_p3e_agnes_providers import (
    DynamicAnalysisInvoker,
    DynamicProposalInvoker,
)

pytestmark = pytest.mark.integration

_LIVE_FLAG: Final = "SEVEN_LENS_P3E_LIVE"
_ROTATED_FLAG: Final = "SEVEN_LENS_P3E_KEY_ROTATED"
_LIMIT_FLAG: Final = "SEVEN_LENS_P3E_REQUEST_LIMIT"
_REQUEST_LIMIT: Final = 6
_CASE_IDS: Final = (
    "P3E-CURRENT-ANALYST",
    "P3E-CURRENT-DEBATE",
    "P3E-CURRENT-MANAGER",
    "P3E-CURRENT-TRADER",
    "P3E-CURRENT-RISK",
    "P3E-CURRENT-PORTFOLIO",
)


def _configured_route() -> AnalysisProviderConfig:
    """Load once; the package default is used when no operator file exists."""

    try:
        return load_analysis_provider_config(default_operator_config_root())
    except Exception:
        return package_default_analysis_provider_config()


@dataclass(frozen=True, slots=True)
class _LiveCase:
    case_id: str
    request: ProviderRequest | ProposalRequest


def _producer_versions(route: AnalysisProviderConfig) -> ProposalProducerVersions:
    return ProposalProducerVersions(
        "tradingagents.1",
        "p3e.1",
        route.route_model_version,
        route.route_provider_version,
        "fixture.1",
        "none.1",
    )


def _fresh_synthetic_cases(
    now: UtcTimestamp,
    route: AnalysisProviderConfig | None = None,
) -> tuple[_LiveCase, ...]:
    if route is None:
        route = _configured_route()
    deadline = UtcTimestamp(now.value + timedelta(minutes=15))
    base = fixture_parent()
    base_snapshot = base.portfolio_snapshot
    snapshot = build_portfolio_snapshot(
        as_of=now,
        nav=base_snapshot.nav,
        cash=base_snapshot.cash,
        buying_power=base_snapshot.buying_power,
        positions=base_snapshot.positions,
        open_orders=base_snapshot.open_orders,
        same_day_fills=base_snapshot.same_day_fills,
        borrow_statuses=base_snapshot.borrow_statuses,
        remaining_limits=base_snapshot.remaining_limits,
    )
    parent = build_analysis_input(
        meta=ContractMeta(SCHEMA_VERSION, base.meta.run_id, now, base.meta.producer_version),
        input_id=base.input_id,
        as_of=now,
        window=base.window,
        deadline=deadline,
        portfolio_snapshot=snapshot,
        holding_symbols=base.holding_symbols,
        candidate_symbols=base.candidate_symbols,
        focus_symbols=base.focus_symbols,
        evidence_refs=base.evidence_refs,
        data_snapshot_refs=base.data_snapshot_refs,
    )
    old_packet = fixture_packet()
    packet = build_evidence_packet(
        schema_version=old_packet.schema_version,
        packet_id=old_packet.packet_id,
        as_of=now,
        source_records=old_packet.source_records,
        fragments=old_packet.fragments,
        claims=old_packet.claims,
        contradiction_claim_ids=old_packet.contradiction_claim_ids,
        missing_evidence=old_packet.missing_evidence,
        freshness_status=old_packet.freshness_status,
        status=old_packet.status,
        universe_hash=parent.universe_hash,
        portfolio_snapshot_hash=snapshot.content_hash,
        data_snapshot_refs=parent.data_snapshot_refs,
        producer_version=old_packet.producer_version,
    )
    versions = _producer_versions(route)

    research = _CapturingAnalysisProvider(route)
    coordinator = ResearchBatchCoordinator(
        AnalysisPipeline(
            research,
            InMemoryAnalysisStateRepository(),
            route=route,
            now=lambda: now.value,
        ),
        versions,
        now=lambda: now.value,
    )
    bundle = coordinator.run(parent, packet)

    proposals = _CapturingProposalProvider(route)
    proposal_pipeline, _ = make_proposal_pipeline(
        proposals,
        now=lambda: now.value,
        producer_versions=versions,
    )
    proposal_pipeline.run(bundle, parent)

    cases = (
        _LiveCase(
            _CASE_IDS[0],
            next(
                request
                for request in research.requests
                if request.envelope.stage is EnvelopeStage.ANALYST
                and request.envelope.role is EnvelopeRole.TECHNICAL
            ),
        ),
        _LiveCase(
            _CASE_IDS[1],
            next(
                request
                for request in research.requests
                if request.envelope.stage is EnvelopeStage.INVESTMENT_DEBATE
                and request.envelope.role is EnvelopeRole.BULL
                and request.envelope.round_number == 1
            ),
        ),
        _LiveCase(
            _CASE_IDS[2],
            next(
                request
                for request in research.requests
                if request.envelope.stage is EnvelopeStage.RESEARCH_MANAGER
            ),
        ),
        _LiveCase(
            _CASE_IDS[3],
            next(
                request
                for request in research.requests
                if request.envelope.stage is EnvelopeStage.TRADER
            ),
        ),
        _LiveCase(
            _CASE_IDS[4],
            next(
                request
                for request in proposals.requests
                if request.envelope.stage is EnvelopeStage.RISK_DEBATE
                and request.envelope.role is EnvelopeRole.AGGRESSIVE
                and request.envelope.round_number == 1
            ),
        ),
        _LiveCase(
            _CASE_IDS[5],
            next(
                request
                for request in proposals.requests
                if request.envelope.stage is EnvelopeStage.PORTFOLIO_MANAGER
            ),
        ),
    )
    if tuple(case.case_id for case in cases) != _CASE_IDS:
        raise AssertionError("live case identity drift")
    return cases


class _CapturingAnalysisProvider:
    def __init__(self, route: AnalysisProviderConfig) -> None:
        self.invoker = DynamicAnalysisInvoker()
        from seven_lens.infrastructure.analysis_providers import ConfiguredAnalysisProvider

        self.provider = ConfiguredAnalysisProvider(self.invoker, route)
        self.requests: list[ProviderRequest] = []

    def execute(self, request: ProviderRequest):
        self.requests.append(request)
        return self.provider.execute(request)


class _CapturingProposalProvider:
    def __init__(self, route: AnalysisProviderConfig) -> None:
        self.invoker = DynamicProposalInvoker()
        from seven_lens.infrastructure.analysis_providers import ConfiguredProposalProvider

        self.provider = ConfiguredProposalProvider(self.invoker, route)
        self.requests: list[ProposalRequest] = []

    def execute(self, request: ProposalRequest):
        self.requests.append(request)
        return self.provider.execute(request)


def _safe_response_shape(
    response: RawHttpResponse,
    expected_endpoint: str,
    expected_envelope: SanitizedProviderEnvelope | None = None,
) -> dict[str, object]:
    shape: dict[str, object] = {
        "status": response.status,
        "final_route_exact": (response.final_url == expected_endpoint),
        "body_bytes": len(response.body),
        "content_types": [
            value for name, value in response.headers if name.lower() == "content-type"
        ],
    }
    try:
        payload = json.loads(response.body)
    except (UnicodeError, json.JSONDecodeError):
        shape["json"] = "INVALID"
        return shape
    shape["json_type"] = type(payload).__name__
    if type(payload) is not dict:
        return shape
    shape["outer_keys"] = sorted(str(key) for key in payload)
    for name in ("id", "object", "created", "model", "choices", "usage"):
        if name in payload:
            shape[f"{name}_type"] = type(payload[name]).__name__
    for name in (
        "service_tier",
        "system_fingerprint",
        "prompt_logprobs",
        "prompt_token_ids",
        "kv_transfer_params",
    ):
        value = payload.get(name)
        if name in payload:
            entry: dict[str, object] = {"type": type(value).__name__}
            if type(value) is str:
                entry["bytes"] = len(value.encode("utf-8"))
            elif type(value) is list:
                entry["items"] = len(value)
                entry["item_types"] = sorted({type(item).__name__ for item in value[:5]})
            elif isinstance(value, dict):
                entry["keys"] = sorted(str(key) for key in value)[:16]
            shape[f"{name}_shape"] = entry
    for name in ("object", "model", "id"):
        value = payload.get(name)
        if type(value) is str and len(value.encode("utf-8")) <= 128:
            shape[name] = value
    message_probe = None
    if type(choices := payload.get("choices")) is list and choices:
        first_choice = choices[0]
        if type(first_choice) is dict and type(first_choice.get("message")) is dict:
            message_probe = first_choice["message"]
    if type(message_probe) is dict and type(message_probe.get("reasoning")) is str:
        shape["reasoning_bytes"] = len(message_probe["reasoning"].encode("utf-8"))
    choices = payload.get("choices")
    if type(choices) is list:
        shape["choices_count"] = len(choices)
        if choices and type(choices[0]) is dict:
            choice = choices[0]
            shape["choice_keys"] = sorted(str(key) for key in choice)
            shape["finish_reason"] = choice.get("finish_reason")
            message = choice.get("message")
            shape["message_type"] = type(message).__name__
            if type(message) is dict:
                shape["message_keys"] = sorted(str(key) for key in message)
                shape["message_role"] = message.get("role")
                content = message.get("content")
                shape["content_type"] = type(content).__name__
                if type(content) is str:
                    stripped = content.strip()
                    shape["content_bytes"] = len(content.encode("utf-8"))
                    shape["content_has_code_fence"] = "```" in content
                    shape["content_starts_object"] = stripped.startswith("{")
                    shape["content_ends_object"] = stripped.endswith("}")
                    candidate = content
                    try:
                        inner = json.loads(candidate)
                    except json.JSONDecodeError:
                        shape["inner_json"] = "INVALID"
                    else:
                        shape["inner_json_type"] = type(inner).__name__
                        if type(inner) is dict:
                            shape["inner_keys"] = sorted(str(key) for key in inner)
                            if expected_envelope is not None:
                                contract = {
                                    EnvelopeStage.ANALYST: OutputContract.ANALYST_REPORT,
                                    EnvelopeStage.INVESTMENT_DEBATE: (
                                        OutputContract.DEBATE_ARGUMENT
                                    ),
                                    EnvelopeStage.RESEARCH_MANAGER: (
                                        OutputContract.RESEARCH_CONCLUSION
                                    ),
                                    EnvelopeStage.TRADER: OutputContract.TRADER_PLAN,
                                    EnvelopeStage.RISK_DEBATE: OutputContract.RISK_ARGUMENT,
                                    EnvelopeStage.PORTFOLIO_MANAGER: (
                                        OutputContract.PORTFOLIO_PROPOSAL
                                    ),
                                }[expected_envelope.stage]
                                try:
                                    output = _parse_output(candidate, contract)
                                except (KeyError, TypeError, ValueError) as error:
                                    shape["contract_parse_error"] = str(error)
                                else:
                                    shape["contract_parse"] = "OK"
                                    try:
                                        _validate_output(output, expected_envelope)
                                    except (KeyError, TypeError, ValueError) as error:
                                        shape["contract_validate_error"] = str(error)
                                    else:
                                        shape["contract_validate"] = "OK"
                                    if type(output) is PortfolioProposal:
                                        shape["portfolio_request_count"] = len(output.requests)
    usage = payload.get("usage")
    if type(usage) is dict:
        shape["usage_keys"] = sorted(str(key) for key in usage)
        shape["usage_types"] = {
            str(key): type(value).__name__ for key, value in sorted(usage.items())
        }
        prompt_tokens = usage.get("prompt_tokens")
        completion_tokens = usage.get("completion_tokens")
        total_tokens = usage.get("total_tokens")
        if all(type(item) is int for item in (prompt_tokens, completion_tokens, total_tokens)):
            shape["token_arithmetic_ok"] = (
                total_tokens == prompt_tokens + completion_tokens  # type: ignore[operator]
            )
            shape["token_counts"] = {
                "prompt": prompt_tokens,
                "completion": completion_tokens,
                "total": total_tokens,
            }
    if type(choices) is list and choices and type(choices[0]) is dict:
        shape["choice_index"] = choices[0].get("index")
        message_probe = choices[0].get("message")
        if type(message_probe) is dict and "reasoning_content" in message_probe:
            reasoning_content = message_probe["reasoning_content"]
            shape["reasoning_content_type"] = type(reasoning_content).__name__
            if type(reasoning_content) is str:
                shape["reasoning_content_bytes"] = len(reasoning_content.encode("utf-8"))
        for name in ("logprobs", "stop_reason", "token_ids"):
            value = choices[0].get(name)
            entry = {"type": type(value).__name__}
            if type(value) is list:
                entry["items"] = len(value)
            elif type(value) is str:
                entry["bytes"] = len(value.encode("utf-8"))
            shape[f"choice_{name}_shape"] = entry

    def _count_pairs(items: list[tuple[str, object]]) -> dict[str, object]:
        result: dict[str, object] = {}
        for key, value in items:
            result.setdefault(key, value)
        return result

    try:
        raw_pairs = json.loads(response.body.decode("utf-8"), object_pairs_hook=_count_pairs)
        _ = raw_pairs
        shape["json_strict_ok"] = True
    except (UnicodeError, json.JSONDecodeError, ValueError):
        shape["json_strict_ok"] = False
    return shape


def _parser_probe(response: RawHttpResponse) -> list[str]:
    """Which parser check rejects this response? (types/steps only, never values)"""

    from seven_lens.application.ports.model_transport import ModelTransportErrorCode
    from seven_lens.infrastructure.chat_completions_transport import (
        ChatCompletionsModelTransport,
        _strict_json_object,
    )

    steps: list[str] = []
    try:
        payload = _strict_json_object(response.body, ModelTransportErrorCode.PROTOCOL)
        steps.append("strict_outer=OK")
    except Exception as error:
        steps.append(f"strict_outer=FAIL:{type(error).__name__}")
        return steps
    probe_transport = object.__new__(ChatCompletionsModelTransport)
    probe_transport._config = _configured_route()
    try:
        probe_transport._parse_response(payload, response.body)
        steps.append("full_parse=OK")
        return steps
    except Exception as error:
        import traceback as _traceback

        frames = _traceback.extract_tb(error.__traceback__)
        failing_line = frames[-1].lineno if frames else -1
        steps.append(f"full_parse={type(error).__name__}:line={failing_line}")

    def try_variant(label: str, mutate) -> None:
        import copy as _copy

        variant = _copy.deepcopy(payload)
        mutate(variant)
        try:
            probe_transport._parse_response(variant, response.body)
            steps.append(f"passes_without:{label}")
        except Exception:
            pass

    for key in [
        k for k in payload if k not in {"id", "object", "created", "model", "choices", "usage"}
    ]:
        try_variant(f"outer.{key}", lambda v, key=key: v.pop(key))
    choice = payload["choices"][0]
    for key in [k for k in choice if k not in {"index", "message", "finish_reason"}]:
        try_variant(f"choice.{key}", lambda v, key=key: v["choices"][0].pop(key))
    message = choice["message"]
    for key in [k for k in message if k not in {"role", "content"}]:
        try_variant(f"message.{key}", lambda v, key=key: v["choices"][0]["message"].pop(key))
    for key in [
        k
        for k in payload["usage"]
        if k not in {"prompt_tokens", "completion_tokens", "total_tokens"}
    ]:
        try_variant(f"usage.{key}", lambda v, key=key: v["usage"].pop(key))
    try_variant("id.regex_probe", lambda v: v.update({"id": "chatcmpl-diagnostic"}))

    def walk_strings(value: object, path: str, findings: list[str]) -> None:
        if type(value) is str:
            markers = ("api_key", "apikey", "authorization", "bearer", "password", "secret")
            hits = [m for m in markers if m in value.lower()]
            findings.append(f"{path}:bytes={len(value.encode('utf-8'))}:markers={hits}")
        elif type(value) is list:
            for item in value[:3]:
                walk_strings(item, path + "[]", findings)
        elif isinstance(value, dict):
            for key, item in list(value.items())[:8]:
                walk_strings(item, f"{path}.{key}", findings)

    findings: list[str] = []
    for key in (
        "service_tier",
        "system_fingerprint",
        "prompt_logprobs",
        "prompt_token_ids",
        "kv_transfer_params",
    ):
        walk_strings(payload.get(key), f"outer.{key}", findings)
    walk_strings(choice.get("logprobs"), "choice.logprobs", findings)
    walk_strings(choice.get("stop_reason"), "choice.stop_reason", findings)
    walk_strings(choice.get("token_ids"), "choice.token_ids", findings)
    for key in ("annotations", "audio", "function_call", "refusal", "tool_calls"):
        walk_strings(message.get(key), f"message.{key}", findings)
    steps.extend(findings[:12])
    return steps


def _nearest_rank(values: list[int], percentile: float) -> int:
    ordered = sorted(values)
    return ordered[max(0, math.ceil(percentile * len(ordered)) - 1)]


def _route_endpoint(route: AnalysisProviderConfig) -> str:
    return route.full_endpoint


def test_live_case_builder_is_exactly_six_synthetic_deidentified_envelopes() -> None:
    now = UtcTimestamp(datetime(2026, 8, 24, 6, 0, tzinfo=UTC))
    cases = _fresh_synthetic_cases(now)

    assert tuple(case.case_id for case in cases) == _CASE_IDS
    assert len({case.request.envelope.envelope_hash for case in cases}) == _REQUEST_LIMIT
    assert [case.request.envelope.stage for case in cases] == [
        EnvelopeStage.ANALYST,
        EnvelopeStage.INVESTMENT_DEBATE,
        EnvelopeStage.RESEARCH_MANAGER,
        EnvelopeStage.TRADER,
        EnvelopeStage.RISK_DEBATE,
        EnvelopeStage.PORTFOLIO_MANAGER,
    ]
    rendered = "".join(case.request.envelope.canonical_json for case in cases).lower()
    for prohibited in (
        "authorization",
        "api_key",
        "account_id",
        "broker_order_id",
        "client_order_id",
        "open.1",
        "fill.1",
    ):
        assert prohibited not in rendered


def test_live_protocol_diagnostic_exposes_shape_but_never_response_content() -> None:
    marker = "fake-sensitive-response-marker"
    response = RawHttpResponse(
        200,
        (("Content-Type", "application/json"), ("X-Private", marker)),
        json.dumps(
            {
                "id": "response.1",
                "object": "chat.completion",
                "created": 1,
                "model": "agnes-2.5-flash",
                "choices": [
                    {
                        "index": 0,
                        "message": {
                            "role": "assistant",
                            "content": json.dumps({"summary": marker}),
                        },
                        "finish_reason": "stop",
                    }
                ],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
            }
        ).encode(),
        "https://apihub.agnes-ai.com/v1/chat/completions",
    )

    rendered = json.dumps(_safe_response_shape(response, response.final_url), sort_keys=True)

    assert marker not in rendered
    assert '"inner_keys": ["summary"]' in rendered
    assert '"content_types": ["application/json"]' in rendered


class _FakeAuditPort:
    """In-memory audit double that enforces the same claim/persist closure."""

    def __init__(self) -> None:
        from seven_lens.analysis.model_audit import (
            ModelCallClaim,
            StoredModelCallAttempt,
        )

        self.claims: dict[object, ModelCallClaim] = {}
        self.attempts: dict[object, StoredModelCallAttempt] = {}
        self.events: list[str] = []

    def load(self, call_id):

        self.events.append("load")
        return self.attempts.get(call_id)

    def claim(self, claim):
        from seven_lens.analysis.model_audit import ModelCallClaimResult

        self.events.append("claim")
        existing = self.claims.get(claim.call_id)
        if existing is not None and existing != claim:
            raise ModelCallAuditError("fake claim collision")
        self.claims.setdefault(claim.call_id, claim)
        attempt = self.attempts.get(claim.call_id)
        if attempt is not None:
            return ModelCallClaimResult(
                __import__(
                    "seven_lens.analysis.model_audit", fromlist=["ModelCallClaimDecision"]
                ).ModelCallClaimDecision.REPLAY,
                attempt,
            )
        if existing is not None:
            return ModelCallClaimResult(
                __import__(
                    "seven_lens.analysis.model_audit", fromlist=["ModelCallClaimDecision"]
                ).ModelCallClaimDecision.IN_PROGRESS,
                None,
            )
        return ModelCallClaimResult(
            __import__(
                "seven_lens.analysis.model_audit", fromlist=["ModelCallClaimDecision"]
            ).ModelCallClaimDecision.CLAIMED,
            None,
        )

    def persist(self, record, result) -> bool:
        from seven_lens.analysis.model_audit import StoredModelCallAttempt

        self.events.append("persist")
        if record.call_id in self.attempts:
            attempt = self.attempts[record.call_id]
            if attempt.record == record and attempt.result == result:
                return False
            raise ModelCallAuditError("fake audit collision")
        self.attempts[record.call_id] = StoredModelCallAttempt(record, result)
        return True


class _ScriptedExecutor:
    """Network-free executor returning one precomputed response per request."""

    def __init__(self, responses: Iterator[RawHttpResponse | BaseException]) -> None:
        self._responses = iter(responses)
        self.count = 0
        self.last_response_shape: dict[str, object] | None = None
        self.expected_envelope: SanitizedProviderEnvelope | None = None

    def execute(self, request: RawHttpRequest) -> RawHttpResponse:
        if self.count >= _REQUEST_LIMIT:
            raise RuntimeError("authorized request limit exhausted")
        self.count += 1
        outcome = next(self._responses)
        if isinstance(outcome, BaseException):
            raise outcome
        response = outcome
        if self.expected_envelope is not None:
            self.last_response_shape = _safe_response_shape(
                response,
                response.final_url,
                self.expected_envelope,
            )
        return response


def _fake_success_response(
    route: AnalysisProviderConfig,
    envelope: SanitizedProviderEnvelope,
    contract: OutputContract,
) -> RawHttpResponse:
    from fakes.secrets import FakeSecretProvider as _FSP  # noqa: F401

    invoker = DynamicAnalysisInvoker()
    proposal_invoker = DynamicProposalInvoker()
    if contract in {
        OutputContract.RISK_ARGUMENT,
        OutputContract.PORTFOLIO_PROPOSAL,
    }:
        output = proposal_invoker.invoke(envelope, contract)
    else:
        output = invoker.invoke(envelope, contract)
    kind = {
        OutputContract.ANALYST_REPORT: ModelCallResultKind.ANALYST_REPORT,
        OutputContract.DEBATE_ARGUMENT: ModelCallResultKind.DEBATE_ARGUMENT,
        OutputContract.RESEARCH_CONCLUSION: ModelCallResultKind.RESEARCH_CONCLUSION,
        OutputContract.TRADER_PLAN: ModelCallResultKind.TRADER_PLAN,
        OutputContract.RISK_ARGUMENT: ModelCallResultKind.RISK_ARGUMENT,
        OutputContract.PORTFOLIO_PROPOSAL: ModelCallResultKind.PORTFOLIO_PROPOSAL,
    }[contract]

    from seven_lens.analysis.model_audit import ModelCallRole, ModelCallStage, derive_model_call_id

    call_id = derive_model_call_id(
        envelope.input_id,
        envelope.context_id or envelope.input_id,
        ModelCallStage(envelope.stage.value),
        ModelCallRole.PORTFOLIO_MANAGER
        if envelope.role is EnvelopeRole.PORTFOLIO_MANAGER_RETRY
        else ModelCallRole(envelope.role.value),
        0 if envelope.round_number is None else envelope.round_number,
        1,
    )
    canonical = CanonicalModelCallResult.from_contract(call_id, kind, output)
    payload = {
        "id": "chatcmpl-fake-conformance",
        "object": "chat.completion",
        "created": 1_777_000_000,
        "model": route.model_id,
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": canonical.payload.to_json()},
                "finish_reason": "stop",
            }
        ],
        "usage": {"prompt_tokens": 128, "completion_tokens": 64, "total_tokens": 192},
    }
    return RawHttpResponse(
        200,
        (("Content-Type", "application/json; charset=utf-8"),),
        json.dumps(payload, separators=(",", ":")).encode("utf-8"),
        _route_endpoint(route),
    )


def _fake_stack(
    route: AnalysisProviderConfig,
    responses: Iterator[RawHttpResponse | BaseException],
    now: UtcTimestamp | None = None,
):
    from fakes.secrets import FakeSecretProvider

    secret_ref = next(iter(analysis_provider_secret_refs()))
    secrets = FakeSecretProvider({secret_ref: SecretValue.from_bytes(b"fake-generic-key")})
    audit = _FakeAuditPort()
    executor = _ScriptedExecutor(responses)
    clock = (lambda: now) if now is not None else None
    stack = build_analysis_provider_stack(
        secret_provider=secrets,
        audit=audit,
        executor=executor,
        clock=clock,
        config=route,
    )
    return stack, executor, audit, secrets


def test_six_case_fake_conformance_succeeds_through_the_production_stack() -> None:

    route = package_default_analysis_provider_config()
    now = UtcTimestamp(datetime(2026, 8, 24, 6, 0, tzinfo=UTC))
    cases = _fresh_synthetic_cases(now, route)

    responses: list[RawHttpResponse] = []
    for case in cases:
        envelope = case.request.envelope
        contract = {
            EnvelopeStage.ANALYST: OutputContract.ANALYST_REPORT,
            EnvelopeStage.INVESTMENT_DEBATE: OutputContract.DEBATE_ARGUMENT,
            EnvelopeStage.RESEARCH_MANAGER: OutputContract.RESEARCH_CONCLUSION,
            EnvelopeStage.TRADER: OutputContract.TRADER_PLAN,
            EnvelopeStage.RISK_DEBATE: OutputContract.RISK_ARGUMENT,
            EnvelopeStage.PORTFOLIO_MANAGER: OutputContract.PORTFOLIO_PROPOSAL,
        }[envelope.stage]
        responses.append(_fake_success_response(route, envelope, contract))

    stack, executor, audit, secrets = _fake_stack(route, iter(responses), now)
    outputs: list[object] = []
    for index, case in enumerate(cases, start=1):
        before = executor.count
        executor.expected_envelope = case.request.envelope
        if type(case.request) is ProviderRequest:
            outputs.append(stack.analysis_provider.execute(case.request))
        else:
            outputs.append(stack.proposal_provider.execute(case.request))
        assert executor.count == before + 1 == index

    assert executor.count == _REQUEST_LIMIT
    assert len(outputs) == _REQUEST_LIMIT
    assert len(audit.attempts) == _REQUEST_LIMIT
    # The scoped secret resolves exactly once per stack (load-once composition).
    assert secrets.calls == [SecretRef.primary(SecretKind.ANALYSIS_PROVIDER_API_KEY)]
    # audit-before-authority: every persisted success carries the exact route.
    assert all(
        attempt.record.route_config_hash == route.route_config_hash_value
        for attempt in audit.attempts.values()
    )
    assert all(
        attempt.record.outcome is ModelCallOutcome.SUCCESS for attempt in audit.attempts.values()
    )
    assert all(
        attempt.record.route_config_hash == route.route_config_hash_value
        for attempt in audit.attempts.values()
    )


@pytest.mark.parametrize(
    ("label", "outcome"),
    [
        ("AUTH", "status-401"),
        ("RATE_LIMIT", "status-429"),
        (
            "TIMEOUT",
            HttpExecutorError(HttpExecutorErrorCode.READ_TIMEOUT),
        ),
        ("PROTOCOL", "model-mismatch"),
        ("SCHEMA", "schema-drift"),
        ("OVERSIZE", "oversize"),
    ],
)
def test_six_case_failure_injections_fail_closed_with_one_request(
    label: str,
    outcome: object,
) -> None:

    route = package_default_analysis_provider_config()
    now = UtcTimestamp(datetime(2026, 8, 24, 6, 0, tzinfo=UTC))
    case = _fresh_synthetic_cases(now, route)[0]
    envelope = case.request.envelope

    if label == "AUTH":
        outcome = RawHttpResponse(
            401,
            (("Content-Type", "application/json"),),
            b"{}",
            _route_endpoint(route),
        )
    elif label == "RATE_LIMIT":
        outcome = RawHttpResponse(
            429,
            (("Content-Type", "application/json"),),
            b"{}",
            _route_endpoint(route),
        )
    elif label == "PROTOCOL":
        raw: RawHttpResponse | BaseException = _fake_success_response(
            route,
            envelope,
            OutputContract.ANALYST_REPORT,
        )
        payload = json.loads(raw.body)
        payload["model"] = "foreign-model-id"
        outcome = RawHttpResponse(
            200,
            raw.headers,
            json.dumps(payload).encode("utf-8"),
            _route_endpoint(route),
        )
    elif label == "SCHEMA":
        raw = _fake_success_response(route, envelope, OutputContract.ANALYST_REPORT)
        payload = json.loads(raw.body)
        payload["choices"][0]["message"]["content"] = '{"decision": }'
        outcome = RawHttpResponse(
            200,
            raw.headers,
            json.dumps(payload).encode("utf-8"),
            _route_endpoint(route),
        )
    elif label == "OVERSIZE":
        raw = _fake_success_response(route, envelope, OutputContract.ANALYST_REPORT)
        outcome = RawHttpResponse(
            200,
            raw.headers,
            b"x" * (route.response_byte_cap + 1),
            _route_endpoint(route),
        )

    expected_codes = {
        "AUTH": ModelTransportErrorCode.AUTH,
        "RATE_LIMIT": ModelTransportErrorCode.RATE_LIMIT,
        "TIMEOUT": ModelTransportErrorCode.TIMEOUT,
        "PROTOCOL": ModelTransportErrorCode.PROTOCOL,
        "SCHEMA": ModelTransportErrorCode.SCHEMA,
        "OVERSIZE": ModelTransportErrorCode.OVERSIZE,
    }

    stack, executor, audit, _secrets = _fake_stack(route, iter([outcome]), now)
    executor.expected_envelope = envelope
    with pytest.raises(ModelInvocationError) as caught:
        stack.analysis_provider.execute(case.request)
    assert caught.value.code is expected_codes[label]
    assert executor.count == 1
    if label != "AUTH":
        # failures before the network boundary leave no audit attempt at all;
        # post-network failures persist exactly one FAILURE record.
        attempts = list(audit.attempts.values())
        assert all(attempt.record.outcome is ModelCallOutcome.FAILURE for attempt in attempts)
    assert len(audit.attempts) <= 1


@pytest.mark.live
def test_authorized_six_case_current_route_conformance(migrated_postgres: str) -> None:
    if os.environ.get(_LIVE_FLAG) != "1":
        pytest.skip("P3-E live provider call is not authorized in this process")
    if os.environ.get(_ROTATED_FLAG) != "1":
        pytest.fail("P3-E live gate requires explicit confirmation of key rotation", pytrace=False)
    if os.environ.get(_LIMIT_FLAG) != str(_REQUEST_LIMIT):
        pytest.fail("P3-E live gate requires the exact six-request limit", pytrace=False)

    route = _configured_route()
    started_at = UtcTimestamp(datetime.now(UTC).replace(microsecond=0))
    cases = _fresh_synthetic_cases(started_at)
    executor = _SixRequestExecutor(route)
    outputs: list[object] = []

    with psycopg.connect(migrated_postgres) as connection:
        audit = PostgresModelCallAuditRepository(connection)
        stack = build_analysis_provider_stack(
            secret_provider=MacOSKeychainSecretProvider(timeout_seconds=2.0),
            audit=audit,
            executor=executor,
            config=route,
        )
        for index, case in enumerate(cases, start=1):
            request_count_before = executor.count
            executor.expected_envelope = case.request.envelope
            try:
                if type(case.request) is ProviderRequest:
                    output = stack.analysis_provider.execute(case.request)
                else:
                    output = stack.proposal_provider.execute(case.request)
            except ModelInvocationError as failure:
                probe_steps: list[str] = []
                if executor.last_raw_response is not None:
                    probe_steps = _parser_probe(executor.last_raw_response)
                response_shape = (
                    "none"
                    if executor.last_response_shape is None
                    else json.dumps(
                        executor.last_response_shape,
                        separators=(",", ":"),
                        sort_keys=True,
                    )
                )
                if probe_steps:
                    print(f"{case.case_id} parser_probe={json.dumps(probe_steps)}")
                pytest.fail(
                    f"{case.case_id} failed closed with {failure.code.value}; "
                    f"network_requests={executor.count}/{_REQUEST_LIMIT}; "
                    f"response_shape={response_shape}",
                    pytrace=False,
                )
            if executor.count != request_count_before + 1 or executor.count != index:
                pytest.fail("P3-E live request count drifted", pytrace=False)
            outputs.append(output)

        rows = connection.execute(
            "SELECT call_id::text, stage, role, round_number, provider, model, api_flavor, "
            "endpoint_policy_id, route_config_hash, request_envelope_hash, "
            "reasoning_requested, reasoning_effective, input_tokens, output_tokens, "
            "latency_ms, started_at, completed_at, outcome, error_code "
            "FROM public.model_call_audits ORDER BY started_at, call_id"
        ).fetchall()

    if executor.count != _REQUEST_LIMIT or len(outputs) != _REQUEST_LIMIT:
        pytest.fail("P3-E live did not complete exactly six provider requests", pytrace=False)
    if len(rows) != _REQUEST_LIMIT:
        pytest.fail("P3-E live audit row count does not equal six", pytrace=False)
    if any(row[17] != ModelCallOutcome.SUCCESS.value or row[18] != "NONE" for row in rows):
        pytest.fail("P3-E live audit contains a failed attempt", pytrace=False)
    expected_identity = (
        route.route_provider_kind,
        route.model_id,
        route.api_flavor,
        route.route_policy_id,
    )
    if any(row[4:8] != expected_identity for row in rows):
        pytest.fail("P3-E live audit route identity drifted", pytrace=False)
    if any(row[8] != route.route_config_hash_value for row in rows):
        pytest.fail("P3-E live audit route config hash drifted", pytrace=False)

    by_envelope_hash = {row[9]: row for row in rows}
    case_records = []
    latencies = []
    for case in cases:
        row = by_envelope_hash.get(case.request.envelope.envelope_hash)
        if row is None:
            pytest.fail(f"{case.case_id} has no exact audit row", pytrace=False)
        latencies.append(row[14])
        case_records.append(
            {
                "case_id": case.case_id,
                "call_id": row[0],
                "stage": row[1],
                "role": row[2],
                "round_number": row[3],
                "request_envelope_hash": row[9],
                "latency_ms": row[14],
                "input_tokens": row[12],
                "output_tokens": row[13],
                "outcome": row[17],
                "error_code": row[18],
            }
        )

    evidence = {
        "schema": "seven-lens.p3e-live-evidence.v2",
        "provider": route.route_provider_kind,
        "model": route.model_id,
        "api_flavor": route.api_flavor,
        "base_url": route.base_url,
        "full_endpoint": route.full_endpoint,
        "endpoint_policy_id": route.route_policy_id,
        "route_config_hash": route.route_config_hash_value,
        "config_generation": route.generation,
        "config_source": route.config_source.value,
        "request_count": executor.count,
        "automatic_retry": False,
        "fallback": None,
        "reasoning_requested": rows[0][10],
        "reasoning_effective": rows[0][11],
        "started_at": str(started_at),
        "completed_at": str(UtcTimestamp(datetime.now(UTC))),
        "latency_ms": {
            "method": "nearest-rank",
            "p50": _nearest_rank(latencies, 0.50),
            "p95": _nearest_rank(latencies, 0.95),
            "max": max(latencies),
        },
        "cases": case_records,
    }
    print("P3E_LIVE_EVIDENCE=" + json.dumps(evidence, separators=(",", ":"), sort_keys=True))


class _SixRequestExecutor:
    """Independent network-call counter that rejects any seventh execution."""

    def __init__(self, route: AnalysisProviderConfig) -> None:
        self._delegate: ChatCompletionsHttpExecutor = StdlibChatCompletionsHttpExecutor()
        self._route = route
        self.count = 0
        self.last_response_shape: dict[str, object] | None = None
        self.last_raw_response: RawHttpResponse | None = None
        self.expected_envelope: SanitizedProviderEnvelope | None = None

    def execute(self, request: RawHttpRequest) -> RawHttpResponse:
        if self.count >= _REQUEST_LIMIT:
            raise RuntimeError("authorized analysis-provider request limit exhausted")
        self.count += 1
        response = self._delegate.execute(request)
        self.last_raw_response = response
        if self.expected_envelope is not None:
            self.last_response_shape = _safe_response_shape(
                response,
                self._route.full_endpoint,
                self.expected_envelope,
            )
        return response
