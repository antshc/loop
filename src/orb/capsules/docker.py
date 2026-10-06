from __future__ import annotations

import atexit
import os
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

from orb.contracts.agent_client import AgentOptions, AgentResult
from orb.contracts.capsule import AgentClientFactory, Capsule
from orb.errors import OrbError
from orb.process import CommandExecutor, CommandResult, checked_output, execute

CAPSULE_HOME = "/home/agent"
CAPSULE_WORKSPACE = f"{CAPSULE_HOME}/workspace"


@dataclass(frozen=True)
class Mount:
    host_path: str
    capsule_path: str
    readonly: bool = False


def default_image_name(repo: Path) -> str:
    return f"orb:{re.sub(r'[^a-z0-9_.-]', '-', repo.name.lower()) or 'local'}"


def _git_mounts(worktree_path: Path) -> tuple[list[Mount], Path]:
    """Mounts that make a worktree's `gitdir:` pointer resolve inside the container; also the repo dir."""
    git_file = worktree_path / ".git"
    if git_file.is_file():
        match = re.match(r"gitdir:\s*(.+)", git_file.read_text().strip())
        if match:
            # gitdir is <repo>/.git/worktrees/<name>; the container needs <repo>/.git at the same path.
            parent_git = Path(match.group(1)).parent.parent
            return [Mount(str(parent_git), str(parent_git))], parent_git.parent
    return [], worktree_path


def _resolve_user_mount(mount: Mount) -> Mount:
    host = Path(mount.host_path).expanduser().resolve()
    if not host.exists():
        raise ValueError(f"mount host_path does not exist: {mount.host_path}")
    target = mount.capsule_path
    if target == "~" or target.startswith("~/"):
        target = CAPSULE_HOME + target[1:]
    if not target.startswith("/"):
        target = f"{CAPSULE_WORKSPACE}/{target}"
    return Mount(str(host), target, mount.readonly)


def _volume_flag(mount: Mount, selinux_label: str | None) -> str:
    options = [*(["ro"] if mount.readonly else []), *([selinux_label] if selinux_label else [])]
    suffix = f":{','.join(options)}" if options else ""
    return f"{mount.host_path}:{mount.capsule_path}{suffix}"


class DockerCapsule(Capsule):
    """Starts a container with the workspace mounted; the agent's commands run inside it via `docker exec`."""

    def __init__(
        self,
        workspace: Path | str,
        agent_factory: AgentClientFactory,
        *,
        image_name: str | None = None,
        container_uid: int | None = None,
        container_gid: int | None = None,
        selinux_label: str | None = "z",
        mounts: Sequence[Mount] = (),
        env: Mapping[str, str] | None = None,
        network: str | Sequence[str] | None = None,
        groups: Sequence[str | int] = (),
        devices: Sequence[str] = (),
        cpus: float | None = None,
        docker: CommandExecutor | None = None,
    ) -> None:
        self._docker = docker or execute
        host_workspace = Path(workspace)
        uid = container_uid if container_uid is not None else os.getuid()
        gid = container_gid if container_gid is not None else os.getgid()
        git_mounts, repo = _git_mounts(host_workspace)
        image = image_name or default_image_name(repo)
        self._check_image_uid(image, uid)

        volumes = [
            Mount(str(host_workspace), CAPSULE_WORKSPACE),
            *git_mounts,
            *(_resolve_user_mount(mount) for mount in mounts),
        ]
        self._container = f"orb-{uuid4()}"
        args = ["docker", "run", "-d", "--name", self._container]
        for key, value in {**(env or {}), "HOME": CAPSULE_HOME}.items():
            args += ["-e", f"{key}={value}"]
        for mount in volumes:
            args += ["-v", _volume_flag(mount, selinux_label)]
        args += ["-w", CAPSULE_WORKSPACE, "--user", f"{uid}:{gid}"]
        for name in [network] if isinstance(network, str) else list(network or ()):
            args += ["--network", name]
        for group in groups:
            args += ["--group-add", str(group)]
        for device in devices:
            args += ["--device", device]
        if cpus is not None:
            args += ["--cpus", str(cpus)]
        command = [*args, image]
        checked_output(" ".join(command), self._docker(command))

        self._closed = False
        atexit.register(self._remove)
        self._agent = agent_factory(self._exec_in_container)

    @property
    def workspace(self) -> str:
        return CAPSULE_WORKSPACE

    def run(
        self,
        prompt: str,
        prompt_args: Mapping[str, str] | None = None,
        options: AgentOptions | None = None,
    ) -> AgentResult:
        return self._agent.run(prompt, prompt_args, options)

    def exec(self, command: str, *, timeout_s: float | None = None) -> str:
        return checked_output(command, self._exec_in_container(command, timeout_s=timeout_s))

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        atexit.unregister(self._remove)
        self._docker(("docker", "stop", self._container))
        self._docker(("docker", "rm", self._container))

    def _exec_in_container(
        self, command: Sequence[str] | str, *, timeout_s: float | None = None
    ) -> CommandResult:
        args = ["docker", "exec", "-w", CAPSULE_WORKSPACE, self._container]
        args += ["sh", "-c", command] if isinstance(command, str) else list(command)
        return self._docker(args, timeout_s=timeout_s)

    def _remove(self) -> None:
        self._docker(("docker", "rm", "-f", self._container))

    def _check_image_uid(self, image: str, expected_uid: int) -> None:
        result = self._docker(("docker", "image", "inspect", image, "--format", "{{.Config.User}}"))
        if result.returncode != 0:
            raise OrbError(f"Image '{image}' not found locally; build it before using the docker capsule.")
        uid_part = result.stdout.strip().split(":")[0]
        # An image with no USER, or a named one, cannot be compared.
        if uid_part.isdigit() and int(uid_part) != expected_uid:
            raise OrbError(
                f"UID mismatch: image '{image}' was built with UID {uid_part}, expected {expected_uid}; "
                f"rebuild the image or pass container_uid={uid_part}."
            )
