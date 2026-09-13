"""Read Git objects in a private bare repository, never a working checkout."""

from __future__ import annotations

import re
import shlex
import subprocess
from dataclasses import dataclass
from pathlib import Path
from tempfile import TemporaryDirectory
from urllib.parse import urlsplit

from .machine import git_environment
from .records import Snapshot, SnapshotEntry, SourceIdentity


class GitError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(f"{code}: {message}")


def validate_locator(locator: str) -> str:
    if not locator or locator.startswith("-") or "\x00" in locator:
        raise GitError("git.failed", "Invalid Git locator")
    if "://" in locator:
        parsed = urlsplit(locator)
        if parsed.scheme not in {"https", "ssh"} or not parsed.hostname:
            raise GitError("git.failed", "Unsupported Git transport")
        return locator
    if "::" in locator:
        raise GitError("git.failed", "External remote helpers are not supported")
    if re.match(r"^(?:[^/@:]+@)?[^/:]+:.+", locator) and not re.match(r"^[A-Za-z]:[/\\]", locator):
        return locator
    return str(Path(locator).absolute())


@dataclass(frozen=True)
class LocalGitRunner:
    executable: str = "git"
    timeout: float = 60

    def acquire(self, locator: str, revision: str) -> Snapshot:
        locator_arg = validate_locator(locator)
        if not revision or revision.startswith("-") or any(char in revision for char in "\x00\r\n :*?["):
            raise GitError("git.revision", "An explicit valid revision is required")
        with TemporaryDirectory(prefix="plugin-fork-git-") as temporary:
            home = Path(temporary)
            repo = home / "objects.git"
            env = git_environment(home)
            ssh_config = home / "ssh_config"
            ssh_config.write_bytes(b"")

            def run(*args: str, revision_command: bool = False) -> bytes:
                command = [
                    self.executable,
                    "-c",
                    "core.hooksPath=" + str(home / "no-hooks"),
                    "-c",
                    "core.attributesFile=" + str(home / "no-attributes"),
                    "-c",
                    "protocol.allow=never",
                    "-c",
                    "protocol.file.allow=always",
                    "-c",
                    "protocol.https.allow=always",
                    "-c",
                    "protocol.ssh.allow=always",
                    "-c",
                    "fetch.recurseSubmodules=false",
                    "-c",
                    "http.followRedirects=false",
                    "-c",
                    "core.sshCommand=ssh -F " + shlex.quote(str(ssh_config)),
                    *args,
                ]
                try:
                    return subprocess.run(
                        command, env=env, cwd=home, capture_output=True, check=True, timeout=self.timeout
                    ).stdout
                except FileNotFoundError as exc:
                    raise GitError("git.missing", "Git executable was not found") from exc
                except subprocess.CalledProcessError as exc:
                    message = exc.stderr.decode("utf-8", errors="replace").strip()
                    missing_ref = "couldn't find remote ref" in message or "not our ref" in message
                    code = "git.revision" if revision_command or missing_ref else "git.failed"
                    raise GitError(code, message) from exc
                except (OSError, subprocess.TimeoutExpired) as exc:
                    raise GitError("git.failed", str(exc)) from exc

            run("init", "--bare", "--template=", str(repo))
            run(
                "--git-dir",
                str(repo),
                "fetch",
                "--no-tags",
                "--no-recurse-submodules",
                "--",
                locator_arg,
                revision,
            )
            commit = (
                run("--git-dir", str(repo), "rev-parse", "--verify", "FETCH_HEAD^{commit}", revision_command=True)
                .decode("ascii")
                .strip()
            )
            tree = run("--git-dir", str(repo), "ls-tree", "-rtz", "--full-tree", commit)
            entries: list[SnapshotEntry] = []
            for record in tree.split(b"\0"):
                if not record:
                    continue
                header, raw_path = record.split(b"\t", 1)
                mode, kind, oid = header.split(b" ")
                if (kind, mode) not in {
                    (b"blob", b"100644"),
                    (b"blob", b"100755"),
                    (b"blob", b"120000"),
                    (b"tree", b"040000"),
                }:
                    raise GitError("git.failed", "Unsupported Git entry (including submodules)")
                try:
                    path = raw_path.decode("utf-8")
                except UnicodeDecodeError as exc:
                    raise GitError("git.failed", "Git path is not UTF-8") from exc
                if kind == b"tree":
                    entries.append(SnapshotEntry(path, b"", "directory", 0))
                    continue
                content = run("--git-dir", str(repo), "cat-file", "blob", oid.decode("ascii"))
                entries.append(
                    SnapshotEntry(path, content, "symlink" if mode == b"120000" else "file", int(mode, 8) & 0o7777)
                )
            return Snapshot(
                SourceIdentity("git", locator, commit, revision), tuple(sorted(entries, key=lambda e: e.path))
            )

    def merge_text(self, base: bytes, local: bytes, incoming: bytes) -> tuple[bytes, bool]:
        """Run only merge-file over private byte inputs, with bounded execution."""
        with TemporaryDirectory(prefix="plugin-fork-merge-") as temporary:
            home = Path(temporary)
            for name, content in (("BASE", base), ("LOCAL", local), ("INCOMING", incoming)):
                (home / name).write_bytes(content)
            try:
                result = subprocess.run(
                    [
                        self.executable,
                        "-c",
                        "core.attributesFile=" + str(home / "no-attributes"),
                        "merge-file",
                        "-p",
                        "--diff3",
                        "-L",
                        "LOCAL",
                        "-L",
                        "BASE",
                        "-L",
                        "INCOMING",
                        "--",
                        "LOCAL",
                        "BASE",
                        "INCOMING",
                    ],
                    env=git_environment(home),
                    cwd=home,
                    capture_output=True,
                    check=False,
                    timeout=self.timeout,
                )
            except FileNotFoundError as exc:
                raise GitError("git.missing", "Git executable was not found") from exc
            except (OSError, subprocess.TimeoutExpired) as exc:
                raise GitError("git.failed", str(exc)) from exc
            # merge-file caps positive conflict counts at 127; errors are -1/255.
            if not 0 <= result.returncode <= 127:
                raise GitError("git.failed", result.stderr.decode("utf-8", errors="replace").strip())
            return result.stdout, result.returncode > 0
