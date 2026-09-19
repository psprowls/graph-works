"""The route catalog emitted by ``gw-serve --describe``."""

from __future__ import annotations

import json
from collections.abc import Sequence

from graph_works_serve.routes import RouteSpec

SCHEMA_VERSION = 1


def describe(routes: Sequence[RouteSpec], version: str) -> dict[str, object]:
    """Project route specifications to stable, JSON-ready catalog data."""
    return {
        "schema_version": SCHEMA_VERSION,
        "server": "graph-works-serve",
        "version": version,
        "routes": [
            {
                "method": spec.method,
                "path": spec.path,
                "summary": spec.summary,
                "params": [
                    {
                        "name": param.name,
                        "type": param.type,
                        "required": param.required,
                        "default": param.default,
                        "location": param.location,
                        "summary": param.summary,
                    }
                    for param in spec.params
                ],
                "response": spec.response,
            }
            for spec in sorted(routes, key=lambda spec: (spec.path, spec.method))
        ],
    }


def render(routes: Sequence[RouteSpec], version: str) -> str:
    """Render the route catalog as indented JSON with LF line endings."""
    return json.dumps(describe(routes, version), indent=2) + "\n"
