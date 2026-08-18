"""One entity's prose refresh: four bounded tools and the loop over them.

The three file tools are hand-built rather than generic because containment is
their whole point. Each resolves a model-supplied string against a root and
refuses anything that lands outside it -- `resolve()` first, so a `..` segment
and a symlink out of the tree are the same case. `commands.agent_tools`'
bundle-keyed helpers do not apply here: this reads *source files*, which are not
bundle members, so traversal is representable and has to be defended against.

**Containment is the boundary; the deny-list is a second, deliberately
incomplete layer.** `resolve_under` is what actually bounds these tools -- it
resolves both sides, so `..` traversal and a symlink out of the tree are one
case. Inside that boundary there was no content filter at all, and for a
`Repository` page the entity root is the whole checkout: a `.env` or a private
key sitting in a scanned repo was readable, and whatever is read is
transcribed verbatim into `SubagentPool`'s JSONL traces on disk (under the
layout's gitignored cache dir). `_DENIED_NAME_PATTERNS` refuses the
credential-shaped names that cost nothing to name, checked against every
component of the entity-root-relative path, not just the leaf. It is **not** a
secret scanner and makes no completeness claim: filtering what lives inside a
scanned repository is the operator's job, and the traces retain everything the
model was shown.

`subagents_io.runner.run_all` is not used: it does one `ainvoke` per item, and a
refresh is a tool loop. `run_loop` is a tool loop but single-item. `SubagentPool`
plus this closure is the composition that gives both -- see `commands.scan`.
"""

from __future__ import annotations

import fnmatch
from collections.abc import Sequence
from pathlib import Path

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_core.tools import BaseTool, tool

from graph_works_core.agent_substrate.agent_loop import run_tool_loop
from graph_works_core.agent_substrate.agent_tools import filter_graph_tools, truncate_text
from graph_works_core.scan.prose_refresher import (
    PROSE_REFRESHER_SYSTEM,
    build_prose_refresh_prompt,
    parse_prose_refresher_output,
)
from graph_works_core.scan.scan_contract import ProseRefreshResult, ProseRefreshTask

#: One source file's budget in a tool response.
MAX_TOOL_FILE_CHARS = 20_000

#: One wiki page's budget in a tool response.
MAX_TOOL_PAGE_CHARS = 20_000

#: How many children `list_repo_tree` names before it truncates.
MAX_TREE_ENTRIES = 200

#: How many tool-call rounds one refresh gets.
MAX_REFRESH_ITERS = 6

#: Credential-shaped names the file tools refuse inside the entity root.
#: Matched case-insensitively against every component of the entity-root-relative
#: path, not just the leaf. Deliberately short and deliberately incomplete -- see
#: the module docstring.
_DENIED_NAME_PATTERNS: tuple[str, ...] = (
    ".env",
    ".env.*",
    "*.pem",
    "*.key",
    "*.p12",
    "id_rsa*",
    "id_ed25519*",
    ".netrc",
    ".npmrc",
)


def is_denied_name(name: str) -> bool:
    """Whether *name* -- a basename, not a path -- is credential-shaped.

    `fnmatchcase` against a casefolded name rather than `fnmatch`, which
    normcases through the platform and is therefore case-sensitive on POSIX and
    not on Windows. A deny-list that changes with the host is worse than no
    deny-list.
    """
    lowered = name.casefold()
    return any(fnmatch.fnmatchcase(lowered, pattern) for pattern in _DENIED_NAME_PATTERNS)


#: The only graph tools this loop may hold. C7's builder makes more; this filter
#: is what keeps a wider builder from widening this loop's surface by accident.
_ALLOWED_GRAPH_TOOL_NAMES = {"cg_find", "cg_describe"}


def resolve_under(base: Path, raw: str) -> Path | None:
    """*raw* resolved against *base*, or `None` when it lands outside it.

    Both sides are `resolve()`d, so `..` traversal and a symlink pointing out of
    the tree are one case rather than two. A *raw* that cannot be resolved at
    all (e.g. an embedded null byte) is refused the same as an out-of-root
    path, not raised -- containment failures are always `None`, never an
    exception, so a tool body needs no `try/except` of its own around this call.
    """
    try:
        base = base.resolve()
        candidate = (base / raw.strip().lstrip("/")).resolve()
    except (OSError, ValueError):
        return None
    return candidate if candidate == base or base in candidate.parents else None


