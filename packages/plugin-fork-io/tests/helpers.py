from pathlib import Path


def write_skill(root: Path, name: str, body: bytes = b"Keep local intent.\n") -> Path:
    skill = root / "skills" / name / "SKILL.md"
    skill.parent.mkdir(parents=True)
    skill.write_bytes(b"---\n" + f"name: {name}\ndescription: Manage {name} work.\n---\n\n".encode() + body)
    return skill


def selection(*names):
    from plugin_fork_io.records import Component, Selection

    return Selection(tuple(Component("skills/" + name, name) for name in names), (), (), (), ())


def codes(result):
    return {finding.code for finding in result.findings}
