"""`gw wiki` sub-app and root-level wiki command registration."""

from __future__ import annotations

import typer

from graph_works_cli.wiki_cli.bootstrap import bootstrap
from graph_works_cli.wiki_cli.drift import drift
from graph_works_cli.wiki_cli.ingest import ingest
from graph_works_cli.wiki_cli.lint import lint
from graph_works_cli.wiki_cli.maintenance import archive, index, stats
from graph_works_cli.wiki_cli.proposals import proposal_app, proposals
from graph_works_cli.wiki_cli.query import query
from graph_works_cli.wiki_cli.scan import scan
from graph_works_cli.wiki_cli.tags import tags_app

wiki_app = typer.Typer(name="wiki", help="Wiki scan/ingest/query/lint.", no_args_is_help=True)
wiki_app.command(name="lint")(lint)
wiki_app.command(name="drift")(drift)
wiki_app.command(name="stats")(stats)
wiki_app.command(name="index")(index)
wiki_app.command(name="archive")(archive)
wiki_app.add_typer(tags_app, name="tags")
wiki_app.command(name="proposals")(proposals)
wiki_app.add_typer(proposal_app, name="proposal")


def register_root_commands(app: typer.Typer) -> None:
    """Install finished wiki commands at the ``gw`` root."""
    app.command(name="bootstrap")(bootstrap)
    app.command(name="scan")(scan)
    app.command(name="ingest")(ingest)
    app.command(name="query")(query)
