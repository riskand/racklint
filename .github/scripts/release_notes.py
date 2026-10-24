# racklint/.github/scripts/release_notes.py
"""Print one version's release notes from CHANGELOG.md.

The release workflow uses this twice: to fail a tag that has no notes before anything is published, and to
write the notes of the GitHub release.

Usage: python .github/scripts/release_notes.py <version> [changelog path]
"""

import argparse
import re
import sys
from collections.abc import Sequence
from pathlib import Path


def section_body(changelog: str, version: str) -> str:
    """Return the text under the `## <version>` heading, without the heading.

    The section ends at the next `## ` heading or the end of the text. Returns "" if there is no such
    section, or if it is empty.
    """
    heading = re.compile(rf"##\s+{re.escape(version)}(\s.*)?")
    body: list[str] = []
    inside = False
    for line in changelog.splitlines():
        if inside:
            if line.startswith("## "):
                break
            body.append(line)
        elif heading.fullmatch(line):
            inside = True
    return "\n".join(body).strip()


_BLOCK_START = re.compile(r"(#|[-*+] |\d+[.)] |>|\|)")


def join_wrapped_lines(text: str) -> str:
    """Join the hard-wrapped lines of each paragraph or list item into one line.

    GitHub shows every newline in a release body as a line break, so the wrapped lines of CHANGELOG.md would
    break sentences in the middle. Fenced code blocks, blank lines, headings and the start of each list item,
    quote or table row are kept as they are.
    """
    out: list[str] = []
    in_fence = False
    can_continue = False
    for line in text.splitlines():
        stripped = line.lstrip()
        if stripped.startswith("```"):
            in_fence = not in_fence
            out.append(line)
            can_continue = False
        elif in_fence:
            out.append(line)
        elif not stripped:
            out.append(line)
            can_continue = False
        elif can_continue and not _BLOCK_START.match(stripped):
            out[-1] = f"{out[-1].rstrip()} {stripped}"
        else:
            out.append(line)
            can_continue = not stripped.startswith("#")
    return "\n".join(out)


def main(argv: Sequence[str]) -> int:
    parser = argparse.ArgumentParser(description="Print one version's release notes from the changelog.")
    parser.add_argument("version", help="version without the leading v, for example 0.1.1")
    parser.add_argument("changelog", nargs="?", type=Path, default=Path("CHANGELOG.md"))
    args = parser.parse_args(argv)

    try:
        text = args.changelog.read_text(encoding="utf-8")
    except OSError as error:
        print(f"cannot read {args.changelog}: {error.strerror}", file=sys.stderr)
        return 1

    notes = section_body(text, args.version)
    if not notes:
        print(f"{args.changelog} has no release notes for version {args.version}", file=sys.stderr)
        return 1
    print(join_wrapped_lines(notes))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
