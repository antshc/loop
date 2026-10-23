"""A throwaway git repository the real agents work in."""

from __future__ import annotations

import shutil
import subprocess
import tempfile
from pathlib import Path

TARGET_BRANCH = "main"

_SEED = {
    "basket.py": '''"""A tiny shopping basket."""

from __future__ import annotations


class Basket:
    def __init__(self) -> None:
        self.prices: list[float] = []

    def add(self, price: float) -> None:
        self.prices.append(price)

    def total(self) -> float:
        return round(sum(self.prices), 2)
''',
    "test_basket.py": '''from basket import Basket


def test_total_sums_prices():
    basket = Basket()
    basket.add(10.0)
    basket.add(5.5)
    assert basket.total() == 15.5
''',
    "README.md": "# Basket\n\nRun the tests with `python3 -m pytest`.\n",
}


class ScratchRepo:
    """A temp git repo on `branch`, cut from `main` with one seed commit."""

    def __init__(self, branch: str, parent: Path | None = None) -> None:
        self.branch = branch
        self.path = Path(tempfile.mkdtemp(prefix="loop-aspire-", dir=parent))
        self._git("init", "-q", "-b", TARGET_BRANCH)
        self._git("config", "user.name", "loop-aspire")
        self._git("config", "user.email", "loop-aspire@example.invalid")
        for name, content in _SEED.items():
            (self.path / name).write_text(content)
        self._git("add", ".")
        self._git("commit", "-q", "-m", "chore: seed basket")
        self._git("checkout", "-q", "-b", branch)

    def remove(self) -> None:
        """Deletes the repo from disk."""
        shutil.rmtree(self.path, ignore_errors=True)

    def _git(self, *args: str) -> str:
        result = subprocess.run(["git", "-C", str(self.path), *args], check=True,
                                capture_output=True, text=True)
        return result.stdout.strip()

