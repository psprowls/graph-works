# Task 1 report: public okf-ext row projection

## Outcome and scope

Implemented Task 1 against producer baseline `0f1abf4` in the dispatched isolated worktree. No subagents or reviewers were dispatched, no `gw work` commands were run, and no full stage gate was run.

`okf_ext.readindex.member_row(member_id, kind, document=None, *, sha256=None)` now produces the same body-free `MemberRow` shape as `IndexView`. `okf_ext.readindex.project.fm_json(document)` exposes the existing frontmatter JSON serializer and exactness flag. The serializer was moved intact from `sync.py`, including nonfinite conversion, custom YAML repr fallback, recursive-root fallback, Unicode handling, `allow_nan=False`, and the frontmatter-only boundary.

The producer and public projection share standard-column and sorted/deduplicated tag helpers. The producer retains its existing parse-error and coercion-failure serialization, body offset, hashing, SQL bindings, reconciliation, transaction, heading, and link behavior. No SQLite schema or projection version change is needed because stored row values are unchanged.

Version is 0.5.4 in distribution metadata, Python `__version__`, version-pin test, and the corresponding uv.lock package entry. The Python attribute and lock entry are necessary companion files for the brief's patch bump. Package AGENTS.md documents the shared public projection. The report is committed explicitly despite the existing `.superpowers` ignore rule.

## RED evidence

Before production edits, wrote `packages/okf-ext/tests/test_readindex_project.py` with corpus equivalence and direct contract tests. Ran:

```text
uv run pytest packages/okf-ext/tests/test_readindex_project.py -v
exit 2: collected 0 items / 1 error
ImportError: cannot import name 'member_row' from 'okf_ext.readindex'
```

This is the exact missing-public-API failure specified by the brief. The recursive fixture delimiter was corrected to the existing producer regression's valid YAML root-anchor form before the first GREEN run.

## GREEN evidence

After extracting the serializer and shared metadata/tag helpers:

```text
uv run pytest packages/okf-ext/tests/test_readindex_project.py -v
exit 0: 12 passed in 3.24s

uv run pytest packages/okf-ext/tests -k readindex -q
exit 0: 118 tests passed (progress output reached 100%)
```

The new tests compare every readable corpus row for ACME Retail, GA4, edge fixtures, and nonconformant fixtures with the public projection, excluding only SHA because load_bundle does not retain bytes. They independently assert asset/ignored defaults, supplied SHA preservation, sorted unique tags, immutable top-level frontmatter, Unicode title preservation, finite/nonfinite JSON values and exactness, custom YAML fallback, and recursive fallback excluding body text. Existing producer tests retain their independent expectations for nested floats, aliases, sequence mapping keys, surrogates, diagnostics, headings, links, and concurrency.

## Scoped package gate

Initial `just check-pkg okf-ext` exited 1 on Ruff I001 in the new test's import block, before types or tests. Corrected import ordering with Ruff and reran the scoped gate:

```text
just check-pkg okf-ext
exit 0
lint: All checks passed; 158 files already formatted
mypy linux: Success, 94 source files
mypy win32: Success, 94 source files
coverage: 98.29% (required 95%)
3704 passed, 5 skipped in 39.97s
```

This recipe intentionally covers both okf-io and okf-ext with the repository's configured root testpaths. The five suite skips remain skips, not passes. Complete scoped-gate output was inspected at `/tmp/core-read-session-task-1-check.log`; no test failures remain. `git diff --check` also passed.

## Self-review

- Compared the moved serializer against baseline: only document parameter naming and returning `(text, exact)` changed; all fallback branches and JSON settings remain intact.
- Compared public construction against view.py decoder field by field: ID, kind, SHA, type, title, effective status, unique sorted tags, MappingProxyType over decoded JSON, exactness, parse error, and coercion failures match.
- No-document construction returns all required empty/default metadata and preserves caller-supplied identity and SHA.
- The equivalence suite covers concept, index, log, and asset rows; the direct test covers ignored rows.
- Sorting producer tag preparation changes insertion order only; view ordering and set semantics are unchanged, with existing surrogate regressions passing.
- Shared helpers remain inside readindex, with no dependency on core or another capability. Package boundary tests pass in the scoped gate.
- Version bump is consistent across static metadata and lockfile; exports remain sorted.
- Only Task 1 files and patch-bump companion files are included in the commit.

## Concerns and remaining work

No unresolved Task 1 concerns. Frontmatter immutability remains shallow, matching IndexView; `fm_exact=False` retains the existing requirement for consumers to use a full read when lossless data is needed. Broader core read-session work, independent review, and controller-owned full stage gate are outside this supervised subtask and remain unrun here.
