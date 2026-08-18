"""A real OKF bundle carrying the five proposal lanes' declarations.

Built from `doc_wiki_okf.resources.seed_files()` -- the twelve packaged
declaration files, keyed by bundle-relative posix path and valued with their
**text** -- rather than hand-written JSON: the lane directories come from each
schema's `x-okf-directory`, and a hand-written copy of those would drift from
the package that owns them. This is `doc-wiki-okf`'s own
`tests/proposal_helpers.seeded_root`, with an added `log.md` because this
vertical appends to one.

No `Proposal` declaration is installed and none is needed: `okf_ext.proposals`
carries its own type, which is why `doc-wiki-okf`'s proposal suite seeds
exactly these twelve files too.
"""

from __future__ import annotations

from pathlib import Path

from doc_wiki_okf.resources import seed_files


def make_bundle(tmp_path: Path, *, without: str | None = None) -> Path:
    """An `okf/` bundle with the declarations installed and one Explanation page.

    *without* drops one type's schema **and** its sections file, which is how
    `classify`'s `undeclared-type` refusal is reached.
    """
    root = tmp_path / "okf"
    root.mkdir(parents=True, exist_ok=True)
    for relative, text in seed_files().items():
        if without is not None and Path(relative).name.startswith(f"{without}."):
            continue
        target = root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")

    (root / "index.md").write_text("---\nokf_version: 0.2\n---\n\n# bundle\n", encoding="utf-8")
    (root / "log.md").write_text("---\nokf_version: 0.2\n---\n\n# log\n", encoding="utf-8")
    explanations = root / "explanations"
    explanations.mkdir(exist_ok=True)
    (explanations / "why.md").write_text(
        "---\ntype: Explanation\ntitle: Why\ndescription: The reason\n---\n\nBecause.\n", encoding="utf-8"
    )
    return root
