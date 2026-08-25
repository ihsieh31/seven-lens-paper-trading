"""Deterministically rebuild the synthetic P3-F fixture hash closure.

This maintenance command is deliberately separate from the final evaluator.  It
may read sealed answers and must never be imported by prompt-tuning code.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import cast
from uuid import UUID

from seven_lens.analysis.model_audit import (
    ModelCallRole,
    ModelCallStage,
    derive_model_call_id,
)
from seven_lens.domain.json_values import JsonValue
from seven_lens.domain.value_objects import RunId
from seven_lens.evals.models import canonical_bytes, content_hash
from seven_lens.evals.production_probes import probe_trace

_SPLITS = ("golden", "training", "dev", "held_out")
_TRACE_BASE = (
    ("ANALYST", "TECHNICAL_ANALYST", 0),
    ("ANALYST", "FUNDAMENTALS_ANALYST", 0),
    ("ANALYST", "NEWS_ANALYST", 0),
    ("ANALYST", "SENTIMENT_ANALYST", 0),
    ("INVESTMENT_DEBATE", "BULL_RESEARCHER", 1),
    ("INVESTMENT_DEBATE", "BEAR_RESEARCHER", 1),
    ("RESEARCH_MANAGER", "RESEARCH_MANAGER", 0),
    ("TRADER", "TRADER", 0),
    ("RISK_DEBATE", "AGGRESSIVE_RISK", 1),
    ("RISK_DEBATE", "CONSERVATIVE_RISK", 1),
    ("RISK_DEBATE", "NEUTRAL_RISK", 1),
    ("PORTFOLIO_MANAGER", "PORTFOLIO_MANAGER", 0),
    ("PORTFOLIO_MANAGER", "PORTFOLIO_MANAGER_RETRY", 0),
)


def rebuild(root: Path) -> None:
    manifests: dict[str, dict[str, str]] = {}
    assignments: list[dict[str, str]] = []
    for split in _SPLITS:
        case_path = root / split / "cases.json"
        answer_path = root / split / "answers.json"
        case_manifest = _object(_strict_json(case_path))
        answer_manifest = _object(_strict_json(answer_path))
        prior_answers = {item["case_id"]: item for item in _objects(answer_manifest["answers"])}
        cases: list[dict[str, JsonValue]] = []
        answers: list[dict[str, JsonValue]] = []
        for index, raw_case in enumerate(_objects(case_manifest["cases"])):
            case = dict(raw_case)
            prior = prior_answers[cast(str, case["case_id"])]
            validity = cast(str, case.pop("validity", prior.get("validity")))
            payload = dict(_object(case["payload"]))
            payload.pop("latency_ms", None)
            family = case["family"]
            if family == "semantic_trace":
                payload["source_variant"] = f"{split}.trace.{index:03d}"
                trace_ordinal = cast(int, payload["trace_ordinal"])
                payload["steps"] = _semantic_trace_steps(trace_ordinal)
            elif family == "memory":
                payload["fact_variant"] = f"{split}.memory.{index:03d}"
            elif family == "route":
                case_id = cast(str, case["case_id"])
                route_index = int(case_id.rsplit(".", maxsplit=1)[1])
                expected_round = cast(int, payload["expected_round_number"])
                fact = f"SYN{route_index:02d}"
                payload["fact_variant"] = fact
                payload["claim"] = _route_claim(
                    case_id=case_id,
                    stage=cast(str, case["stage"]),
                    role=cast(str, case["role"]),
                    expected_round=expected_round,
                    fact=fact,
                    route_index=route_index,
                )
            case["payload"] = cast(JsonValue, payload)
            case.pop("fixture_hash", None)
            case["fixture_hash"] = content_hash(cast(JsonValue, case))
            cases.append(cast(dict[str, JsonValue], case))

            trace_hash: str | None = None
            if family == "semantic_trace":
                accepted, trace_hash = probe_trace(
                    cast(list[dict[str, JsonValue]], payload["steps"]),
                    cast(int, payload["trace_ordinal"]),
                    cast(str, payload["source_variant"]),
                )
                if not accepted:
                    raise RuntimeError("semantic trace fixture is not production-replay safe")
            answer: dict[str, JsonValue] = {
                "case_id": cast(str, case["case_id"]),
                "validity": validity,
                "decision": cast(str, prior["decision"]),
                "trace_hash": trace_hash,
            }
            answer["answer_hash"] = content_hash(cast(JsonValue, answer))
            answers.append(answer)
            assignments.append(
                {
                    "case_id": cast(str, case["case_id"]),
                    "split": split,
                    "fixture_hash": cast(str, case["fixture_hash"]),
                }
            )

        new_cases: dict[str, JsonValue] = {
            "schema_version": "seven-lens.p3f.eval-cases.v1",
            "manifest_id": cast(str, case_manifest["manifest_id"]),
            "split_version": "p3f-synthetic-v3",
            "split": split,
            "cases": cast(JsonValue, cases),
        }
        new_cases["content_hash"] = content_hash(cast(JsonValue, new_cases))
        new_answers: dict[str, JsonValue] = {
            "schema_version": "seven-lens.p3f.eval-answers.v1",
            "manifest_id": cast(str, answer_manifest["manifest_id"]),
            "split_version": "p3f-synthetic-v3",
            "split": split,
            "answers": cast(JsonValue, answers),
        }
        new_answers["content_hash"] = content_hash(cast(JsonValue, new_answers))
        case_path.write_bytes(canonical_bytes(cast(JsonValue, new_cases)) + b"\n")
        answer_path.write_bytes(canonical_bytes(cast(JsonValue, new_answers)) + b"\n")
        manifests[split] = {
            "cases_hash": cast(str, new_cases["content_hash"]),
            "answers_hash": cast(str, new_answers["content_hash"]),
        }

    split_manifest: dict[str, JsonValue] = {
        "schema_version": "seven-lens.p3f.eval-splits.v1",
        "split_version": "p3f-synthetic-v3",
        "manifests": cast(JsonValue, manifests),
        "case_assignments": cast(JsonValue, assignments),
    }
    split_manifest["split_hash"] = content_hash(cast(JsonValue, split_manifest))
    (root / "split_manifest.json").write_bytes(
        canonical_bytes(cast(JsonValue, split_manifest)) + b"\n"
    )

    # The frozen artifact is generated only after every case/answer/assignment
    # hash has been rebuilt, so it closes over the exact final corpus bytes.
    from seven_lens.evals.corpus import load_eval_corpus
    from seven_lens.evals.runner import run_final_offline_evaluation

    report = run_final_offline_evaluation(load_eval_corpus(root))
    report_path = root / "reports" / "offline-scripted-v3.json"
    report_path.write_bytes(report.to_bytes())


def _object(value: object) -> dict[str, object]:
    if type(value) is not dict:
        raise ValueError("fixture factory expected an exact object")
    return cast(dict[str, object], value)


def _strict_json(path: Path) -> object:
    try:
        return json.loads(
            path.read_bytes().decode("utf-8"),
            object_pairs_hook=_reject_duplicate_pairs,
            parse_constant=_reject_constant,
        )
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError) as error:
        raise ValueError("fixture factory requires strict UTF-8 JSON") from error


def _reject_duplicate_pairs(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def _reject_constant(value: str) -> object:
    raise ValueError(f"non-finite JSON constant {value!r} is forbidden")


def _objects(value: object) -> list[dict[str, object]]:
    if type(value) is not list or any(type(item) is not dict for item in value):
        raise ValueError("fixture factory expected a list of exact objects")
    return cast(list[dict[str, object]], value)


def _semantic_trace_steps(ordinal: int) -> list[dict[str, JsonValue]]:
    """Build 24 distinct canonical prefix/resume traces, never ordinal aliases."""

    if not 0 <= ordinal < 24:
        raise ValueError("semantic trace ordinal is invalid")
    round_number = 1 if ordinal <= 12 else 2
    full: list[dict[str, JsonValue]] = [
        {
            "stage": stage,
            "role": role,
            "round_number": (
                round_number if stage in {"INVESTMENT_DEBATE", "RISK_DEBATE"} else base_round
            ),
        }
        for stage, role, base_round in _TRACE_BASE
    ]
    if ordinal <= 12:
        return full[: ordinal + 1]
    if ordinal <= 21:
        return full[: ordinal - 8]
    return full[ordinal - 21 :]


_ROLE_MAP = {
    "TECHNICAL_ANALYST": ModelCallRole.TECHNICAL,
    "FUNDAMENTALS_ANALYST": ModelCallRole.FUNDAMENTALS,
    "NEWS_ANALYST": ModelCallRole.NEWS,
    "SENTIMENT_ANALYST": ModelCallRole.SENTIMENT,
    "BULL_RESEARCHER": ModelCallRole.BULL,
    "BEAR_RESEARCHER": ModelCallRole.BEAR,
    "RESEARCH_MANAGER": ModelCallRole.RESEARCH_MANAGER,
    "TRADER": ModelCallRole.TRADER,
    "AGGRESSIVE_RISK": ModelCallRole.AGGRESSIVE,
    "CONSERVATIVE_RISK": ModelCallRole.CONSERVATIVE,
    "NEUTRAL_RISK": ModelCallRole.NEUTRAL,
    "PORTFOLIO_MANAGER": ModelCallRole.PORTFOLIO_MANAGER,
    "PORTFOLIO_MANAGER_RETRY": ModelCallRole.PORTFOLIO_MANAGER,
}


def _route_claim(
    *,
    case_id: str,
    stage: str,
    role: str,
    expected_round: int,
    fact: str,
    route_index: int,
) -> dict[str, JsonValue]:
    """Build distinct production claim material, then one of ten semantic violations."""

    input_id = _route_id("input", case_id)
    context_id = _route_id("context", case_id)
    run_id = _route_id("run", case_id)
    route_ordinal = 1
    call_id = derive_model_call_id(
        input_id,
        context_id,
        ModelCallStage(stage),
        _ROLE_MAP[role],
        expected_round,
        route_ordinal,
    )
    claim: dict[str, JsonValue] = {
        "call_id": str(call_id),
        "run_id": str(run_id),
        "input_id": str(input_id),
        "context_id": str(context_id),
        "round_number": expected_round,
        "provider": "AGNES",
        "model": "agnes-2.5-flash",
        "api_flavor": "CHAT_COMPLETIONS",
        "endpoint_policy_id": "p3e-agnes-2.5-flash-only-v1",
        "route_ordinal": route_ordinal,
        "prompt_template_hash": _route_hash("prompt-template", case_id),
        "request_envelope_hash": _route_hash("request-envelope", case_id),
        "reasoning_requested": "MAX",
        "citation_text": fact,
    }
    if route_index < 20:
        return claim
    violation = route_index - 20
    if violation == 0:
        claim["round_number"] = 3 if expected_round == 0 else 0
    elif violation == 1:
        claim["model"] = "foreign-model"
    elif violation == 2:
        claim["route_ordinal"] = 2
    elif violation == 3:
        claim["prompt_template_hash"] = "g" * 64
    elif violation == 4:
        claim["citation_text"] = "ZZZZ"
    elif violation == 5:
        claim["call_id"] = str(_route_id("wrong-call", case_id))
    elif violation == 6:
        claim["run_id"] = "not-a-run-uuid"
    elif violation == 7:
        claim["input_id"] = "not-an-input-uuid"
    elif violation == 8:
        claim["endpoint_policy_id"] = "foreign-endpoint-policy"
    elif violation == 9:
        claim["request_envelope_hash"] = "z" * 64
    else:
        raise ValueError("route index must be within the frozen 30-case route batch")
    return claim


def _route_id(domain: str, case_id: str) -> RunId:
    digest = hashlib.sha256(f"seven-lens.p3f.{domain}\x00{case_id}".encode()).digest()
    return RunId(UUID(bytes=digest[:16], version=4))


def _route_hash(domain: str, case_id: str) -> str:
    return hashlib.sha256(f"seven-lens.p3f.{domain}\x00{case_id}".encode()).hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--fixtures", type=Path, required=True)
    args = parser.parse_args()
    rebuild(args.fixtures)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
