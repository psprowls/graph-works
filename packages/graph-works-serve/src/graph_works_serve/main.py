"""`gw-serve`: launch the sidecar for one workspace.

The only uvicorn import. The workspace is resolved once here; handlers
re-derive the layout from that fixed root per request.
"""

from __future__ import annotations

import argparse
import importlib.metadata
import os
import secrets
import signal
import socket
import sys
import types
from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, NoReturn, cast
from urllib.parse import urlsplit

import uvicorn
from graph_works_core.workspace import discovery
from graph_works_core.workspace.errors import WorkspaceNotFound

from graph_works_serve.app import build_app
from graph_works_serve.catalog import render
from graph_works_serve.context import ServeContext
from graph_works_serve.discovery_file import ServeRecord, claim_instance, record_path, remove_if_owned, write_record
from graph_works_serve.routes import ROUTES

HOST = "127.0.0.1"
NOT_INITIALIZED = 3
ALREADY_RUNNING = 1


def version() -> str:
    """Return the installed sidecar version."""
    return importlib.metadata.version("graph-works-serve")


def bind(port: int) -> socket.socket:
    """Bind and listen on the loopback address."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind((HOST, port))
    sock.listen(128)
    return sock


class _Server(uvicorn.Server):
    """Close the change hub before uvicorn's graceful shutdown.

    uvicorn waits for open connections *before* running the lifespan
    shutdown, and an SSE stream never closes by itself. Closing the hub ends
    every stream, the connections drain, and the lifespan then stops the
    watcher. `close_threadsafe` because this runs inside a signal handler.
    """

    def handle_exit(self, sig: int, frame: types.FrameType | None) -> None:
        hub = getattr(getattr(self.config.app, "state", None), "hub", None)
        if hub is not None:
            hub.close_threadsafe()
        super().handle_exit(sig, frame)


def serve(app: object, sock: socket.socket) -> None:
    """Run Uvicorn with access logging disabled and the change-source lifespan enabled."""
    config = uvicorn.Config(cast(Any, app), access_log=False, log_level="warning", lifespan="on")
    _Server(config).run(sockets=[sock])


def parse_origin(value: str) -> str:
    """An exact ``scheme://host[:port]`` origin, or an argparse error naming *value*."""
    parts = urlsplit(value)
    try:
        port_ok = parts.port is None or parts.port > 0
    except ValueError:
        port_ok = False
    if (
        parts.scheme not in {"http", "https"}
        or not parts.hostname
        or parts.username is not None
        or not port_ok
        or value != f"{parts.scheme}://{parts.netloc}"
    ):
        raise argparse.ArgumentTypeError(f"invalid origin {value!r}: expected http(s)://host[:port]")
    return value


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="gw-serve", description="Serve one graph-works workspace over loopback HTTP.")
    parser.add_argument("--workspace", default="", help="Workspace path (default: GRAPH_WORKS_DIR, then git walk-up).")
    parser.add_argument("--port", type=int, default=0, help="Port on 127.0.0.1 (default 0: ephemeral).")
    parser.add_argument(
        "--allow-origin",
        action="append",
        type=parse_origin,
        default=[],
        metavar="ORIGIN",
        help="Answer CORS for this exact origin (repeatable), e.g. http://127.0.0.1:4780.",
    )
    parser.add_argument("--describe", action="store_true", help="Print the route catalog as JSON and exit.")
    return parser


def main(argv: Sequence[str] | None = None, *, serve_fn: Callable[[object, socket.socket], None] = serve) -> int:
    """Run one sidecar instance and return its process exit status."""
    args = _parser().parse_args(argv)
    if args.describe:
        sys.stdout.write(render(ROUTES, version()))
        return 0
    try:
        layout = discovery.resolve(workspace=args.workspace or None, cwd=Path.cwd(), environ=os.environ)
    except WorkspaceNotFound as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return NOT_INITIALIZED
    path = record_path(layout.cache_dir)
    with claim_instance(path) as claim:
        if not claim.acquired:
            location = f" at {claim.running.url()}" if claim.running is not None else ""
            print(f"gw-serve already running for {layout.root}{location}", file=sys.stderr)
            return ALREADY_RUNNING
        sock = bind(args.port)
        port = sock.getsockname()[1]
        token = secrets.token_urlsafe(32)
        pid = os.getpid()
        context = ServeContext(root=layout.root, cwd=Path.cwd(), port=port, pid=pid, gw_version=version())
        try:
            write_record(
                path,
                ServeRecord(
                    pid=pid,
                    host=HOST,
                    port=port,
                    token=token,
                    gw_version=version(),
                    workspace=str(layout.root),
                    started_at=datetime.now(UTC).isoformat(timespec="seconds"),
                ),
            )
            print(f"gw-serve listening on http://{HOST}:{port} (serve.json: {path})", file=sys.stderr)
            # Uvicorn 0.53 restores and replays captured signals after its graceful
            # shutdown. Keep a temporary parent handler so SIGTERM still requests
            # Uvicorn's graceful shutdown but does not subsequently terminate us.
            previous_term = signal.signal(signal.SIGTERM, lambda _signal, _frame: None)
            try:
                serve_fn(build_app(context, token=token, allow_origins=frozenset(args.allow_origin)), sock)
            finally:
                signal.signal(signal.SIGTERM, previous_term)
        finally:
            remove_if_owned(path, pid)
            sock.close()
    return 0


def run() -> NoReturn:
    """Exit the console script with :func:`main`'s status."""
    raise SystemExit(main())


if __name__ == "__main__":  # pragma: no cover
    run()
