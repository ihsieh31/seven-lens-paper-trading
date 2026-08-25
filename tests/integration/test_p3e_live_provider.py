# mypy: ignore-errors
"""Authorized six-case Agnes conformance with durable, payload-free audit evidence."""

from __future__ import annotations

import json
import math
import os
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
from seven_lens.analysis.model_audit import ModelCallOutcome
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
from seven_lens.application.model_invoker import (
    ModelInvocationError,
    _parse_output,
    _validate_output,
)
from seven_lens.application.p3e_composition import build_agnes_provider_stack
from seven_lens.application.ports.analysis import InMemoryAnalysisStateRepository
from seven_lens.domain.value_objects import UtcTimestamp
from seven_lens.infrastructure.agnes_providers import AgnesAnalysisProvider, AgnesProposalProvider
from seven_lens.infrastructure.agnes_transport import (
    AgnesHttpExecutor,
    RawHttpRequest,
    RawHttpResponse,
    StdlibAgnesHttpExecutor,
)
from seven_lens.infrastructure.macos_keychain import MacOSKeychainSecretProvider
from seven_lens.infrastructure.postgres_model_audit import PostgresModelCallAuditRepository
from seven_lens.sources.contracts import build_evidence_packet
from test_p3bc_evidence_and_infrastructure import evidence_packet as fixture_packet
from test_p3d_proposal_contracts import parent_input as fixture_parent
from test_p3d_research_and_proposal_pipeline import make_proposal_pipeline
from test_p3e_agnes_providers import DynamicAnalysisInvoker, DynamicProposalInvoker

pytestmark = pytest.mark.integration

_LIVE_FLAG: Final = "SEVEN_LENS_P3E_LIVE"
_ROTATED_FLAG: Final = "SEVEN_LENS_P3E_KEY_ROTATED"
_LIMIT_FLAG: Final = "SEVEN_LENS_P3E_REQUEST_LIMIT"
_REQUEST_LIMIT: Final = 6
_CASE_IDS: Final = (
    "P3E-LIVE-ANALYST",
    "P3E-LIVE-DEBATE",
    "P3E-LIVE-RESEARCH-MANAGER",
    "P3E-LIVE-TRADER",
    "P3E-LIVE-RISK",
    "P3E-LIVE-PORTFOLIO-MANAGER",
)


class _CapturingAnalysisProvider:
    def __init__(self) -> None:
        self.invoker = DynamicAnalysisInvoker()
        self.provider = AgnesAnalysisProvider(self.invoker)
        self.requests: list[ProviderRequest] = []

    def execute(self, request: ProviderRequest):
        self.requests.append(request)
        return self.provider.execute(request)


class _CapturingProposalProvider:
    def __init__(self) -> None:
        self.invoker = DynamicProposalInvoker()
        self.provider = AgnesProposalProvider(self.invoker)
        self.requests: list[ProposalRequest] = []

    def execute(self, request: ProposalRequest):
        self.requests.append(request)
        return self.provider.execute(request)


class _SixRequestExecutor:
    """Independent network-call counter that rejects any seventh execution."""

    def __init__(self, delegate: AgnesHttpExecutor) -> None:
        self._delegate = delegate
        self.count = 0
        self.last_response_shape: dict[str, object] | None = None
        self.expected_envelope: SanitizedProviderEnvelope | None = None

    def execute(self, request: RawHttpRequest) -> RawHttpResponse:
        if self.count >= _REQUEST_LIMIT:
            raise RuntimeError("authorized Agnes request limit exhausted")
        self.count += 1
        response = self._delegate.execute(request)
        self.last_response_shape = _safe_response_shape(response, self.expected_envelope)
        return response


@dataclass(frozen=True, slots=True)
class _LiveCase:
    case_id: str
    request: ProviderRequest | ProposalRequest


