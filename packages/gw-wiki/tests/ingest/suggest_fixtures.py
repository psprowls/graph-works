"""A real OKF bundle carrying proposal and code-wiki declarations.

Built from both packages' packaged seed files rather than hand-written JSON:
proposal lanes come from doc-wiki declarations, while proposal catalog
discovery reads the installed code-wiki type/catalog policy. This is
`doc-wiki-okf`'s own `tests/proposal_helpers.seeded_root`, widened with the
code-wiki declarations and an added `log.md` because this vertical appends to
one.

`Proposal` now ships with doc-wiki-okf and is installed with the rest.
"""

from __future__ import annotations

from pathlib import Path

from code_wiki_okf.init import seed_files as code_wiki_seed_files
from doc_wiki_okf.resources import seed_files


def make_bundle(tmp_path: Path, *, without: str | None = None) -> Path:
    """An `okf/` bundle with the declarations installed and one Explanation page.

    *without* drops one type's schema **and** its sections file, which is how
    `classify`'s `undeclared-type` refusal is reached.
    """
    root = tmp_path / "okf"
    root.mkdir(parents=True, exist_ok=True)
    files = {**seed_files(), **code_wiki_seed_files()}
    for relative, text in files.items():
        if without is not None and Path(relative).name.startswith(f"{without}."):
            continue
        target = root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")

    (root / "index.md").write_text("---\nokf_version: 0.2\n---\n\n# bundle\n", encoding="utf-8")
    (root / "log.md").write_text("---\nokf_version: 0.2\n---\n\n# log\n", encoding="utf-8")
    explanations = root / "docs" / "explanations"
    explanations.mkdir(parents=True, exist_ok=True)
    (explanations / "why.md").write_text(
        "---\ntype: Explanation\ntitle: Why\ndescription: The reason\n---\n\nBecause.\n", encoding="utf-8"
    )
    return root


RUNBOOK_SCHEMA = """{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "$ref": "_base-diataxis.schema.json",
  "unevaluatedProperties": false,
  "required": ["service"],
  "properties": { "type": { "const": "Runbook" }, "service": { "type": "string", "minLength": 1 } },
  "x-okf-directory": "runbooks/",
  "x-okf-accept-proposals": true,
  "x-okf-proposal-guidance": {
    "summary": "an operator restoring a service needs exact recovery steps.",
    "question": "Is the reader an operator restoring a running service?"
  },
  "x-okf-proposal-promotion": { "frontmatter": { "service": "unassigned" } }
}
"""

RUNBOOK_SECTIONS = (
    "sections:\n  - heading: Steps\n    required: true\n    placeholder: |\n"
    "      > TODO: the recovery steps.\nadditional_sections: true\n"
)


def add_runbook(root: Path) -> None:
    """Declare a user-authored `Runbook` type: a schema and a sections file, no Python."""
    (root / "schema" / "Runbook.schema.json").write_text(RUNBOOK_SCHEMA, encoding="utf-8")
    (root / "sections" / "Runbook.yaml").write_text(RUNBOOK_SECTIONS, encoding="utf-8")
