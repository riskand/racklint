# racklint/tests/test_release_notes.py
"""Tests for the release notes script, which is not part of a package and so is loaded by path."""

import importlib.util
import itertools
import re
from collections.abc import Callable
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / ".github" / "scripts" / "release_notes.py"
CHANGELOG = ROOT / "CHANGELOG.md"

_spec = importlib.util.spec_from_file_location("release_notes", SCRIPT)
assert _spec is not None
assert _spec.loader is not None
release_notes = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(release_notes)

join_wrapped_lines: Callable[[str], str] = release_notes.join_wrapped_lines
section_body: Callable[[str, str], str] = release_notes.section_body
main: Callable[[list[str]], int] = release_notes.main

BLOCK_START = re.compile(r"\s*(#|[-*+] |\d+[.)] |>|\|)")


def test_wrapped_paragraph_is_joined() -> None:
    text = "First line of a sentence\nthat goes on\n  and ends here.\n"
    assert join_wrapped_lines(text) == "First line of a sentence that goes on and ends here."


def test_wrapped_list_item_is_joined() -> None:
    text = "- **Vault:** a token\n  or an AppRole.\n  Set the address.\n- Next item\n"
    assert join_wrapped_lines(text) == "- **Vault:** a token or an AppRole. Set the address.\n- Next item"


def test_nested_list_item_keeps_indentation() -> None:
    text = (
        "- Outer item\n  that wraps:\n  - **Inner:** nested\n    text that wraps.\n  - Second inner\n- Two\n"
    )
    assert join_wrapped_lines(text) == (
        "- Outer item that wraps:\n  - **Inner:** nested text that wraps.\n  - Second inner\n- Two"
    )


def test_numbered_list_items_stay_separate() -> None:
    text = "1. first\n   wrapped;\n2. second;\n10) third\n   wrapped.\n"
    assert join_wrapped_lines(text) == "1. first wrapped;\n2. second;\n10) third wrapped."


@pytest.mark.parametrize("marker", ["#", "-", "*", "+", "1.", "2)", ">", "|"])
def test_every_block_marker_starts_a_new_line(marker: str) -> None:
    assert join_wrapped_lines(f"text\n{marker} more") == f"text\n{marker} more"


def test_heading_is_not_joined_to_the_text_after_it() -> None:
    assert join_wrapped_lines("### New\nSome text\nthat wraps.\n") == "### New\nSome text that wraps."


def test_text_before_a_heading_is_not_joined_to_it() -> None:
    assert join_wrapped_lines("Some text\n### Next\n") == "Some text\n### Next"


def test_code_fence_is_left_untouched() -> None:
    text = "Run this\nto start:\n```sh\nracklint check \\\n    --rack r1\n  indented\n```\nThen it\nends.\n"
    assert join_wrapped_lines(text) == (
        "Run this to start:\n```sh\nracklint check \\\n    --rack r1\n  indented\n```\nThen it ends."
    )


def test_text_after_a_fence_is_not_joined_to_the_fence() -> None:
    assert join_wrapped_lines("```\ncode\n```\ntext\n") == "```\ncode\n```\ntext"


def test_blank_lines_are_kept() -> None:
    text = "One\nparagraph.\n\n\nTwo\nparagraph.\n\n- item\n"
    assert join_wrapped_lines(text) == "One paragraph.\n\n\nTwo paragraph.\n\n- item"


def test_paragraphs_are_not_joined_across_a_blank_line() -> None:
    assert join_wrapped_lines("a\n\nb") == "a\n\nb"


def test_empty_text() -> None:
    assert join_wrapped_lines("") == ""


def _continues_previous(lines: list[str]) -> list[str]:
    """The lines that a reader would see as a wrapped continuation of the line before them."""
    return [
        current
        for previous, current in itertools.pairwise(lines)
        if previous.strip()
        and current.strip()
        and not previous.lstrip().startswith("#")
        and not BLOCK_START.match(current)
    ]


def test_changelog_section_has_no_wrapped_line() -> None:
    text = CHANGELOG.read_text(encoding="utf-8")
    raw = section_body(text, "0.1.1")
    assert _continues_previous(raw.splitlines()), (
        "the raw section should be wrapped, or this test checks nothing"
    )

    joined = join_wrapped_lines(raw)
    assert _continues_previous(joined.splitlines()) == []
    assert (
        "racklint checks servers and switches against what NetBox plans for them, "
        "before they go into service, and changes nothing." in joined
    )


def test_main_prints_the_joined_section(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["0.1.1", str(CHANGELOG)]) == 0
    out = capsys.readouterr().out
    assert _continues_previous(out.splitlines()) == []
    assert out.startswith(
        "racklint checks servers and switches against what NetBox plans for them, "
        "before they go into service, and changes nothing."
    )