def build_prose_refresh_tools(
    task: ProseRefreshTask,
    *,
    bundle_root: Path,
    graph_tools: Sequence[BaseTool] = (),
) -> list[BaseTool]:
    """The refresher's three own tools, plus whichever graph tools are allowed."""
    entity_root = Path(task.entity_root).resolve() if task.entity_root else None
    bundle = Path(bundle_root).resolve()

    @tool
    def read_repo_file(path: str) -> str:
        """Read one source file under this entity's root, bounded to a safe size."""
        if entity_root is None:
            return "ERROR: this entity has no repository root"
        target = resolve_under(entity_root, path)
        if target is None:
            return f"ERROR: path is outside the entity root: {path}"
        # Check every component of the entity-root-relative path, not just the leaf.
        if any(is_denied_name(part) for part in target.relative_to(entity_root).parts):
            return f"ERROR: refused: credential-shaped path: {path}"
        if not target.is_file():
            return f"ERROR: not a readable file: {path}"
        try:
            text = target.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            return f"ERROR: {exc}"
        return truncate_text(text, MAX_TOOL_FILE_CHARS)

    @tool
    def list_repo_tree(path: str = "") -> str:
        """List the immediate children of one directory under this entity's root."""
        if entity_root is None:
            return "ERROR: this entity has no repository root"
        target = resolve_under(entity_root, path)
        if target is None:
            return f"ERROR: path is outside the entity root: {path}"
        # Check every component of the entity-root-relative path, not just children.
        # The root itself (target == entity_root) has relative_to yielding Path(".") with empty parts.
        if any(is_denied_name(part) for part in target.relative_to(entity_root).parts):
            return f"ERROR: refused: credential-shaped path: {path}"
        if not target.is_dir():
            return f"ERROR: not a directory: {path}"
        try:
            children = sorted(
                child.name + ("/" if child.is_dir() else "")
                for child in target.iterdir()
                if not is_denied_name(child.name)
            )
        except OSError as exc:
            return f"ERROR: {exc}"
        if len(children) > MAX_TREE_ENTRIES:
            kept = children[:MAX_TREE_ENTRIES]
            return "\n".join([*kept, f"[TRUNCATED after {MAX_TREE_ENTRIES} of {len(children)} entries]"])
        return "\n".join(children) or "(empty)"

    @tool
    def read_bundle_page(path: str) -> str:
        """Read one wiki page by its bundle-relative path, bounded to a safe size."""
        target = resolve_under(bundle, path)
        if target is None:
            return f"ERROR: path is outside the bundle: {path}"
        if not target.is_file():
            return f"ERROR: not a readable page: {path}"
        try:
            text = target.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            return f"ERROR: {exc}"
        return truncate_text(text, MAX_TOOL_PAGE_CHARS)

    return [
        read_repo_file,
        list_repo_tree,
        read_bundle_page,
        *filter_graph_tools(list(graph_tools), _ALLOWED_GRAPH_TOOL_NAMES),
    ]


async def run_prose_refresh(
    task: ProseRefreshTask,
    *,
    llm: BaseChatModel,
    bundle_root: Path,
    graph_tools: Sequence[BaseTool] = (),
) -> ProseRefreshResult:
    """Run the capped loop once for *task* and return what it said.

    The sections come back **unsanitized**: filtering is `apply_scan_results`',
    applied once so the file surface gets exactly the same treatment.

    A loop that hit its iteration cap *after* producing usable text is `ok` with
    a note (`agent_loop`'s own contract), and that note is deliberately not
    carried into `error` -- a non-`None` `error` makes phase 3 discard the
    answer, and a usable answer must not be discarded for a cap note.
    """
    tools = build_prose_refresh_tools(task, bundle_root=bundle_root, graph_tools=graph_tools)
    loop = await run_tool_loop(
        llm=llm,
        tools=tools,
        messages=[SystemMessage(PROSE_REFRESHER_SYSTEM), HumanMessage(build_prose_refresh_prompt(task))],
        max_iterations=MAX_REFRESH_ITERS,
        cap_label="prose refresher",
    )
    if loop.status != "ok":
        return ProseRefreshResult(uri=task.uri, error=loop.error or "prose refresher failed")
    sections, parsed = parse_prose_refresher_output(loop.final_text)
    if not parsed:
        return ProseRefreshResult(uri=task.uri, error="prose refresher output did not parse")
    return ProseRefreshResult(uri=task.uri, sections=sections)


__all__ = [
    "MAX_REFRESH_ITERS",
    "MAX_TOOL_FILE_CHARS",
    "MAX_TOOL_PAGE_CHARS",
    "MAX_TREE_ENTRIES",
    "build_prose_refresh_tools",
    "is_denied_name",
    "resolve_under",
    "run_prose_refresh",
]
