"""Hash-closed corpus loading with a hard tuning/held-out boundary."""

from __future__ import annotations

import json
import os
import stat
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Final, cast

from seven_lens.domain.json_values import JsonValue
from seven_lens.evals.models import (
    AnswerManifest,
    CaseManifest,
    EvalCase,
    EvalSplit,
    ExpectedAnswer,
    content_hash,
)

SPLIT_VERSION: Final = "p3f-synthetic-v3"
P3F_RESPONSE_CONTRACT_REMEDIATION_SPLIT_VERSION: Final = "p3f-synthetic-v4"
P3F_TRANSPORT_TIMEOUT_REMEDIATION_SPLIT_VERSION: Final = "p3f-synthetic-v5"
P3F_TRANSPORT_DEADLINE_REMEDIATION_SPLIT_VERSION: Final = "p3f-synthetic-v6"
P3F_TRANSPORT_RECOVERY_SPLIT_VERSION: Final = "p3f-synthetic-v7"
P3F_TRANSPORT_RETEST_SPLIT_VERSION: Final = "p3f-synthetic-v8"
P3F_TRANSPORT_RETEST_V2_SPLIT_VERSION: Final = "p3f-synthetic-v9"
P3F_RETRY_GATE_REDESIGN_SPLIT_VERSION: Final = "p3f-synthetic-v10"
P3F_FENCE_WIRE_REMEDIATION_SPLIT_VERSION: Final = "p3f-synthetic-v11"
P3F_SHAPE_DIAGNOSTICS_SPLIT_VERSION: Final = "p3f-synthetic-v12"
P3F_ANALYSIS_ROUTE_SPLIT_VERSION: Final = "p3f-synthetic-v14"
P3F_CURRENT_ROUTE_LIVE_SPLIT_VERSION: Final = "p3f-synthetic-v14"
SUPPORTED_SPLIT_VERSIONS: Final = frozenset(
    {
        SPLIT_VERSION,
        P3F_RESPONSE_CONTRACT_REMEDIATION_SPLIT_VERSION,
        P3F_TRANSPORT_TIMEOUT_REMEDIATION_SPLIT_VERSION,
        P3F_TRANSPORT_DEADLINE_REMEDIATION_SPLIT_VERSION,
        P3F_TRANSPORT_RECOVERY_SPLIT_VERSION,
        P3F_TRANSPORT_RETEST_SPLIT_VERSION,
        P3F_TRANSPORT_RETEST_V2_SPLIT_VERSION,
        P3F_RETRY_GATE_REDESIGN_SPLIT_VERSION,
        P3F_FENCE_WIRE_REMEDIATION_SPLIT_VERSION,
        P3F_SHAPE_DIAGNOSTICS_SPLIT_VERSION,
        P3F_ANALYSIS_ROUTE_SPLIT_VERSION,
        P3F_CURRENT_ROUTE_LIVE_SPLIT_VERSION,
    }
)
MAX_FIXTURE_BYTES: Final = 8 * 1024 * 1024
_SPLIT_FILENAME: Final = "split_manifest.json"


class CorpusIntegrityError(ValueError):
    """A frozen corpus byte, identity, split, or hash invariant failed."""


class AntiContaminationError(PermissionError):
    """A tuning path attempted to observe held-out expected output."""


@dataclass(frozen=True, slots=True)
class SplitReference:
    cases_hash: str
    answers_hash: str


@dataclass(frozen=True, slots=True)
class SplitManifest:
    split_version: str
    manifests: MappingProxyType[EvalSplit, SplitReference]
    case_assignments: MappingProxyType[str, tuple[EvalSplit, str]]
    split_hash: str


