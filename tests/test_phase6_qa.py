import unittest

from agl.models import EntryStatus, TranslationEntry
from agl.qa import (
    GlossaryEntry,
    run_qa_entry,
)


def make_entry(
    source_text: str,
    target_text: str | None,
    entry_id: str = "test_entry",
) -> TranslationEntry:
    return TranslationEntry(
        id=entry_id,
        source_text=source_text,
        target_text=target_text,
        context="test",
        file_path="test.json",
        engine="test",
        location={},
        status=EntryStatus.MACHINE_TRANSLATED,
    )


class TestPhase6QA(unittest.TestCase):
    def test_good_entry_passes(self):
        entry = make_entry(
            source_text="You have \\v[1] gold.",
            target_text="你拥有 \\v[1] 金币。",
        )

        result = run_qa_entry(entry, target_language="zh-CN")

        self.assertTrue(result.passed)
        self.assertEqual(len(result.errors), 0)

    def test_empty_translation_is_error(self):
        entry = make_entry(
            source_text="Hello",
            target_text="",
        )

        result = run_qa_entry(entry, target_language="zh-CN")

        self.assertFalse(result.passed)

        codes = {issue.code for issue in result.errors}
        self.assertIn("empty_translation", codes)

    def test_placeholder_missing_is_error(self):
        entry = make_entry(
            source_text="You have \\v[1] gold.",
            target_text="你拥有金币。",
        )

        result = run_qa_entry(entry, target_language="zh-CN")

        self.assertFalse(result.passed)

        codes = {issue.code for issue in result.errors}
        self.assertIn("placeholder_missing", codes)

    def test_model_artifact_is_error(self):
        entry = make_entry(
            source_text="Hello",
            target_text="Sure, here is the translation.",
        )

        result = run_qa_entry(entry, target_language="zh-CN")

        self.assertFalse(result.passed)

        codes = {issue.code for issue in result.errors}
        self.assertIn("model_artifact", codes)

    def test_glossary_required_missing_is_error(self):
        glossary = [
            GlossaryEntry(
                source_term="Fireball",
                target_term="火球术",
                level="required",
                case_sensitive=False,
            )
        ]

        entry = make_entry(
            source_text="Fireball hits the enemy.",
            target_text="火焰球击中了敌人。",
        )

        result = run_qa_entry(
            entry,
            target_language="zh-CN",
            glossary=glossary,
        )

        self.assertFalse(result.passed)

        codes = {issue.code for issue in result.errors}
        self.assertIn("glossary_missing", codes)

    def test_glossary_required_present_passes(self):
        glossary = [
            GlossaryEntry(
                source_term="Fireball",
                target_term="火球术",
                level="required",
                case_sensitive=False,
            )
        ]

        entry = make_entry(
            source_text="Fireball hits the enemy.",
            target_text="火球术击中了敌人。",
        )

        result = run_qa_entry(
            entry,
            target_language="zh-CN",
            glossary=glossary,
        )

        self.assertTrue(result.passed)

    def test_expected_cjk_warning(self):
        entry = make_entry(
            source_text="Start Game",
            target_text="Start Game",
        )

        result = run_qa_entry(entry, target_language="zh-CN")

        warning_codes = {issue.code for issue in result.warnings}

        self.assertIn("same_as_source", warning_codes)
        self.assertIn("expected_cjk", warning_codes)


if __name__ == "__main__":
    unittest.main()