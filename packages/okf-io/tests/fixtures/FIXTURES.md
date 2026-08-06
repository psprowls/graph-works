# Test fixtures

## Vendored bundles

Copied **verbatim** so the suite has no dependency on a path outside this
repository. These copies are a deliberate point-in-time contract and **do not
track the archive**. If the archive moves on, these do not follow unless someone
re-vendors them deliberately and updates this file.

| Bundle | Source | Archive commit | Vendored |
|---|---|---|---|
| `bundles/acme_retail` | `/Users/pat/Personal/archive/okf/knowledge-catalog/okf/bundles/acme_retail` | `3fcbb9f828c2f23d109c855ee403c3a4c81f3a96` | 2026-08-02 |
| `bundles/ga4` | `/Users/pat/Personal/archive/okf/knowledge-catalog/okf/bundles/ga4` | `3fcbb9f828c2f23d109c855ee403c3a4c81f3a96` | 2026-08-02 |

The `Source` column is a local clone path. Upstream is
`https://github.com/GoogleCloudPlatform/knowledge-catalog.git`, so the pinned
commit is resolvable without that clone: `git clone` it, `git checkout` the SHA
above, and re-copy from `okf/bundles/`.

### Why these two

`acme_retail` is the only bundle anywhere that exercises the full v0.2 surface:
attested computations with `parameters` / `executor` / `attester`, `verified`,
`stale_after`, `usage_window`, `usage_count`, a deprecated concept, non-markdown
members (`attesters/sql_equality.py`, `viz.html`), the undeclared `not:`
extension key, and mixed absolute/relative link styles. Its YAML dialect uses
padded flow mappings (`{ by: …, at: … }`) and sequences indented under their key.

`ga4` is thin machine-generated v0.2 — the common case — written by the
TypeScript `yaml` library. Its dialect uses sequence dashes at the parent indent,
quoted timestamps, and **long plain scalars pre-folded at ~80 columns**. That last
property is why the line-splice write-back exists (plan §0): ruamel cannot
reproduce those folds.

## The v0.1 corpus

`legacy/` is a **second** vendored set, pinned to a different commit and
serving a different purpose. `780fe9d` is knowledge-catalog's entire v0.1 → v0.2
migration; these files are taken from `780fe9d^`, the commit *before* it, which
is the largest collection of real v0.1 documents that exists.

| Fixture | Source | Archive commit | Vendored |
|---|---|---|---|
| `legacy/stackoverflow/references/badge_classes.md` | `okf/bundles/stackoverflow/references/badge_classes.md` | `780fe9d^` | 2026-08-05 |
| `legacy/stackoverflow/tables/post_history.md` | `okf/bundles/stackoverflow/tables/post_history.md` | `780fe9d^` | 2026-08-05 |
| `legacy/ga4/references/metrics/avg_pageviews.md` | `okf/bundles/ga4/references/metrics/avg_pageviews.md` | `780fe9d^` | 2026-08-05 |

Upstream is `https://github.com/GoogleCloudPlatform/knowledge-catalog.git`, so
`780fe9d^` resolves without the local clone: `git clone` it, `git show
780fe9d^:<path>` each file. The directory names mirror the upstream bundles
they came from; `legacy/` itself loads as one bundle, which is what the
migration properties walk.

**`780fe9d` is a corpus of inputs, not a golden target.** Reading the commit:
all 65 files carrying a citations section before it carry none after, and the
way they got there was an agent regenerating each document — prose rewritten,
`id` values invented semantically (`ga4-demo-docs`), footnote references woven
into newly written sentences, and the ga4 metrics files deleted and replaced
outright. A mechanical rewriter cannot reproduce that and does not try. Compare
against these inputs; never against that commit's outputs.

### Why these three, and why not a fourth

One per dialect that exists in the wild: `badge_classes.md` writes linked list
items (64 of the 65 files), `avg_pageviews.md` writes a bare URL as a list item
with no link, and `post_history.md` writes the bracketed-number form
(`[1] [Title](url)`) — under a `### Citations` heading rather than `# Citations`,
which is why the locator ignores heading level.

The design spec asked for a fourth: a `timestamp`-only document with no
citations. **No such document exists at `780fe9d^`** — every one of the 65
timestamped v0.1 concepts carries a citations section. `edge/legacy_timestamp.md`
is that document, hand-built.

## Edge fixtures

`edge/` is hand-built, one file per item in the design document's §2.5 list plus
the encoding cases the splitter must survive. Unlike the vendored bundles these
are ours to change — but each one is a named regression, so change the assertion
along with the file, never the file alone.

## The nonconformant corpus

`nonconformant/` is hand-built and triggers every code in the validation
catalog in one walk. Its reviewed expected output is `nonconformant.golden.txt`.

It sits **beside** `bundles/`, not inside it, for two reasons. `bundles/` is
vendored verbatim and pinned to an upstream commit; this is ours. And
`helpers.all_concept_files()` walks `bundles/` to feed a prior child's round-trip
acceptance properties, which assume every file decodes as UTF-8 —
`nonconformant/concepts/not-utf8.md` deliberately does not.

Change a fixture and the golden file changes with it. Regenerating the golden
alone proves nothing: the assertions that hold it honest are the hand-written
error-code set in `test_catalog.py` and the zero-errors property on the two
vendored bundles.
