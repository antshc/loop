from __future__ import annotations

import argparse

from afk_proto.adapters.copilot_cli import DummyCopilotCli
from afk_proto.adapters.file_execution_store import FileExecutionStore
from afk_proto.adapters.platform_factory import create_platform_adapter
from afk_proto.runtime.context import RunContext
from afk_proto.runtime.contracts.command import Command
from . import slice as flow


class FixPrsCommand(Command):
    name = "fix-prs"
    help = "Apply requested changes from open review threads."

    def configure(self, parser: argparse.ArgumentParser) -> None:
        pass

    def run(self, args: argparse.Namespace, ctx: RunContext) -> int:
        deps = flow.Deps(
            agent=DummyCopilotCli(ctx),
            platform=create_platform_adapter(ctx),
            store=FileExecutionStore(ctx.log_dir),
        )
        return flow.run(args, ctx, deps)


command = FixPrsCommand()
