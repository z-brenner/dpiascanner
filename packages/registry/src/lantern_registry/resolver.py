"""Resolve imported packages that are not in the registry.

For each unregistered third-party package the application imports, find its pinned version
in a lockfile, fetch its source into a sandbox directory, and run the analyzer one level deep
on it (its own code, not its dependencies) to discover which of its functions make outbound
network calls and to which hosts. Results are marked ``inferred-from-dependency-source``:
they are evidence about the package, not a vendor's documented behavior.

The analyzer is injected (``PackageAnalyzer``) so this package does not depend on
lantern-analysis. Fetching is behind ``PackageSource`` so tests run offline.
"""

from __future__ import annotations

import io
import json
import re
import shutil
import sys
import tarfile
import tomllib
import urllib.request
import zipfile
from collections.abc import Callable, Iterable, Mapping
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Protocol

import yaml

from lantern_registry.model import Registry

INFERRED = "inferred-from-dependency-source"
MAX_ARCHIVE_BYTES = 50_000_000
MAX_MEMBER_BYTES = 5_000_000
USER_AGENT = "lantern-dependency-resolver/0.1"

# Packages whose egress is covered by rule packs, or that do not send data anywhere.
PYTHON_IGNORE = frozenset(
    {
        "fastapi",
        "starlette",
        "pydantic",
        "pydantic_core",
        "sqlalchemy",
        "sqlmodel",
        "django",
        "flask",
        "werkzeug",
        "yaml",
        "uvicorn",
        "gunicorn",
        "typing_extensions",
        "alembic",
        "jinja2",
        "click",
        "typer",
        "attr",
        "attrs",
        "dateutil",
        "pytz",
        "requests",
        "httpx",
        "aiohttp",
        "urllib3",
        "certifi",
        "anyio",
        "psycopg",
        "psycopg2",
        "asyncpg",
        "pymysql",
        "redis",
        "celery",
        "kombu",
        "passlib",
        "bcrypt",
        "argon2",
        "jwt",
        "jose",
        "cryptography",
        "orjson",
        "ujson",
        "multipart",
        "email_validator",
        "emails",
        "tenacity",
        "structlog",
        "loguru",
        "rich",
        "numpy",
        "pandas",
        "pytest",
        "dotenv",
        "decouple",
        "environ",
        "pydantic_settings",
    }
)
NPM_IGNORE = frozenset(
    {
        "express",
        "react",
        "react-dom",
        "next",
        "@prisma/client",
        "prisma",
        "zod",
        "lodash",
        "axios",
        "got",
        "node-fetch",
        "ky",
        "pino",
        "winston",
        "dotenv",
        "cors",
        "helmet",
        "jsonwebtoken",
        "bcrypt",
        "bcryptjs",
        "uuid",
        "date-fns",
        "dayjs",
        "typescript",
        "tsx",
        "express-validator",
        "body-parser",
        "cookie-parser",
        "morgan",
        "@prisma/adapter-pg",
        "pg",
        "mysql2",
        "ioredis",
        "redis",
        "bullmq",
        "class-validator",
        "class-transformer",
        "rxjs",
        "@nestjs/common",
        "@nestjs/core",
        "reflect-metadata",
        "vue",
        "svelte",
        "@types/node",
    }
)


# ---------------------------------------------------------------------------- lockfiles


@dataclass(frozen=True)
class LockedPackage:
    ecosystem: str  # pypi | npm
    name: str
    version: str
    lockfile: str


