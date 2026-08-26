from __future__ import annotations

import json
import shutil
from collections import Counter, defaultdict
from dataclasses import replace
from pathlib import Path
from typing import cast

import pytest

from seven_lens.evals import (
    AntiContaminationError,
    CorpusIntegrityError,
    EvalCase,
    EvalFamily,
    EvalSplit,
    load_eval_corpus,
)
from seven_lens.evals.fixture_factory import create_response_contract_remediation_split, rebuild
from seven_lens.evals.runner import (
    _semantic_fingerprint,
    run_and_verify_frozen,
    run_final_offline_evaluation,
    validate_offline_corpus,
)

FIXTURES = Path(__file__).parent / "fixtures" / "p3f_evals_v12"
SPLIT_HASH = "054f09c773c903e2090a84cee2103688e2cd85949eed513a66006be6e0e23efb"
REPORT_HASH = "b6792a8865d7f22f28b98119d96677dd8d1abe381d5e5ca88275192e710f011c"


def test_frozen_corpus_counts_hashes_and_route_denominators() -> None:
    corpus = load_eval_corpus(FIXTURES)
    cases, answers = corpus.load_final_evaluation()

    assert corpus.split_manifest.split_hash == SPLIT_HASH
    assert len(cases) == 616
    assert len(answers) == 616
    assert len({case.case_id for case in cases}) == 616
    assert len({case.fixture_hash for case in cases}) == 616
    assert Counter(case.family for case in cases) == {
        EvalFamily.SAFETY: 130,
        EvalFamily.SEMANTIC_TRACE: 24,
        EvalFamily.MEMORY: 72,
        EvalFamily.ROUTE: 390,
    }
    for family in EvalFamily:
        payloads = [
            json.dumps(dict(case.payload), sort_keys=True, separators=(",", ":"))
            for case in cases
            if case.family is family
        ]
        assert len(payloads) == len(set(payloads))

    routes: dict[str, list[EvalCase]] = defaultdict(list)
    for case in cases:
        if case.family is EvalFamily.ROUTE:
            routes[f"{case.stage}/{case.role}"].append(case)
    assert len(routes) == 13
    for route_cases in routes.values():
        validity = Counter(answers[case.case_id].validity.value for case in route_cases)
        modes = Counter(case.mode.value for case in route_cases)
        assert validity["valid"] == 20
        assert validity["invalid"] + validity["ambiguous"] == 10
        assert modes == {"normal": 15, "emergency": 15}
        assert len({_semantic_fingerprint(case) for case in route_cases}) == 30
        valid_cases = route_cases[:20]
        assert len({_semantic_fingerprint(case) for case in valid_cases}) == 20
        invalid_cases = route_cases[20:]
        assert len({_semantic_fingerprint(case) for case in invalid_cases}) == 10


def test_response_contract_remediation_split_is_hash_closed() -> None:
    corpus = load_eval_corpus(FIXTURES)
    cases, answers = corpus.load_final_evaluation()

    assert corpus.split_manifest.split_version == "p3f-synthetic-v12"
    assert corpus.split_manifest.split_hash == SPLIT_HASH
    assert len(cases) == len(answers) == 616
    assert all(case.case_id.startswith("p3f.v12.") for case in cases)
    assert (
        run_and_verify_frozen(
            FIXTURES,
            FIXTURES / "reports" / "offline-scripted-v12.json",
        ).report_hash
        == REPORT_HASH
    )


def test_response_contract_remediation_generator_requires_a_new_destination(tmp_path: Path) -> None:
    destination = tmp_path / "p3f_evals_v12"
    split_hash, report_hash = create_response_contract_remediation_split(
        destination,
        split_version="p3f-synthetic-v12",
    )

    assert split_hash == SPLIT_HASH
    assert report_hash == REPORT_HASH
    with pytest.raises(ValueError, match="must not already exist"):
        create_response_contract_remediation_split(
            destination,
            split_version="p3f-synthetic-v12",
        )


