"""The declarative route table, shared by the app and route description."""

from __future__ import annotations

import os
import sys
from collections.abc import AsyncIterator, Awaitable, Callable, Iterable, Mapping
from dataclasses import dataclass
from datetime import date
from functools import partial
from pathlib import Path
from typing import Literal, cast

from config_io import RegistryError, StoreValidationError
from graph_works_core.agent_config import AGENTS
from graph_works_core.agent_config import show as show_agent_config
from graph_works_core.agent_config.records import AgentName
from graph_works_core.orchestrate.commands import run_orchestrate
from graph_works_core.util.commands import run_log_read
from graph_works_core.wiki_page.commands import run_page_read
from graph_works_core.work import commands as work
from graph_works_core.workspace import manifest
from graph_works_core.workspace.errors import WorkspaceError
from graph_works_core.workspace.layout import WorkspaceLayout
from graph_works_wire import agent_config as wire_agent_config
from graph_works_wire import config as wire_config
from graph_works_wire import util as wire_util
from graph_works_wire import wiki as wire_wiki
from graph_works_wire import work as wire_work

from graph_works_serve import mutations, sse
from graph_works_serve.context import Reply, ServeContext
from graph_works_serve.errors import Catch, call, refusal
from graph_works_serve.hub import Hub
from graph_works_serve.mutation_specs import ADVANCE, ARCHIVE, DECIDE
from graph_works_serve.mutations import MutationSpec, Outcome
from graph_works_serve.params import Param, ParamError, parse

Handler = Callable[[ServeContext, Mapping[str, object]], Reply]
MutationHandler = Callable[[WorkspaceLayout, bytes, str | None], Outcome]
StreamHandler = Callable[[Hub, Callable[[], Awaitable[bool]]], AsyncIterator[bytes]]


@dataclass(frozen=True, slots=True)
class RouteSpec:
    """One endpoint and its framework-independent handler."""

    method: Literal["GET", "POST"]
    path: str
    summary: str
    params: tuple[Param, ...]
    handler: Handler | StreamHandler | MutationHandler
    response: Literal["json", "event-stream"] = "json"


def mutation_routes(spec: MutationSpec) -> tuple[RouteSpec, RouteSpec]:
    """Build the stateless plan/apply pair for a mutation specification."""
    return (
        RouteSpec(
            method="POST",
            path=f"{spec.route}/plan",
            params=spec.params,
            handler=partial(mutations.plan, spec),
            summary=f"{spec.summary}: plan (writes nothing) and digest it.",
        ),
        RouteSpec(
            method="POST",
            path=f"{spec.route}/apply",
            params=(*spec.params, *mutations.APPLY_FIELDS),
            handler=partial(mutations.apply, spec),
            summary=f"{spec.summary}: apply the plan whose digest is echoed; 409 if it went stale.",
        ),
    )


def handle_mutation(spec: RouteSpec, context: ServeContext, body: bytes, content_type: str | None) -> Outcome:
    """Resolve and dispatch together in the adapter's worker thread."""
    handler = cast(MutationHandler, spec.handler)
    return call(spec.path, lambda: handler(context.layout(), body, content_type), _ROUTED)


def handle(spec: RouteSpec, context: ServeContext, items: Iterable[tuple[str, str]]) -> Reply:
    """Parse a request's query items before dispatching to its route handler."""
    try:
        args = parse(spec.params, items)
    except ParamError as exc:
        return refusal(spec.path, "usage", str(exc))
    return cast(Handler, spec.handler)(context, args)


def _health(context: ServeContext, _args: Mapping[str, object]) -> Reply:
    return Reply(
        200,
        {
            "status": "ok",
            "gw_version": context.gw_version,
            "workspace": str(context.root),
            "pid": context.pid,
        },
    )


_LAYOUT = (Catch(WorkspaceError, "workspace", 4),)
_ROUTED = (*_LAYOUT, Catch(ValueError, "unresolved", 7), Catch(OSError, "io", 1))
_PATH = Param("path", "str", required=True, summary="Extensionless bundle-relative canonical concept path.")