def _canonical_pypi(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def _parse_requirements(path: Path) -> Iterable[tuple[str, str]]:
    for line in path.read_text(errors="replace").splitlines():
        line = line.split("#", 1)[0].strip()
        m = re.match(
            r"^([A-Za-z0-9][A-Za-z0-9._-]*)(?:\[[^\]]*\])?\s*==\s*([A-Za-z0-9._+!-]+)", line
        )
        if m:
            yield m.group(1), m.group(2)


def _parse_toml_packages(path: Path) -> Iterable[tuple[str, str]]:
    data = tomllib.loads(path.read_text())
    for pkg in data.get("package", []):
        if "name" in pkg and "version" in pkg:
            yield pkg["name"], pkg["version"]


def _parse_pipfile_lock(path: Path) -> Iterable[tuple[str, str]]:
    data = json.loads(path.read_text())
    for section in ("default", "develop"):
        for name, info in data.get(section, {}).items():
            version = str(info.get("version", "")).lstrip("=")
            if version:
                yield name, version


def _parse_package_lock(path: Path) -> Iterable[tuple[str, str]]:
    data = json.loads(path.read_text())
    packages = data.get("packages")
    if isinstance(packages, dict):
        for key, info in packages.items():
            if key.startswith("node_modules/") and "version" in info:
                name = key.split("node_modules/")[-1]
                yield name, str(info["version"])
    for name, info in (data.get("dependencies") or {}).items():
        if isinstance(info, dict) and "version" in info:
            yield name, str(info["version"])


def _parse_pnpm_lock(path: Path) -> Iterable[tuple[str, str]]:
    data = yaml.safe_load(path.read_text()) or {}
    for key in data.get("packages") or {}:
        spec = str(key).lstrip("/")
        m = re.match(r"^(@?[^@]+)@([^()]+)", spec)
        if m:
            yield m.group(1), m.group(2)


def _parse_yarn_lock(path: Path) -> Iterable[tuple[str, str]]:
    current: list[str] = []
    for line in path.read_text(errors="replace").splitlines():
        if line and not line.startswith((" ", "#")) and line.rstrip().endswith(":"):
            current = [
                re.sub(r"@[^@]*$", "", s.strip().strip('"')) for s in line.rstrip(":").split(",")
            ]
        m = re.match(r'^\s+version\s+"?([^"\s]+)"?', line)
        if m and current:
            for name in current:
                yield name, m.group(1)
            current = []


LOCKFILES: dict[str, tuple[str, Callable[[Path], Iterable[tuple[str, str]]]]] = {
    "uv.lock": ("pypi", _parse_toml_packages),
    "poetry.lock": ("pypi", _parse_toml_packages),
    "Pipfile.lock": ("pypi", _parse_pipfile_lock),
    "package-lock.json": ("npm", _parse_package_lock),
    "pnpm-lock.yaml": ("npm", _parse_pnpm_lock),
    "yarn.lock": ("npm", _parse_yarn_lock),
}


def read_lockfiles(root: Path) -> dict[tuple[str, str], LockedPackage]:
    """Pinned versions keyed by (ecosystem, canonical name). First lockfile found wins."""
    found: dict[tuple[str, str], LockedPackage] = {}
    candidates = sorted(
        p
        for p in root.rglob("*")
        if p.is_file()
        and "node_modules" not in p.parts
        and ".venv" not in p.parts
        and (p.name in LOCKFILES or re.match(r"^requirements[\w.-]*\.txt$", p.name))
    )
    for path in candidates:
        ecosystem, parser = LOCKFILES.get(path.name, ("pypi", _parse_requirements))
        rel = path.relative_to(root).as_posix()
        try:
            pairs = list(parser(path))
        except (ValueError, tomllib.TOMLDecodeError, yaml.YAMLError, OSError):
            continue
        for name, version in pairs:
            key = (ecosystem, _canonical_pypi(name) if ecosystem == "pypi" else name)
            found.setdefault(key, LockedPackage(ecosystem, name, version, rel))
    return found


# ---------------------------------------------------------------------------- sources


class PackageSource(Protocol):
    def fetch(self, package: LockedPackage, dest: Path) -> Path:
        """Put the package's source under dest and return the directory to analyze."""


class LocalDirectorySource:
    """A mirror laid out as <root>/<ecosystem>/<name>-<version>/ (tests, air-gapped runs)."""

    def __init__(self, root: Path) -> None:
        self.root = root

    def fetch(self, package: LockedPackage, dest: Path) -> Path:
        src = self.root / package.ecosystem / f"{package.name}-{package.version}"
        if not src.is_dir():
            raise FileNotFoundError(
                f"{package.name} {package.version} not in local mirror {self.root}"
            )
        target = dest / f"{package.name}-{package.version}"
        shutil.copytree(src, target, dirs_exist_ok=True)
        return target


def _download(url: str) -> bytes:
    if not url.startswith("https://"):
        raise ValueError(f"refusing non-https download: {url}")
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})  # noqa: S310
    with urllib.request.urlopen(request, timeout=60) as response:  # noqa: S310 - https only
        data: bytes = response.read(MAX_ARCHIVE_BYTES + 1)
    if len(data) > MAX_ARCHIVE_BYTES:
        raise ValueError(f"archive larger than {MAX_ARCHIVE_BYTES} bytes: {url}")
    return data


def _safe_extract(data: bytes, dest: Path) -> None:
    """Extract tar or zip data, refusing absolute paths, traversal, links, and huge members."""
    dest.mkdir(parents=True, exist_ok=True)
    root = dest.resolve()

    def target_for(name: str) -> Path:
        target = (dest / name).resolve()
        if not str(target).startswith(str(root) + "/"):
            raise ValueError(f"unsafe path in archive: {name}")
        return target

    if data[:2] == b"PK":
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            for info in zf.infolist():
                if info.is_dir() or info.file_size > MAX_MEMBER_BYTES:
                    continue
                target = target_for(info.filename)
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(zf.read(info))
        return
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:*") as tf:
        for member in tf.getmembers():
            if not member.isfile() or member.size > MAX_MEMBER_BYTES:
                continue
            target = target_for(member.name)
            target.parent.mkdir(parents=True, exist_ok=True)
            extracted = tf.extractfile(member)
            if extracted is not None:
                target.write_bytes(extracted.read())


class PyPISource:
    index = "https://pypi.org/pypi"

    def fetch(self, package: LockedPackage, dest: Path) -> Path:
        meta = json.loads(_download(f"{self.index}/{package.name}/{package.version}/json"))
        files = meta.get("urls", [])
        wheel = next((f for f in files if f["packagetype"] == "bdist_wheel"), None)
        sdist = next((f for f in files if f["packagetype"] == "sdist"), None)
        chosen = wheel or sdist
        if chosen is None:
            raise FileNotFoundError(f"no downloadable files for {package.name} {package.version}")
        target = dest / f"{package.name}-{package.version}"
        _safe_extract(_download(chosen["url"]), target)
        return target


