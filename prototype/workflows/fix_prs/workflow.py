from __future__ import annotations

import argparse
from dataclasses import dataclass
from pathlib import Path

from ship_proto.runtime.attempts import may_attempt
from ship_proto.runtime.context import RunContext
from ship_proto.runtime.contracts.agent_client import AgentClient
from ship_proto.runtime.contracts.execution_store import ExecutionStore
from ship_proto.runtime.contracts.platform_adapter import PlatformAdapter
from ship_proto.runtime.errors import ExitCode

PROMPT = (Path(__file__).parent / "prompt.md").read_text()


@dataclass(frozen=True)
class Deps:
    agent: AgentClient
    platform: PlatformAdapter
    store: ExecutionStore


def run(args: argparse.Namespace, ctx: RunContext, deps: Deps) -> int:
    failed = False
    for pull_request in deps.platform.list_pull_requests():
        for thread in deps.platform.review_threads(pull_request.id):
            if thread.resolved:
                continue
            key = f"fix-prs:{pull_request.id}:{thread.id}"
            if not may_attempt(deps.store, key):
                ctx.logger.warning("skip %s: attempt cap reached", key)
                continue
            result = deps.agent.run(PROMPT.format(title=pull_request.title, path=thread.path, body=thread.body))
            deps.store.record(key, result.success)
            ctx.logger.info("%s -> %s", key, "ok" if result.success else "failed")
            failed = failed or not result.success
    return ExitCode.FAILED if failed else ExitCode.OK
