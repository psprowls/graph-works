"""Before/after read performance report; latency budgets are targets, not gates.

Usage: python scripts/read_perf_report.py --before before.json --after after.json --out report.md
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from collections import defaultdict
from pathlib import Path

COUNTERS = ("files_parsed", "link_graph_builds", "git_calls")
Key = tuple[str, str, str]


class Refusal(Exception):
    """Input cannot support a trustworthy report."""


def percentile(values: list[float], pct: int) -> float:
    if not values:
        raise Refusal("no timed samples")
    ordered = sorted(values)
    return ordered[max(0, math.ceil(pct / 100 * len(ordered)) - 1)]


def budget_ms(p95_seconds: float) -> float:
    raw = p95_seconds * 1500
    if raw == 0:
        return 0.0
    step = 10 ** (math.floor(math.log10(raw)) - 1)
    return float(math.ceil(round(raw / step, 9)) * step)


def _number(value: object) -> bool:
    return type(value) in (int, float) and math.isfinite(value) and value >= 0


def _load(path: Path, *, before: bool = False) -> dict:
    """Adapt raw-sample fixtures and bench_reads schema 1 at the input boundary."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, UnicodeError):
        raise Refusal(f"{path}: not valid JSON") from None
    except OSError as error:
        raise Refusal(f"{path}: {error.strerror}") from None
    if not isinstance(data, dict):
        raise Refusal(f"{path}: expected an object")
    aggregate = "rows" in data
    rows = data.get("rows" if aggregate else "samples")
    host = data.get("host")
    if not isinstance(rows, list):
        raise Refusal(f"{path}: no samples list")
    if not isinstance(host, dict):
        raise Refusal(f"{path}: no host metadata")
    samples = []
    unavailable = []
    for raw in rows:
        if not isinstance(raw, dict):
            raise Refusal(f"{path}: malformed row")
        if aggregate:
            if data.get("schema") != 1:
                raise Refusal(f"{path}: unsupported schema")
            probe, mode, target = raw.get("read"), raw.get("mode"), raw.get("target")
            if not isinstance(target, str) or not target:
                raise Refusal(f"{path}: missing target")
            for dimension in ("items", "wiki_pages"):
                value = raw.get(dimension)
                if value is not None and (type(value) is not int or value < 0):
                    raise Refusal(f"{path}: invalid {dimension}")
            size = f"{target}: {raw.get('items')}x{raw.get('wiki_pages')}"
            counts = raw.get("counts")
            if not isinstance(counts, dict) or type(raw.get("counts_stable")) is not bool:
                raise Refusal(f"{path}: malformed count evidence")
            if type(raw.get("repeats")) is not int or raw["repeats"] < 1:
                raise Refusal(f"{path}: no timed repeats")
            timing = {"p50": raw.get("p50_ms"), "p95": raw.get("p95_ms")}
            multiplier = 0.001
        else:
            probe, size, mode = raw.get("probe"), raw.get("size"), raw.get("mode")
            counts = raw
            repeats = raw.get("repeats", 1)
            if type(repeats) is not int or repeats < 1:
                raise Refusal(f"{path}: invalid raw repeats")
            if type(raw.get("counts_stable", True)) is not bool:
                raise Refusal(f"{path}: invalid raw counts_stable")
            timing = {"seconds": raw.get("seconds")}
            multiplier = 1
        if not isinstance(probe, str) or not probe or size is None or mode not in ("warm", "cold"):
            raise Refusal(f"{path}: malformed probe, size or mode")
        label = f"{probe} at {size} ({mode})"
        if (
            before
            and probe == "search.lexical-cold-sync"
            and raw.get("error") == "LookupError: lexical store unavailable in this revision"
        ):
            unavailable.append(f"before {label}: {raw['error']}")
            continue
        if raw.get("error"):
            raise Refusal(f"{label}: errored row: {raw['error']}")
        for value in timing.values():
            if value is None:
                raise Refusal(f"{label}: no timed samples")
            if not _number(value):
                raise Refusal(f"{label}: invalid timing")
        if aggregate and timing["p50"] > timing["p95"]:
            raise Refusal(f"{label}: p50 exceeds p95")
        for counter in COUNTERS:
            value = counts.get(counter)
            if value is not None and (type(value) is not int or value < 0):
                raise Refusal(f"{label}: invalid {counter}")
        samples.append(
            {
                "probe": probe,
                "size": str(size),
                "mode": mode,
                **{k: v * multiplier for k, v in timing.items()},
                "counts": {name: counts.get(name) for name in COUNTERS},
                "counts_stable": raw.get("counts_stable", True),
                "repeats": raw.get("repeats", 1),
            }
        )
    if aggregate:
        source = data.get("source")
        if not isinstance(source, dict):
            raise Refusal(f"{path}: no source metadata")
        revision = f"{source.get('graph_works')} (dirty: {source.get('dirty')})"
        if source.get("harness_revision"):
            revision += f"; harness {source['harness_revision']}"
        loads = [host.get("loadavg_before"), host.get("loadavg_after")]
        cores = host.get("cpu_count")
        active_loads = [v[0] for v in loads if isinstance(v, list) and v and _number(v[0])]
        load_known = _number(cores) and cores > 0 and bool(active_loads)
        contended = host.get("idle") is False or (load_known and any(v > cores / 2 for v in active_loads))
        state = "contended" if contended else "idle" if host.get("idle") is True or load_known else "unknown"
        description = (
            f"{host.get('platform')}, {host.get('machine')}, {cores} cores, Python {host.get('python')}, "
            f"load before {loads[0]}, after {loads[1]}, idle: {host.get('idle')}; "
            "cache cold/warm by row, repeats by row"
            f", CPU {host.get('cpu', 'n/a')}, memory {host.get('memory_gb', 'n/a')} GB"
        )
    else:
        revision = data.get("revision", "n/a")
        cores, load = host.get("cores"), data.get("load_avg_1m")
        load_known = _number(cores) and cores > 0 and _number(load)
        state = "contended" if load_known and load > cores / 2 else "idle" if load_known else "unknown"
        description = (
            f"{host.get('cpu')}, {cores} cores, {host.get('memory_gb')} GB, {host.get('os')}, "
            f"Python {host.get('python')}, load {load}, cache {data.get('cache_state')}, "
            f"{data.get('repetitions')} repetitions"
        )
    return {
        "samples": samples,
        "revision": revision,
        "description": description,
        "state": state,
        "unavailable": unavailable,
    }


