from plugin_fork_io.adaptations import replay
from plugin_fork_io.records import Adaptation


def test_byte_span_replay_preserves_unedited_bytes():
    original = b'\xef\xbb\xbf---\r\nname: "build"\r\n---\r\n'
    start = original.index(b'"build"')
    edit = Adaptation("skill-name", "skills/build/SKILL.md", start, start + 7, b'"build"', b'"local-build"', "name")
    result, findings = replay(original, (edit,))
    assert not findings
    assert result == original.replace(b'"build"', b'"local-build"')


def test_replay_refuses_changed_context():
    edit = Adaptation("invocation", "x.md", 0, 6, b"$build", b"$local-build", "invocation")
    result, findings = replay(b"$other", (edit,))
    assert result == b"$other"
    assert findings[0].code == "adaptation.ambiguous"


def test_yaml_name_scalar_preserves_bom_crlf_comments():
    from plugin_fork_io.adaptations import name_adaptation

    original = b'\xef\xbb\xbf---\r\nname: "build" # author comment\r\ndescription: Build\r\n---\r\n'
    edit, findings = name_adaptation(original, "SKILL.md", "local-build")
    assert not findings and edit
    result, findings = replay(original, (edit,))
    assert result == original.replace(b'"build"', b'"local-build"')


def test_yaml_duplicate_or_block_scalar_refuses():
    from plugin_fork_io.adaptations import name_adaptation

    for body in [b"name: build\nname: build\n", b"name: >-\n  build\n"]:
        edit, findings = name_adaptation(b"---\n" + body + b"---\n", "SKILL.md", "local-build")
        assert edit is None and findings[0].code == "adaptation.ambiguous"


def test_plain_invocations_use_markdown_token_locations():
    from plugin_fork_io.references import scan_markdown

    content = b"Use $build, /build or /skill:build. Scratch /tmp/build is data.\n"
    dependencies, findings = scan_markdown(content, "SKILL.md")
    assert not findings and len(dependencies) == 3
    assert [content[d.start : d.end] for d in dependencies] == [b"$build", b"/build", b"/skill:build"]


def test_reference_destination_has_original_byte_span():
    from plugin_fork_io.references import scan_markdown

    content = b"[Guide][ref]\r\n\r\n[ref]: ../shared/a\\(b\\).md\r\n"
    dependencies, findings = scan_markdown(content, "skills/review/SKILL.md")
    assert not findings and len(dependencies) == 1
    dependency = dependencies[0]
    assert content[dependency.start : dependency.end] == b"../shared/a\\(b\\).md"


def test_namespaced_invocation_resolves_skill_name():
    from plugin_fork_io.references import scan_markdown

    content = b"Use `/upstream:build` or `$build`.\nREQUIRED SUB-SKILL: upstream:build\n"
    dependencies, findings = scan_markdown(content, "SKILL.md")
    assert not findings and [d.target for d in dependencies] == ["build", "build", "build"]


def test_unlocated_markdown_keeps_semantic_dependency_evidence():
    from plugin_fork_io.references import scan_markdown

    dependencies, findings = scan_markdown(b"> Use [missing](missing.md)\n> before continuing.\n", "review/SKILL.md")
    assert len(dependencies) == 1 and dependencies[0].target == "review/missing.md"
    assert dependencies[0].required and dependencies[0].start == dependencies[0].end
    assert any(f.code == "reference.location" for f in findings)


def test_optional_evidence_is_reference_scoped_and_required_statement_wins():
    from plugin_fork_io.references import scan_markdown

    dependencies, _ = scan_markdown(b"Optional: `$helper`; REQUIRED SUB-SKILL: `$missing`\n", "SKILL.md")
    assert [(d.target, d.required) for d in dependencies] == [("helper", False), ("missing", True)]


def test_repeated_invocation_cannot_borrow_optional_prefix_across_emphasis():
    from plugin_fork_io.references import scan_markdown

    dependencies, _ = scan_markdown(b"Optional: $missing **then required:** $missing\n", "SKILL.md")
    assert [(d.target, d.required) for d in dependencies] == [("missing", False), ("missing", True)]
