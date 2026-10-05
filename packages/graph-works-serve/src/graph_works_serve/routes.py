"""The declarative route table, shared by the app and route description."""

from __future__ import annotations

import os
import sys
from collections.abc import AsyncIterator, Callable, Iterable, Mapping
from dataclasses import dataclass, replace
from datetime import date
from functools import partial
from pathlib import Path
from types import MappingProxyType
from typing import Literal, cast

from config_io import RegistryError, StoreValidationError
from graph_works_core.agent_config import AGENTS
from graph_works_core.agent_config import show as show_agent_config
from graph_works_core.agent_config.records import AgentName
from graph_works_core.code_read import (
    run_code_excerpt,
    run_code_graph_neighborhood,
    run_code_graph_search,
    run_code_graph_tree,
)
from graph_works_core.lint_drift.lint import run_mechanical
from graph_works_core.orchestrate.commands import run_orchestrate
from graph_works_core.proposals import PAGE_STATUSES, run_proposal_checks, run_proposal_preview, run_proposals_read
from graph_works_core.query.commands import MAX_TOP_K, MIN_TOP_K, brief_embedder, plan_query_brief
from graph_works_core.read_session import ReadSession
from graph_works_core.util.commands import run_log_read
from graph_works_core.wiki_page.citations import run_wiki_citations
from graph_works_core.wiki_page.commands import run_page_read, run_wiki_tree
from graph_works_core.work import commands as work
from graph_works_core.work.affecting import run_work_affecting
from graph_works_core.workspace import manifest
from graph_works_core.workspace.config import load_workspace_config
from graph_works_core.workspace.dispatch_config import run_dispatch_rules
from graph_works_core.workspace.errors import QueryError, WorkspaceError
from graph_works_core.workspace.layout import WorkspaceLayout
from graph_works_core.workspace.lint_repos import lint_repositories
from graph_works_core.workspace.schema_read import run_schema_read
from graph_works_wire import agent_config as wire_agent_config
from graph_works_wire import code as wire_code
from graph_works_wire import config as wire_config
from graph_works_wire import util as wire_util
from graph_works_wire import wiki as wire_wiki
from graph_works_wire import work as wire_work

from graph_works_serve import mutations, sse
from graph_works_serve.context import Reply, ServeContext
from graph_works_serve.errors import Catch, call, refusal
from graph_works_serve.mutation_specs import ADVANCE, ARCHIVE, DECIDE, DECISION_ANSWER, SCAN, SECTION_WRITE
from graph_works_serve.mutations import MutationSpec, Outcome, WriteThrough
from graph_works_serve.params import Param, ParamError, parse

Handler = Callable[[ServeContext, Mapping[str, object]], Reply]
MutationHandler = Callable[[WorkspaceLayout, bytes, str | None, WriteThrough | None], Outcome]
StreamHandler = Callable[..., AsyncIterator[bytes]]


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
    return call(
        spec.path, lambda: handler(context.layout(), body, content_type, context.read_state.write_through), _ROUTED
    )


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


def _memo(context: ServeContext, read: Callable[[WorkspaceLayout, ReadSession], Reply]) -> Reply:
    """Serve a display read from the generation's snapshot, stamping that generation on the reply."""
    layout = context.layout()
    with context.read_state.session(layout) as (generation, session):
        reply = read(layout, session)
    return replace(reply, generation=generation)


def _status(context: ServeContext, _args: Mapping[str, object]) -> Reply:
    def read(layout: WorkspaceLayout, session: ReadSession) -> Reply:
        return Reply(200, wire_work.status_payload(work.run_status(layout, session=session)))

    def run() -> Reply:
        return _memo(context, read)

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
    def read(layout: WorkspaceLayout, session: ReadSession) -> Reply:
        result = work.run_item_read(layout, cast(str, args["path"]), session=session)
        payload = wire_work.item_payload(result)
        if result.refusal is not None:
            return refusal("/v1/work/item", "unresolved", f"{result.refusal}: {result.path}", payload=payload)
        return Reply(200, payload)

    def run() -> Reply:
        return _memo(context, read)

    return call("/v1/work/item", run, (*_LAYOUT, Catch(OSError, "io", 1)))


