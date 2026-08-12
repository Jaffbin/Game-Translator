import tempfile
import unittest
from pathlib import Path

from agl.cache import TranslationMemory
from agl.pipeline import translate_one
from agl.placeholders import PlaceholderProtector
from agl.providers import MockProvider


class TestPlaceholderProtector(unittest.TestCase):
    def test_rpgmaker_and_renpy_placeholders(self):
        protector = PlaceholderProtector()

        text = "You have \\v[1] gold, [player_name]. {item} gained."
        protected, matches = protector.protect(text)

        self.assertNotIn("\\v[1]", protected)
        self.assertNotIn("[player_name]", protected)
        self.assertNotIn("{item}", protected)

        self.assertIn("[[AGL_PH_0]]", protected)
        self.assertIn("[[AGL_PH_1]]", protected)
        self.assertIn("[[AGL_PH_2]]", protected)

        fake_translation = protected.replace("gold", "金币")
        fake_translation = fake_translation.replace("gained", "获得了")

        self.assertTrue(protector.validate(fake_translation, matches))

        restored = protector.restore(fake_translation, matches)

        self.assertIn("\\v[1]", restored)
        self.assertIn("[player_name]", restored)
        self.assertIn("{item}", restored)

    def test_validation_fails_when_placeholder_missing(self):
        protector = PlaceholderProtector()

        text = "Gold: \\v[1]"
        protected, matches = protector.protect(text)

        bad_translation = protected.replace("[[AGL_PH_0]]", "")

        self.assertFalse(protector.validate(bad_translation, matches))

    def test_validation_fails_when_extra_placeholder_token_exists(self):
        protector = PlaceholderProtector()

        text = "Gold: \\v[1]"
        protected, matches = protector.protect(text)

        bad_translation = protected + " [[AGL_PH_99]]"

        self.assertFalse(protector.validate(bad_translation, matches))


class TestTranslationPipeline(unittest.TestCase):
    def test_mock_translation_and_cache(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            with TranslationMemory(Path(tmp_dir) / "cache.db") as cache:
                provider = MockProvider()

                source = "Hello world"
                target_language = "zh-CN"

                first = translate_one(
                    provider=provider,
                    cache=cache,
                    source_text=source,
                    target_language=target_language,
                )

                self.assertTrue(first)

                cached = cache.get(source, target_language)
                self.assertEqual(first, cached)

                # Second call should hit cache.
                second = translate_one(
                    provider=provider,
                    cache=cache,
                    source_text=source,
                    target_language=target_language,
                )

                self.assertEqual(first, second)

    def test_placeholder_restore_with_mock_provider(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            with TranslationMemory(Path(tmp_dir) / "cache.db") as cache:
                provider = MockProvider()

                source = "You have \\v[1] gold."
                target_language = "zh-CN"

                result = translate_one(
                    provider=provider,
                    cache=cache,
                    source_text=source,
                    target_language=target_language,
                )

                self.assertIn("\\v[1]", result)


if __name__ == "__main__":
    unittest.main()