def test_tuning_api_cannot_load_held_out_expected_outputs(monkeypatch: pytest.MonkeyPatch) -> None:
    corpus = load_eval_corpus(FIXTURES)
    opened: list[Path] = []
    original = Path.read_text

    def observed_read_text(path: Path, *args: object, **kwargs: object) -> str:
        opened.append(path)
        return original(path, *args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(Path, "read_text", observed_read_text)
    with pytest.raises(AntiContaminationError, match="sealed from tuning"):
        corpus.load_for_tuning(EvalSplit.HELD_OUT)

    assert not any(
        path.name == "answers.json" and path.parent.name == "held_out" for path in opened
    )
    public = corpus.load_public_cases(EvalSplit.HELD_OUT)
    assert len(public.cases) == 390
    assert all(not hasattr(case, "validity") for case in public.cases)
    assert b'"decision"' not in (FIXTURES / "held_out" / "cases.json").read_bytes()
    assert b'"validity"' not in (FIXTURES / "held_out" / "cases.json").read_bytes()


def test_duplicate_canonical_payload_alias_is_rejected_even_with_distinct_case_id() -> None:
    cases, answers = load_eval_corpus(FIXTURES).load_final_evaluation()
    traces = [case for case in cases if case.family is EvalFamily.SEMANTIC_TRACE]
    forged = replace(traces[2], payload=traces[0].payload)
    changed = tuple(forged if case.case_id == forged.case_id else case for case in cases)

    with pytest.raises(ValueError, match="semantic duplicate aliases"):
        validate_offline_corpus(changed, answers)


def test_trace_source_identity_cannot_disguise_a_duplicate_graph() -> None:
    cases, answers = load_eval_corpus(FIXTURES).load_final_evaluation()
    traces = [case for case in cases if case.family is EvalFamily.SEMANTIC_TRACE]
    disguised = replace(
        traces[2],
        payload=traces[2].payload.__class__(
            {
                **dict(traces[2].payload),
                "steps": traces[0].payload["steps"],
                "source_variant": "different-source-identity",
            }
        ),
    )
    changed = tuple(disguised if case.case_id == disguised.case_id else case for case in cases)

    with pytest.raises(ValueError, match="semantic duplicate aliases"):
        validate_offline_corpus(changed, answers)


def test_manifest_tamper_and_symlink_root_fail_closed(tmp_path: Path) -> None:
    copied = tmp_path / "corpus"
    shutil.copytree(FIXTURES, copied)
    answer_path = copied / "held_out" / "answers.json"
    value = json.loads(answer_path.read_text(encoding="utf-8"))
    original_decision = value["answers"][0]["decision"]
    value["answers"][0]["decision"] = "REJECT" if original_decision != "REJECT" else "ACCEPT"
    answer_path.write_text(json.dumps(value), encoding="utf-8")

    corpus = load_eval_corpus(copied)
    with pytest.raises(ValueError, match="answer manifest hash mismatch"):
        corpus.load_final_evaluation()

    linked = tmp_path / "linked"
    linked.symlink_to(FIXTURES, target_is_directory=True)
    with pytest.raises(CorpusIntegrityError, match="real directory"):
        load_eval_corpus(linked)


def test_duplicate_json_key_and_nested_symlink_fail_closed(tmp_path: Path) -> None:
    duplicated = tmp_path / "duplicated"
    shutil.copytree(FIXTURES, duplicated)
    held_out_cases = duplicated / "held_out" / "cases.json"
    raw = held_out_cases.read_text(encoding="utf-8")
    marker = '"manifest_id":"p3f-v12-held_out-cases-v1"'
    held_out_cases.write_text(raw.replace(marker, f"{marker},{marker}", 1), encoding="utf-8")
    with pytest.raises(CorpusIntegrityError, match="strict UTF-8 JSON"):
        load_eval_corpus(duplicated)

    nested = tmp_path / "nested"
    shutil.copytree(FIXTURES, nested)
    shutil.rmtree(nested / "held_out")
    (nested / "held_out").symlink_to(FIXTURES / "held_out", target_is_directory=True)
    with pytest.raises(CorpusIntegrityError, match="safe regular file"):
        load_eval_corpus(nested)


def test_fixture_factory_rejects_duplicate_json_keys_before_refreeze(tmp_path: Path) -> None:
    copied = tmp_path / "factory-duplicate"
    shutil.copytree(FIXTURES, copied)
    cases_path = copied / "held_out" / "cases.json"
    raw = cases_path.read_bytes().replace(
        b'"schema_version":"seven-lens.p3f.eval-cases.v1"',
        b'"schema_version":"seven-lens.p3f.eval-cases.v1",'
        b'"schema_version":"seven-lens.p3f.eval-cases.v1"',
        1,
    )
    cases_path.write_bytes(raw)

    with pytest.raises(ValueError, match="strict UTF-8 JSON"):
        rebuild(copied)


def test_offline_report_recomputes_all_metrics_and_matches_frozen_bytes() -> None:
    corpus = load_eval_corpus(FIXTURES)
    report = run_final_offline_evaluation(corpus)
    metrics = report.wire["metrics"]
    assert type(metrics) is dict
    metrics_obj = cast(dict[str, object], metrics)

    assert report.report_hash == REPORT_HASH
    assert report.wire["offline_passed"] is True
    assert report.wire["execution"] == {
        "kind": "OFFLINE_SCRIPTED",
        "provider_requests": 0,
        "real_provider_evidence": "PENDING_EXPLICIT_AUTHORIZATION",
        "automatic_retries": 0,
    }
    assert _object(metrics_obj, "accepted_safety_violations")["numerator"] == 0
    assert _object(metrics_obj, "accepted_schema_integrity_citation_lineage")["numerator"] == 308
    assert _object(metrics_obj, "scripted_record_replay_hash")["numerator"] == 24
    assert _object(metrics_obj, "graph_trace_round_parity")["numerator"] == 24
    assert _object(metrics_obj, "invalid_ambiguous_fail_closed_recall")["numerator"] == 308
    assert _object(metrics_obj, "live_model_quality")["status"] == "NOT_RUN"
    latency = _object(metrics_obj, "latency")
    assert _object(latency, "normal")["status"] == "NOT_APPLICABLE_OFFLINE"
    assert _object(latency, "emergency")["status"] == "NOT_APPLICABLE_OFFLINE"
    counts = report.wire["counts"]
    assert type(counts) is dict
    assert counts["unique_canonical_payloads"] == {
        "safety": 130,
        "semantic_trace": 24,
        "memory": 72,
        "route": 390,
    }

    frozen = FIXTURES / "reports" / "offline-scripted-v12.json"
    assert run_and_verify_frozen(FIXTURES, frozen).report_hash == REPORT_HASH


def test_frozen_report_byte_mutation_is_rejected(tmp_path: Path) -> None:
    changed = tmp_path / "report.json"
    changed.write_bytes((FIXTURES / "reports" / "offline-scripted-v12.json").read_bytes() + b" ")

    with pytest.raises(ValueError, match="does not match frozen report bytes"):
        run_and_verify_frozen(FIXTURES, changed)


def _object(container: dict[str, object], key: str) -> dict[str, object]:
    value = container[key]
    assert type(value) is dict
    return cast(dict[str, object], value)