def _page(context: ServeContext, args: Mapping[str, object]) -> Reply:
    def read(layout: WorkspaceLayout, session: ReadSession) -> Reply:
        result = run_page_read(layout, cast(str, args["id"]), session=session)
        payload = wire_wiki.page_payload(result)
        if result.refusal is not None:
            return refusal("/v1/wiki/page", "unresolved", f"{result.refusal}: {result.id}", payload=payload)
        return Reply(200, payload)

    def run() -> Reply:
        return _memo(context, read)

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


_READ = (*_LAYOUT, Catch(OSError, "io", 1))


def _work_list(context: ServeContext, _args: Mapping[str, object]) -> Reply:
    def read(layout: WorkspaceLayout, session: ReadSession) -> Reply:
        return Reply(200, wire_work.work_list_payload(work.run_work_list(layout, session=session)))

    def run() -> Reply:
        return _memo(context, read)

    return call("/v1/work/list", run, _READ)


def _work_affecting(context: ServeContext, args: Mapping[str, object]) -> Reply:
    def run() -> Reply:
        result = run_work_affecting(context.layout(), cast(str, args["repo"]), cast(str, args["path"]))
        payload = wire_work.work_affecting_payload(result)
        if result.refusal is not None:
            return refusal("/v1/work/affecting", "unresolved", f"{result.refusal}: {result.repo}", payload=payload)
        return Reply(200, payload)

    return call("/v1/work/affecting", run, _READ)


def _proposals(context: ServeContext, args: Mapping[str, object]) -> Reply:
    page_status = _str(args, "page_status") or "proposed"
    if page_status not in PAGE_STATUSES:
        return refusal(
            "/v1/wiki/proposals", "usage", f"page_status: unknown {page_status}; expected {'|'.join(PAGE_STATUSES)}"
        )

    def run() -> Reply:
        return Reply(200, wire_wiki.proposals_payload(run_proposals_read(context.layout(), page_status)))

    return call("/v1/wiki/proposals", run, (*_READ, Catch(ValueError, "io", 1)))


def _work_queue(context: ServeContext, _args: Mapping[str, object]) -> Reply:
    def read(layout: WorkspaceLayout, session: ReadSession) -> Reply:
        return Reply(200, wire_work.work_queue_payload(work.run_work_queue(layout, session=session)))

    def run() -> Reply:
        return _memo(context, read)

    return call("/v1/work/queue", run, _READ)


def _open_decisions(context: ServeContext, _args: Mapping[str, object]) -> Reply:
    def read(layout: WorkspaceLayout, session: ReadSession) -> Reply:
        return Reply(200, wire_work.open_decisions_payload(work.run_open_decisions(layout, session=session)))

    def run() -> Reply:
        return _memo(context, read)

    return call("/v1/work/decisions/open", run, _READ)


def _schema(context: ServeContext, _args: Mapping[str, object]) -> Reply:
    def run() -> Reply:
        return Reply(200, wire_config.schema_read_payload(run_schema_read(context.layout())))

    return call("/v1/schema", run, _READ)


def _wiki_tree(context: ServeContext, _args: Mapping[str, object]) -> Reply:
    def read(layout: WorkspaceLayout, session: ReadSession) -> Reply:
        return Reply(200, wire_wiki.wiki_tree_payload(run_wiki_tree(layout, session=session)))

    def run() -> Reply:
        return _memo(context, read)

    return call("/v1/wiki/tree", run, _READ)


def _dispatch_rules(context: ServeContext, _args: Mapping[str, object]) -> Reply:
    def run() -> Reply:
        return Reply(200, wire_config.dispatch_rules_payload(run_dispatch_rules(context.layout())))

    return call("/v1/dispatch/rules", run, _READ)