def _safe_response_shape(
    response: RawHttpResponse,
    expected_envelope: SanitizedProviderEnvelope | None = None,
) -> dict[str, object]:
    shape: dict[str, object] = {
        "status": response.status,
        "final_route_exact": (
            response.final_url == "https://apihub.agnes-ai.com/v1/chat/completions"
        ),
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
    for name in ("object", "model"):
        value = payload.get(name)
        if type(value) is str and len(value.encode("utf-8")) <= 128:
            shape[name] = value
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
                    shape["content_starts_json_fence"] = stripped.startswith("```json")
                    candidate = content
                    if (
                        stripped.count("```") == 2
                        and stripped.startswith("```json\n")
                        and stripped.endswith("\n```")
                    ):
                        candidate = stripped[len("```json\n") : -len("\n```")]
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
    for name in ("metadata",):
        value = payload.get(name)
        if type(value) is dict:
            shape[f"{name}_keys"] = sorted(str(key) for key in value)
            shape[f"{name}_types"] = {
                str(key): type(item).__name__ for key, item in sorted(value.items())
            }
    if type(choices) is list and choices and type(choices[0]) is dict:
        provider_fields = choices[0].get("provider_specific_fields")
        if type(provider_fields) is dict:
            shape["provider_specific_keys"] = sorted(str(key) for key in provider_fields)
            shape["provider_specific_types"] = {
                str(key): type(item).__name__ for key, item in sorted(provider_fields.items())
            }
        message = choices[0].get("message")
        if type(message) is dict and "reasoning_content" in message:
            reasoning_content = message["reasoning_content"]
            shape["reasoning_content_type"] = type(reasoning_content).__name__
            if type(reasoning_content) is str:
                shape["reasoning_content_bytes"] = len(reasoning_content.encode("utf-8"))
    if type(usage) is dict:
        for name in ("prompt_tokens_details", "completion_tokens_details"):
            detail = usage.get(name)
            if type(detail) is dict:
                shape[f"{name}_keys"] = sorted(str(key) for key in detail)
                shape[f"{name}_types"] = {
                    str(key): type(item).__name__ for key, item in sorted(detail.items())
                }
    return shape


def _fresh_synthetic_cases(now: UtcTimestamp) -> tuple[_LiveCase, ...]:
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
    versions = ProposalProducerVersions(
        "tradingagents.1",
        "p3e.1",
        "agnes-2.5-flash",
        "agnes.1",
        packet.producer_version,
        "none.1",
    )

    research = _CapturingAnalysisProvider()
    coordinator = ResearchBatchCoordinator(
        AnalysisPipeline(
            research,
            InMemoryAnalysisStateRepository(),
            now=lambda: now.value,
        ),
        versions,
        now=lambda: now.value,
    )
    bundle = coordinator.run(parent, packet)

    proposals = _CapturingProposalProvider()
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


def _nearest_rank(values: list[int], percentile: float) -> int:
    ordered = sorted(values)
    return ordered[max(0, math.ceil(percentile * len(ordered)) - 1)]


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

    rendered = json.dumps(_safe_response_shape(response), sort_keys=True)

    assert marker not in rendered
    assert '"inner_keys": ["summary"]' in rendered
    assert '"content_types": ["application/json"]' in rendered


@pytest.mark.live
def test_authorized_six_case_agnes_conformance(migrated_postgres: str) -> None:
    if os.environ.get(_LIVE_FLAG) != "1":
        pytest.skip("P3-E live provider call is not authorized in this process")
    if os.environ.get(_ROTATED_FLAG) != "1":
        pytest.fail("P3-E live gate requires explicit confirmation of key rotation", pytrace=False)
    if os.environ.get(_LIMIT_FLAG) != str(_REQUEST_LIMIT):
        pytest.fail("P3-E live gate requires the exact six-request limit", pytrace=False)

    started_at = UtcTimestamp(datetime.now(UTC).replace(microsecond=0))
    cases = _fresh_synthetic_cases(started_at)
    executor = _SixRequestExecutor(StdlibAgnesHttpExecutor())
    outputs: list[object] = []

    with psycopg.connect(migrated_postgres) as connection:
        audit = PostgresModelCallAuditRepository(connection)
        stack = build_agnes_provider_stack(
            secret_provider=MacOSKeychainSecretProvider(timeout_seconds=2.0),
            audit=audit,
            executor=executor,
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
                response_shape = (
                    "none"
                    if executor.last_response_shape is None
                    else json.dumps(
                        executor.last_response_shape,
                        separators=(",", ":"),
                        sort_keys=True,
                    )
                )
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
            "endpoint_policy_id, request_envelope_hash, reasoning_requested, "
            "reasoning_effective, input_tokens, output_tokens, latency_ms, "
            "started_at, completed_at, outcome, error_code "
            "FROM public.model_call_audits ORDER BY started_at, call_id"
        ).fetchall()

    if executor.count != _REQUEST_LIMIT or len(outputs) != _REQUEST_LIMIT:
        pytest.fail("P3-E live did not complete exactly six provider requests", pytrace=False)
    if len(rows) != _REQUEST_LIMIT:
        pytest.fail("P3-E live audit row count does not equal six", pytrace=False)
    if any(row[16] != ModelCallOutcome.SUCCESS.value or row[17] != "NONE" for row in rows):
        pytest.fail("P3-E live audit contains a failed attempt", pytrace=False)
    if any(
        row[4:8]
        != (
            "AGNES",
            "agnes-2.5-flash",
            "CHAT_COMPLETIONS",
            "p3e-agnes-2.5-flash-only-v1",
        )
        for row in rows
    ):
        pytest.fail("P3-E live audit route identity drifted", pytrace=False)

    by_envelope_hash = {row[8]: row for row in rows}
    case_records = []
    latencies = []
    for case in cases:
        row = by_envelope_hash.get(case.request.envelope.envelope_hash)
        if row is None:
            pytest.fail(f"{case.case_id} has no exact audit row", pytrace=False)
        latencies.append(row[13])
        case_records.append(
            {
                "case_id": case.case_id,
                "call_id": row[0],
                "stage": row[1],
                "role": row[2],
                "round_number": row[3],
                "request_envelope_hash": row[8],
                "latency_ms": row[13],
                "input_tokens": row[11],
                "output_tokens": row[12],
                "outcome": row[16],
                "error_code": row[17],
            }
        )

    evidence = {
        "schema": "seven-lens.p3e-live-evidence.v1",
        "provider": "AGNES",
        "model": "agnes-2.5-flash",
        "api_flavor": "CHAT_COMPLETIONS",
        "endpoint": "https://apihub.agnes-ai.com/v1/chat/completions",
        "endpoint_policy_id": "p3e-agnes-2.5-flash-only-v1",
        "request_count": executor.count,
        "automatic_retry": False,
        "fallback": None,
        "reasoning_requested": rows[0][9],
        "reasoning_effective": rows[0][10],
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
