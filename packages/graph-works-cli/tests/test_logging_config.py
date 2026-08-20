"""Unit tests for graph_works_cli.logging_config.configure_verbose_logging."""

from __future__ import annotations

import logging
import sys

import pytest


@pytest.fixture(autouse=True)
def _restore_logging():
    """Snapshot and restore global logging state — these tests mutate the root logger."""
    root = logging.getLogger()
    saved_handlers = root.handlers[:]
    saved_handler_levels = [(handler, handler.level) for handler in saved_handlers]
    saved_level = root.level
    trace = logging.getLogger("subagents_io.pool.trace")
    saved_trace_handlers = trace.handlers[:]
    saved_trace_handler_levels = [(handler, handler.level) for handler in saved_trace_handlers]
    saved_trace_level = trace.level
    saved_trace_propagate = trace.propagate
    restored_levels = {
        name: logging.getLogger(name).level for name in ("boto3", "botocore", "urllib3", "explicit.debug.child")
    }
    yield
    root.handlers[:] = saved_handlers
    for handler, level in saved_handler_levels:
        handler.setLevel(level)
    root.setLevel(saved_level)
    trace.handlers[:] = saved_trace_handlers
    for handler, level in saved_trace_handler_levels:
        handler.setLevel(level)
    trace.setLevel(saved_trace_level)
    trace.propagate = saved_trace_propagate
    for name, level in restored_levels.items():
        logging.getLogger(name).setLevel(level)


def _gw_handlers(logger):
    return [h for h in logger.handlers if getattr(h, "_gw_verbose_handler", False)]


def test_verbosity_zero_is_noop():
    from graph_works_cli.logging_config import configure_verbose_logging

    root = logging.getLogger()
    before = root.handlers[:]
    configure_verbose_logging(0)
    assert root.handlers == before
    assert _gw_handlers(root) == []


def test_verbosity_one_installs_info_stderr_handler():
    from graph_works_cli.logging_config import configure_verbose_logging

    configure_verbose_logging(1)
    root = logging.getLogger()
    assert root.level == logging.INFO
    handlers = _gw_handlers(root)
    assert len(handlers) == 1
    assert handlers[0].stream is sys.stderr


def test_verbosity_two_installs_debug_handler():
    from graph_works_cli.logging_config import configure_verbose_logging

    configure_verbose_logging(2)
    assert logging.getLogger().level == logging.DEBUG


def test_verbosity_one_filters_debug_from_explicit_debug_child(capsys):
    from graph_works_cli.logging_config import configure_verbose_logging

    configure_verbose_logging(1)
    child = logging.getLogger("explicit.debug.child")
    child.setLevel(logging.DEBUG)
    child.debug("debug should be filtered")

    assert "debug should be filtered" not in capsys.readouterr().err


def test_trace_logger_has_bare_formatter_and_no_propagate():
    from graph_works_cli.logging_config import configure_verbose_logging

    configure_verbose_logging(1)
    trace = logging.getLogger("subagents_io.pool.trace")
    handlers = _gw_handlers(trace)
    assert len(handlers) == 1
    assert handlers[0].formatter._fmt == "%(message)s"
    assert trace.propagate is False


def test_boto_loggers_pinned_to_warning():
    from graph_works_cli.logging_config import configure_verbose_logging

    configure_verbose_logging(2)
    for name in ("boto3", "botocore", "urllib3"):
        assert logging.getLogger(name).level == logging.WARNING


def test_idempotent_no_duplicate_handlers():
    from graph_works_cli.logging_config import configure_verbose_logging

    configure_verbose_logging(1)
    configure_verbose_logging(1)
    assert len(_gw_handlers(logging.getLogger())) == 1
    assert len(_gw_handlers(logging.getLogger("subagents_io.pool.trace"))) == 1


def test_reconfiguration_updates_the_existing_root_handler_threshold():
    from graph_works_cli.logging_config import configure_verbose_logging

    configure_verbose_logging(1)
    handler = _gw_handlers(logging.getLogger())[0]
    assert handler.level == logging.INFO

    configure_verbose_logging(2)

    assert _gw_handlers(logging.getLogger()) == [handler]
    assert handler.level == logging.DEBUG
