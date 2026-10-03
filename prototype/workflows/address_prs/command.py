from __future__ import annotations

import argparse

from ship_proto.adapters.copilot_cli import DummyCopilotCli
from ship_proto.adapters.file_execution_store import FileExecutionStore
from ship_proto.adapters.platform_factory import create_platform_adapter
from ship_proto.runtime.context import RunContext
from ship_proto.runtime.contracts.command import Command
from . import workflow as flow


class AddressPrsCommand(Command):
    name = "address-prs"
    help = "Reply to open review threads."

    def configure(self, parser: argparse.ArgumentParser) -> None:
        parser.add_argument("--review", default=None, help="only this review id")

    def run(self, args: argparse.Namespace, ctx: RunContext) -> int:
        deps = flow.Deps(
            agent=DummyCopilotCli(ctx),
            platform=create_platform_adapter(ctx),
            store=FileExecutionStore(ctx.log_dir),
        )
        return flow.run(args, ctx, deps)


command = AddressPrsCommand()
