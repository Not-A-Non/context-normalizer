from __future__ import annotations

import hashlib
import re
from dataclasses import asdict, dataclass
from functools import lru_cache
from typing import Iterable

from .config import Rule


RULESET_SCHEMA = 1
PROTECTED_PATTERNS = (
    r"```[\s\S]*?```",
    r"~~~[\s\S]*?~~~",
    r"`[^`\r\n]+`",
    r'"(?:\\.|[^"\\])*"',
    r"(?<![A-Za-z0-9_])'(?:\\.|[^'\\\r\n])*'(?![A-Za-z0-9_])",
    r"https?://[^\s<>]+",
    r"\b[A-Za-z]:\\[^\r\n<>|?*]+?(?=\s|$)",
    r"\b[0-9a-fA-F]{40,64}\b",
)
_PROTECTED_RE = re.compile("|".join(f"(?:{part})" for part in PROTECTED_PATTERNS))
_SENTINEL = ""
_SENTINEL_END = ""
NEGATION_RE = re.compile(
    r"\b(?:no|not|never|without|cannot|can't|won't|don't|doesn't|didn't|isn't|aren't)\b",
    re.IGNORECASE,
)
NUMBER_RE = re.compile(r"\d+(?:[.,]\d+)*")


@dataclass(frozen=True)
class AppliedRule:
    source: str
    normalized: str
    count: int


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def protect(text: str) -> tuple[str, list[str], str]:
    # Grow the marker until it cannot occur in the input, so text that already
    # contains sentinel codepoints can never alias a stash token.
    marker = _SENTINEL
    while marker in text:
        marker += _SENTINEL
    spans: list[str] = []

    def stash(match: re.Match[str]) -> str:
        token = f"{marker}{len(spans)}{_SENTINEL_END}"
        spans.append(match.group(0))
        return token

    return _PROTECTED_RE.sub(stash, text), spans, marker


def restore(text: str, spans: Iterable[str], marker: str) -> str:
    restored = text
    for index, value in enumerate(spans):
        restored = restored.replace(f"{marker}{index}{_SENTINEL_END}", value)
    return restored


def case_like(source: str, normalized: str) -> str:
    if len(source) > 1 and source.isupper():
        return normalized.upper()
    if source != source.lower() and source == source.capitalize():
        return normalized.capitalize() if normalized.islower() else normalized
    return normalized


def _word_char(value: str) -> bool:
    return value == "_" or value.isalnum()


@lru_cache(maxsize=128)
def _vocabulary_pattern(sources: tuple[str, ...]) -> re.Pattern[str]:
    """Compile a longest-first vocabulary alternation once per rule set."""
    alternatives = "|".join(re.escape(source) for source in sources)
    return re.compile(rf"(?:{alternatives})", re.IGNORECASE)


def _longest_first(sources: Iterable[str]) -> tuple[str, ...]:
    return tuple(sorted(sources, key=len, reverse=True))


def _match_has_boundary_or_adjacent_repeat(text: str, start: int, end: int) -> bool:
    folded = text[start:end].casefold()
    length = end - start
    # Extend to the maximal run of adjacent repeats, then require word
    # boundaries at the run edges so a token tiling inside a longer word
    # (for example "na" inside "banana") never matches.
    run_start = start
    while (
        run_start >= length
        and text[run_start - length : run_start].casefold() == folded
    ):
        run_start -= length
    run_end = end
    while (
        run_end + length <= len(text)
        and text[run_end : run_end + length].casefold() == folded
    ):
        run_end += length
    before_boundary = run_start == 0 or not _word_char(text[run_start - 1])
    after_boundary = run_end == len(text) or not _word_char(text[run_end])
    return before_boundary and after_boundary


def translate_reversible_text(
    text: str,
    rules: Iterable[Rule],
    *,
    reverse: bool = False,
) -> str:
    """Translate a validated reversible rule set without protecting code spans."""

    rule_list = list(rules)
    pairs = [
        (
            rule.normalized if reverse else rule.source,
            rule.source if reverse else rule.normalized,
        )
        for rule in rule_list
    ]
    pairs = [(source, normalized) for source, normalized in pairs if source]
    if not pairs:
        return text
    by_source = {source.casefold(): normalized for source, normalized in pairs}
    pattern = _vocabulary_pattern(_longest_first(source for source, _ in pairs))

    def normalize_match(match: re.Match[str]) -> str:
        if not _match_has_boundary_or_adjacent_repeat(
            text,
            match.start(),
            match.end(),
        ):
            return match.group(0)
        normalized = by_source.get(match.group(0).casefold())
        if normalized is None:
            return match.group(0)
        return case_like(match.group(0), normalized)

    return pattern.sub(normalize_match, text)


def invariants(text: str) -> dict[str, list[str]]:
    _, protected, _ = protect(text)
    return {
        "protected_spans": protected,
        "numbers": NUMBER_RE.findall(text),
        "negations": [value.lower() for value in NEGATION_RE.findall(text)],
    }


def normalize_text(
    text: str,
    rules: Iterable[Rule],
    *,
    context: str | None,
) -> tuple[str, dict[str, object]]:
    protected_text, protected, marker = protect(text)
    rewritten = protected_text
    applied: list[AppliedRule] = []
    rule_list = list(rules)
    by_source = {rule.source.casefold(): rule for rule in rule_list}
    counts = {rule.source.casefold(): 0 for rule in rule_list}
    if rule_list:
        pattern = _vocabulary_pattern(_longest_first(rule.source for rule in rule_list))

        def normalize_match(match: re.Match[str]) -> str:
            if not _match_has_boundary_or_adjacent_repeat(
                rewritten,
                match.start(),
                match.end(),
            ):
                return match.group(0)
            rule = by_source.get(match.group(0).casefold())
            if rule is None:
                return match.group(0)
            counts[rule.source.casefold()] += 1
            return case_like(match.group(0), rule.normalized)

        rewritten = pattern.sub(normalize_match, rewritten)
    for rule in rule_list:
        count = counts[rule.source.casefold()]
        if count:
            applied.append(AppliedRule(rule.source, rule.normalized, count))

    body = restore(rewritten, protected, marker)
    normalized = (
        f"[Project context]\n{context.strip()}\n\n[Request]\n{body}"
        if context
        else body
    )

    original_invariants = invariants(text)
    normalized_invariants = invariants(body)
    checks = {
        name: original_invariants[name] == normalized_invariants[name]
        for name in original_invariants
    }
    if not all(checks.values()):
        raise ValueError(f"semantic invariant failed: {checks}")

    audit: dict[str, object] = {
        "schema_version": RULESET_SCHEMA,
        "original_sha256": sha256_text(text),
        "normalized_sha256": sha256_text(normalized),
        "context_added": context is not None,
        "protected_span_count": len(protected),
        "invariant_checks": checks,
        "normalizations": [asdict(item) for item in applied],
    }
    return normalized, audit
