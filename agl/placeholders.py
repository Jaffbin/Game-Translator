from __future__ import annotations

import re
from typing import List, Tuple


class PlaceholderError(ValueError):
    pass


DEFAULT_PLACEHOLDER_PATTERNS = [
    # RPG Maker control codes, e.g. \v[1], \n[2], \c[3], \i[4]
    r"\\[vVnNcCiIpPgGeE]\[\d+\]",

    # RPG Maker font size codes like \{ and \}
    r"\\[{}]",

    # Ren'Py variable interpolation, e.g. [player_name], [gold_amount]
    r"\[[a-zA-Z_][a-zA-Z0-9_\.]*\]",

    # Generic template placeholders, e.g. {item_name}, {0}
    r"\{[^{}\n]*\}",

    # printf-like placeholders, e.g. %s, %d, %1$s
    r"%\d+\$[sdif]|%[sdif]",

    # Rich text tags, e.g. <color=red>, </color>
    r"<[^>\n]+>",
]


class PlaceholderProtector:
    """
    Protects placeholders before machine translation and restores them after.

    Important:
      Replacement is done in ONE pass using a combined regex.
      This prevents our own placeholder tokens from being re-matched
      by later patterns.
    """

    TOKEN_TEMPLATE = "[[AGL_PH_{}]]"
    TOKEN_REGEX = re.compile(r"\[\[AGL_PH_\d+\]\]")

    def __init__(self, extra_patterns: List[str] | None = None):
        self.patterns = list(DEFAULT_PLACEHOLDER_PATTERNS)
        if extra_patterns:
            self.patterns.extend(extra_patterns)

        combined_pattern = "|".join(
            f"(?:{pattern})" for pattern in self.patterns
        )
        self._combined_regex = re.compile(combined_pattern)

    def protect(self, text: str) -> Tuple[str, List[str]]:
        matches: List[str] = []

        def _replace(match: re.Match) -> str:
            matches.append(match.group(0))
            return self.TOKEN_TEMPLATE.format(len(matches) - 1)

        protected = self._combined_regex.sub(_replace, text or "")
        return protected, matches

    def tokens(self, match_count: int) -> List[str]:
        return [self.TOKEN_TEMPLATE.format(i) for i in range(match_count)]

    def validate(self, translated_protected: str, matches: List[str]) -> bool:
        """
        Ensure every expected placeholder token appears exactly once,
        and no unexpected AGL_PH token appears.
        """
        if translated_protected is None:
            return False

        expected_tokens = self.tokens(len(matches))
        found_tokens = self.TOKEN_REGEX.findall(translated_protected)

        if set(found_tokens) != set(expected_tokens):
            return False

        for token in expected_tokens:
            if translated_protected.count(token) != 1:
                return False

        return True

    def restore(self, translated_protected: str, matches: List[str]) -> str:
        restored = translated_protected or ""

        for i, original_placeholder in enumerate(matches):
            token = self.TOKEN_TEMPLATE.format(i)
            restored = restored.replace(token, original_placeholder)

        return restored

    def translate_safe(
        self,
        text: str,
        translate_function,
    ) -> str:
        """
        Helper:
          protect -> translate -> validate -> restore
        """
        protected, matches = self.protect(text)
        translated_protected = translate_function(protected)

        if not self.validate(translated_protected, matches):
            raise PlaceholderError(
                "Placeholder validation failed. "
                "The translation model removed or modified protected placeholders. "
                f"Original text: {text!r} "
                f"Protected text: {protected!r} "
                f"Translated protected text: {translated_protected!r}"
            )

        return self.restore(translated_protected, matches)