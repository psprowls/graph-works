"""Verbose-logging configuration for the `gw` CLI.

Installs stderr logging handlers gated by the root `-v/-vv` flag. Absent the flag (verbosity == 0)
this is a no-op: no handler is installed and the CLI produces no new stderr output.

The fan-out trace logger (`subagents_io.pool.trace` — `subagents_io.pool`'s own `__name__ + ".trace"`)
gets a DEDICATED handler with a bare `%(message)s` formatter and `propagate=False`, so live per-item
completion lines stay byte-identical to `gw util trace` output.
"""

from __future__ import annotations

import logging
import sys

# Must match subagents_io.pool's own trace logger name (`__name__ + ".trace"`).
_FANOUT_TRACE_LOGGER = "subagents_io.pool.trace"

# Marks handlers this module installed, so repeated calls are idempotent.
_HANDLER_FLAG = "_gw_verbose_handler"


def configure_verbose_logging(verbosity: int) -> None:
    """Install stderr logging handlers for the given verbosity.

    verbosity == 0 -> no-op (no handler installed).
    verbosity == 1 -> INFO  on the root logger.
    verbosity >= 2 -> DEBUG on the root logger.

    All output goes to stderr; stdout stays clean. Idempotent: safe to call more than once per
    process; never duplicates handlers.
    """
    if verbosity <= 0:
        return

    level = logging.INFO if verbosity == 1 else logging.DEBUG

    root = logging.getLogger()
    if not _has_gw_handler(root):
        root_handler = logging.StreamHandler(sys.stderr)
        root_handler.setFormatter(logging.Formatter("%(levelname)s %(name)s: %(message)s"))
        setattr(root_handler, _HANDLER_FLAG, True)
        root.addHandler(root_handler)
    for marked_handler in root.handlers:
        if getattr(marked_handler, _HANDLER_FLAG, False):
            marked_handler.setLevel(level)
    root.setLevel(level)

    # Dedicated handler for per-item trace lines: bare message, no propagation, so the
    # LEVEL/name prefix never leaks into trace-format output.
    trace_logger = logging.getLogger(_FANOUT_TRACE_LOGGER)
    if not _has_gw_handler(trace_logger):
        trace_handler = logging.StreamHandler(sys.stderr)
        trace_handler.setFormatter(logging.Formatter("%(message)s"))
        setattr(trace_handler, _HANDLER_FLAG, True)
        trace_logger.addHandler(trace_handler)
    trace_logger.setLevel(logging.INFO)
    trace_logger.propagate = False

    for noisy in ("boto3", "botocore", "urllib3"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


def _has_gw_handler(logger: logging.Logger) -> bool:
    """True if this module already installed a handler on `logger`."""
    return any(getattr(h, _HANDLER_FLAG, False) for h in logger.handlers)