def _group(run: dict) -> dict[Key, list[dict]]:
    grouped = defaultdict(list)
    for sample in run["samples"]:
        grouped[(sample["probe"], sample["size"], sample["mode"])].append(sample)
    for key, samples in grouped.items():
        if any("p50" in s for s in samples) and len(samples) != 1:
            raise Refusal(f"{key}: duplicate aggregate row")
    return dict(grouped)


def _stats(samples: list[dict]) -> tuple[float, float]:
    if "p50" in samples[0]:
        return samples[0]["p50"], samples[0]["p95"]
    values = [sample["seconds"] for sample in samples]
    return percentile(values, 50), percentile(values, 95)


def _count(samples: list[dict], name: str) -> str:
    values = [sample["counts"][name] for sample in samples]
    return "n/a" if any(v is None for v in values) else str(max(values))


def _violations(grouped: dict[Key, list[dict]]) -> list[str]:
    found = []
    for (probe, size, mode), samples in sorted(grouped.items()):
        label = f"{probe} at {size} ({mode})"
        if mode != "warm" or probe in ("search.lexical-cold-sync", "search.cosine-scan"):
            continue
        if any(not s["counts_stable"] for s in samples):
            found.append(f"{label}: counts varied between repeats")
        for name in COUNTERS:
            value = _count(samples, name)
            if value != "0":
                found.append(f"{label}: {name}={value}")
    return found


def _format(value: float) -> str:
    return f"{value:.12g}"


def render(before: dict, after: dict) -> tuple[str, list[str]]:
    b, a = _group(before), _group(after)
    if not a:
        raise Refusal("after: no timed samples")
    violations = _violations(a)
    lines = ["# Read performance: before and after", ""]
    for label, run in (("before", before), ("after", after)):
        lines.append(f"- {label}: {run['state']} — revision `{run['revision']}`, {run['description']}")
    lines.append("")
    for note in before.get("unavailable", []):
        lines += [f"- Unavailable: {note}", ""]
    if violations:
        lines += ["## Invariant violations", "", *[f"- {v}" for v in violations], ""]
    lines += [
        "## Latency",
        "",
        "Milliseconds. Budgets are after p95 x 1.5 rounded up to two significant figures; reported, not gated (D-004).",
        "",
        "| Probe | Size | Mode | Before p50 | Before p95 | After p50 | After p95 | Budget |",
        "| --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for key, samples in sorted(a.items()):
        before_values = [_format(t * 1000) for t in _stats(b[key])] if key in b else ["n/a", "n/a"]
        p50, p95 = _stats(samples)
        lines.append(
            "| "
            + " | ".join([*key, *before_values, _format(p50 * 1000), _format(p95 * 1000), _format(budget_ms(p95))])
            + " |"
        )
    lines += [
        "",
        "## Structural counts",
        "",
        "Counts are maxima for raw samples; aggregate rows retain the harness's first-repeat counts. "
        "n/a means unknown. Unstable aggregate counts cannot establish a zero-work invariant.",
        "",
        "| Probe | Size | Mode | Before files | Before links | Before git | "
        "After files | After links | After git | Evidence |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for key, samples in sorted(a.items()):
        before_counts = [_count(b[key], name) for name in COUNTERS] if key in b else ["n/a"] * 3
        evidence = []
        for label, observed in (("before", b.get(key, [])), ("after", samples)):
            if observed:
                if any(not s["counts_stable"] for s in observed):
                    evidence.append(f"{label} counts varied between repeats")
                evidence.append(f"{label} repeats: {sum(s['repeats'] for s in observed)}")
        note = "; ".join(evidence)
        lines.append(
            "| " + " | ".join([*key, *before_counts, *[_count(samples, name) for name in COUNTERS], note]) + " |"
        )
    return "\n".join(lines) + "\n", violations


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--before", type=Path, required=True)
    parser.add_argument("--after", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    options = parser.parse_args(argv)
    try:
        text, violations = render(_load(options.before, before=True), _load(options.after))
        options.out.write_text(text, encoding="utf-8", newline="\n")
    except (Refusal, OSError) as error:
        print(f"read_perf_report: {error}", file=sys.stderr)
        return 1
    return 2 if violations else 0


if __name__ == "__main__":
    sys.exit(main())