def _str(args: Mapping[str, object], name: str) -> str | None:
    value = args[name]
    return cast(str, value) if value else None


def _status(context: ServeContext, _args: Mapping[str, object]) -> Reply:
    def run() -> Reply:
        return Reply(200, wire_work.status_payload(work.run_status(context.layout())))

    return call("/v1/work/status", run, (*_LAYOUT, Catch(OSError, "io", 1), Catch(ValueError, "io", 1)))


def _next(context: ServeContext, args: Mapping[str, object]) -> Reply:
    def run() -> Reply:
        layout = context.layout()
        result = work.run_next(
            layout,
            cast(str, args["path"]),
            descend=cast(bool, args["descend"]),
            dry_run=True,
        )
        return Reply(200, wire_work.next_payload(result, bundle_root=layout.bundle_dir))

    return call("/v1/work/next", run, _ROUTED)


def _orchestrate(context: ServeContext, args: Mapping[str, object]) -> Reply:
    def run() -> Reply:
        result = run_orchestrate(
            context.layout(),
            cast(str, args["path"]),
            live=cast(tuple[str, ...], args["live"]),
            repo_name=_str(args, "repo_name"),
        )
        return Reply(200, wire_work.orchestrate_payload(result))

    return call("/v1/work/orchestrate", run, _ROUTED)


def _decisions(context: ServeContext, args: Mapping[str, object]) -> Reply:
    def run() -> Reply:
        result = work.run_decision_list(
            context.layout(),
            cast(str, args["path"]),
            status=_str(args, "status"),
            affects=_str(args, "affects"),
            cites=_str(args, "cites"),
        )
        return Reply(200, wire_work.decision_payload(result))

    return call("/v1/work/decisions", run, _ROUTED)


def _item(context: ServeContext, args: Mapping[str, object]) -> Reply:
    def run() -> Reply:
        result = work.run_item_read(context.layout(), cast(str, args["path"]))
        payload = wire_work.item_payload(result)
        if result.refusal is not None:
            return refusal("/v1/work/item", "unresolved", f"{result.refusal}: {result.path}", payload=payload)
        return Reply(200, payload)

    return call("/v1/work/item", run, (*_LAYOUT, Catch(OSError, "io", 1)))


def _page(context: ServeContext, args: Mapping[str, object]) -> Reply:
    def run() -> Reply:
        result = run_page_read(context.layout(), cast(str, args["id"]))
        payload = wire_wiki.page_payload(result)
        if result.refusal is not None:
            return refusal("/v1/wiki/page", "unresolved", f"{result.refusal}: {result.id}", payload=payload)
        return Reply(200, payload)

    return call("/v1/wiki/page", run, (*_LAYOUT, Catch(OSError, "io", 1)))


def _log(context: ServeContext, args: Mapping[str, object]) -> Reply:
    def run() -> Reply:
        result = run_log_read(
            context.layout(),
            last=cast(int, args["last"]),
            op=_str(args, "op"),
            since=cast(date | None, args["since"]),
        )
        return Reply(200, wire_util.log_read_payload(result))

    return call("/v1/log", run, (*_LAYOUT, Catch(ValueError, "usage", 1), Catch(OSError, "io", 1)))


def _config(context: ServeContext, args: Mapping[str, object]) -> Reply:
    def run() -> Reply:
        layout = context.layout()
        key = _str(args, "key")
        if key is None:
            results = manifest.resolve_checked_all(layout, environ=os.environ)
            return Reply(200, wire_config.resolved_list_payload(results))
        result = manifest.resolve_checked_key(layout, key, environ=os.environ)
        return Reply(200, wire_config.resolved_payload(result))

    return call(
        "/v1/config",
        run,
        (Catch(RegistryError, "unresolved", 1), Catch(StoreValidationError, "workspace", 4), *_LAYOUT),
    )


