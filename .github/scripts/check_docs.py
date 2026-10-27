"""Source check for the repository's Markdown (stdlib only).

Four checks over README.md, CLAUDE.md, TestingStrategy.md and every Markdown
file under docs/, so a docs slip fails the pull request instead of waiting for
a reader to trip over it:

1. Every relative link resolves to a file or directory in the repository.
   External links (http, https, mailto) and in-page anchors are not checked.
2. No file carries more than one front-matter block (a `---` line, one or more
   `key: value` lines, a closing `---`), which is what a stray fragment left by
   a merge looks like.
3. Every count the docs state about what is on disk matches it: the "N tests
   across M files" line in README.md and TestingStrategy.md, the per-file
   "(N tests)" lines in the README's Test Coverage block, and the ADR range
   "0001–NNNN" in CLAUDE.md. A test is a `def test_...` line under tests/, which
   is how the suite is written (no parametrize), so the count is the number of
   collected tests.
4. Every test the docs cite by name exists. The README's safety-guarantee
   table names its guards as pytest addresses them
   (`tests/test_gate.py::TestEvidenceUpdate::test_rate_limit_enforced`), and
   a citation outlives a rename silently: the row still reads as proven while
   it points at nothing. A citation is `file::Class::test`, `file::Class` or
   `file::test`, with or without the `tests/` prefix; a bare `::name` later on
   the same line continues the previous citation's file (and class, when the
   name is a test). Each is resolved against the `ast` of the file it names.

Run from anywhere: python .github/scripts/check_docs.py
"""

from __future__ import annotations

import ast
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TOP_LEVEL = ("README.md", "CLAUDE.md", "TestingStrategy.md")
INLINE_CODE = re.compile(r"`[^`\n]*`")
LINK = re.compile(r"\]\(\s*<?([^)\s>]+)>?(?:\s+\"[^\"]*\")?\s*\)")
SCHEME = re.compile(r"^[a-z][a-z0-9+.-]*:", re.IGNORECASE)
FRONT_KEY = re.compile(r"^[A-Za-z_][\w-]*\s*:")
TEST_DEF = re.compile(r"^\s*def test_", re.MULTILINE)
SUITE_COUNT = re.compile(r"(\d+) tests across (\d+) files")
FILE_COUNT = re.compile(r"^(tests/test_\w+\.py)\s.*\((\d+) tests?\)", re.MULTILINE)
ADR_RANGE = re.compile(r"\b0001[–-](\d{4})\b")
CITATION = re.compile(r"((?:tests/)?test_\w+\.py)?::(\w+)(?:::(\w+))?")


def pages() -> list[Path]:
    found = [ROOT / name for name in TOP_LEVEL if (ROOT / name).exists()]
    found.extend(sorted((ROOT / "docs").rglob("*.md")))
    return found


def prose_lines(text: str) -> list[tuple[int, str]]:
    """Lines outside fenced code blocks, with their line numbers, inline code blanked."""
    kept, fenced = [], False
    for number, line in enumerate(text.split("\n"), start=1):
        if line.lstrip().startswith("```"):
            fenced = not fenced
            continue
        if not fenced:
            kept.append((number, INLINE_CODE.sub("", line)))
    return kept


def front_matter_blocks(lines: list[tuple[int, str]]) -> list[int]:
    """Line numbers at which a front-matter block opens."""
    opens: list[int] = []
    i = 0
    while i < len(lines):
        number, line = lines[i]
        if line.strip() == "---":
            j = i + 1
            while j < len(lines) and FRONT_KEY.match(lines[j][1]):
                j += 1
            if j > i + 1 and j < len(lines) and lines[j][1].strip() == "---":
                opens.append(number)
                i = j
        i += 1
    return opens


def check_links(page: Path, lines: list[tuple[int, str]], errors: list[str]) -> None:
    rel = page.relative_to(ROOT).as_posix()
    for number, line in lines:
        for match in LINK.finditer(line):
            target = match.group(1)
            if SCHEME.match(target) or target.startswith("#"):
                continue
            path = target.split("#", 1)[0].split("?", 1)[0]
            if not (page.parent / path).exists():
                errors.append(f"{rel}:{number}: link {target!r} resolves to no file")


