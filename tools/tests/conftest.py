"""Pytest configuration for tools tests."""

import sys
from pathlib import Path

# Add tools/src to Python path so pytest can find afk and modules packages
tools_src = Path(__file__).parent.parent / "src"
sys.path.insert(0, str(tools_src))
