# Read benchmarks

This harness measures the seven graph-works display reads: work status, list, item detail and queue, wiki tree, page detail and citations. Structural counts are the host-independent evidence for the [data and UI performance design](/work/epic-data-and-ui-performance/references/01-design.md), decision D-004. Latencies are evidence, never a test gate. Search has its own benchmark in [feature-search-freshness](/work/epic-data-and-ui-performance/children/feature-search-freshness.md).

Run under graph-works-core's environment after provisioning workspace dependencies with `uv sync --all-packages`:

```bash
uv run --package graph-works-core python scripts/bench_reads.py run --json out.json --markdown out.md
uv run --package graph-works-core python scripts/bench_reads.py run --no-synthetic --workspace "$GRAPH_WORKS_DIR"
uv run --package graph-works-core python scripts/bench_reads.py generate /tmp/corpus --items 1000 --wiki-pages 1000
```

`--matrix` accepts comma-separated `ITEMSxWIKI` or `ITEMSxWIKI@WARM/COLD` entries. The default matrix is `100x1000,1000x1000,10000x1000@3/2,1000x100,1000x5000`: the first three isolate work scaling at fixed wiki size; the last two isolate unrelated wiki growth at fixed work size. Defaults are five warm repeats and three cold repeats; the largest size uses three and two. Cold zero skips cold runs. `--workspace-repeats 3/2` sets the live workspace repetitions independently.

The deterministic generator includes epics, child items, archived items, dependencies, reference artifacts, linked wiki pages and malformed frontmatter. Its host Git repository has tracked source files cited by the pages. It refuses an existing nonempty output root. Generated corpora are temporary and removed after measurement; `--scratch` chooses their parent. The harness never writes into the workspace it measures. Choose output paths outside that workspace if it must remain completely untouched.

`files_parsed` counts calls to `Document.parse`, including direct parses and bundle loads. `link_graph_builds` counts construction of `okf_io.links.LinkGraph`, including calls through previously imported `build_link_graph` names and validation. `git_calls` counts Git processes through `subprocess.Popen`, including absolute paths and core's `probe_git`. Patches are restored on block exit, including exceptions; nested counting blocks are refused. Counting is process-wide and must not overlap unrelated activity in the same interpreter.

Warm mode discards one initial read, then times and counts each read in the same process. Cold mode starts a fresh interpreter for every read and measures interpreter startup, imports and the read end to end. Warm process does not imply a persistent read-model cache: the baseline may still parse every file again. Both modes use the host filesystem cache as found; the harness does not flush it. The table reports the first sample's counts and flags variation across repetitions. A failing read becomes an error row so other reads can continue.

p50 and p95 use nearest rank. With fewer than 20 samples p95 is the maximum; the table always displays n. JSON also records source revision and dirty state, workspace revision, generator version, platform, architecture, Python version, CPU count, load before and after, and idle detection. These fields must accompany comparisons.

`--max-load` defaults to a one-minute load per CPU of 0.5; `--idle-wait` waits up to 600 seconds for that threshold before measurement. `idle: false` means the host remained contended and the numbers must be labelled accordingly; `idle: null` means load information is unavailable. Idle detection is a pre-run observation, so also inspect load afterward and record other known host activity. An idle baseline is recorded evidence, not a guarantee of isolation throughout the run.
