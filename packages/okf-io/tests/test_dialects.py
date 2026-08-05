"""Forced re-dump diagnostic (spec §4.2). **Non-gating by design.**

Re-emits every fixture's frontmatter through ruamel regardless of dirtiness and
reports the byte-identical ratio. This is the two-layer model's go/no-go signal
in readable form. It always passes: a dialect quirk is a documented finding, not
a blocker — and since the line-splice write-back never re-emits an unchanged
line, a low ratio here no longer implies unfaithful saves.
"""

from __future__ import annotations

from helpers import BUNDLES, MALFORMED, all_concept_files, fixture_id, has_frontmatter, read
from okf_io import _yaml

#: Measured baseline for the two vendored bundles at plan time (plan §0).
VENDORED_BASELINE = 12


def _redump_matches(path) -> bool:
    split = _yaml.split(read(path))
    style = _yaml.sniff_style(split.fm_text)
    return _yaml.dump_fm(_yaml.load_fm(split.fm_text), style=style) == split.fm_text


def test_forced_redump_report(capsys):
    identical: list[str] = []
    differing: list[str] = []
    vendored_identical = 0
    vendored_total = 0

    for path in all_concept_files():
        if path.name in MALFORMED or not has_frontmatter(path):
            continue
        matched = _redump_matches(path)
        (identical if matched else differing).append(fixture_id(path))
        if BUNDLES in path.parents:
            vendored_total += 1
            vendored_identical += int(matched)

    total = len(identical) + len(differing)
    with capsys.disabled():
        print(f"\n  forced re-dump byte-identical: {len(identical)}/{total} (all fixtures)")
        print(f"  vendored bundles:              {vendored_identical}/{vendored_total}")
        for name in differing:
            print(f"    differs: {name}")

    assert total > 0, "diagnostic collected nothing"
    assert vendored_identical >= VENDORED_BASELINE, (
        f"vendored re-dump fidelity regressed below the plan §0 baseline: "
        f"{vendored_identical}/{vendored_total} < {VENDORED_BASELINE}"
    )
