"""Shared fail-closed fact-token and injection closure for reflection and memory."""

from __future__ import annotations

import re

from seven_lens.memory.contracts import FactKind, FactRef

_NUMBER_TOKEN = re.compile(r"(?<![A-Za-z0-9_.-])-?(?:0|[1-9][0-9]*)(?:\.[0-9]+)?(?![A-Za-z0-9_.-])")
_SCIENTIFIC_TOKEN = re.compile(
    r"(?<![A-Za-z0-9_.-])-?(?:0|[1-9][0-9]*)(?:\.[0-9]+)?[eE][+-]?[0-9]+"
    r"(?![A-Za-z0-9_.-])"
)
_DATE_TOKEN = re.compile(r"(?<![0-9])[0-9]{4}-[0-9]{2}-[0-9]{2}(?![0-9])")
_UPPER_TOKEN = re.compile(r"(?<![A-Za-z0-9.\-])[A-Z][A-Z0-9.\-]{0,9}(?![A-Za-z0-9.\-])")
_INSTRUCTION_POLICY = (
    re.compile(r"\b(?:ignore|disregard|override|bypass|forget)\s+(?:all\s+)?(?:prior|previous)\b"),
    re.compile(
        r"\b(?:ignore|disregard|override|bypass|forget)\b.{0,80}\b(?:prior|previous|all|the)?\s*(?:instruction|rule|policy|constraint)s?\b"
    ),
    re.compile(
        r"\b(?:execute|place|submit|send|cancel|modify)\b.{0,48}\b(?:a\s+)?(?:trade|order)s?\b"
    ),
    re.compile(
        r"\b(?:read|reveal|expose|print|return)\b.{0,48}\b(?:secret|credential|api\s*key|authorization)s?\b"
    ),
    re.compile(r"\b(?:call|invoke|use|run)\b.{0,48}\b(?:tool|shell|command)s?\b"),
    re.compile(r"\b(?:set|make|mark|promote)\b.{0,32}\bcurrent\b"),
    re.compile(r"\bsystem\s+prompt\b"),
)


def validate_text_fact_closure(
    texts: tuple[str, ...],
    *,
    available_facts: dict[str, FactRef],
    cited_fact_ids: tuple[str, ...],
    risk_reason_values: tuple[str, ...] | None = None,
) -> None:
    """Require every factual token to be both typed and cited by exact fact id."""
    if type(texts) is not tuple or any(type(item) is not str for item in texts):
        raise ValueError("fact closure requires an exact text tuple")
    if (
        type(available_facts) is not dict
        or any(
            type(key) is not str or type(fact) is not FactRef or key != fact.fact_id
            for key, fact in available_facts.items()
        )
        or type(cited_fact_ids) is not tuple
        or any(type(item) is not str for item in cited_fact_ids)
    ):
        raise ValueError("fact closure requires exact keyed fact contracts")
    if risk_reason_values is not None and (
        type(risk_reason_values) is not tuple
        or any(type(item) is not str for item in risk_reason_values)
    ):
        raise ValueError("risk reason values must be an exact text tuple")
    if not set(cited_fact_ids).issubset(available_facts):
        raise ValueError("fact closure contains a foreign fact id")
    evidence = {fact_id: available_facts[fact_id] for fact_id in cited_fact_ids}
    values_by_kind = {
        kind: {fact.value for fact in evidence.values() if fact.kind is kind} for kind in FactKind
    }
    text = "\n".join(texts)
    date_spans: list[tuple[int, int]] = []
    for match in _DATE_TOKEN.finditer(text):
        token = match.group(0)
        date_spans.append(match.span())
        if token not in values_by_kind[FactKind.DATE]:
            raise ValueError("fact closure found an unreferenced date")
    if _SCIENTIFIC_TOKEN.search(text) is not None:
        raise ValueError("fact closure found a non-canonical numeric token")
    for match in _NUMBER_TOKEN.finditer(text):
        if any(start <= match.start() and match.end() <= end for start, end in date_spans):
            continue
        token = match.group(0)
        if token not in values_by_kind[FactKind.NUMBER]:
            raise ValueError("fact closure found an unreferenced number")
    # Uppercase tokens are ambiguous by nature.  Treat every ticker-shaped token as a factual
    # symbol/reason claim and require a typed citation. This intentionally fails closed for an
    # invented ticker even when it does not appear anywhere in the approved source set.
    canonical_upper_values = values_by_kind[FactKind.SYMBOL] | values_by_kind[FactKind.RISK_REASON]
    if risk_reason_values is None:
        typed_upper_values = canonical_upper_values
    else:
        requested_reasons = set(risk_reason_values)
        if not requested_reasons.issubset(values_by_kind[FactKind.RISK_REASON]):
            raise ValueError("fact closure found a non-risk fact used as a risk reason")
        typed_upper_values = values_by_kind[FactKind.SYMBOL] | requested_reasons
    for token in _UPPER_TOKEN.findall(text):
        if token not in typed_upper_values:
            raise ValueError("fact closure found an unreferenced symbol or risk reason")
    # Canonical ticker and risk-reason facts are case sensitive.  A provider must not evade
    # closure by lower-casing an otherwise exact fact token.
    casefolded_text = text.casefold()
    for value in canonical_upper_values:
        if value not in text and re.search(
            rf"(?<![A-Za-z0-9.\-]){re.escape(value.casefold())}(?![A-Za-z0-9.\-])",
            casefolded_text,
        ):
            raise ValueError("fact closure found a non-canonical symbol or risk reason")


def reject_instruction_like_text(texts: tuple[str, ...]) -> None:
    if type(texts) is not tuple or any(type(item) is not str for item in texts):
        raise ValueError("instruction policy requires an exact text tuple")
    # Normalize punctuation/spacing before applying capability-oriented policy patterns.  This
    # protects the structured output fields rather than attempting to enumerate exact phrases.
    joined = re.sub(r"[^a-z0-9]+", " ", "\n".join(texts).casefold()).strip()
    if any(pattern.search(joined) for pattern in _INSTRUCTION_POLICY):
        raise ValueError("untrusted content contains an instruction-like phrase")
