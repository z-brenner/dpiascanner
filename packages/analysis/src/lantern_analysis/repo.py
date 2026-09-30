"""Enumerate the files to analyze and read per-repository Katz settings."""

from __future__ import annotations

import fnmatch
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from lantern_analysis.parsing.typescript import TS_EXTENSIONS

DEFAULT_EXCLUDE_DIRS = frozenset(
    {
        ".git",
        ".hg",
        "node_modules",
        ".venv",
        "venv",
        "env",
        "__pycache__",
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
        ".tox",
        "dist",
        "build",
        "out",
        "coverage",
        ".next",
        ".nuxt",
        "generated",
        "vendor",
        "site-packages",
        "tests",
        "test",
        "__tests__",
        "testing",
        "spec",
        "specs",
        "__mocks__",
        "mocks",
        "e2e",
        "cypress",
    }
)
DEFAULT_EXCLUDE_FILES = (
    "test_*.py",
    "*_test.py",
    "conftest.py",
    "*.test.ts",
    "*.test.tsx",
    "*.spec.ts",
    "*.spec.tsx",
    "*.test.js",
    "*.spec.js",
    "*.d.ts",
    "*.min.js",
    "setup.py",
)
MAX_FILE_BYTES = 1_500_000


@dataclass
class RepoSettings:
    """Settings from ``lantern.yml`` at the repository root."""

    exclude: list[str] = field(default_factory=list)
    include_tests: bool = False
    raw: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def load(cls, root: Path) -> RepoSettings:
        for name in ("lantern.yml", "lantern.yaml", ".lantern.yml"):
            path = root / name
            if path.is_file():
                data = yaml.safe_load(path.read_text()) or {}
                analysis = data.get("analysis", {}) or {}
                return cls(
                    exclude=list(analysis.get("exclude", [])),
                    include_tests=bool(analysis.get("include_tests", False)),
                    raw=data,
                )
        return cls()


def language_of(path: str) -> str | None:
    if path.endswith(".py"):
        return "python"
    if path.endswith(TS_EXTENSIONS) and not path.endswith(".d.ts"):
        return "typescript"
    return None


def discover(
    root: Path, settings: RepoSettings, extra_exclude: list[str] | None = None
) -> dict[str, list[str]]:
    """Relative paths of analyzable files, grouped by language, in sorted order."""
    exclude_globs = [*settings.exclude, *(extra_exclude or [])]
    exclude_dirs = (
        DEFAULT_EXCLUDE_DIRS - {"tests", "test", "__tests__", "testing", "spec", "specs"}
        if settings.include_tests
        else DEFAULT_EXCLUDE_DIRS
    )
    out: dict[str, list[str]] = {"python": [], "typescript": []}
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        rel = path.relative_to(root).as_posix()
        parts = rel.split("/")
        if any(p in exclude_dirs for p in parts[:-1]):
            continue
        if not settings.include_tests and any(
            fnmatch.fnmatch(parts[-1], pat) for pat in DEFAULT_EXCLUDE_FILES
        ):
            continue
        if any(fnmatch.fnmatch(rel, pat) for pat in exclude_globs):
            continue
        lang = language_of(rel)
        if lang is None:
            continue
        try:
            if path.stat().st_size > MAX_FILE_BYTES:
                continue
        except OSError:
            continue
        out[lang].append(rel)
    return {k: v for k, v in out.items() if v}
