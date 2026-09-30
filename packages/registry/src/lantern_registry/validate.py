"""Validate the registry: structure always, URL reachability on request.

    python -m lantern_registry.validate                 # schema checks only (CI)
    python -m lantern_registry.validate --check-urls    # also fetch every URL (monthly job)

Exit status is non-zero when an entry is malformed or a URL is broken. URLs that answer
401, 403 or 429 are reported as "blocked" (usually bot protection) and need a manual look;
they do not fail the run.
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from datetime import date
from typing import Any
from urllib.parse import urlparse

import yaml

from lantern_registry.model import (
    DESTINATION_CLASSES,
    ECOSYSTEMS,
    LANGUAGES,
    Registry,
    RegistryEntry,
)

HOOK_KINDS = frozenset({"event_scrubber", "flag", "config"})
MAX_REVIEW_AGE_DAYS = 365
BLOCKED_STATUSES = frozenset({401, 403, 429})
USER_AGENT = "Mozilla/5.0 (compatible; lantern-registry-validator/1.0)"


@dataclass
class Report:
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    urls: dict[str, str] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return not self.errors


def _is_https(url: str) -> bool:
    parsed = urlparse(url)
    return parsed.scheme == "https" and bool(parsed.netloc)


def check_entry(entry: RegistryEntry, today: date) -> list[str]:
    problems: list[str] = []
    where = f"entry {entry.id}"
    if entry.destination_class not in DESTINATION_CLASSES:
        problems.append(
            f"{where}: destination_class {entry.destination_class!r} is not a sink_v1 option"
        )
    if set(entry.packages) - set(ECOSYSTEMS):
        problems.append(f"{where}: unknown ecosystems {set(entry.packages) - set(ECOSYSTEMS)}")
    if not any(entry.packages.values()):
        problems.append(f"{where}: no package names")
    if set(entry.imports) - set(LANGUAGES):
        problems.append(f"{where}: unknown languages {set(entry.imports) - set(LANGUAGES)}")
    if not any(entry.imports.values()):
        problems.append(f"{where}: no import names")
    if not entry.sink_calls:
        problems.append(f"{where}: sink_calls is empty")
    overlap = set(entry.sink_calls) & set(entry.setup_calls)
    if overlap:
        problems.append(f"{where}: calls listed as both sink and setup: {sorted(overlap)}")
    if not entry.auto_collected:
        problems.append(
            f"{where}: auto_collected must say what the SDK collects on its own, even if nothing"
        )
    if not entry.endpoints:
        problems.append(f"{where}: no endpoints")
    if not _is_https(entry.dpa_url):
        problems.append(f"{where}: dpa_url must be an https URL")
    for ref in entry.references:
        if not _is_https(ref):
            problems.append(f"{where}: reference {ref!r} must be https")
    for hook in entry.mitigation_hooks:
        if hook.kind not in HOOK_KINDS:
            problems.append(f"{where}: hook {hook.name} has unknown kind {hook.kind}")
        if hook.kind == "event_scrubber" and not (hook.config_call and hook.argument):
            problems.append(
                f"{where}: event_scrubber hook {hook.name} needs config_call and argument"
            )
        if set(hook.languages) - set(LANGUAGES):
            problems.append(f"{where}: hook {hook.name} has unknown languages")
    for method, path in entry.event_paths.items():
        if method not in entry.sink_calls and "*" not in entry.sink_calls:
            problems.append(f"{where}: event_paths names {method}, which is not a sink call")
        if not path:
            problems.append(f"{where}: empty event path for {method}")
    if entry.last_reviewed > today:
        problems.append(f"{where}: last_reviewed is in the future")
    return problems


def check_structure(registry: Registry, raw: dict[str, Any], today: date | None = None) -> Report:
    today = today or date.today()
    report = Report()
    if raw.get("schema_version") != 1:
        report.errors.append("schema_version must be 1")
    for entry in registry.entries:
        report.errors.extend(check_entry(entry, today))
        age = (today - entry.last_reviewed).days
        if age > MAX_REVIEW_AGE_DAYS:
            report.warnings.append(f"entry {entry.id}: last reviewed {age} days ago")
    claimed: dict[tuple[str, str], str] = {}
    for entry in registry.entries:
        for language, patterns in entry.imports.items():
            for pattern in patterns:
                key = (language, pattern)
                if key in claimed:
                    report.errors.append(
                        f"import {pattern} ({language}) claimed by {claimed[key]} and {entry.id}"
                    )
                claimed[key] = entry.id
    return report


def fetch_status(url: str, timeout: float = 20.0) -> int:
    if not _is_https(url):
        raise ValueError(f"refusing non-https URL: {url}")
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})  # noqa: S310
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310 - https only
            return int(response.status)
    except urllib.error.HTTPError as exc:
        return int(exc.code)


def check_urls(registry: Registry, report: Report, timeout: float = 20.0) -> None:
    urls = sorted(
        {e.dpa_url for e in registry.entries} | {r for e in registry.entries for r in e.references}
    )
    for url in urls:
        try:
            status = fetch_status(url, timeout)
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            report.errors.append(f"{url}: unreachable ({exc})")
            report.urls[url] = "unreachable"
            continue
        if 200 <= status < 400:
            report.urls[url] = f"ok ({status})"
        elif status in BLOCKED_STATUSES:
            report.urls[url] = f"blocked ({status})"
            report.warnings.append(f"{url}: answered {status}; check by hand")
        else:
            report.urls[url] = f"broken ({status})"
            report.errors.append(f"{url}: answered {status}")


def validate(path: str | None = None, *, with_urls: bool = False) -> Report:
    from importlib import resources

    text = (
        (resources.files("lantern_registry") / "registry.yaml").read_text("utf-8")
        if path is None
        else open(path, encoding="utf-8").read()  # noqa: SIM115
    )
    raw = yaml.safe_load(text)
    registry = Registry.from_yaml(text)
    report = check_structure(registry, raw)
    if with_urls:
        check_urls(registry, report)
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description="Validate the Katz SDK registry.")
    parser.add_argument("--path", default=None)
    parser.add_argument("--check-urls", action="store_true")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    report = validate(args.path, with_urls=args.check_urls)
    if args.json:
        print(
            json.dumps(
                {"errors": report.errors, "warnings": report.warnings, "urls": report.urls},
                indent=2,
            )
        )
    else:
        for url, status in sorted(report.urls.items()):
            print(f"{status:>16}  {url}")
        for warning in report.warnings:
            print(f"warning: {warning}")
        for error in report.errors:
            print(f"error: {error}")
        print("registry OK" if report.ok else f"registry has {len(report.errors)} error(s)")
    sys.exit(0 if report.ok else 1)


if __name__ == "__main__":
    main()