@dataclass(frozen=True, slots=True)
class EvalCorpus:
    root: Path
    split_manifest: SplitManifest

    def load_public_cases(self, split: EvalSplit) -> CaseManifest:
        """Load inputs only. This method never opens an answer file."""
        manifest = CaseManifest.from_wire(_read_json(self.root, split.value, "cases.json"))
        self._validate_case_manifest(split, manifest)
        return manifest

    def load_for_tuning(
        self, split: EvalSplit
    ) -> tuple[CaseManifest, MappingProxyType[str, ExpectedAnswer]]:
        """Load expected outputs only for non-held-out prompt/template work."""
        if split is EvalSplit.HELD_OUT:
            raise AntiContaminationError("held-out expected outputs are sealed from tuning")
        cases = self.load_public_cases(split)
        answers = self._load_answers(split)
        return cases, answers

    def load_final_evaluation(
        self,
    ) -> tuple[tuple[EvalCase, ...], MappingProxyType[str, ExpectedAnswer]]:
        """Unseal all answers only at the final, hash-verified evaluation boundary."""
        all_cases: list[EvalCase] = []
        all_answers: dict[str, ExpectedAnswer] = {}
        for split in EvalSplit:
            case_manifest = self.load_public_cases(split)
            answers = self._load_answers(split)
            all_cases.extend(case_manifest.cases)
            overlap = set(all_answers).intersection(answers)
            if overlap:
                raise CorpusIntegrityError("case IDs overlap across answer manifests")
            all_answers.update(answers)
        if set(all_answers) != {case.case_id for case in all_cases}:
            raise CorpusIntegrityError("final answer closure does not match all case IDs")
        return tuple(all_cases), MappingProxyType(all_answers)

    def _load_answers(self, split: EvalSplit) -> MappingProxyType[str, ExpectedAnswer]:
        manifest = AnswerManifest.from_wire(_read_json(self.root, split.value, "answers.json"))
        reference = self.split_manifest.manifests[split]
        if (
            manifest.split is not split
            or manifest.split_version != self.split_manifest.split_version
            or manifest.content_hash != reference.answers_hash
        ):
            raise CorpusIntegrityError("answer manifest does not match frozen split manifest")
        answers = {answer.case_id: answer for answer in manifest.answers}
        public_ids = {case.case_id for case in self.load_public_cases(split).cases}
        if set(answers) != public_ids:
            raise CorpusIntegrityError("answer manifest does not close over public case IDs")
        return MappingProxyType(answers)

    def _validate_case_manifest(self, split: EvalSplit, manifest: CaseManifest) -> None:
        reference = self.split_manifest.manifests[split]
        if (
            manifest.split is not split
            or manifest.split_version != self.split_manifest.split_version
            or manifest.content_hash != reference.cases_hash
        ):
            raise CorpusIntegrityError("case manifest does not match frozen split manifest")
        for case in manifest.cases:
            assigned = self.split_manifest.case_assignments.get(case.case_id)
            if assigned != (split, case.fixture_hash):
                raise CorpusIntegrityError("case assignment or fixture hash is not frozen")