def _agent_config(context: ServeContext, args: Mapping[str, object]) -> Reply:
    requested = cast(tuple[str, ...], args["agent"])
    unknown = sorted(set(requested) - set(AGENTS))
    if unknown:
        return refusal("/v1/agent-config", "usage", f"agent: unknown {', '.join(unknown)}; expected {'|'.join(AGENTS)}")
    agents = cast(tuple[AgentName, ...], requested) if requested else AGENTS
    user_id: int | None
    if sys.platform == "win32":
        user_id = None
    else:
        user_id = os.getuid()
    project = _str(args, "project")

    def run() -> Reply:
        if project is not None:
            report = show_agent_config(
                None,
                project=Path(project).expanduser(),
                home=Path.home(),
                env=os.environ,
                agents=agents,
                platform=sys.platform,
                user_id=user_id,
            )
        else:
            report = show_agent_config(
                context.layout(),
                home=Path.home(),
                env=os.environ,
                agents=agents,
                platform=sys.platform,
                user_id=user_id,
            )
        return Reply(200, wire_agent_config.agent_config_payload(report))

    return call("/v1/agent-config", run, _LAYOUT)


EVENTS_ROUTE = RouteSpec(
    method="GET",
    path="/v1/events",
    params=(),
    handler=sse.event_stream,
    summary="Stream workspace change events (Server-Sent Events).",
    response="event-stream",
)


ROUTES: tuple[RouteSpec, ...] = (
    EVENTS_ROUTE,
    RouteSpec("GET", "/v1/health", "Liveness and identity of this gw-serve process.", (), _health),
    RouteSpec("GET", "/v1/work/status", "Active-item rollup and the item worth resuming.", (), _status),
    RouteSpec(
        "GET",
        "/v1/work/next",
        "The stage to dispatch for an item (read-only; normalized is always null).",
        (_PATH, Param("descend", "bool", default=False, summary="Switch to the next actionable child leaf.")),
        _next,
    ),
    RouteSpec(
        "GET",
        "/v1/work/orchestrate",
        "The auto-drive dispatch plan for a subtree. Read-only.",
        (
            Param("path", "str", required=True, summary="Extensionless bundle-relative canonical root concept path."),
            Param("live", "csv", summary="Running dispatch keys."),
            Param("repo_name", "str", summary="Select among several declared repositories."),
        ),
        _orchestrate,
    ),
    RouteSpec(
        "GET",
        "/v1/work/decisions",
        "The owning epic's decisions, filtered; counts cover the whole ledger.",
        (
            _PATH,
            Param("status", "str", summary="answered|assumed|open|superseded."),
            Param("affects", "str", summary="Entries whose affects contain this work path."),
            Param("cites", "str", summary="Entries referencing this id, e.g. D-014."),
        ),
        _decisions,
    ),
    RouteSpec("GET", "/v1/work/item", "One work item: frontmatter, body, sources, owned references.", (_PATH,), _item),
    RouteSpec(
        "GET",
        "/v1/wiki/page",
        "One wiki page with outlinks, backlinks and broken links.",
        (Param("id", "str", required=True, summary="Extensionless bundle-relative page id."),),
        _page,
    ),
    RouteSpec(
        "GET",
        "/v1/log",
        "Workspace log entries, newest first (the /gw:log filters).",
        (
            Param("last", "int", default=10, minimum=0, summary="Entries to return after filtering."),
            Param("op", "str", summary="Operation label, case-insensitive."),
            Param("since", "date", summary="Inclusive YYYY-MM-DD lower bound."),
        ),
        _log,
    ),
    RouteSpec(
        "GET",
        "/v1/config",
        "Every resolved config key, or one key's value and origin.",
        (Param("key", "str", summary="One catalog key; omit to list all."),),
        _config,
    ),
    RouteSpec(
        "GET",
        "/v1/agent-config",
        "Each coding agent's configuration layers, trust and effective values.",
        (
            Param("project", "str", summary="Report this one directory instead of the workspace's projects."),
            Param("agent", "csv", summary="Limit to agents: claude,codex,pi."),
        ),
        _agent_config,
    ),
    *mutation_routes(ADVANCE),
    *mutation_routes(ARCHIVE),
    *mutation_routes(DECIDE),
)
