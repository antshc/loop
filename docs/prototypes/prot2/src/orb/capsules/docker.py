from __future__ import annotations

import atexit
import os
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

from orb.capsules.worktree import WorktreeCapsule, WorktreeCapsuleProvider
from orb.errors import OrbError
from orb.process import CommandResult, execute, run_command
from orb.worktree import Worktree

CAPSULE_HOME = "/home/agent"
CAPSULE_WORKTREE = Path(CAPSULE_HOME) / "workspace"


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
        target = f"{CAPSULE_WORKTREE}/{target}"
    return Mount(str(host), target, mount.readonly)


def _volume_flag(mount: Mount, selinux_label: str | None) -> str:
    options = [*(["ro"] if mount.readonly else []), *([selinux_label] if selinux_label else [])]
    suffix = f":{','.join(options)}" if options else ""
    return f"{mount.host_path}:{mount.capsule_path}{suffix}"


def _check_image_uid(image: str, expected_uid: int) -> None:
    result = execute(("docker", "image", "inspect", image, "--format", "{{.Config.User}}"))
    if result.returncode != 0:
        raise OrbError(f"Image '{image}' not found locally; build it before using the docker capsule.")
    uid_part = result.stdout.strip().split(":")[0]
    # An image with no USER, or a named one, cannot be compared.
    if uid_part.isdigit() and int(uid_part) != expected_uid:
        raise OrbError(
            f"UID mismatch: image '{image}' was built with UID {uid_part}, expected {expected_uid}; "
            f"rebuild the image or pass container_uid={uid_part} to docker()."
        )


class DockerCapsule(WorktreeCapsule):
    """A worktree bind-mounted into a container; commands run inside it."""

    def __init__(self, worktree: Worktree, container: str) -> None:
        super().__init__(worktree)
        self._container = container
        self._closed = False
        atexit.register(self._remove)

    def execute(
        self,
        command: Sequence[str] | str,
        *,
        cwd: Path | None = None,
        timeout_s: float | None = None,
    ) -> CommandResult:
        args = ["docker", "exec"]
        if cwd is not None:
            args += ["-w", str(cwd)]
        args.append(self._container)
        args += ["sh", "-c", command] if isinstance(command, str) else list(command)
        return execute(args, timeout_s=timeout_s)

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        atexit.unregister(self._remove)
        execute(("docker", "stop", self._container))
        execute(("docker", "rm", self._container))
        super().close()

    def _remove(self) -> None:
        execute(("docker", "rm", "-f", self._container))


class DockerCapsuleProvider(WorktreeCapsuleProvider):
    """Worktree capsules whose commands run in a container with the worktree mounted at /home/agent/workspace."""

    def __init__(
        self,
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
        worktrees_dir: Path | None = None,
    ) -> None:
        super().__init__(env=env, worktrees_dir=worktrees_dir)
        self._image_name = image_name
        self._uid = container_uid if container_uid is not None else os.getuid()
        self._gid = container_gid if container_gid is not None else os.getgid()
        self._selinux_label = selinux_label
        self._mounts = tuple(_resolve_user_mount(mount) for mount in mounts)
        self._networks = [network] if isinstance(network, str) else list(network or ())
        self._groups = tuple(groups)
        self._devices = tuple(devices)
        self._cpus = cpus

    @property
    def name(self) -> str:
        return "docker"

    def _attach(self, worktree: Worktree) -> DockerCapsule:
        worktree_path = worktree.path
        git_mounts, repo = _git_mounts(worktree_path)
        image = self._image_name or default_image_name(repo)
        _check_image_uid(image, self._uid)

        mounts = [Mount(str(worktree_path), str(CAPSULE_WORKTREE)), *git_mounts, *self._mounts]
        container = f"orb-{uuid4()}"
        args = ["docker", "run", "-d", "--name", container]
        for key, value in {**self._env, "HOME": CAPSULE_HOME}.items():
            args += ["-e", f"{key}={value}"]
        for mount in mounts:
            args += ["-v", _volume_flag(mount, self._selinux_label)]
        args += ["-w", str(CAPSULE_WORKTREE), "--user", f"{self._uid}:{self._gid}"]
        for network in self._networks:
            args += ["--network", network]
        for group in self._groups:
            args += ["--group-add", str(group)]
        for device in self._devices:
            args += ["--device", device]
        if self._cpus is not None:
            args += ["--cpus", str(self._cpus)]
        run_command([*args, image])
        return DockerCapsule(worktree, container)


def docker(
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
    worktrees_dir: Path | None = None,
) -> DockerCapsuleProvider:
    return DockerCapsuleProvider(
        image_name=image_name,
        container_uid=container_uid,
        container_gid=container_gid,
        selinux_label=selinux_label,
        mounts=mounts,
        env=env,
        network=network,
        groups=groups,
        devices=devices,
        cpus=cpus,
        worktrees_dir=worktrees_dir,
    )
