"""`serve.json` sidecar discovery, liveness, and ownership operations.

POSIX behaviour (see README ``## Platform``): the ``0600`` mode and
``os.kill(pid, 0)`` liveness probe.  Each is confined to one function so a
native-Windows implementation can be added independently.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import urllib.request
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from pathlib import Path

if sys.platform != "win32":
    from fcntl import LOCK_EX, LOCK_NB, LOCK_UN, flock
else:
    LOCK_EX = 0
    LOCK_NB = 0
    LOCK_UN = 0

    def flock(_: int, __: int) -> None:
        """Make the POSIX-only lock seam explicit to the Windows type arm."""
        msg = "serve.json locking requires POSIX or WSL"
        raise OSError(msg)


SCHEMA_VERSION = 1
FILENAME = "serve.json"


@dataclass(frozen=True, slots=True)
class ServeRecord:
    """The private connection details for one running loopback sidecar."""

    pid: int
    host: str
    port: int
    token: str
    gw_version: str
    workspace: str
    started_at: str

    def url(self) -> str:
        """Return this sidecar's loopback base URL."""
        return f"http://{self.host}:{self.port}"


@dataclass(frozen=True, slots=True)
class InstanceClaim:
    """The result of attempting to reserve a workspace for one sidecar."""

    acquired: bool
    running: ServeRecord | None


def record_path(cache_dir: Path) -> Path:
    """Return the cache location used for the discovery record."""
    return cache_dir / FILENAME


@contextmanager
def _record_lock(path: Path) -> Iterator[None]:
    """Serialize record replacement and owner cleanup across POSIX processes."""
    if sys.platform == "win32":
        msg = "serve.json locking requires POSIX or WSL"
        raise OSError(msg)
    lock_path = path.with_name(f".{path.name}.lock")
    descriptor = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o600)
    try:
        os.chmod(lock_path, 0o600)  # noqa: PTH101 -- private lock filename
        flock(descriptor, LOCK_EX)
        try:
            yield
        finally:
            flock(descriptor, LOCK_UN)
    finally:
        os.close(descriptor)


@contextmanager
def claim_instance(
    path: Path,
    *,
    alive: Callable[[int], bool] | None = None,
    healthy: Callable[[ServeRecord], bool] | None = None,
) -> Iterator[InstanceClaim]:
    """Hold the nonblocking lifetime claim for *path* while a sidecar runs.

    The independent lock deliberately spans the interval before HTTP health is
    available.  ``serve.json`` remains a replaceable discovery record, while
    this lock is the authority that prevents a simultaneous launcher from
    replacing a first sidecar's pre-readiness record.
    """
    if sys.platform == "win32":
        msg = "serve.json locking requires POSIX or WSL"
        raise OSError(msg)
    if alive is None:
        alive = pid_alive
    if healthy is None:
        healthy = health_ok
    path.parent.mkdir(parents=True, exist_ok=True)
    lock_path = path.with_name(f".{path.name}.instance.lock")
    descriptor = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o600)
    locked = False
    try:
        os.chmod(lock_path, 0o600)  # noqa: PTH101 -- private lock filename
        try:
            flock(descriptor, LOCK_EX | LOCK_NB)
        except BlockingIOError:
            yield InstanceClaim(acquired=False, running=read_record(path))
            return
        locked = True
        running = live_instance(path, alive=alive, healthy=healthy)
        yield InstanceClaim(acquired=running is None, running=running)
    finally:
        try:
            if locked:
                flock(descriptor, LOCK_UN)
        finally:
            os.close(descriptor)


def write_record(path: Path, record: ServeRecord) -> None:
    """Atomically write *record* at mode ``0600`` in *path*'s directory."""
    path.parent.mkdir(parents=True, exist_ok=True)
    data = json.dumps({"schema_version": SCHEMA_VERSION, **asdict(record)}, indent=2) + "\n"
    descriptor, temporary = tempfile.mkstemp(prefix=".serve-", suffix=".json", dir=path.parent)
    try:
        try:
            os.chmod(temporary, 0o600)  # noqa: PTH101 -- temporary filename from mkstemp
        except BaseException:
            os.close(descriptor)
            raise
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as output:
            output.write(data)
        with _record_lock(path):
            os.replace(temporary, path)  # noqa: PTH105 -- required atomic replacement
    except BaseException:
        Path(temporary).unlink(missing_ok=True)
        raise


def read_record(path: Path) -> ServeRecord | None:
    """Read a valid local discovery record, treating all other data as stale."""
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(raw, dict) or raw.get("schema_version") != SCHEMA_VERSION:
        return None

    required = {
        "schema_version",
        "pid",
        "host",
        "port",
        "token",
        "gw_version",
        "workspace",
        "started_at",
    }
    if set(raw) != required:
        return None

    pid = raw["pid"]
    host = raw["host"]
    port = raw["port"]
    token = raw["token"]
    gw_version = raw["gw_version"]
    workspace = raw["workspace"]
    started_at = raw["started_at"]
    if (
        type(pid) is not int
        or pid <= 0
        or host != "127.0.0.1"
        or type(port) is not int
        or not 1 <= port <= 65535
        or any(type(value) is not str for value in (token, gw_version, workspace, started_at))
    ):
        return None
    return ServeRecord(
        pid=pid,
        host=host,
        port=port,
        token=token,
        gw_version=gw_version,
        workspace=workspace,
        started_at=started_at,
    )


def pid_alive(pid: int) -> bool:
    """Return whether positive *pid* exists, without signalling it."""
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OverflowError:
        return False
    except OSError:
        return False
    return True


def health_ok(record: ServeRecord, *, timeout: float = 2.0) -> bool:
    """Return whether *record* accepts its bearer token at ``/v1/health``."""
    request = urllib.request.Request(f"{record.url()}/v1/health", headers={"Authorization": f"Bearer {record.token}"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return bool(response.status == 200)
    except (OSError, ValueError):
        return False


def live_instance(
    path: Path,
    *,
    alive: Callable[[int], bool] = pid_alive,
    healthy: Callable[[ServeRecord], bool] = health_ok,
) -> ServeRecord | None:
    """Return the live instance named by *path*, if it is still healthy."""
    record = read_record(path)
    if record is None or not alive(record.pid) or not healthy(record):
        return None
    return record


def remove_if_owned(path: Path, pid: int) -> bool:
    """Remove *path* only if its record belongs to *pid*."""
    try:
        with _record_lock(path):
            record = read_record(path)
            if record is None or record.pid != pid:
                return False
            path.unlink(missing_ok=True)
            return True
    except FileNotFoundError:
        return False
