import io
import tarfile
from pathlib import Path

import pytest

from lantern_registry import load_registry
from lantern_registry.resolver import (
    LocalDirectorySource,
    ProfileResult,
    _safe_extract,
    read_lockfiles,
    resolve_unregistered,
    unregistered_imports,
)

FIXTURES = Path(__file__).resolve().parents[3] / "fixtures"
MIRROR = LocalDirectorySource(FIXTURES / "dependency-mirror")


def test_reads_requirements_and_package_lock() -> None:
    py = read_lockfiles(FIXTURES / "canary-python")
    assert py[("pypi", "mixpanel")].version == "5.0.0"
    assert py[("pypi", "sentry-sdk")].lockfile == "requirements.txt"
    ts = read_lockfiles(FIXTURES / "canary-typescript")
    assert ts[("npm", "mixpanel")].version == "0.24.0"
    assert ts[("npm", "@aws-sdk/client-s3")].version == "3.1141.0"


def test_canary_dependencies_are_all_covered_or_ignored() -> None:
    imports = {
        "python": {
            "fastapi",
            "sqlalchemy.orm",
            "pydantic",
            "yaml",
            "httpx",
            "mixpanel",
            "sentry_sdk",
            "stripe",
            "boto3",
            "botocore.config",
            "os",
            "json",
        },
        "typescript": {
            "express",
            "mixpanel",
            "@sentry/node",
            "stripe",
            "@aws-sdk/client-s3",
            "axios",
            "pino",
            "node:fs",
            "@prisma/adapter-pg",
        },
    }
    assert unregistered_imports(imports, load_registry()) == {}


def _fake_analyzer(calls: list[str]):  # type: ignore[no-untyped-def]
    def analyze(path: Path, language: str, name: str) -> ProfileResult:
        calls.append(name)
        if name == "acme_geo":
            return ProfileResult(["locate"], ["api.acme-geo.example"], [])
        return ProfileResult()

    return analyze


def test_resolver_profiles_locked_unregistered_packages(tmp_path: Path) -> None:
    calls: list[str] = []
    profiles = resolve_unregistered(
        FIXTURES / "unregistered-sdk-python",
        {"python": {"acme_geo", "tiny_utils", "fastapi", "not_locked_pkg"}},
        load_registry(),
        _fake_analyzer(calls),
        MIRROR,
        tmp_path,
    )
    by_name = {p.name: p for p in profiles}
    assert by_name["acme-geo"].status == "profiled"
    assert by_name["acme-geo"].network_methods == ["locate"]
    assert by_name["acme-geo"].evidence == "inferred-from-dependency-source"
    assert by_name["tiny-utils"].status == "no-network"
    assert by_name["not-locked-pkg"].status == "not-locked"
    assert "fastapi" not in by_name
    assert sorted(calls) == ["acme_geo", "tiny_utils"]


def test_resolver_respects_the_cap(tmp_path: Path) -> None:
    profiles = resolve_unregistered(
        FIXTURES / "unregistered-sdk-python",
        {"python": {"acme_geo", "tiny_utils"}},
        load_registry(),
        _fake_analyzer([]),
        MIRROR,
        tmp_path,
        cap=1,
    )
    assert sorted(p.status for p in profiles) == ["profiled", "skipped-cap"]


def test_fetch_errors_are_recorded_not_raised(tmp_path: Path) -> None:
    empty = LocalDirectorySource(tmp_path / "nothing-here")
    profiles = resolve_unregistered(
        FIXTURES / "unregistered-sdk-python",
        {"python": {"acme_geo"}},
        load_registry(),
        _fake_analyzer([]),
        empty,
        tmp_path,
    )
    assert profiles[0].status == "error" and "FileNotFoundError" in profiles[0].reason


def test_safe_extract_rejects_path_traversal(tmp_path: Path) -> None:
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as tf:
        data = b"owned"
        info = tarfile.TarInfo("../../escape.txt")
        info.size = len(data)
        tf.addfile(info, io.BytesIO(data))
    with pytest.raises(ValueError, match="unsafe path"):
        _safe_extract(buffer.getvalue(), tmp_path / "out")
    assert not (tmp_path / "escape.txt").exists()
