"""Consistent SQLite checkpoints for callers coordinating a larger mutation.

The caller serializes its operation, calls the yielded restore function on
failure, and leaves it unused on success. SQLite backup includes committed WAL
pages and restores through the existing database, so open readers remain valid.
An initially absent graph restores to an empty, queryable cache.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Callable, Iterator
from contextlib import closing, contextmanager
from pathlib import Path
from tempfile import TemporaryDirectory

from code_graph_io import schema


@contextmanager
def graph_checkpoint(graph_dir: Path) -> Iterator[Callable[[], None]]:
    """Save the graph until context exit; storage errors surface as `OSError`."""
    graph_dir.mkdir(parents=True, exist_ok=True)
    database = graph_dir / "code.db"
    with (
        TemporaryDirectory(prefix="graph-checkpoint-", dir=graph_dir) as temporary,
        closing(sqlite3.connect(Path(temporary) / "snapshot.db")) as snapshot,
    ):
        try:
            if database.exists():
                uri = database.resolve().as_uri() + "?mode=ro"
                with closing(sqlite3.connect(uri, uri=True)) as source:
                    source.backup(snapshot)
            else:
                schema.apply_schema(snapshot)
                snapshot.commit()
        except sqlite3.Error as exc:
            raise OSError(f"cannot create graph checkpoint: {exc}") from exc

        def restore() -> None:
            try:
                with closing(sqlite3.connect(database)) as destination:
                    snapshot.backup(destination)
            except sqlite3.Error as exc:
                raise OSError(f"cannot restore graph checkpoint: {exc}") from exc

        yield restore