def _dispatch_explain(context: ServeContext, args: Mapping[str, object]) -> Reply:
    def run() -> Reply:
        explanation = work.run_dispatch_explain(context.layout(), cast(str, args["path"]))
        return Reply(200, wire_work.dispatch_explain_payload(explanation))

    return call("/v1/dispatch/explain", run, _ROUTED)


def _proposal_checks(context: ServeContext, args: Mapping[str, object]) -> Reply:
    def run() -> Reply:
        result = run_proposal_checks(context.layout(), cast(str, args["target"]), today=mutations.now().date())
        payload = wire_wiki.proposal_checks_payload(result)
        if result.refusal is not None:
            return refusal(
                "/v1/wiki/proposal/checks", "unresolved", f"{result.refusal}: {result.target}", payload=payload
            )
        return Reply(200, payload)

    return call("/v1/wiki/proposal/checks", run, _READ)


def _proposal_preview(context: ServeContext, args: Mapping[str, object]) -> Reply:
    def run() -> Reply:
        result = run_proposal_preview(context.layout(), cast(str, args["target"]), today=mutations.now().date())
        payload = wire_wiki.proposal_preview_payload(result)
        if result.refusal is not None:
            return refusal(
                "/v1/wiki/proposal/preview", "unresolved", f"{result.refusal}: {result.target}", payload=payload
            )
        return Reply(200, payload)

    return call("/v1/wiki/proposal/preview", run, _READ)


def _citations(context: ServeContext, args: Mapping[str, object]) -> Reply:
    def read(layout: WorkspaceLayout, session: ReadSession) -> Reply:
        result = run_wiki_citations(layout, cast(str, args["id"]), session=session)
        payload = wire_wiki.citations_payload(result)
        if result.refusal is not None:
            return refusal("/v1/wiki/citations", "unresolved", f"{result.refusal}: {result.id}", payload=payload)
        return Reply(200, payload)

    def run() -> Reply:
        return _memo(context, read)

    return call("/v1/wiki/citations", run, _READ)


def _code_graph_tree(context: ServeContext, args: Mapping[str, object]) -> Reply:
    def run() -> Reply:
        tree = run_code_graph_tree(context.layout(), cast(str, args["repo"]))
        payload = wire_code.code_graph_tree_payload(tree)
        if tree.refusal is not None:
            return refusal("/v1/code-graph/tree", "unresolved", f"{tree.refusal}: {tree.repo}", payload=payload)
        return Reply(200, payload)

    return call("/v1/code-graph/tree", run, _READ)


def _code_graph_search(context: ServeContext, args: Mapping[str, object]) -> Reply:
    def run() -> Reply:
        result = run_code_graph_search(
            context.layout(),
            cast(str, args["q"]),
            repo=cast("str | None", args.get("repo")),
            limit=cast(int, args["limit"]),
        )
        payload = wire_code.code_graph_search_payload(result)
        if result.refusal is not None:
            return refusal("/v1/code-graph/search", "unresolved", f"{result.refusal}: {result.repo}", payload=payload)
        return Reply(200, payload)

    return call("/v1/code-graph/search", run, _READ)


#: A neighbourhood refusal's wire reason.
_NEIGHBORHOOD_REFUSALS: Mapping[str, str] = MappingProxyType(
    {"unknown-page": "unresolved", "not-in-graph": "unresolved", "no-resource": "refused", "no-graph": "refused"}
)


def _code_graph_neighborhood(context: ServeContext, args: Mapping[str, object]) -> Reply:
    def run() -> Reply:
        result = run_code_graph_neighborhood(context.layout(), cast(str, args["id"]), cast(int, args["depth"]))
        payload = wire_code.code_graph_neighborhood_payload(result)
        if result.refusal is None:
            return Reply(200, payload)
        return refusal(
            "/v1/code-graph/neighborhood",
            _NEIGHBORHOOD_REFUSALS[result.refusal],
            f"{result.refusal}: {result.id}",
            payload=payload,
        )

    return call("/v1/code-graph/neighborhood", run, (*_READ, Catch(ValueError, "usage", 1)))


