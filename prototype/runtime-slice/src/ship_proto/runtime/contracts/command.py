from __future__ import annotations

import argparse
from abc import ABC, abstractmethod

from ship_proto.runtime.context import RunContext


class Command(ABC):
    name: str
    help: str

    @abstractmethod
    def configure(self, parser: argparse.ArgumentParser) -> None: ...

    @abstractmethod
    def run(self, args: argparse.Namespace, ctx: RunContext) -> int: ...
