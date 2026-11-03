"""Installed factory helpers work without repository capability code."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path


def test_installed_factory_imports_without_repository_capabilities(
    tmp_path: Path,
) -> None:
    """Import every installed helper with checkout imports forbidden."""
    code = """
import importlib
import importlib.abc
import pkgutil
import sys

class NoCheckout(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname.split('.')[0] in {'software_factory', 'workflows', 'tests'}:
            raise AssertionError(f'installed helper imported checkout: {fullname}')

sys.meta_path.insert(0, NoCheckout())
import loop.software_factory as helpers
modules = list(pkgutil.walk_packages(helpers.__path__, helpers.__name__ + '.'))
assert len(modules) >= 20
for module in modules:
    importlib.import_module(module.name)
from loop.software_factory.agents import LastJsonObject
assert LastJsonObject().decode('reply {"value": 3}') == {'value': 3}
from loop.software_factory.stages.checks import as_verify
assert as_verify(('checks',), run=lambda *args, **kwargs: '')(None) is None
"""
    result = subprocess.run(
        [sys.executable, "-I", "-c", code],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr
