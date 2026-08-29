from __future__ import annotations

import hashlib
import json
import shutil
import stat
import sys
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from types import MappingProxyType
from typing import Any, cast

import pytest

import seven_lens.evals.provider_eval as provider_eval_module
from seven_lens.application.ports.model_transport import (
    JsonModelRequest,
    JsonModelResponse,
    ModelTransportError,
    ModelTransportErrorCode,
)
from seven_lens.config.analysis_provider import (
    AnalysisProviderConfig,
    ConfigSource,
    canonical_operator_bytes,
    package_default_analysis_provider_config,
)
from seven_lens.config.provider import agnes_25_flash_config
from seven_lens.domain.json_values import JsonValue
from seven_lens.domain.value_objects import UtcTimestamp
from seven_lens.evals import EvalFamily, EvalSplit, load_eval_corpus
from seven_lens.evals.__main__ import main as eval_cli_main
from seven_lens.evals.corpus import EvalCorpus
from seven_lens.evals.models import EvalCase, ExpectedAnswer, ExpectedDecision, content_hash
from seven_lens.evals.provider_eval import (
    _LIVE_DEVELOPER_PROMPT,
    _LIVE_SYSTEM_PROMPT,
    LIVE_PROMPT_TEMPLATE_HASH,
    MAX_RETRIES_PER_CASE,
    NO_FEE_CAP_APPROVED_SENTINEL,
    AgnesLivePostExecutor,
    AnalysisProviderLivePostExecutor,
    LiveEvalAuthorization,
    LiveEvalAuthorizationError,
    LiveEvalEvidenceError,
    LiveEvalExecutionError,
    LiveEvalRun,
    ResponseContractViolation,
    SanitizedLiveEvidence,
    ScriptedSingleAttemptExecutor,
    StrictLiveDecisionParser,
    TrustedLiveGrant,
    _live_model_request,
    _prepare_local_evidence_path,
    build_blind_live_route_contract,
    build_sanitized_live_evidence,
    execute_authorized_live_eval,
    live_plan_summary,
    recompute_live_metrics,
    run_production_live_eval,
    write_local_live_evidence,
)
from seven_lens.infrastructure.agnes_transport import (
    AgnesJsonModelTransport,
    StdlibAgnesHttpExecutor,
    build_agnes_request_body,
)
from seven_lens.infrastructure.chat_completions_transport import (
    ChatCompletionsModelTransport,
    StdlibChatCompletionsHttpExecutor,
)
from seven_lens.security.secret_values import SecretValue

FIXTURES = Path(__file__).parent / "fixtures" / "p3f_evals_v12"
GRANT = "external-user-approved-grant"


def _corpus_material() -> tuple[
    EvalCorpus, tuple[EvalCase, ...], MappingProxyType[str, ExpectedAnswer]
]:
    corpus = load_eval_corpus(FIXTURES)
    cases = corpus.load_public_cases(EvalSplit.HELD_OUT).cases
    _, answers = corpus.load_final_evaluation()
    route = tuple(case for case in cases if case.family is EvalFamily.ROUTE)
    return corpus, route, answers


def _selected() -> tuple[tuple[EvalCase, ...], MappingProxyType[str, ExpectedAnswer]]:
    _, route, answers = _corpus_material()
    return (*route[:2], route[20]), answers


def _authorization(case_ids: list[str], **changes: object) -> LiveEvalAuthorization:
    split_hash = _corpus_material()[0].split_manifest.split_hash
    value: dict[str, object] = {
        "schema_version": "seven-lens.p3f.live-auth.v4",
        "authorization_id": "user-approved-eval-001",
        "split_hash": split_hash,
        "case_ids": case_ids,
        "request_cap": sum(
            _corpus_material()[2][case_id].validity.value == "valid" for case_id in case_ids
        ),
        "attempt_cap": 3
        * sum(_corpus_material()[2][case_id].validity.value == "valid" for case_id in case_ids),
        "cost_cap_usd_cents": 50,
        "timeout_ms": 45_000,
        "request_byte_cap": 131_072,
        "response_byte_cap": 131_072,
        "expires_at": "2030-01-01T00:00:00+00:00",
        "privacy_class": "SYNTHETIC_ONLY",
        "provider_policy_id": "p3e-agnes-2.5-flash-only-v1",
        "parser_id": "p3f-strict-route-decision-v5",
        "prompt_template_hash": LIVE_PROMPT_TEMPLATE_HASH,
        "automatic_retries": MAX_RETRIES_PER_CASE,
        "retryable_error_codes": ["RATE_LIMIT", "TIMEOUT", "TRANSIENT"],
        "circuit_breaker_consecutive_exhausted_cases": 3,
        "stop_on_first_error": False,
    }
    value.update(changes)
    value["config_hash"] = content_hash(cast(JsonValue, value))
    return LiveEvalAuthorization.from_json(json.dumps(value).encode())


def _grant(
    authorization: LiveEvalAuthorization, *, config_hash: str | None = None
) -> TrustedLiveGrant:
    return TrustedLiveGrant(
        config_hash or authorization.config_hash,
        hashlib.sha256(GRANT.encode()).hexdigest(),
    )


def _authorization_bytes(authorization: LiveEvalAuthorization) -> bytes:
    value = {
        "schema_version": "seven-lens.p3f.live-auth.v4",
        "authorization_id": authorization.authorization_id,
        "split_hash": authorization.split_hash,
        "case_ids": list(authorization.case_ids),
        "request_cap": authorization.request_cap,
        "attempt_cap": authorization.attempt_cap,
        "cost_cap_usd_cents": authorization.cost_cap_usd_cents,
        "timeout_ms": authorization.timeout_ms,
        "request_byte_cap": authorization.request_byte_cap,
        "response_byte_cap": authorization.response_byte_cap,
        "expires_at": authorization.expires_at.isoformat(),
        "privacy_class": authorization.privacy_class,
        "provider_policy_id": authorization.provider_policy_id,
        "parser_id": authorization.parser_id,
        "prompt_template_hash": authorization.prompt_template_hash,
        "automatic_retries": authorization.automatic_retries,
        "retryable_error_codes": list(authorization.retryable_error_codes),
        "circuit_breaker_consecutive_exhausted_cases": (
            authorization.circuit_breaker_consecutive_exhausted_cases
        ),
        "stop_on_first_error": authorization.stop_on_first_error,
        "config_hash": authorization.config_hash,
    }
    return json.dumps(value, separators=(",", ":"), sort_keys=True).encode()


