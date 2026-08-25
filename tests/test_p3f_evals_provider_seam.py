from __future__ import annotations

import hashlib
import json
import shutil
import stat
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import cast

import pytest

import seven_lens.evals.provider_eval as provider_eval_module
from seven_lens.application.ports.model_transport import JsonModelResponse
from seven_lens.config.provider import agnes_25_flash_config
from seven_lens.domain.value_objects import UtcTimestamp
from seven_lens.evals import EvalFamily, EvalSplit, load_eval_corpus
from seven_lens.evals.__main__ import main as eval_cli_main
from seven_lens.evals.models import ExpectedDecision, content_hash
from seven_lens.evals.provider_eval import (
    _LIVE_DEVELOPER_PROMPT,
    _LIVE_SYSTEM_PROMPT,
    LIVE_PROMPT_TEMPLATE_HASH,
    NO_FEE_CAP_APPROVED_SENTINEL,
    AgnesLivePostExecutor,
    LiveEvalAuthorization,
    LiveEvalAuthorizationError,
    LiveEvalEvidenceError,
    LiveEvalExecutionError,
    SanitizedLiveEvidence,
    ScriptedSingleAttemptExecutor,
    StrictLiveDecisionParser,
    TrustedLiveGrant,
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
)
from seven_lens.security.secret_values import SecretValue

FIXTURES = Path(__file__).parent / "fixtures" / "p3f_evals"
GRANT = "external-user-approved-grant"


def _corpus_material():
    corpus = load_eval_corpus(FIXTURES)
    cases = corpus.load_public_cases(EvalSplit.HELD_OUT).cases
    _, answers = corpus.load_final_evaluation()
    route = tuple(case for case in cases if case.family is EvalFamily.ROUTE)
    return corpus, route, answers


def _selected():
    _, route, answers = _corpus_material()
    return (*route[:2], route[20]), answers


def _authorization(case_ids: list[str], **changes: object) -> LiveEvalAuthorization:
    split_hash = _corpus_material()[0].split_manifest.split_hash
    value: dict[str, object] = {
        "schema_version": "seven-lens.p3f.live-auth.v3",
        "authorization_id": "user-approved-eval-001",
        "split_hash": split_hash,
        "case_ids": case_ids,
        "request_cap": sum(
            _corpus_material()[2][case_id].validity.value == "valid" for case_id in case_ids
        ),
        "cost_cap_usd_cents": 50,
        "timeout_ms": 45_000,
        "request_byte_cap": 131_072,
        "response_byte_cap": 131_072,
        "expires_at": "2030-01-01T00:00:00+00:00",
        "privacy_class": "SYNTHETIC_ONLY",
        "provider_policy_id": "p3e-agnes-2.5-flash-only-v1",
        "parser_id": "p3f-strict-route-decision-v3",
        "prompt_template_hash": LIVE_PROMPT_TEMPLATE_HASH,
        "automatic_retries": 0,
        "stop_on_first_error": True,
    }
    value.update(changes)
    value["config_hash"] = content_hash(value)  # type: ignore[arg-type]
    return LiveEvalAuthorization.from_json(json.dumps(value).encode())


def _grant(authorization: LiveEvalAuthorization, *, config_hash: str | None = None):
    return TrustedLiveGrant(
        config_hash or authorization.config_hash,
        hashlib.sha256(GRANT.encode()).hexdigest(),
    )


def _authorization_bytes(authorization: LiveEvalAuthorization) -> bytes:
    value = {
        "schema_version": "seven-lens.p3f.live-auth.v3",
        "authorization_id": authorization.authorization_id,
        "split_hash": authorization.split_hash,
        "case_ids": list(authorization.case_ids),
        "request_cap": authorization.request_cap,
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
        "stop_on_first_error": authorization.stop_on_first_error,
        "config_hash": authorization.config_hash,
    }
    return json.dumps(value, separators=(",", ":"), sort_keys=True).encode()


def _response(case, decision: ExpectedDecision) -> bytes:
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


def _execute(cases, authorization, executor, **changes):
    values = {
        "corpus_root": FIXTURES,
        "authorization": authorization,
        "trusted_grant": _grant(authorization),
        "supplied_grant": GRANT,
        "executor": executor,
        "now": datetime(2026, 8, 24, tzinfo=UTC),
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
        _execute(cases, auth, opaque)  # type: ignore[arg-type]
    assert opaque.attempts == []


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
            "route",
            "schema_version",
        }
        assert not forbidden.intersection(payload)
        assert not forbidden.intersection(payload["production_contract"])

    metrics = recompute_live_metrics(run, authorization=auth, cases=route, answers=answers)
    assert metrics["request_count"] == 260
    assert metrics["pre_network_reject_count"] == 130
    assert cast(dict[str, object], metrics["valid_primary"])["numerator"] == 260
    assert cast(dict[str, object], metrics["invalid_ambiguous_recall"])["numerator"] == 130
    assert metrics["real_provider_evidence"] is False


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

    slow = ScriptedSingleAttemptExecutor((_response(cases[0], ExpectedDecision.ACCEPT),))
    ticks = iter((0, 46_000_000_000, 46_000_000_000))
    with pytest.raises(LiveEvalExecutionError) as timeout:
        _execute(
            (cases[0],),
            _authorization([cases[0].case_id]),
            slow,
            monotonic_ns=lambda: next(ticks),
        )
    assert timeout.value.partial_run.records[0].outcome == "FAILED"


def test_authorization_cap_has_no_slack_and_plan_exposes_trust_match() -> None:
    cases, _ = _selected()
    with pytest.raises(LiveEvalAuthorizationError, match="safety policy"):
        _authorization([case.case_id for case in cases], request_cap=4)
    auth = _authorization([case.case_id for case in cases])
    plan = live_plan_summary(auth, auth.config_hash, corpus_root=FIXTURES)
    assert plan["trusted_config_match"] is True
    assert plan["automatic_retries"] == 0
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
    tampered_config = agnes_25_flash_config()
    object.__setattr__(tampered_config, "model_id", "agnes-2.5-flash-revision")
    monkeypatch.setattr(provider_eval_module, "agnes_25_flash_config", lambda: tampered_config)
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
        b'"schema_version":"seven-lens.p3f.live-auth.v3"',
        b'"schema_version":"seven-lens.p3f.live-auth.v3",'
        b'"schema_version":"seven-lens.p3f.live-auth.v3"',
        1,
    )
    with pytest.raises(LiveEvalAuthorizationError, match="strict JSON"):
        LiveEvalAuthorization.from_json(duplicated)


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
        b'"schema_version":"seven-lens.p3f.live-evidence.v1"',
        b'"schema_version":"seven-lens.p3f.live-evidence.v1",'
        b'"schema_version":"seven-lens.p3f.live-evidence.v1"',
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
