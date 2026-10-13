"""Unit tests for content decoders: `pytest tests/unit/test_content.py`."""

from __future__ import annotations

import unittest

from workflows.content import ContentDecodeError, LastJsonObject


class LastJsonObjectTests(unittest.TestCase):
    def test_returns_the_last_object_carrying_a_status(self) -> None:
        text = 'noise {"status": "completed", "result": {}} {"other": 1} {"status": "failed", "result": {"reason": "r"}} tail'
        decoder = LastJsonObject(lambda value: "status" in value)
        self.assertEqual(decoder.decode(text)["status"], "failed")

    def test_nested_objects_and_braces_inside_strings_parse_correctly(self) -> None:
        text = '{"status": "completed", "result": {"plan": "do {this} first", "steps": {"a": 1}}}'
        decoder = LastJsonObject(lambda value: "status" in value)
        self.assertEqual(decoder.decode(text)["result"], {"plan": "do {this} first", "steps": {"a": 1}})

    def test_prose_around_the_object_is_ignored(self) -> None:
        text = 'thinking out loud\n{"status": "completed", "result": {}}\ndone'
        decoder = LastJsonObject(lambda value: "status" in value)
        self.assertEqual(decoder.decode(text)["status"], "completed")

    def test_where_filters_out_non_matching_objects(self) -> None:
        text = '{"other": 1} {"status": "completed", "result": {}}'
        decoder = LastJsonObject(lambda value: "status" in value)
        self.assertEqual(decoder.decode(text), {"status": "completed", "result": {}})

    def test_default_where_matches_every_object(self) -> None:
        self.assertEqual(LastJsonObject().decode('{"a": 1} {"b": 2}'), {"b": 2})

    def test_no_match_raises_content_decode_error(self) -> None:
        for text in ["", "no json", '{"status": "completed"', '{"other": 1}', "[1]"]:
            with self.subTest(text=text):
                with self.assertRaises(ContentDecodeError):
                    LastJsonObject(lambda value: "status" in value).decode(text)


if __name__ == "__main__":
    unittest.main()