#: An excerpt refusal's wire reason and, where it differs from the reason's default, its status.
_EXCERPT_REFUSALS: Mapping[str, tuple[str, int | None]] = MappingProxyType(
    {
        "unknown-repository": ("unresolved", None),
        "unknown-file": ("unresolved", None),
        "outside-repository": ("refused", 403),
    }
)


def _excerpt(context: ServeContext, args: Mapping[str, object]) -> Reply:
    def run() -> Reply:
        result = run_code_excerpt(
            context.layout(),
            cast(str, args["repo"]),
            cast(str, args["path"]),
            cast(int, args["start"]),
            cast(int | None, args["end"]),
        )
        payload = wire_code.excerpt_payload(result)
        if result.refusal is None or result.refusal == "out-of-range":
            return Reply(200, payload)
        reason, status = _EXCERPT_REFUSALS[result.refusal]
        message = f"{result.refusal}: {result.repo}/{result.path}"
        return refusal("/v1/code/excerpt", reason, message, payload=payload, status=status)

    return call("/v1/code/excerpt", run, (*_READ, Catch(ValueError, "usage", 1)))


def _query_brief(context: ServeContext, args: Mapping[str, object]) -> Reply:
    """Retrieval only (D-001): no model call, and no embedder is a lexical brief, not an error."""

    def run() -> Reply:
        brief = plan_query_brief(
            cast(str, args["q"]),
            context.layout(),
            embedder=brief_embedder(),
            top_k=cast(int, args["limit"]),
            page=_str(args, "page"),
        )
        payload = wire_wiki.query_brief_payload(brief)
        if brief.refusal is not None:
            return refusal("/v1/query/brief", "unresolved", f"{brief.refusal}: {brief.page}", payload=payload)
        return Reply(200, payload)

    # QueryError is a WorkspaceError, so it is matched before `_READ`'s workspace catch.
    return call("/v1/query/brief", run, (Catch(QueryError, "refused", 1), *_READ, Catch(ValueError, "usage", 1)))


