import unittest
from types import MappingProxyType
from unittest.mock import patch

from makeghrepo import inputs, names


class InputGrammar(unittest.TestCase):
    def test_only_first_word_can_be_a_name(self):
        self.assertEqual(
            inputs.parse_words(["sample", "python", "sqlite", "api"]),
            ("sample", ["python", "sqlite", "api"]),
        )
        self.assertEqual(inputs.parse_words(["sample"]), ("sample", []))
        self.assertEqual(inputs.parse_words([]), (None, []))

    def test_reserved_first_word_selects_an_action_without_a_name(self):
        self.assertEqual(
            inputs.parse_words(["python", "sqlite", "api"]),
            (None, ["python", "sqlite", "api"]),
        )

    def test_unknown_later_words_never_become_commands_or_names(self):
        for words in (
            ["python", "sample"],
            ["sample", "another-name"],
            ["sample", "cobol"],
            ["sample", "api; inert"],
            ["sample", "*"],
            ["sample", "python\n"],
            ["sample", "gh"],
            ["sample", "auth"],
        ):
            with self.subTest(words=words), self.assertRaises(ValueError):
                inputs.parse_words(words)

    def test_every_installed_keyword_maps_only_to_its_declared_action(self):
        for word, (action, language) in inputs.RESERVED_WORDS.items():
            with self.subTest(word=word):
                self.assertEqual(action, "language")
                self.assertEqual(inputs.parse_words([word, word.upper()]), (None, [language]))

    def test_unrecognized_registry_action_is_rejected(self):
        with (
            patch.object(inputs, "RESERVED_WORDS", MappingProxyType({"inert": ("eval", "text")})),
            self.assertRaises(ValueError),
        ):
            inputs.parse_words(["inert"])

    def test_repository_name_must_match_the_whole_canonical_value(self):
        for value in ("safe\n", "safe\0", "safe/other", "safe*", "safe;inert"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                names.validate_name(value)