class NpmSource:
    index = "https://registry.npmjs.org"

    def fetch(self, package: LockedPackage, dest: Path) -> Path:
        meta = json.loads(_download(f"{self.index}/{package.name}/{package.version}"))
        tarball = meta["dist"]["tarball"]
        target = dest / f"{package.name.replace('/', '__')}-{package.version}"
        _safe_extract(_download(tarball), target)
        inner = target / "package"
        return inner if inner.is_dir() else target


class RegistrySources:
    """Dispatch to PyPI or npm by ecosystem."""

    def __init__(self) -> None:
        self.sources: dict[str, PackageSource] = {"pypi": PyPISource(), "npm": NpmSource()}

    def fetch(self, package: LockedPackage, dest: Path) -> Path:
        return self.sources[package.ecosystem].fetch(package, dest)


# ---------------------------------------------------------------------------- profiling


@dataclass
class ProfileResult:
    network_methods: list[str] = field(default_factory=list)
    endpoints: list[str] = field(default_factory=list)
    sink_locations: list[dict[str, Any]] = field(default_factory=list)


PackageAnalyzer = Callable[[Path, str, str], ProfileResult]
"""(package_dir, language, import_name) -> what the package sends and where."""


@dataclass
class DependencyProfile:
    ecosystem: str
    name: str
    version: str
    import_names: list[str]
    status: str  # profiled | no-network | not-locked | error | skipped-cap
    lockfile: str = ""
    network_methods: list[str] = field(default_factory=list)
    endpoints: list[str] = field(default_factory=list)
    sink_locations: list[dict[str, Any]] = field(default_factory=list)
    reason: str = ""
    evidence: str = INFERRED

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def matches_import(self, path: str) -> bool:
        return any(
            path == n or path.startswith(n + ".") or path.startswith(n + "/")
            for n in self.import_names
        )


def import_to_package(language: str, specifier: str) -> tuple[str, str]:
    """(ecosystem, package name) for an import. Python import names are guessed as dist names."""
    if language == "python":
        return "pypi", _canonical_pypi(specifier.split(".")[0])
    parts = specifier.split("/")
    name = "/".join(parts[:2]) if specifier.startswith("@") else parts[0]
    return "npm", name


def unregistered_imports(
    imports: Mapping[str, Iterable[str]], registry: Registry
) -> dict[tuple[str, str], tuple[str, set[str]]]:
    """(ecosystem, package) -> (language, import names) for imports no registry entry covers."""
    out: dict[tuple[str, str], tuple[str, set[str]]] = {}
    stdlib = set(sys.stdlib_module_names)
    for language, specs in imports.items():
        for spec in sorted(set(specs)):
            if not spec or spec.startswith("."):
                continue
            top = spec.split(".")[0] if language == "python" else spec
            if language == "python" and (top in stdlib or top in PYTHON_IGNORE):
                continue
            if language == "typescript" and (
                spec.startswith("node:")
                or import_to_package(language, spec)[1] in NPM_IGNORE
                or top in stdlib
            ):
                continue
            if registry.match_import(language, spec) is not None:
                continue
            key = import_to_package(language, spec)
            name = top if language == "python" else key[1]
            out.setdefault(key, (language, set()))[1].add(name)
    return out


def resolve_unregistered(
    root: Path,
    imports: Mapping[str, Iterable[str]],
    registry: Registry,
    analyzer: PackageAnalyzer,
    source: PackageSource,
    workdir: Path,
    cap: int = 10,
) -> list[DependencyProfile]:
    locked = read_lockfiles(root)
    profiles: list[DependencyProfile] = []
    fetched = 0
    for (ecosystem, name), (language, import_names) in sorted(
        unregistered_imports(imports, registry).items()
    ):
        pin = locked.get((ecosystem, name))
        base = DependencyProfile(
            ecosystem, name, pin.version if pin else "", sorted(import_names), ""
        )
        if pin is None:
            base.status, base.reason = "not-locked", "no pinned version in any lockfile"
            profiles.append(base)
            continue
        base.lockfile = pin.lockfile
        if fetched >= cap:
            base.status, base.reason = (
                "skipped-cap",
                f"dependency analysis capped at {cap} packages",
            )
            profiles.append(base)
            continue
        fetched += 1
        try:
            package_dir = source.fetch(pin, workdir)
            result = analyzer(package_dir, language, sorted(import_names)[0])
        except Exception as exc:
            base.status, base.reason = "error", f"{type(exc).__name__}: {exc}"[:300]
            profiles.append(base)
            continue
        base.network_methods = sorted(set(result.network_methods))
        base.endpoints = sorted(set(result.endpoints))
        base.sink_locations = result.sink_locations
        base.status = "profiled" if base.network_methods else "no-network"
        profiles.append(base)
    return profiles