def _response(case: EvalCase, decision: ExpectedDecision) -> bytes:
    return json.dumps(
        {
            "case_id": case.case_id,
            "route": f"{case.stage}/{case.role}",
            "decision": decision.value,
            "citations": [case.payload["fact_variant"]],
            "reason_codes": ["SYNTHETIC_CONTRACT_CHECK"],
        },
        separators=(",", ":"),
        sort_keys=True,
    ).encode()


def _execute(
    cases: tuple[EvalCase, ...],
    authorization: LiveEvalAuthorization,
    executor: (
        AnalysisProviderLivePostExecutor | AgnesLivePostExecutor | ScriptedSingleAttemptExecutor
    ),
    **changes: object,
) -> LiveEvalRun:
    values: dict[str, Any] = {
        "corpus_root": FIXTURES,
        "authorization": authorization,
        "trusted_grant": _grant(authorization),
        "supplied_grant": GRANT,
        "executor": executor,
        "now": datetime(2026, 8, 24, tzinfo=UTC),
        "sleep": lambda seconds: None,
    }
    values.update(changes)
    return execute_authorized_live_eval(**values)


def test_external_trusted_config_hash_is_required_before_zero_posts() -> None:
    cases, _ = _selected()
    auth = _authorization([case.case_id for case in cases])
    executor = ScriptedSingleAttemptExecutor(
        tuple(_response(case, ExpectedDecision.ACCEPT) for case in cases)
    )

    with pytest.raises(LiveEvalAuthorizationError, match="trusted external"):
        _execute(
            cases,
            auth,
            executor,
            trusted_grant=TrustedLiveGrant("0" * 64, _grant(auth).grant_sha256),
        )
    assert executor.attempts == []


def test_live_entrypoint_reloads_hash_closed_corpus_before_zero_posts(tmp_path: Path) -> None:
    cases, _ = _selected()
    auth = _authorization([case.case_id for case in cases])
    executor = ScriptedSingleAttemptExecutor(
        tuple(_response(case, ExpectedDecision.ACCEPT) for case in cases)
    )
    copied = tmp_path / "corpus"
    shutil.copytree(FIXTURES, copied)
    held_out = copied / "held_out" / "cases.json"
    wire = json.loads(held_out.read_text(encoding="utf-8"))
    wire["cases"][0]["mode"] = "emergency"
    held_out.write_text(json.dumps(wire), encoding="utf-8")

    with pytest.raises(ValueError, match="case manifest hash mismatch"):
        _execute(cases, auth, executor, corpus_root=copied)
    assert executor.attempts == []


def test_arbitrary_opaque_executor_is_rejected_before_it_can_post() -> None:
    cases, _ = _selected()
    auth = _authorization([case.case_id for case in cases])

    class OpaqueRetryingExecutor:
        def __init__(self) -> None:
            self.attempts: list[str] = []

        def post_once(self, *args: object) -> bytes:
            self.attempts.extend(["hidden-1", "hidden-2"])
            return b"{}"

    opaque = OpaqueRetryingExecutor()
    with pytest.raises(LiveEvalAuthorizationError, match="package-owned"):
        _execute(cases, auth, cast(Any, opaque))
    assert opaque.attempts == []


