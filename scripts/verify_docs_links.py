#!/usr/bin/env python3
"""Check local Markdown links and anchors in an explicit document scope."""

import argparse
import json
import re
import unicodedata
from dataclasses import asdict, dataclass
from pathlib import Path
from urllib.parse import unquote, urlsplit


@dataclass(frozen=True, slots=True)
class LinkFinding:
    """One deterministic local-link validation finding."""

    path: str
    line: int
    target: str
    reason: str


@dataclass(frozen=True, slots=True)
class _Link:
    target: str
    line: int


_FENCE_RE = re.compile(r"^( {0,3})(`{3,}|~{3,})")
_HEADING_RE = re.compile(r"^ {0,3}#{1,6}\s+(.+?)\s*#*\s*$")
_HTML_TAG_RE = re.compile(r"<[^>]+>")
_EXTERNAL_SCHEMES = {"http", "https", "mailto"}


def _markdown_files(paths: list[Path]) -> list[Path]:
    files: set[Path] = set()
    for raw_path in paths:
        path = raw_path.resolve()
        if path.is_dir():
            files.update(item.resolve() for item in path.rglob("*.md") if item.is_file())
        elif path.is_file() and path.suffix.lower() == ".md":
            files.add(path)
    return sorted(files)


def _escaped(text: str, index: int) -> bool:
    backslashes = 0
    index -= 1
    while index >= 0 and text[index] == "\\":
        backslashes += 1
        index -= 1
    return backslashes % 2 == 1


def _matching_bracket(text: str, start: int) -> int | None:
    depth = 0
    index = start
    while index < len(text):
        if text[index] == "\\":
            index += 2
            continue
        if text[index] == "[":
            depth += 1
        elif text[index] == "]":
            depth -= 1
            if depth == 0:
                return index
        index += 1
    return None


def _destination_end(text: str, start: int) -> tuple[str, int] | None:
    if start >= len(text):
        return None
    if text[start] == "<":
        end = start + 1
        while end < len(text):
            if text[end] == ">" and not _escaped(text, end):
                return text[start + 1 : end], end + 1
            end += 1
        return None

    index = start
    depth = 0
    quote: str | None = None
    while index < len(text):
        char = text[index]
        if char == "\\":
            index += 2
            continue
        if quote is not None:
            if char == quote:
                quote = None
            index += 1
            continue
        if char in {'"', "'"} and depth == 0:
            quote = char
            index += 1
            continue
        if char == "(":
            depth += 1
        elif char == ")":
            if depth == 0:
                raw = text[start:index].strip()
                if any(char.isspace() for char in raw):
                    raw = raw.split()[0]
                return raw, index + 1
            depth -= 1
        index += 1
    return None


def _inline_links(line: str, line_number: int) -> list[_Link]:
    links: list[_Link] = []
    index = 0
    while index < len(line):
        if line[index] == "`":
            run = 1
            while index + run < len(line) and line[index + run] == "`":
                run += 1
            closing = line.find("`" * run, index + run)
            index = len(line) if closing < 0 else closing + run
            continue
        label_start = index + 1 if line.startswith("![", index) else index
        if line[label_start : label_start + 1] != "[":
            index += 1
            continue
        label_end = _matching_bracket(line, label_start)
        if label_end is None or label_end + 1 >= len(line) or line[label_end + 1] != "(":
            index = label_end + 1 if label_end is not None else index + 1
            continue
        parsed = _destination_end(line, label_end + 2)
        if parsed is not None:
            target, end = parsed
            links.append(_Link(target=target, line=line_number))
            index = end
        else:
            index = label_end + 1
    return links


def _iter_links(text: str) -> list[_Link]:
    links: list[_Link] = []
    fence_char: str | None = None
    fence_length = 0
    for line_number, line in enumerate(text.splitlines(), 1):
        fence = _FENCE_RE.match(line)
        if fence is not None:
            marker = fence.group(2)
            if fence_char is None:
                fence_char, fence_length = marker[0], len(marker)
            elif marker[0] == fence_char and len(marker) >= fence_length:
                fence_char, fence_length = None, 0
            continue
        if fence_char is None:
            links.extend(_inline_links(line, line_number))
    return links


def _anchor_slug(heading: str) -> str:
    heading = _HTML_TAG_RE.sub("", heading)
    heading = re.sub(r"[`*_~]", "", heading).casefold()
    chars: list[str] = []
    for char in heading:
        category = unicodedata.category(char)
        if char in {"-", "_"} or category[0] in {"L", "N"}:
            chars.append(char)
        elif char.isspace():
            chars.append("-")
    return re.sub(r"-+", "-", "".join(chars)).strip("-")


def _anchors(path: Path) -> set[str]:
    result: set[str] = set()
    counts: dict[str, int] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        match = _HEADING_RE.match(line)
        if match is None:
            continue
        base = _anchor_slug(match.group(1))
        if not base:
            continue
        suffix = counts.get(base, 0)
        result.add(base if suffix == 0 else f"{base}-{suffix}")
        counts[base] = suffix + 1
    return result


def _finding(path: Path, link: _Link, reason: str) -> LinkFinding:
    return LinkFinding(path=str(path), line=link.line, target=link.target, reason=reason)


def _check_link(path: Path, link: _Link) -> LinkFinding | None:
    target = unquote(link.target).strip()
    parsed = urlsplit(target)
    if parsed.scheme in _EXTERNAL_SCHEMES or target.startswith("//"):
        return None
    if parsed.scheme:
        return _finding(path, link, "unsupported-scheme")

    raw_path = parsed.path
    target_path = path if not raw_path else (path.parent / raw_path).resolve()
    if not target_path.exists():
        return _finding(path, link, "missing-file")
    if parsed.fragment and parsed.fragment not in _anchors(target_path):
        return _finding(path, link, "missing-anchor")
    return None


def scan_paths(paths: list[Path]) -> list[LinkFinding]:
    """Return sorted findings for all Markdown files in the explicit scope."""
    findings: list[LinkFinding] = []
    for path in _markdown_files(paths):
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeError) as exc:
            findings.append(LinkFinding(str(path), 0, "", f"read-error:{type(exc).__name__}"))
            continue
        for link in _iter_links(text):
            result = _check_link(path, link)
            if result is not None:
                findings.append(result)
    return sorted(findings, key=lambda item: (item.path, item.line, item.target, item.reason))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("paths", nargs="+", type=Path)
    parser.add_argument("--json", action="store_true", dest="as_json")
    args = parser.parse_args(argv)
    findings = scan_paths(args.paths)
    if args.as_json:
        payload = {"schemaVersion": "1", "findings": [asdict(item) for item in findings]}
        print(json.dumps(payload, ensure_ascii=False))
    else:
        for item in findings:
            print(f"{item.path}:{item.line}: {item.reason}: {item.target}")
    return 1 if findings else 0


if __name__ == "__main__":
    raise SystemExit(main())
