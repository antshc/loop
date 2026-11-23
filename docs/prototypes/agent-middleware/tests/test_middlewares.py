from __future__ import annotations

import unittest
from dataclasses import dataclass
from typing import Any

from agentmw import (
    AgentCallContext,
    AgentReplyError,
    AgentRequest,
    AgentResult,
    AgentRunError,
    Issue,
    ReplyMismatch,
    RetryPolicy,
)
from agentmw.fakes import FakeGitHubClient
from agentmw.integrations import TicketCache
from agentmw.middlewares import (
    GitHubTicketMiddleware,
    JsonReplyMiddleware,
    ReplyRetryMiddleware,
    ResponseFormatMiddleware,
    ResultCheckMiddleware,
)
from agentmw.middlewares.json_reply import to_typed


@dataclass(frozen=True)
class Inner:
    path: str


@dataclass(frozen=True)
class Reply:
    ok: bool
    count: int
    files: list[Inner]
    note: str | None


def context(**metadata: str) -> AgentCallContext:
    # Middlewares under test never touch the container.
    services: Any = None
    return AgentCallContext(AgentRequest("dev", "", Reply, metadata=metadata), services)


async def done(ctx: AgentCallContext) -> None:
    pass


def returning(*outputs: str):
    remaining = list(outputs)

    async def terminal(ctx: AgentCallContext) -> None:
        ctx.result = AgentResult(remaining.pop(0), 0)

    return terminal


class TicketTests(unittest.IsolatedAsyncioTestCase):
    async def test_fills_ticket_from_the_named_issue(self) -> None:
        github = FakeGitHubClient([Issue("o/r", 7, "Title", "Body")])
        ctx = context(issue="o/r#7")

        await GitHubTicketMiddleware(TicketCache(github)).invoke(ctx, done)

        self.assertEqual("### o/r#7: Title\n\nBody", ctx.args["TICKET"])

    async def test_passes_through_without_an_issue(self) -> None:
        ctx = context()

        await GitHubTicketMiddleware(TicketCache(FakeGitHubClient([]))).invoke(
            ctx, done
        )

        self.assertNotIn("TICKET", ctx.args)

    async def test_rejects_a_malformed_reference(self) -> None:
        with self.assertRaises(ValueError):
            await GitHubTicketMiddleware(TicketCache(FakeGitHubClient([]))).invoke(
                context(issue="nope"), done
            )


class ResponseFormatTests(unittest.IsolatedAsyncioTestCase):
    async def test_describes_the_reply_dataclass(self) -> None:
        ctx = context()

        await ResponseFormatMiddleware().invoke(ctx, done)

        fmt = ctx.args["RESPONSE_FORMAT"]
        self.assertIn('"ok": "bool"', fmt)
        self.assertIn('"path": "str"', fmt)
        self.assertIn('"note": "str | null"', fmt)


class ResultCheckTests(unittest.IsolatedAsyncioTestCase):
    async def test_non_zero_exit_raises_run_error(self) -> None:
        async def failing(ctx: AgentCallContext) -> None:
            ctx.result = AgentResult("boom", 2)

        with self.assertRaises(AgentRunError):
            await ResultCheckMiddleware().invoke(context(), failing)

    async def test_zero_exit_passes(self) -> None:
        await ResultCheckMiddleware().invoke(context(), returning("x"))


class JsonReplyTests(unittest.IsolatedAsyncioTestCase):
    async def test_parses_the_last_json_object_into_the_reply_type(self) -> None:
        ctx = context()
        output = (
            'noise {"ok": false} then '
            '{"ok": true, "count": 2, "files": [{"path": "a"}], "note": null}'
        )

        await JsonReplyMiddleware().invoke(ctx, returning(output))

        self.assertEqual(Reply(True, 2, [Inner("a")], None), ctx.reply)

    async def test_missing_field_is_a_mismatch(self) -> None:
        with self.assertRaisesRegex(ReplyMismatch, "missing"):
            await JsonReplyMiddleware().invoke(context(), returning('{"ok": true}'))

    def test_bool_is_not_an_int(self) -> None:
        with self.assertRaises(ReplyMismatch):
            to_typed(int, True)


class RetryTests(unittest.IsolatedAsyncioTestCase):
    async def test_retries_a_mismatch_with_feedback(self) -> None:
        ctx = context()
        seen: list[str | None] = []

        async def flaky(ctx: AgentCallContext) -> None:
            seen.append(ctx.feedback)
            if ctx.attempt == 1:
                raise ReplyMismatch("bad shape")

        await ReplyRetryMiddleware(RetryPolicy(3)).invoke(ctx, flaky)

        self.assertEqual([None, "bad shape"], seen)
        self.assertEqual(2, ctx.attempt)

    async def test_gives_up_after_max_attempts(self) -> None:
        async def always(ctx: AgentCallContext) -> None:
            raise ReplyMismatch("bad")

        with self.assertRaises(AgentReplyError):
            await ReplyRetryMiddleware(RetryPolicy(2)).invoke(context(), always)

    async def test_does_not_retry_run_errors(self) -> None:
        calls = 0

        async def crash(ctx: AgentCallContext) -> None:
            nonlocal calls
            calls += 1
            raise AgentRunError("dev", "exit 1")

        with self.assertRaises(AgentRunError):
            await ReplyRetryMiddleware(RetryPolicy(3)).invoke(context(), crash)
        self.assertEqual(1, calls)


if __name__ == "__main__":
    unittest.main()