def load_eval_corpus(root: Path) -> EvalCorpus:
    try:
        normalized = root.resolve(strict=True)
    except OSError:
        raise CorpusIntegrityError("eval corpus root does not exist") from None
    if root.is_symlink() or not normalized.is_dir():
        raise CorpusIntegrityError("eval corpus root must be a real directory")
    raw = _read_json(normalized, _SPLIT_FILENAME)
    if type(raw) is not dict:
        raise CorpusIntegrityError("split manifest must be an exact object")
    required = {
        "schema_version",
        "split_version",
        "manifests",
        "case_assignments",
        "split_hash",
    }
    if set(raw) != required or raw["schema_version"] != "seven-lens.p3f.eval-splits.v1":
        raise CorpusIntegrityError("split manifest schema is invalid")
    split_hash = raw["split_hash"]
    material = {key: value for key, value in raw.items() if key != "split_hash"}
    if type(split_hash) is not str or content_hash(cast(JsonValue, material)) != split_hash:
        raise CorpusIntegrityError("split manifest hash mismatch")
    split_version = raw["split_version"]
    if type(split_version) is not str or split_version not in SUPPORTED_SPLIT_VERSIONS:
        raise CorpusIntegrityError("split manifest version is invalid")
    if type(raw["manifests"]) is not dict:
        raise CorpusIntegrityError("split manifest version is invalid")
    refs: dict[EvalSplit, SplitReference] = {}
    manifests = cast(dict[str, object], raw["manifests"])
    if set(manifests) != {split.value for split in EvalSplit}:
        raise CorpusIntegrityError("split manifest references are incomplete")
    for split in EvalSplit:
        ref = manifests[split.value]
        if type(ref) is not dict or set(ref) != {"cases_hash", "answers_hash"}:
            raise CorpusIntegrityError("split manifest reference is invalid")
        refs[split] = SplitReference(
            cases_hash=cast(dict[str, str], ref)["cases_hash"],
            answers_hash=cast(dict[str, str], ref)["answers_hash"],
        )
    assignments_raw = raw["case_assignments"]
    if type(assignments_raw) is not list:
        raise CorpusIntegrityError("case assignments must be a list")
    assignments: dict[str, tuple[EvalSplit, str]] = {}
    for item in assignments_raw:
        if type(item) is not dict or set(item) != {"case_id", "split", "fixture_hash"}:
            raise CorpusIntegrityError("case assignment is invalid")
        assignment = cast(dict[str, str], item)
        try:
            split = EvalSplit(assignment["split"])
        except ValueError:
            raise CorpusIntegrityError("case assignment split is invalid") from None
        case_id = assignment["case_id"]
        if case_id in assignments:
            raise CorpusIntegrityError("case assignment IDs are duplicated")
        assignments[case_id] = (split, assignment["fixture_hash"])
    corpus = EvalCorpus(
        root=normalized,
        split_manifest=SplitManifest(
            split_version=split_version,
            manifests=MappingProxyType(refs),
            case_assignments=MappingProxyType(assignments),
            split_hash=split_hash,
        ),
    )
    seen: set[str] = set()
    for split in EvalSplit:
        manifest = corpus.load_public_cases(split)
        ids = {case.case_id for case in manifest.cases}
        if seen.intersection(ids):
            raise CorpusIntegrityError("case IDs overlap across splits")
        seen.update(ids)
    if seen != set(assignments):
        raise CorpusIntegrityError("split assignments do not close over cases")
    return corpus


def _read_json(root: Path, *parts: str) -> JsonValue:
    if not parts or any(
        type(part) is not str or not part or part in {".", ".."} or "/" in part or "\x00" in part
        for part in parts
    ):
        raise CorpusIntegrityError("eval fixture relative path is invalid")
    directory_flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
    file_flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    descriptors: list[int] = []
    try:
        current = os.open(root, directory_flags)
        descriptors.append(current)
        for part in parts[:-1]:
            current = os.open(part, directory_flags, dir_fd=current)
            descriptors.append(current)
        fixture = os.open(parts[-1], file_flags, dir_fd=current)
        descriptors.append(fixture)
        metadata = os.fstat(fixture)
        if not stat.S_ISREG(metadata.st_mode):
            raise CorpusIntegrityError("eval fixtures must be regular non-symlink files")
        if metadata.st_size > MAX_FIXTURE_BYTES:
            raise CorpusIntegrityError("eval fixture exceeds byte budget")
        chunks: list[bytes] = []
        total = 0
        while True:
            chunk = os.read(fixture, min(65_536, MAX_FIXTURE_BYTES + 1 - total))
            if not chunk:
                break
            chunks.append(chunk)
            total += len(chunk)
            if total > MAX_FIXTURE_BYTES:
                raise CorpusIntegrityError("eval fixture exceeds byte budget")
        raw = b"".join(chunks)
    except FileNotFoundError as error:
        raise CorpusIntegrityError("required eval fixture is missing") from error
    except OSError as error:
        raise CorpusIntegrityError("eval fixture path is not a safe regular file") from error
    finally:
        for descriptor in reversed(descriptors):
            os.close(descriptor)
    try:
        value = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=_reject_duplicate_pairs,
            parse_constant=_reject_constant,
        )
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError) as error:
        raise CorpusIntegrityError("eval fixture is not strict UTF-8 JSON") from error
    return cast(JsonValue, value)


def _reject_constant(value: str) -> object:
    raise ValueError(f"non-finite JSON constant {value!r} is forbidden")


def _reject_duplicate_pairs(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result