def test_counts() -> dict[str, int]:
    return {
        path.relative_to(ROOT).as_posix(): len(TEST_DEF.findall(path.read_text(encoding="utf-8")))
        for path in sorted((ROOT / "tests").glob("test_*.py"))
    }


def check_counts(errors: list[str]) -> None:
    counts = test_counts()
    total, files = sum(counts.values()), len(counts)
    for name in ("README.md", "TestingStrategy.md"):
        text = (ROOT / name).read_text(encoding="utf-8")
        for match in SUITE_COUNT.finditer(text):
            stated = (int(match.group(1)), int(match.group(2)))
            if stated != (total, files):
                errors.append(f"{name}: says {match.group(0)!r}; tests/ holds {total} tests across {files} files")
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    for match in FILE_COUNT.finditer(readme):
        path, stated = match.group(1), int(match.group(2))
        actual = counts.get(path)
        if actual is None:
            errors.append(f"README.md: names {path}, which does not exist")
        elif actual != stated:
            errors.append(f"README.md: says {path} has {stated} tests; it has {actual}")
    claude = (ROOT / "CLAUDE.md").read_text(encoding="utf-8")
    adrs = len(list((ROOT / "docs" / "adr").glob("[0-9][0-9][0-9][0-9]-*.md")))
    for match in ADR_RANGE.finditer(claude):
        if int(match.group(1)) != adrs:
            errors.append(f"CLAUDE.md: says ADRs run to {match.group(1)}; docs/adr/ holds {adrs}")


def test_names() -> dict[str, dict[str | None, set[str]]]:
    """Per test file: class name -> its method names, plus None -> module-level functions."""
    names: dict[str, dict[str | None, set[str]]] = {}
    for path in sorted((ROOT / "tests").glob("test_*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        scopes: dict[str | None, set[str]] = {None: set()}
        for node in tree.body:
            if isinstance(node, ast.ClassDef):
                scopes[node.name] = {
                    sub.name for sub in node.body if isinstance(sub, ast.FunctionDef | ast.AsyncFunctionDef)
                }
            elif isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
                scopes[None].add(node.name)
        names[path.name] = scopes
    return names


def check_citations(page: Path, text: str, names: dict[str, dict[str | None, set[str]]], errors: list[str]) -> None:
    """Every `file::Class::test` the page cites names a class and test that exist."""
    rel = page.relative_to(ROOT).as_posix()
    for number, line in enumerate(text.split("\n"), start=1):
        file = klass = None
        for match in CITATION.finditer(line):
            cited, first, second = match.groups()
            if cited is not None:
                file, klass = cited.rsplit("/", 1)[-1], None
                scope, member = (first, second) if second else (None, first)
                if scope is None and member in names.get(file, {}):
                    scope, member = member, None  # `file::Class` cites the class itself
            elif file is None:
                continue  # a `::name` with no citation before it on the line is not one
            else:
                scope, member = (klass, first) if first.startswith("test_") else (first, None)
            if scope is not None:
                klass = scope
            scopes = names.get(file)
            if scopes is None:
                errors.append(f"{rel}:{number}: cites {match.group(0)!r}; tests/{file} does not exist")
            elif scope not in scopes:
                errors.append(f"{rel}:{number}: cites {match.group(0)!r}; tests/{file} has no class {scope}")
            elif member is not None and member not in scopes[scope]:
                where = f"class {scope}" if scope else "class or module level"
                errors.append(f"{rel}:{number}: cites {match.group(0)!r}; tests/{file} has no {member} at {where}")


def main() -> int:
    errors: list[str] = []
    names = test_names()
    checked = 0
    for page in pages():
        rel = page.relative_to(ROOT).as_posix()
        text = page.read_text(encoding="utf-8").replace("\r\n", "\n")
        lines = prose_lines(text)
        opens = front_matter_blocks(lines)
        if len(opens) > 1:
            errors.append(f"{rel}: {len(opens)} front-matter blocks (lines {', '.join(map(str, opens))})")
        check_links(page, lines, errors)
        check_citations(page, text, names, errors)
        checked += 1
    check_counts(errors)
    for error in errors:
        print(error)
    print(f"{checked} files checked, {len(errors)} problem(s)")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
