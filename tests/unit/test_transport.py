"""Unit tests for the CLI stdout transports: `pytest tests/unit/test_transport.py`."""

from __future__ import annotations

import unittest

from loop import CodexJson, CopilotJson, CopilotText


class TransportContractTests(unittest.TestCase):
    """Each transport's encode/decode round-trips the assistant text."""

    def test_round_trips_the_text(self) -> None:
        for transport in (CopilotText(), CopilotJson(), CodexJson()):
            with self.subTest(transport=type(transport).__name__):
                self.assertEqual(transport.decode(transport.encode("hello", "h1")).text, "hello")

    def test_non_json_lines_are_skipped(self) -> None:
        for transport in (CopilotJson(), CodexJson()):
            with self.subTest(transport=type(transport).__name__):
                stdout = "not json\n" + transport.encode("hello", "h1") + "\nalso not json"
                self.assertEqual(transport.decode(stdout).text, "hello")


class CopilotTextTests(unittest.TestCase):
    def test_decode_and_encode_return_the_text_unchanged(self) -> None:
        transport = CopilotText()
        self.assertEqual(transport.decode("hello").text, "hello")
        self.assertEqual(transport.encode("hello", "h1"), "hello")


class CopilotJsonTests(unittest.TestCase):
    def test_concatenates_deltas_and_reads_the_result_event(self) -> None:
        stdout = "\n".join([
            '{"type":"assistant.message_start"}',
            '{"type":"assistant.message_delta","data":{"deltaContent":"OK"}}',
            '{"type":"assistant.message_delta","data":{"deltaContent":", done"}}',
            '{"type":"assistant.turn_end"}',
            '{"type":"result","sessionId":"sess-1","exitCode":0}',
        ])
        transcript = CopilotJson().decode(stdout)
        self.assertEqual((transcript.text, transcript.handle_value, transcript.exit_code), ("OK, done", "sess-1", 0))

    def test_encode_produces_a_delta_and_a_result_event_decode_can_read_back(self) -> None:
        stdout = CopilotJson().encode("hello", "sess-1")
        transcript = CopilotJson().decode(stdout)
        self.assertEqual((transcript.text, transcript.handle_value, transcript.exit_code), ("hello", "sess-1", 0))


class CodexJsonTests(unittest.TestCase):
    def test_reads_the_thread_id_and_the_last_agent_message(self) -> None:
        stdout = "\n".join([
            '{"type":"thread.started","thread_id":"t1"}',
            "not json",
            '{"type":"item.completed","item":{"type":"agent_message","text":"first"}}',
            '{"type":"item.completed","item":{"type":"agent_message","text":"last"}}',
        ])
        transcript = CodexJson().decode(stdout)
        self.assertEqual((transcript.text, transcript.handle_value), ("last", "t1"))

    def test_decode_without_a_thread_started_event_has_no_handle(self) -> None:
        transcript = CodexJson().decode("")
        self.assertIsNone(transcript.handle_value)


if __name__ == "__main__":
    unittest.main()
