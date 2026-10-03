from __future__ import annotations

import ast
import logging
from pathlib import Path

import pytest

from afk_proto.adapters.azure_devops.adapter import AzureDevOpsAdapter
from afk_proto.adapters.azure_devops.fake_az import FakeAz
from afk_proto.adapters.copilot_cli import DummyCopilotCli
from afk_proto.adapters.github.adapter import GitHubAdapter
from afk_proto.adapters.github.fake_gh import FakeGh
from afk_proto.adapters.platform_factory import create_platform_adapter
from afk_proto.cli import main
from afk_proto.runtime.attempts import MAX_FAILED_ATTEMPTS
from afk_proto.runtime.context import RunContext
from afk_proto.runtime.contracts.platform_adapter import PlatformAdapter, WorkItem
from afk_proto.runtime.discovery import discover_commands

SRC = Path(__file__).parents[1] / "src" / "afk_proto"
SLICES_DIR = Path(__file__).parents[2] / "slices"
GITHUB_REMOTE = "https://github.com/owner/repo.git"
AZURE_REMOTE = "https://dev.azure.com/org/project/_git/repo"


def imports_of(path: Path) -> set[str]:
    found: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text())):
        if isinstance(node, ast.Import):
            found.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            found.add(node.module)
    return found


def test_discovers_every_slice() -> None:
    assert set(discover_commands(SLICES_DIR)) == {"dev", "fix-prs", "address-prs"}


def test_slices_dir_option_selects_the_discovery_root(tmp_path: Path) -> None:
    root = tmp_path / "custom_slices"
    (root / "hello").mkdir(parents=True)
    (root / "__init__.py").write_text("")
    (root / "hello" / "__init__.py").write_text("")
    (root / "hello" / "command.py").write_text(
        "class C:\n"
        "    name = 'hello'\n"
        "    help = ''\n"
        "    def configure(self, parser): pass\n"
        "    def run(self, args, ctx): return 7\n"
        "command = C()\n"
    )

    assert main(["--slices-dir", str(root), "hello"]) == 7


def test_duplicate_command_names_are_rejected(tmp_path: Path) -> None:
    for slice_name in ("one", "two"):
        slice_dir = tmp_path / "dup_slices" / slice_name
        slice_dir.mkdir(parents=True)
        (slice_dir / "__init__.py").write_text("")
        (slice_dir / "command.py").write_text(
            "class C:\n    name = 'same'\n    help = ''\ncommand = C()\n"
        )
    (tmp_path / "dup_slices" / "__init__.py").write_text("")

    with pytest.raises(ValueError, match="duplicate command: same"):
        discover_commands(tmp_path / "dup_slices")


@pytest.mark.parametrize("remote_url", [GITHUB_REMOTE, AZURE_REMOTE])
@pytest.mark.parametrize("command", ["dev", "fix-prs", "address-prs"])
def test_dummy_run_succeeds(command: str, remote_url: str, tmp_path: Path) -> None:
    assert main(["--log-dir", str(tmp_path), "--remote-url", remote_url, command]) == 0


def test_unsupported_remote_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="unsupported remote"):
        main(["--log-dir", str(tmp_path), "--remote-url", "https://example.com/x/y", "dev"])


def make_ctx(remote_url: str, tmp_path: Path) -> RunContext:
    return RunContext(Path.cwd(), remote_url, tmp_path, False, logging.getLogger("test"))


def test_factory_selects_adapter_from_remote(tmp_path: Path) -> None:
    assert isinstance(create_platform_adapter(make_ctx(GITHUB_REMOTE, tmp_path)), GitHubAdapter)
    assert isinstance(create_platform_adapter(make_ctx(AZURE_REMOTE, tmp_path)), AzureDevOpsAdapter)


@pytest.mark.parametrize("remote_url", [GITHUB_REMOTE, AZURE_REMOTE])
def test_both_platforms_yield_identical_normalized_models(remote_url: str, tmp_path: Path) -> None:
    platform = create_platform_adapter(make_ctx(remote_url, tmp_path))

    specs = platform.list_specs()
    assert [(spec.title, spec.state, spec.tags[0]) for spec in specs] == [
        ("Add login page", "open", "spec"),
        ("Add logout button", "open", "spec"),
    ]
    assert all(isinstance(spec.id, str) for spec in specs)

    pull_request = platform.list_pull_requests()[0]
    assert pull_request.title == "Add login page (PR)"

    threads = platform.review_threads(pull_request.id)
    assert [(thread.path, thread.body, thread.resolved) for thread in threads] == [
        ("src/app.py", "Rename this variable.", False),
        ("src/app.py", "Looks good.", True),
    ]


def test_incomplete_adapter_cannot_be_instantiated() -> None:
    class Incomplete(PlatformAdapter):
        def list_specs(self) -> list[WorkItem]:
            return []

    with pytest.raises(TypeError, match="abstract"):
        Incomplete()


def test_github_adapter_keeps_gh_details_inside() -> None:
    gh = FakeGh()
    adapter = GitHubAdapter("owner", "repo", gh=gh)

    adapter.review_threads("10")
    adapter.reply_to_thread("10", "t1", "done")

    threads_call, reply_call = gh.calls
    assert "number=10" in threads_call and "-F" in threads_call
    assert "thread=t1" in reply_call and "owner=owner" not in reply_call


def test_dry_run_skips_reply() -> None:
    gh, az = FakeGh(), FakeAz()
    GitHubAdapter("owner", "repo", gh=gh, dry_run=True).reply_to_thread("10", "t1", "done")
    AzureDevOpsAdapter("org", "project", "repo", az=az, dry_run=True).reply_to_thread("7", "5", "done")

    assert gh.calls == [] and az.calls == []


def test_attempt_cap_stops_retries(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.syspath_prepend(str(SLICES_DIR.parent))
    monkeypatch.setattr(
        "slices.dev.command.DummyCopilotCli",
        lambda ctx: DummyCopilotCli(ctx, fail_if_contains="login"),
    )
    codes = [main(["--log-dir", str(tmp_path), "dev"]) for _ in range(MAX_FAILED_ATTEMPTS + 1)]

    assert codes[:MAX_FAILED_ATTEMPTS] == [1] * MAX_FAILED_ATTEMPTS
    assert codes[-1] == 0


def test_runtime_imports_no_slice_adapter_or_cli() -> None:
    for path in (SRC / "runtime").rglob("*.py"):
        for module in imports_of(path):
            assert not module.startswith(("slices", "afk_proto.adapters", "afk_proto.cli")), path


def test_slice_flow_imports_no_adapter_or_other_slice() -> None:
    for slice_dir in SLICES_DIR.iterdir():
        if not slice_dir.is_dir() or slice_dir.name == "__pycache__":
            continue
        for module in imports_of(slice_dir / "slice.py"):
            assert not module.startswith("afk_proto.adapters"), slice_dir
        for path in slice_dir.glob("*.py"):
            for module in imports_of(path):
                other = module.startswith("slices.") and not module.startswith(f"slices.{slice_dir.name}")
                assert not other, path