def _wiki_lint(context: ServeContext, _args: Mapping[str, object]) -> Reply:
    """Mechanical lint only (D-001): no judge, so `semantic` is null."""

    def run() -> Reply:
        layout = context.layout()
        report = run_mechanical(
            layout, load_workspace_config(layout), today=mutations.now().date(), repositories=lint_repositories(layout)
        )
        return Reply(200, wire_wiki.wiki_lint_payload(report))

    return call("/v1/wiki/lint", run, (*_READ, Catch(ValueError, "workspace", 4)))


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
    RouteSpec(
        "GET",
        "/v1/work/affecting",
        "Active work items in one repository whose affects contain, or sit under, a path.",
        (
            Param("repo", "str", required=True, summary="Declared repository name."),
            Param("path", "str", required=True, summary="Repo-relative POSIX path, file or directory."),
        ),
        _work_affecting,
    ),
    RouteSpec("GET", "/v1/work/list", "Every active work item as a board row, sorted by path.", (), _work_list),
    RouteSpec(
        "GET",
        "/v1/wiki/proposals",
        "Proposals by page_status (the gw wiki proposals --json array when proposed).",
        (Param("page_status", "str", summary="proposed (default)|approved|rejected|superseded|created."),),
        _proposals,
    ),
    RouteSpec(
        "GET", "/v1/schema", "Every schema and section declaration file, parsed as-is, keyed by stem.", (), _schema
    ),
    RouteSpec(
        "GET",
        "/v1/wiki/tree",
        "The root index's ## sections with ### nested, and the pages under each.",
        (),
        _wiki_tree,
    ),
    RouteSpec(
        "GET",
        "/v1/wiki/proposal/checks",
        "Mechanical review checks for one proposal: schema, citations, code drift, related ADRs.",
        (Param("target", "str", required=True, summary="The proposal's target page path."),),
        _proposal_checks,
    ),
    RouteSpec(
        "GET",
        "/v1/wiki/proposal/preview",
        "The page a proposal would produce, its current target and a unified diff; writes nothing.",
        (Param("target", "str", required=True, summary="The proposal's target page path."),),
        _proposal_preview,
    ),
    RouteSpec(
        "GET",
        "/v1/wiki/citations",
        "A page's path:N code citations in body order, body-relative lines, resolved to repositories.",
        (Param("id", "str", required=True, summary="Extensionless bundle-relative page id."),),
        _citations,
    ),
    RouteSpec(
        "GET",
        "/v1/code/excerpt",
        "Lines of a declared repository's tracked file around a cited range (5 lines context, 400-line cap).",
        (
            Param("repo", "str", required=True, summary="Declared repository name."),
            Param("path", "str", required=True, summary="Repo-relative POSIX file path."),
            Param("start", "int", required=True, minimum=1, summary="First cited line, 1-based."),
            Param("end", "int", minimum=1, summary="Last cited line; defaults to start."),
        ),
        _excerpt,
    ),
    RouteSpec(
        "GET",
        "/v1/code-graph/tree",
        "Every page under one repository's code-graph folder, index documents included, as a flat parent-linked list.",
        (Param("repo", "str", required=True, summary="Declared repository name."),),
        _code_graph_tree,
    ),
    RouteSpec(
        "GET",
        "/v1/code-graph/search",
        "Code-graph pages matching a term: exact title, then title prefix, then any substring.",
        (
            Param("q", "str", required=True, summary="Search term, case-insensitive."),
            Param("repo", "str", summary="Limit to one declared repository."),
            Param("limit", "int", default=50, minimum=1, summary="Maximum hits."),
        ),
        _code_graph_search,
    ),
    RouteSpec(
        "GET",
        "/v1/code-graph/neighborhood",
        "A code-graph page's depends-on/used-by neighbourhood to depth 1-3 (cap 200 nodes); not live.",
        (
            Param("id", "str", required=True, summary="Extensionless bundle-relative page id."),
            Param("depth", "int", default=1, minimum=1, summary="Hops, 1-3."),
        ),
        _code_graph_neighborhood,
    ),
    RouteSpec(
        "GET",
        "/v1/dispatch/rules",
        "Dispatch vocabulary, packaged rows and workspace rules in fold order.",
        (),
        _dispatch_rules,
    ),
    RouteSpec(
        "GET",
        "/v1/dispatch/explain",
        "Why an item resolves to its dispatch profile, rule by rule (equal to /v1/work/next's dispatch).",
        (_PATH,),
        _dispatch_explain,
    ),
    RouteSpec(
        "GET", "/v1/work/queue", "Every active item's next stage: skill, mode, reason and blockers.", (), _work_queue
    ),
    RouteSpec(
        "GET",
        "/v1/work/decisions/open",
        "Every open decision across active owners' ledgers, with the items each holds.",
        (),
        _open_decisions,
    ),
    RouteSpec(
        "GET",
        "/v1/query/brief",
        "Retrieval brief for one question: hybrid when the embedder works, else lexical; no model call.",
        (
            Param("q", "str", required=True, summary="The question."),
            Param("page", "str", summary="Pin this page id first, then its matching links."),
            Param(
                "limit",
                "int",
                default=5,
                minimum=MIN_TOP_K,
                summary=f"Pages to return, {MIN_TOP_K}-{MAX_TOP_K}.",
            ),
        ),
        _query_brief,
    ),
    RouteSpec("GET", "/v1/wiki/lint", "Mechanical wiki lint findings and counts; no model.", (), _wiki_lint),
    *mutation_routes(ADVANCE),
    *mutation_routes(ARCHIVE),
    *mutation_routes(DECIDE),
    *mutation_routes(DECISION_ANSWER),
    *mutation_routes(SECTION_WRITE),
    *mutation_routes(SCAN),
)
