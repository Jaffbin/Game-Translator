from __future__ import annotations

import csv
import re
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

from .models import TranslationEntry
from .placeholders import PlaceholderProtector


_PROTECTOR = PlaceholderProtector()


CJK_REGEX = re.compile(
    r"[\u4e00-\u9fff\u3040-\u30ff\uac00-\ud7af]"
)


MODEL_ARTIFACT_REGEXES = [
    re.compile(
        r"^(sure|okay|ok|yes|here is|here's|translation|translated|note|explanation)\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"^(Sure,?\s+)?(here is|here's)\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"^(好的|当然|以下是|翻译如下|译文如下|这是翻译|翻译[:：]|译文[:：])"
    ),
]


@dataclass
class GlossaryEntry:
    source_term: str
    target_term: str
    level: str = "required"
    case_sensitive: bool = False


@dataclass
class QAIssue:
    level: str
    code: str
    message: str


@dataclass
class QAResult:
    entry_id: str
    passed: bool
    errors: List[QAIssue] = field(default_factory=list)
    warnings: List[QAIssue] = field(default_factory=list)

    @property
    def issues(self) -> List[QAIssue]:
        return self.errors + self.warnings


# ----------------------------------------------------------------------
# Glossary
# ----------------------------------------------------------------------

def load_glossary(path: Path | str) -> List[GlossaryEntry]:
    """
    Load glossary CSV.

    Expected columns:

      source_term,target_term,level,case_sensitive

    level:
      required  -> missing term is an error
      preferred -> missing term is a warning
    """
    path = Path(path)

    if not path.exists():
        return []

    entries: List[GlossaryEntry] = []

    with path.open(
        "r",
        encoding="utf-8-sig",
        newline="",
    ) as f:
        reader = csv.DictReader(f)

        for row in reader:
            source_term = (
                row.get("source_term")
                or row.get("source")
                or ""
            ).strip()

            target_term = (
                row.get("target_term")
                or row.get("target")
                or ""
            ).strip()

            if not source_term:
                continue

            level = (row.get("level") or "required").strip().lower()

            if level not in {"required", "preferred"}:
                level = "required"

            case_sensitive_raw = (
                row.get("case_sensitive")
                or "false"
            ).strip().lower()

            case_sensitive = case_sensitive_raw in {
                "1",
                "true",
                "yes",
                "y",
                "on",
            }

            entries.append(
                GlossaryEntry(
                    source_term=source_term,
                    target_term=target_term,
                    level=level,
                    case_sensitive=case_sensitive,
                )
            )

    return entries


# ----------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------

def _contains(
    text: str,
    term: str,
    case_sensitive: bool,
) -> bool:
    if not term:
        return False

    if case_sensitive:
        return term in text

    return term.lower() in text.lower()


def _placeholder_counts(text: str) -> Counter:
    """
    Count protected placeholders/control codes in text.
    """
    _, matches = _PROTECTOR.protect(text or "")
    return Counter(matches)


def summarize_issues(result: QAResult, max_length: int = 1000) -> str:
    messages = []

    for issue in result.errors:
        messages.append(f"[ERROR:{issue.code}] {issue.message}")

    for issue in result.warnings:
        messages.append(f"[WARN:{issue.code}] {issue.message}")

    summary = "; ".join(messages)

    if len(summary) > max_length:
        summary = summary[:max_length] + "..."

    return summary


# ----------------------------------------------------------------------
# QA core
# ----------------------------------------------------------------------

def run_qa_entry(
    entry: TranslationEntry,
    target_language: str = "",
    glossary: Optional[List[GlossaryEntry]] = None,
    max_length_ratio: float = 3.0,
    max_length_add: int = 30,
) -> QAResult:
    errors: List[QAIssue] = []
    warnings: List[QAIssue] = []

    source = entry.source_text or ""
    target = entry.target_text

    if entry.ignored:
        return QAResult(
            entry_id=entry.id,
            passed=True,
            errors=errors,
            warnings=warnings,
        )

    # ------------------------------------------------------------------
    # Empty translation
    # ------------------------------------------------------------------

    if target is None or not target.strip():
        errors.append(
            QAIssue(
                level="error",
                code="empty_translation",
                message="Target text is empty.",
            )
        )

        return QAResult(
            entry_id=entry.id,
            passed=False,
            errors=errors,
            warnings=warnings,
        )

    stripped_target = target.strip()
    stripped_source = source.strip()

    # ------------------------------------------------------------------
    # Model artifacts
    # ------------------------------------------------------------------

    for regex in MODEL_ARTIFACT_REGEXES:
        if regex.search(stripped_target):
            errors.append(
                QAIssue(
                    level="error",
                    code="model_artifact",
                    message="Target looks like model explanation, not a translation.",
                )
            )
            break

    # ------------------------------------------------------------------
    # Placeholder checks
    # ------------------------------------------------------------------

    expected_placeholders = _placeholder_counts(source)
    actual_placeholders = _placeholder_counts(target)

    missing = expected_placeholders - actual_placeholders
    extra = actual_placeholders - expected_placeholders

    if missing:
        missing_items = list(missing.elements())
        errors.append(
            QAIssue(
                level="error",
                code="placeholder_missing",
                message=f"Missing placeholders/control codes: {missing_items}",
            )
        )

    if extra:
        extra_items = list(extra.elements())
        warnings.append(
            QAIssue(
                level="warning",
                code="placeholder_extra",
                message=f"Extra placeholders/control codes: {extra_items}",
            )
        )

    # ------------------------------------------------------------------
    # Glossary
    # ------------------------------------------------------------------

    if glossary:
        for glossary_entry in glossary:
            if not _contains(
                source,
                glossary_entry.source_term,
                glossary_entry.case_sensitive,
            ):
                continue

            required_target = (
                glossary_entry.target_term
                or glossary_entry.source_term
            )

            if _contains(
                target,
                required_target,
                glossary_entry.case_sensitive,
            ):
                continue

            message = (
                f"Glossary term '{glossary_entry.source_term}' "
                f"should appear as '{required_target}'."
            )

            if glossary_entry.level == "required":
                errors.append(
                    QAIssue(
                        level="error",
                        code="glossary_missing",
                        message=message,
                    )
                )
            else:
                warnings.append(
                    QAIssue(
                        level="warning",
                        code="glossary_preferred",
                        message=message,
                    )
                )

    # ------------------------------------------------------------------
    # Length check
    # ------------------------------------------------------------------

    source_length = len(stripped_source)
    target_length = len(stripped_target)

    if source_length > 0:
        limit = int(source_length * max_length_ratio + max_length_add)

        if target_length > limit:
            warnings.append(
                QAIssue(
                    level="warning",
                    code="too_long",
                    message=(
                        f"Target length {target_length} exceeds "
                        f"suggested limit {limit}."
                    ),
                )
            )

    # ------------------------------------------------------------------
    # Whitespace checks
    # ------------------------------------------------------------------

    if target != target.strip():
        warnings.append(
            QAIssue(
                level="warning",
                code="leading_trailing_space",
                message="Target has leading or trailing whitespace.",
            )
        )

    if target.count("\n") > source.count("\n") + 1:
        warnings.append(
            QAIssue(
                level="warning",
                code="extra_newlines",
                message="Target has more newlines than source.",
            )
        )

    # ------------------------------------------------------------------
    # Same as source
    # ------------------------------------------------------------------

    if stripped_target == stripped_source:
        warnings.append(
            QAIssue(
                level="warning",
                code="same_as_source",
                message="Target is identical to source.",
            )
        )

    # ------------------------------------------------------------------
    # Target language hint
    # ------------------------------------------------------------------

    target_language_lower = (target_language or "").lower()

    if (
        target_language_lower.startswith("zh")
        and not CJK_REGEX.search(target)
        and re.search(r"[A-Za-z]", source)
    ):
        warnings.append(
            QAIssue(
                level="warning",
                code="expected_cjk",
                message=(
                    "Target language is Chinese but no CJK character found."
                ),
            )
        )

    # ------------------------------------------------------------------
    # Quote balance
    # ------------------------------------------------------------------

    if target.count('"') % 2 != 0:
        warnings.append(
            QAIssue(
                level="warning",
                code="unbalanced_quotes",
                message="Target contains an odd number of double quotes.",
            )
        )

    passed = len(errors) == 0

    return QAResult(
        entry_id=entry.id,
        passed=passed,
        errors=errors,
        warnings=warnings,
    )


def run_qa_entries(
    entries: List[TranslationEntry],
    target_language: str = "",
    glossary: Optional[List[GlossaryEntry]] = None,
) -> tuple[List[QAResult], Dict[str, int | Dict[str, int]]]:
    results: List[QAResult] = []

    stats: Dict[str, int | Dict[str, int]] = {
        "total_entries": 0,
        "passed_entries": 0,
        "failed_entries": 0,
        "entries_with_warnings": 0,
        "entries_with_errors": 0,
        "issue_counts": {},
    }

    issue_counts: Dict[str, int] = {}

    for entry in entries:
        result = run_qa_entry(
            entry=entry,
            target_language=target_language,
            glossary=glossary,
        )

        results.append(result)

        stats["total_entries"] += 1

        if result.passed:
            stats["passed_entries"] += 1
        else:
            stats["failed_entries"] += 1

        if result.warnings:
            stats["entries_with_warnings"] += 1

        if result.errors:
            stats["entries_with_errors"] += 1

        for issue in result.issues:
            issue_counts[issue.code] = issue_counts.get(issue.code, 0) + 1

    stats["issue_counts"] = issue_counts

    return results, stats