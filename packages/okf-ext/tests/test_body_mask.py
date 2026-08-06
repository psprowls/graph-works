"""The prose/code mask: a pipe table inside code is somebody's example."""

from __future__ import annotations

from okf_ext.body import prose_lines

FENCED = "".join(
    [
        "text\n",  # 1
        "\n",  # 2
        "```\n",  # 3
        "| a | b |\n",  # 4
        "```\n",  # 5
        "\n",  # 6
        "~~~py\n",  # 7
        "| c | d |\n",  # 8
        "~~~\n",  # 9
        "\n",  # 10
        "    | indented |\n",  # 11
        "\n",  # 12
        "| real | table |\n",  # 13
    ]
)


def test_backtick_fences_tilde_fences_and_indented_blocks_are_all_masked():
    prose = prose_lines(FENCED)
    for masked in (3, 4, 5, 7, 8, 9, 11):
        assert masked not in prose, f"line {masked} should be code"
    for visible in (1, 2, 6, 10, 12, 13):
        assert visible in prose, f"line {visible} should be prose"


def test_a_fence_nested_in_a_list_item_is_masked():
    body = "- item\n\n  ```\n  | a | b |\n  ```\n\n| real |\n"
    prose = prose_lines(body)
    assert 4 not in prose
    assert 7 in prose


def test_an_info_string_does_not_leak_a_fence():
    body = "```markdown title=x\n| a |\n```\n| real |\n"
    prose = prose_lines(body)
    assert 2 not in prose
    assert 4 in prose


def test_an_unclosed_fence_masks_to_the_end_of_the_body():
    body = "text\n```\n| a | b |\n"
    prose = prose_lines(body)
    assert prose == frozenset({1})


def test_a_raw_html_block_is_masked():
    # A `<pre>`/`<table>`-shaped raw HTML block is not a fence or an indented
    # block, but it is still somebody's example, not a table -- the same
    # false positive `okf_io._md` refuses to accept for links.
    body = "text\n\n<pre>\n| a | b |\n|---|---|\n</pre>\n\n| real |\n"
    prose = prose_lines(body)
    for masked in (3, 4, 5, 6):
        assert masked not in prose, f"line {masked} should be code"
    for visible in (1, 2, 7, 8):
        assert visible in prose, f"line {visible} should be prose"
