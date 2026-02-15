#!/usr/bin/env python3
"""Lightweight code quality validator for Python projects.

This tool is intentionally small and dependency-free. It scans Python files,
checks basic style and structural issues, and can optionally run tests.
"""

from __future__ import annotations

import argparse
import ast
import fnmatch
import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, List, Optional


DEFAULT_EXCLUDES = {
    ".git",
    ".venv",
    "venv",
    "env",
    "__pycache__",
    "node_modules",
    "dist",
    "build",
}


@dataclass(frozen=True)
class Issue:
    path: str
    line: int
    kind: str
    message: str

    def format(self) -> str:
        if self.line > 0:
            return f"{self.path}:{self.line}: {self.kind}: {self.message}"
        return f"{self.path}: {self.kind}: {self.message}"


@dataclass(frozen=True)
class CheckConfig:
    root: Path
    include: List[str]
    exclude: List[str]
    max_line_length: int
    max_function_length: int
    run_tests: bool


def parse_args(argv: Optional[List[str]] = None) -> CheckConfig:
    parser = argparse.ArgumentParser(description="Agentic code quality validator")
    parser.add_argument(
        "--root",
        default=".",
        help="Root folder to scan (default: current directory)",
    )
    parser.add_argument(
        "--include",
        action="append",
        default=["**/*.py"],
        help="Glob of files to include (repeatable, default: **/*.py)",
    )
    parser.add_argument(
        "--exclude",
        action="append",
        default=sorted(DEFAULT_EXCLUDES),
        help="Folder or glob to exclude (repeatable)",
    )
    parser.add_argument(
        "--max-line-length",
        type=int,
        default=100,
        help="Max line length (default: 100)",
    )
    parser.add_argument(
        "--max-function-length",
        type=int,
        default=60,
        help="Max function length in lines (default: 60)",
    )
    parser.add_argument(
        "--run-tests",
        action="store_true",
        help="Run pytest if tests/ or test_*.py exists",
    )

    args = parser.parse_args(argv)
    root = Path(args.root).resolve()
    include = args.include if args.include else ["**/*.py"]
    exclude = args.exclude if args.exclude else []
    return CheckConfig(
        root=root,
        include=include,
        exclude=exclude,
        max_line_length=args.max_line_length,
        max_function_length=args.max_function_length,
        run_tests=args.run_tests,
    )


def _is_excluded(path: Path, config: CheckConfig) -> bool:
    for part in path.parts:
        if part in config.exclude:
            return True
    rel = str(path.relative_to(config.root))
    for pattern in config.exclude:
        if fnmatch.fnmatch(rel, pattern):
            return True
    return False


def iter_files(config: CheckConfig) -> Iterable[Path]:
    for pattern in config.include:
        for path in config.root.glob(pattern):
            if not path.is_file():
                continue
            if _is_excluded(path, config):
                continue
            yield path


def check_file(path: Path, config: CheckConfig) -> List[Issue]:
    issues: List[Issue] = []
    try:
        text = path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        issues.append(Issue(str(path), 0, "encoding", "not utf-8"))
        return issues

    lines = text.splitlines()
    for idx, line in enumerate(lines, start=1):
        if "\t" in line:
            issues.append(Issue(str(path), idx, "style", "tab character"))
        if len(line) > config.max_line_length:
            issues.append(
                Issue(
                    str(path),
                    idx,
                    "style",
                    f"line too long ({len(line)} > {config.max_line_length})",
                )
            )
        if line.rstrip(" ") != line:
            issues.append(Issue(str(path), idx, "style", "trailing whitespace"))
        if "TODO" in line or "FIXME" in line:
            issues.append(Issue(str(path), idx, "note", "TODO/FIXME present"))

    try:
        tree = ast.parse(text)
    except SyntaxError as exc:
        issues.append(
            Issue(
                str(path),
                exc.lineno or 0,
                "syntax",
                exc.msg,
            )
        )
        return issues

    issues.extend(check_function_lengths(path, tree, config.max_function_length))
    return issues


def check_function_lengths(
    path: Path, tree: ast.AST, max_len: int
) -> List[Issue]:
    issues: List[Issue] = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if node.end_lineno is None or node.lineno is None:
                continue
            length = node.end_lineno - node.lineno + 1
            if length > max_len:
                issues.append(
                    Issue(
                        str(path),
                        node.lineno,
                        "maintainability",
                        f"function '{node.name}' too long ({length} > {max_len})",
                    )
                )
    return issues


def has_tests(root: Path) -> bool:
    if (root / "tests").exists():
        return True
    for path in root.rglob("test_*.py"):
        if path.is_file():
            return True
    return False


def run_pytest(root: Path) -> int:
    command = [sys.executable, "-m", "pytest", "-q"]
    result = subprocess.run(command, cwd=str(root))
    return result.returncode


def render_markdown(issues: List[Issue], test_status: Optional[int]) -> str:
    lines: List[str] = []
    lines.append("# Agentic Validator Report")
    lines.append("")
    lines.append(f"- Issues: {len(issues)}")
    if test_status is None:
        lines.append("- Tests: not run")
    else:
        lines.append(f"- Tests: exit code {test_status}")
    lines.append("")

    if not issues:
        lines.append("No issues found.")
        return "\n".join(lines)

    lines.append("## Issues")
    for issue in issues:
        lines.append(f"- {issue.format()}")
    return "\n".join(lines)


def main(argv: Optional[List[str]] = None) -> int:
    config = parse_args(argv)
    if not config.root.exists():
        print(f"Root does not exist: {config.root}")
        return 2

    issues: List[Issue] = []
    for path in sorted(iter_files(config)):
        issues.extend(check_file(path, config))

    test_status: Optional[int] = None
    if config.run_tests:
        if has_tests(config.root):
            test_status = run_pytest(config.root)
        else:
            test_status = 0

    report = render_markdown(issues, test_status)
    print(report)

    if issues:
        return 1
    if test_status not in (None, 0):
        return test_status
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