def test_mismatched_production_executor_route_is_rejected_before_post(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cases, answers = _selected()
    case = next(case for case in cases if answers[case.case_id].validity.value == "valid")
    auth = _authorization([case.case_id])
    operator_route = AnalysisProviderConfig(
        config_source=ConfigSource.OPERATOR_FILE,
        generation=1,
        base_url="https://integrate.api.nvidia.com/v1",
        model_id="openai/gpt-oss-120b",
    )
    transport = ChatCompletionsModelTransport(
        config=operator_route,
        api_key=SecretValue.from_bytes(b"test-only-not-a-provider-key"),
        executor=StdlibChatCompletionsHttpExecutor(),
        clock=lambda: UtcTimestamp(datetime(2026, 8, 24, tzinfo=UTC)),
    )
    executor = AnalysisProviderLivePostExecutor(transport, route=operator_route)
    executor._production_composed = True
    posts = 0

    def fake_execute(self: ChatCompletionsModelTransport, request: object) -> JsonModelResponse:
        nonlocal posts
        posts += 1
        return JsonModelResponse(
            provider_response_id="synthetic.response",
            model_id=operator_route.model_id,
            content=_response(case, ExpectedDecision.ACCEPT).decode(),
            response_hash="a" * 64,
            prompt_tokens=1,
            completion_tokens=1,
            total_tokens=2,
        )

    monkeypatch.setattr(ChatCompletionsModelTransport, "execute", fake_execute)
    with pytest.raises(LiveEvalAuthorizationError, match="route"):
        _execute(
            (case,),
            auth,
            executor,
            route=package_default_analysis_provider_config(),
        )
    assert posts == 0


def test_production_route_policy_mismatch_is_zero_keychain_and_zero_evidence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _, route_cases, _ = _corpus_material()
    authorization = _authorization(
        [case.case_id for case in route_cases],
        cost_cap_usd_cents=NO_FEE_CAP_APPROVED_SENTINEL,
    )
    operator_route = AnalysisProviderConfig(
        config_source=ConfigSource.OPERATOR_FILE,
        generation=1,
        base_url="https://integrate.api.nvidia.com/v1",
        model_id="openai/gpt-oss-120b",
    )
    plan = live_plan_summary(
        authorization,
        authorization.config_hash,
        corpus_root=FIXTURES,
        route=operator_route,
    )
    keychain_calls = 0

    def forbidden_keychain(*args: object, **kwargs: object) -> object:
        nonlocal keychain_calls
        keychain_calls += 1
        raise AssertionError("route mismatch must reject before Keychain")

    monkeypatch.setattr(
        AnalysisProviderLivePostExecutor,
        "from_macos_keychain",
        classmethod(forbidden_keychain),
    )
    (tmp_path / ".gitignore").write_text("/.seven-lens-local/\n", encoding="utf-8")
    with pytest.raises(LiveEvalAuthorizationError, match="provider policy"):
        run_production_live_eval(
            repo_root=tmp_path,
            corpus_root=FIXTURES,
            authorization=authorization,
            trusted_config_hash=authorization.config_hash,
            trusted_grant_sha256=hashlib.sha256(str(plan["plan_hash"]).encode("utf-8")).hexdigest(),
            supplied_grant=str(plan["plan_hash"]),
            evidence_filename="must-not-exist.json",
            now=datetime(2026, 8, 24, tzinfo=UTC),
            route=operator_route,
        )
    assert keychain_calls == 0
    assert not (tmp_path / ".seven-lens-local").exists()


def test_single_attempt_accounting_and_blind_oracle_metrics() -> None:
    cases, answers = _selected()
    auth = _authorization([case.case_id for case in cases])
    decisions = [answers[case.case_id].decision for case in cases]
    executor = ScriptedSingleAttemptExecutor(
        tuple(_response(case, decision) for case, decision in zip(cases, decisions, strict=True))
    )
    run = _execute(cases, auth, executor)

    assert run.authorized_case_count == len(cases) == 3
    assert run.request_count == len(executor.attempts) == 2
    assert run.pre_network_reject_count == 1
    assert run.fallback_count == 0
    assert run.execution_kind == "SCRIPTED_TEST_ONLY"
    assert {record.attempt_ordinal for record in run.records} == {None, 1, 2}
    metrics = recompute_live_metrics(run, authorization=auth, cases=cases, answers=answers)
    assert metrics["real_provider_evidence"] is False
    assert cast(dict[str, object], metrics["valid_primary"])["numerator"] == 2
    assert cast(dict[str, object], metrics["valid_primary"])["passed"] is False
    assert cast(dict[str, object], metrics["invalid_ambiguous_recall"])["numerator"] == 1
    assert cast(dict[str, object], metrics["invalid_ambiguous_recall"])["passed"] is False
    assert metrics["errors"] == 0


def test_full_live_route_set_is_260_posts_and_130_local_rejections() -> None:
    corpus, route, answers = _corpus_material()
    auth = _authorization([case.case_id for case in route])
    valid = tuple(case for case in route if answers[case.case_id].validity.value == "valid")
    executor = ScriptedSingleAttemptExecutor(
        tuple(_response(case, answers[case.case_id].decision) for case in valid)
    )

    run = _execute(route, auth, executor)
    plan = live_plan_summary(auth, auth.config_hash, corpus_root=FIXTURES)

    assert corpus.split_manifest.split_hash == auth.split_hash
    assert run.authorized_case_count == len(run.records) == 390
    assert run.request_count == len(executor.attempts) == len(executor.payloads) == 260
    assert run.pre_network_reject_count == 130
    assert plan["pre_network_reject_count"] == 130
    assert plan["request_cap"] == 260
    assert plan["attempt_cap"] == 780
    assert list(cast(dict[str, object], plan["payload_hashes"]).values()) == executor.attempts
    assert (
        list(cast(dict[str, object], plan["provider_request_hashes"]).values())
        == executor.request_hashes
    )
    assert sum(record.attempt_ordinal is None for record in run.records) == 130
    assert sum(record.outcome == "PRE_NETWORK_REJECTED" for record in run.records) == 130
    forbidden = {"validity", "decision", "mutation", "ordinal", "expected_round_number"}
    for raw in executor.payloads:
        payload = json.loads(raw)
        assert set(payload) == {
            "authorization_id",
            "case_id",
            "contract_hash",
            "mode",
            "production_contract",
            "prompt_template_hash",
            "required_cited_fact",
            "response_contract",
            "route",
            "schema_version",
        }
        assert not forbidden.intersection(payload)
        assert not forbidden.intersection(payload["production_contract"])
        assert payload["response_contract"] == {
            "schema_version": "seven-lens.p3f.live-response-contract.v1",
            "type": "object",
            "additional_properties": False,
            "required": ["case_id", "route", "decision", "citations", "reason_codes"],
            "const": {
                "case_id": payload["case_id"],
                "route": payload["route"],
                "decision": "ACCEPT",
                "citations": [payload["required_cited_fact"]],
                "reason_codes": ["SYNTHETIC_CONTRACT_CHECK"],
            },
        }

    metrics = recompute_live_metrics(run, authorization=auth, cases=route, answers=answers)
    assert metrics["request_count"] == 260
    assert metrics["pre_network_reject_count"] == 130
    assert cast(dict[str, object], metrics["valid_primary"])["numerator"] == 260
    assert cast(dict[str, object], metrics["invalid_ambiguous_recall"])["numerator"] == 130
    assert metrics["real_provider_evidence"] is False
    assert metrics["logical_request_count"] == 260
    assert metrics["retry_count"] == 0
    assert metrics["live_model_quality_gate_passed"] is False
    assert metrics["provider_transport_gate_passed"] is False

    real_metrics = recompute_live_metrics(
        replace(run, execution_kind="PRODUCTION_ANALYSIS_PROVIDER_KEYCHAIN_STDLIB"),
        authorization=auth,
        cases=route,
        answers=answers,
    )
    assert real_metrics["live_model_quality_gate_passed"] is True
    assert real_metrics["provider_transport_gate_passed"] is True


def test_parser_has_no_validity_or_expected_answer_and_rejects_identity_drift() -> None:
    cases, _ = _selected()
    case = cases[0]
    assert not hasattr(case, "validity")
    contract = build_blind_live_route_contract(case)
    assert not hasattr(contract, "validity")
    assert "mutation" not in contract.production_contract
    assert "ordinal" not in contract.production_contract
    parser = StrictLiveDecisionParser()
    parsed = parser.parse(contract, _response(case, ExpectedDecision.ACCEPT))
    assert parsed.decision is ExpectedDecision.ACCEPT
    wrong = json.loads(_response(case, ExpectedDecision.ACCEPT))
    wrong["case_id"] = "foreign.case"
    with pytest.raises(ValueError, match="identity or evidence"):
        parser.parse(contract, json.dumps(wrong).encode())
    meaningless = json.loads(_response(case, ExpectedDecision.ACCEPT))
    meaningless["reason_codes"] = ["BANANA"]
    with pytest.raises(ValueError, match="identity or evidence"):
        parser.parse(contract, json.dumps(meaningless).encode())
    wrong_decision = json.loads(_response(case, ExpectedDecision.ACCEPT))
    wrong_decision["decision"] = "ABSTAIN"
    with pytest.raises(ValueError, match="identity or evidence"):
        parser.parse(contract, json.dumps(wrong_decision).encode())


def test_parser_strips_one_exact_json_fence_and_rejects_every_other_shape() -> None:
    cases, _ = _selected()
    case = cases[0]
    contract = build_blind_live_route_contract(case)
    parser = StrictLiveDecisionParser()
    body = _response(case, ExpectedDecision.ACCEPT).decode("utf-8")

    fenced = parser.parse(contract, f"```json\n{body}\n```".encode())
    assert fenced.decision is ExpectedDecision.ACCEPT
    padded = parser.parse(contract, f"  \n```json\n{body}\n```  ".encode())
    assert padded.citations == (contract.required_cited_fact,)

    for variant in (
        f"```JSON\n{body}\n```",
        f"```json{body}```",
        f"```json\r\n{body}\r\n```",
        f"{body}\n```",
        f"```json\n{body}\n```\ntrailing prose",
        f"prose\n```json\n{body}\n```",
        f"```json\n{body}\n```\n```json\n{body}\n```",
    ):
        with pytest.raises(ValueError, match="not strict JSON"):
            parser.parse(contract, variant.encode())

    fenced_wrong_citation = json.loads(body)
    fenced_wrong_citation["citations"] = ["foreign.fact"]
    with pytest.raises(ValueError, match="identity or evidence"):
        parser.parse(
            contract,
            f"```json\n{json.dumps(fenced_wrong_citation)}\n```".encode(),
        )


def test_parser_failure_diagnostics_are_content_free_and_stage_specific() -> None:
    cases, _ = _selected()
    case = cases[0]
    contract = build_blind_live_route_contract(case)
    parser = StrictLiveDecisionParser()
    body = _response(case, ExpectedDecision.ACCEPT).decode("utf-8")

    with pytest.raises(ResponseContractViolation) as prose:
        parser.parse(contract, f"```json\n{body}\n```\nsuffix".encode())
    assert prose.value.sanitized_diagnostics["stage"] == "JSON_PARSE"
    assert prose.value.sanitized_diagnostics["code_fence_markers"] == 2
    assert prose.value.sanitized_diagnostics["starts_object"] is False
    assert b"suffix" not in str(prose.value.sanitized_diagnostics).encode()

    extra_key = json.loads(body)
    extra_key["explanation"] = "unused"
    with pytest.raises(ResponseContractViolation) as field_set:
        parser.parse(contract, json.dumps(extra_key).encode())
    field_diagnostics = cast(dict[str, object], field_set.value.sanitized_diagnostics)
    assert field_diagnostics["stage"] == "FIELD_SET"
    assert sorted(cast(list[str], field_diagnostics["outer_keys"])) == [
        "case_id",
        "citations",
        "decision",
        "explanation",
        "reason_codes",
        "route",
    ]
    assert field_diagnostics["top_level_type"] == "dict"

    wrong_citation = json.loads(body)
    wrong_citation["citations"] = ["foreign.fact"]
    with pytest.raises(ResponseContractViolation) as closure:
        parser.parse(contract, json.dumps(wrong_citation).encode())
    assert closure.value.sanitized_diagnostics == {
        "stage": "IDENTITY_CLOSURE",
        "mismatched_fields": ["citations"],
    }

    multi = json.loads(body)
    multi["route"] = "foreign/stage"
    multi["reason_codes"] = ["BANANA"]
    with pytest.raises(ResponseContractViolation) as multi_failure:
        parser.parse(contract, json.dumps(multi).encode())
    assert multi_failure.value.sanitized_diagnostics["mismatched_fields"] == [
        "route",
        "reason_codes",
    ]

    parsed_ok = parser.parse(contract, f"```json\n{body}\n```".encode())
    assert parsed_ok.decision is ExpectedDecision.ACCEPT


def test_failed_response_contract_attempt_records_sanitized_diagnostics() -> None:
    cases, answers = _selected()
    auth = _authorization([cases[0].case_id])
    body = _response(cases[0], ExpectedDecision.ACCEPT).decode("utf-8")

    with pytest.raises(LiveEvalExecutionError) as stopped:
        execute_authorized_live_eval(
            corpus_root=FIXTURES,
            authorization=auth,
            trusted_grant=_grant(auth),
            supplied_grant=GRANT,
            executor=ScriptedSingleAttemptExecutor((f"{body}\n```".encode(),)),
            now=datetime(2026, 8, 24, tzinfo=UTC),
            sleep=lambda seconds: None,
        )
    failed_record = stopped.value.partial_run.records[-1]
    assert failed_record.outcome == "FAILED"
    assert failed_record.error_code == "RESPONSE_CONTRACT"
    diagnostics = failed_record.failure_diagnostics
    assert type(diagnostics) is dict
    assert cast(dict[str, object], diagnostics)["stage"] == "JSON_PARSE"

    fenced_executor = ScriptedSingleAttemptExecutor((f"```json\n{body}\n```".encode(),))
    success_run = execute_authorized_live_eval(
        corpus_root=FIXTURES,
        authorization=auth,
        trusted_grant=_grant(auth),
        supplied_grant=GRANT,
        executor=fenced_executor,
        sleep=lambda seconds: None,
        now=datetime(2026, 8, 24, tzinfo=UTC),
    )
    assert success_run.records[-1].outcome == "STRICTLY_PARSED"
    assert success_run.records[-1].failure_diagnostics is None
    metrics = recompute_live_metrics(
        replace(success_run, execution_kind="PRODUCTION_ANALYSIS_PROVIDER_KEYCHAIN_STDLIB"),
        authorization=auth,
        cases=cases,
        answers=answers,
    )
    assert cast(dict[str, object], metrics["valid_primary"])["numerator"] == 1


def test_live_requests_pin_const_response_format_and_legacy_wire_is_unchanged() -> None:
    cases, _ = _selected()
    auth = _authorization([case.case_id for case in cases])
    contract = build_blind_live_route_contract(cases[0])
    request = _live_model_request(contract, b"{}", UtcTimestamp(datetime(2030, 1, 1, tzinfo=UTC)))
    body = json.loads(build_agnes_request_body(agnes_25_flash_config(), request))
    assert set(body) == {
        "max_tokens",
        "messages",
        "model",
        "stream",
        "temperature",
        "response_format",
    }
    # json_object mode: some providers reject json_schema intermittently with a
    # 400 "unavailable" while accepting json_object on every call.  The exact
    # literal values stay pinned by the prompt response_contract and the local
    # strict parser, never by the provider.
    assert body["response_format"] == {"type": "json_object"}

    legacy = JsonModelRequest(request.call_id, request.messages, request.deadline, 2_048)
    legacy_wire = json.loads(build_agnes_request_body(agnes_25_flash_config(), legacy))
    assert set(legacy_wire) == {"max_tokens", "messages", "model", "stream", "temperature"}

    plan = live_plan_summary(auth, auth.config_hash, corpus_root=FIXTURES)
    assert plan["response_format_enforced"] is True


def test_failure_and_timeout_are_attempt_level_fail_fast_records() -> None:
    cases, _ = _selected()
    auth = _authorization([case.case_id for case in cases])
    executor = ScriptedSingleAttemptExecutor(
        (_response(cases[0], ExpectedDecision.ACCEPT), RuntimeError("synthetic"), b"unused")
    )
    with pytest.raises(LiveEvalExecutionError) as error:
        _execute(cases, auth, executor)
    assert len(executor.attempts) == 2
    assert error.value.partial_run.request_count == 2
    assert error.value.partial_run.records[-1].outcome == "FAILED"

    slow = ScriptedSingleAttemptExecutor(
        tuple(_response(cases[0], ExpectedDecision.ACCEPT) for _ in range(3))
    )
    ticks = iter((0, 46_000_000_000, 46_000_000_000) * 3)
    run = _execute(
        (cases[0],),
        _authorization([cases[0].case_id]),
        slow,
        monotonic_ns=lambda: next(ticks),
        sleep=lambda _: None,
    )
    assert run.request_count == 3
    assert all(record.outcome == "FAILED" for record in run.records)
    assert all(record.error_code == "TIMEOUT" for record in run.records)


def test_transient_errors_retry_twice_then_continue_without_hiding_attempts() -> None:
    cases, answers = _selected()
    auth = _authorization([case.case_id for case in cases])
    executor = ScriptedSingleAttemptExecutor(
        (
            ModelTransportError(ModelTransportErrorCode.TIMEOUT),
            ModelTransportError(ModelTransportErrorCode.TRANSIENT),
            _response(cases[0], answers[cases[0].case_id].decision),
            _response(cases[1], answers[cases[1].case_id].decision),
        )
    )
    delays: list[float] = []

    run = _execute(cases, auth, executor, sleep=delays.append)

    assert run.request_count == 4
    assert [
        record.case_attempt_ordinal for record in run.records if record.case_id == cases[0].case_id
    ] == [1, 2, 3]
    case_records = [record for record in run.records if record.case_id == cases[0].case_id]
    assert [record.error_code for record in case_records[:2]] == ["TIMEOUT", "TRANSIENT"]
    assert case_records[2].outcome == "STRICTLY_PARSED"
    # Only the two approved retry backoffs are slept; successful logical cases
    # do not add provider-specific pacing.
    assert len(delays) == 2
    assert 2.0 <= delays[0] < 3.0
    assert 4.0 <= delays[1] < 5.0


def test_three_consecutive_transport_exhaustions_open_circuit_breaker() -> None:
    _, route, _ = _corpus_material()
    valid = route[:4]
    auth = _authorization([case.case_id for case in valid])
    failures = tuple(ModelTransportError(ModelTransportErrorCode.TIMEOUT) for _ in range(9))
    executor = ScriptedSingleAttemptExecutor(failures)

    with pytest.raises(LiveEvalExecutionError, match="circuit breaker") as caught:
        _execute(valid, auth, executor, sleep=lambda _: None)

    assert caught.value.partial_run.request_count == 9
    assert len({record.case_id for record in caught.value.partial_run.records}) == 3
    assert all(record.error_code == "TIMEOUT" for record in caught.value.partial_run.records)


@pytest.mark.parametrize(
    "code",
    [
        ModelTransportErrorCode.AUTH,
        ModelTransportErrorCode.CONFIG,
        ModelTransportErrorCode.PERMANENT,
    ],
)
def test_non_retryable_provider_errors_stop_after_one_attempt(
    code: ModelTransportErrorCode,
) -> None:
    cases, _ = _selected()
    auth = _authorization([case.case_id for case in cases])
    executor = ScriptedSingleAttemptExecutor((ModelTransportError(code),))

    with pytest.raises(LiveEvalExecutionError, match="non-retryable") as caught:
        _execute(cases, auth, executor)

    assert caught.value.partial_run.request_count == 1
    assert len(executor.attempts) == 1


def test_authorization_cap_has_no_slack_and_plan_exposes_trust_match() -> None:
    cases, _ = _selected()
    with pytest.raises(LiveEvalAuthorizationError, match="safety policy"):
        _authorization([case.case_id for case in cases], request_cap=4)
    with pytest.raises(LiveEvalAuthorizationError, match="safety policy"):
        _authorization([case.case_id for case in cases], attempt_cap=7)
    with pytest.raises(LiveEvalAuthorizationError, match="safety policy"):
        _authorization(
            [case.case_id for case in cases],
            retryable_error_codes=["AUTH", "TIMEOUT", "TRANSIENT"],
        )
    auth = _authorization([case.case_id for case in cases])
    plan = live_plan_summary(auth, auth.config_hash, corpus_root=FIXTURES)
    assert plan["trusted_config_match"] is True
    assert plan["automatic_retries"] == 2
    assert plan["attempt_cap"] == 6
    assert plan["retryable_error_codes"] == ["RATE_LIMIT", "TIMEOUT", "TRANSIENT"]
    assert plan["circuit_breaker_consecutive_exhausted_cases"] == 3
    assert plan["fallback_attempts"] == 0
    assert plan["request_cap"] == 2
    assert plan["pre_network_reject_count"] == 1
    assert len(cast(dict[str, object], plan["payload_hashes"])) == 2
    assert plan["network_started"] is False


def test_live_plan_hash_closes_over_prompt_and_model_wire_mutations(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cases, _ = _selected()
    auth = _authorization([case.case_id for case in cases])
    baseline = live_plan_summary(auth, auth.config_hash, corpus_root=FIXTURES)

    monkeypatch.setattr(
        provider_eval_module,
        "_LIVE_SYSTEM_PROMPT",
        _LIVE_SYSTEM_PROMPT + " Package revision marker.",
    )
    changed_prompt = live_plan_summary(auth, auth.config_hash, corpus_root=FIXTURES)
    assert changed_prompt["provider_request_hash_root"] != baseline["provider_request_hash_root"]
    assert changed_prompt["plan_hash"] != baseline["plan_hash"]

    monkeypatch.setattr(provider_eval_module, "_LIVE_SYSTEM_PROMPT", _LIVE_SYSTEM_PROMPT)
    from seven_lens.config.analysis_provider import package_default_analysis_provider_config

    tampered_route = package_default_analysis_provider_config()
    object.__setattr__(tampered_route, "model_id", "openai/gpt-oss-120b-revision")
    monkeypatch.setattr(provider_eval_module, "_default_route", lambda: tampered_route)
    changed_model = live_plan_summary(auth, auth.config_hash, corpus_root=FIXTURES)
    assert changed_model["provider_request_hash_root"] != baseline["provider_request_hash_root"]
    assert changed_model["plan_hash"] != baseline["plan_hash"]


def test_authorization_identifier_and_json_reader_are_strict() -> None:
    cases, _ = _selected()
    for unsafe in ("api-key-approval", "../approval", "https://approval", "UPPERCASE"):
        with pytest.raises(LiveEvalAuthorizationError):
            _authorization([case.case_id for case in cases], authorization_id=unsafe)
    valid = _authorization([case.case_id for case in cases])
    duplicated = _authorization_bytes(valid).replace(
        b'"schema_version":"seven-lens.p3f.live-auth.v4"',
        b'"schema_version":"seven-lens.p3f.live-auth.v4",'
        b'"schema_version":"seven-lens.p3f.live-auth.v4"',
        1,
    )
    with pytest.raises(LiveEvalAuthorizationError, match="strict JSON"):
        LiveEvalAuthorization.from_json(duplicated)


def test_authorization_timeout_cannot_exceed_the_authorized_emergency_ceiling() -> None:
    cases, _ = _selected()
    with pytest.raises(LiveEvalAuthorizationError, match="safety policy"):
        _authorization([cases[0].case_id], timeout_ms=180_001)


def test_agnes_attempt_preserves_raw_response_body_hash_not_content_hash(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cases, _ = _selected()
    case = cases[0]
    contract = build_blind_live_route_contract(case)
    payload = b'{"synthetic":"request"}'
    content = _response(case, ExpectedDecision.ACCEPT).decode()
    raw_body = b'{"provider_wrapper":"different bytes from normalized content"}'
    raw_hash = hashlib.sha256(raw_body).hexdigest()
    content_hash_value = hashlib.sha256(content.encode()).hexdigest()
    assert raw_hash != content_hash_value
    transport = AgnesJsonModelTransport(
        config=agnes_25_flash_config(),
        api_key=SecretValue.from_bytes(b"test-only-not-a-provider-key"),
        executor=StdlibAgnesHttpExecutor(),
        clock=lambda: UtcTimestamp(datetime(2026, 8, 24, tzinfo=UTC)),
    )

    def fake_execute(self: AgnesJsonModelTransport, request: object) -> JsonModelResponse:
        return JsonModelResponse(
            provider_response_id="not-persisted",
            model_id="agnes-2.5-flash",
            content=content,
            response_hash=raw_hash,
            prompt_tokens=11,
            completion_tokens=7,
            total_tokens=18,
        )

    monkeypatch.setattr(AgnesJsonModelTransport, "execute", fake_execute)
    executor = AgnesLivePostExecutor(transport)
    response = executor.post_once(
        contract,
        payload,
        UtcTimestamp(datetime(2026, 8, 24, 0, 1, tzinfo=UTC)),
    )
    assert response == content.encode()
    assert executor.response_hashes == [raw_hash]
    assert executor.response_hashes != [content_hash_value]
    assert executor.token_usage == [(11, 7, 18)]


def test_unattested_agnes_executor_and_finite_cost_cap_are_zero_post(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cases, _ = _selected()
    auth = _authorization([case.case_id for case in cases])
    transport = AgnesJsonModelTransport(
        config=agnes_25_flash_config(),
        api_key=SecretValue.from_bytes(b"test-only-not-a-provider-key"),
        executor=StdlibAgnesHttpExecutor(),
        clock=lambda: UtcTimestamp(datetime(2026, 8, 24, tzinfo=UTC)),
    )
    unattested = AgnesLivePostExecutor(transport)
    with pytest.raises(LiveEvalAuthorizationError, match="not Keychain/stdlib"):
        _execute(cases, auth, unattested)
    assert unattested.attempts == []

    keychain_calls = 0

    def forbidden_keychain(*args: object, **kwargs: object) -> object:
        nonlocal keychain_calls
        keychain_calls += 1
        raise AssertionError("Keychain must not be touched for unenforceable finite cost cap")

    monkeypatch.setattr(provider_eval_module, "MacOSKeychainSecretProvider", forbidden_keychain)
    with pytest.raises(LiveEvalAuthorizationError, match="exact frozen 390-case"):
        run_production_live_eval(
            repo_root=tmp_path,
            corpus_root=FIXTURES,
            authorization=auth,
            trusted_config_hash=auth.config_hash,
            trusted_grant_sha256=_grant(auth).grant_sha256,
            supplied_grant=GRANT,
            evidence_filename="must-not-exist.json",
            now=datetime(2026, 8, 24, tzinfo=UTC),
        )
    _, route, _ = _corpus_material()
    full_auth = _authorization([case.case_id for case in route])
    exact_plan_grant = cast(
        str,
        live_plan_summary(full_auth, full_auth.config_hash, corpus_root=FIXTURES)["plan_hash"],
    )
    with pytest.raises(LiveEvalAuthorizationError, match="no verifiable unit price"):
        run_production_live_eval(
            repo_root=tmp_path,
            corpus_root=FIXTURES,
            authorization=full_auth,
            trusted_config_hash=full_auth.config_hash,
            trusted_grant_sha256=hashlib.sha256(exact_plan_grant.encode()).hexdigest(),
            supplied_grant=exact_plan_grant,
            evidence_filename="must-not-exist.json",
            now=datetime(2026, 8, 24, tzinfo=UTC),
        )
    assert keychain_calls == 0
    assert not (tmp_path / ".seven-lens-local").exists()
    with pytest.raises(LiveEvalAuthorizationError, match="safety policy"):
        _authorization(
            [case.case_id for case in cases],
            cost_cap_usd_cents=NO_FEE_CAP_APPROVED_SENTINEL - 1,
        )


def test_sanitized_evidence_is_private_hash_closed_and_strict(tmp_path: Path) -> None:
    cases, answers = _selected()
    auth = _authorization(
        [case.case_id for case in cases],
        cost_cap_usd_cents=NO_FEE_CAP_APPROVED_SENTINEL,
    )
    executor = ScriptedSingleAttemptExecutor(
        tuple(_response(case, answers[case.case_id].decision) for case in cases)
    )
    run = _execute(cases, auth, executor)
    plan = live_plan_summary(auth, auth.config_hash, corpus_root=FIXTURES)
    evidence = build_sanitized_live_evidence(
        run=run,
        authorization=auth,
        corpus_root=FIXTURES,
        plan=plan,
        completed=True,
        grant_sha256=_grant(auth).grant_sha256,
    )

    raw = evidence.to_bytes()
    assert SanitizedLiveEvidence.from_json(raw) == evidence
    forbidden = (
        b"Authorization",
        b"provider_response_id",
        b"raw_prompt",
        b"raw_response",
        b"account_id",
        b"broker_order_id",
        b"test-only-not-a-provider-key",
    )
    assert all(marker not in raw for marker in forbidden)
    (tmp_path / ".gitignore").write_text("/.seven-lens-local/\n", encoding="utf-8")
    path = _prepare_local_evidence_path(tmp_path, "sanitized-live.json")
    write_local_live_evidence(path, evidence)
    assert path.read_bytes() == raw
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert stat.S_IMODE(path.parent.stat().st_mode) == 0o700
    duplicated = raw.replace(
        b'"schema_version":"seven-lens.p3f.live-evidence.v3"',
        b'"schema_version":"seven-lens.p3f.live-evidence.v3",'
        b'"schema_version":"seven-lens.p3f.live-evidence.v3"',
        1,
    )
    with pytest.raises(LiveEvalEvidenceError, match="strict JSON"):
        SanitizedLiveEvidence.from_json(duplicated)


def test_live_prompt_is_fixed_exact_and_has_no_oracle_label() -> None:
    material = _LIVE_SYSTEM_PROMPT + "\n" + _LIVE_DEVELOPER_PROMPT
    for field in ("case_id", "route", "decision", "citations", "reason_codes"):
        assert field in material
    for decision in ("ACCEPT", "REJECT", "ABSTAIN"):
        assert decision in material
    assert "No Markdown" in material
    assert "extra keys" in material
    assert "response_contract" in material
    assert "SYNTHETIC_CONTRACT_CHECK" in material
    assert "validity" not in material
    assert "expected answer" not in material.lower()
    assert (
        hashlib.sha256((_LIVE_SYSTEM_PROMPT + "\x00" + _LIVE_DEVELOPER_PROMPT).encode()).hexdigest()
        == LIVE_PROMPT_TEMPLATE_HASH
    )


def test_cli_live_plan_is_default_zero_network(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    cases, _ = _selected()
    auth = _authorization([case.case_id for case in cases])
    authorization_file = tmp_path / "authorization.json"
    authorization_file.write_bytes(_authorization_bytes(auth))

    # Bind the CLI's route resolution to the package default via the test-only
    # override; the production CLI resolves the operator route instead.
    monkeypatch.setenv("SEVEN_LENS_ANALYSIS_PROVIDER_CONFIG_ROOT", str(tmp_path / "absent-root"))

    def forbidden_keychain(*args: object, **kwargs: object) -> object:
        raise AssertionError("dry-run must not touch Keychain")

    monkeypatch.setattr(provider_eval_module, "MacOSKeychainSecretProvider", forbidden_keychain)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "seven-lens-evals",
            "live-plan",
            "--authorization-file",
            str(authorization_file),
            "--trusted-config-hash",
            auth.config_hash,
            "--fixtures",
            str(FIXTURES),
        ],
    )
    assert eval_cli_main() == 0
    output = json.loads(capsys.readouterr().out)
    assert output["network_started"] is False
    assert output["request_cap"] == 2
    assert output["pre_network_reject_count"] == 1

    script = (Path(__file__).parents[1] / "scripts" / "run_p3f_live_evals.sh").read_text()
    assert "${SEVEN_LENS_P3F_LIVE:-0}" in script
    assert '!= "1"' in script
    assert "live-plan" in script
    assert "--execute-live" in script


def test_cli_live_plan_binds_operator_route_plan_hash(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    # A non-default operator route must make the CLI live-plan emit the exact
    # route-bound plan hash that live-run later requires as the external grant.
    # Binding to the package default (the other CLI test) cannot detect a missing
    # route argument because the default and None collapse to the same hash.
    route = AnalysisProviderConfig(
        config_source=ConfigSource.OPERATOR_FILE,
        generation=1,
        base_url="https://integrate.api.nvidia.com/v1",
        model_id="openai/gpt-oss-120b",
    )
    config_root = tmp_path / "operator-config"
    config_root.mkdir()
    (config_root / "analysis-provider.json").write_bytes(canonical_operator_bytes(route))
    monkeypatch.setenv("SEVEN_LENS_ANALYSIS_PROVIDER_CONFIG_ROOT", str(config_root))

    cases, _ = _selected()
    split_hash = _corpus_material()[0].split_manifest.split_hash
    valid = sum(_corpus_material()[2][case.case_id].validity.value == "valid" for case in cases)
    value: dict[str, object] = {
        "schema_version": "seven-lens.p3f.live-auth.v4",
        "authorization_id": "user-approved-eval-route",
        "split_hash": split_hash,
        "case_ids": [case.case_id for case in cases],
        "request_cap": valid,
        "attempt_cap": 3 * valid,
        "cost_cap_usd_cents": 50,
        "timeout_ms": 45_000,
        "request_byte_cap": 131_072,
        "response_byte_cap": 131_072,
        "expires_at": "2030-01-01T00:00:00+00:00",
        "privacy_class": "SYNTHETIC_ONLY",
        "provider_policy_id": route.route_policy_id,
        "parser_id": "p3f-strict-route-decision-v5",
        "prompt_template_hash": LIVE_PROMPT_TEMPLATE_HASH,
        "automatic_retries": MAX_RETRIES_PER_CASE,
        "retryable_error_codes": ["RATE_LIMIT", "TIMEOUT", "TRANSIENT"],
        "circuit_breaker_consecutive_exhausted_cases": 3,
        "stop_on_first_error": False,
    }
    value["config_hash"] = content_hash(cast(JsonValue, value))
    auth = LiveEvalAuthorization.from_json(json.dumps(value).encode(), route=route)
    authorization_file = tmp_path / "authorization.json"
    authorization_file.write_bytes(_authorization_bytes(auth))

    def forbidden_keychain(*args: object, **kwargs: object) -> object:
        raise AssertionError("live-plan must not touch Keychain")

    monkeypatch.setattr(provider_eval_module, "MacOSKeychainSecretProvider", forbidden_keychain)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "seven-lens-evals",
            "live-plan",
            "--authorization-file",
            str(authorization_file),
            "--trusted-config-hash",
            auth.config_hash,
            "--fixtures",
            str(FIXTURES),
        ],
    )
    assert eval_cli_main() == 0
    emitted = json.loads(capsys.readouterr().out)["plan_hash"]

    # The CLI hash must equal the route-bound plan live-run recomputes internally.
    assert (
        emitted
        == live_plan_summary(auth, auth.config_hash, corpus_root=FIXTURES, route=route)["plan_hash"]
    )
    # And must differ from the default-route plan, proving a dropped route argument
    # (the historical defect) is caught by this test.
    assert emitted != live_plan_summary(auth, auth.config_hash, corpus_root=FIXTURES)["plan_hash"]


def test_protocol_errors_are_never_retried() -> None:
    # Protocol failures are not availability signals. The live run stops instead
    # of normalizing or retrying a potentially incompatible response contract.
    cases, _ = _selected()
    auth = _authorization([cases[0].case_id, cases[1].case_id])
    protocol_then_ok = ScriptedSingleAttemptExecutor(
        (
            ModelTransportError(ModelTransportErrorCode.PROTOCOL),
            _response(cases[0], ExpectedDecision.ACCEPT),
            _response(cases[1], ExpectedDecision.ACCEPT),
        )
    )
    with pytest.raises(LiveEvalExecutionError, match="non-retryable") as caught:
        _execute(cases, auth, protocol_then_ok)
    assert caught.value.partial_run.request_count == 1
    assert caught.value.partial_run.records[0].error_code == "PROTOCOL"
