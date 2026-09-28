"""Registry entry types and loading."""

from __future__ import annotations

import fnmatch
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date
from functools import cache
from importlib import resources
from pathlib import Path
from typing import Any

import yaml

ECOSYSTEMS = ("pypi", "npm")
LANGUAGES = ("python", "typescript")
DESTINATION_CLASSES = (
    "first_party_store",
    "log",
    "queue",
    "analytics",
    "ad_tech",
    "cloud_provider",
    "payment_processor",
    "communications",
    "ai_model_provider",
    "unknown_third_party",
    "cross_border",
)


class RegistryError(ValueError):
    pass


@dataclass(frozen=True)
class Endpoint:
    host: str
    region: str
    notes: str = ""


@dataclass(frozen=True)
class MitigationHook:
    """A vendor-provided control that can reduce what the SDK sends.

    ``kind`` is ``event_scrubber`` (a callback that edits outgoing events, such as Sentry
    before_send), ``flag`` (an init option such as send_default_pii), or ``config`` (an
    integration setting such as Segment destination filters).
    """

    name: str
    kind: str
    languages: tuple[str, ...]
    config_call: str = ""
    argument: str = ""
    description: str = ""


@dataclass(frozen=True)
class ProcessorClaims:
    gdpr_processor: bool | None
    cpra_service_provider: bool | None
    notes: str = ""


@dataclass(frozen=True)
class RegistryEntry:
    id: str
    vendor: str
    packages: Mapping[str, tuple[str, ...]]
    imports: Mapping[str, tuple[str, ...]]
    destination_class: str
    sink_calls: tuple[str, ...]
    setup_calls: tuple[str, ...]
    event_paths: Mapping[str, str]
    auto_collected: tuple[str, ...]
    endpoints: tuple[Endpoint, ...]
    processor_claims: ProcessorClaims
    dpa_url: str
    mitigation_hooks: tuple[MitigationHook, ...]
    last_reviewed: date
    captures_unhandled_exceptions: bool = False
    notes: str = ""
    references: tuple[str, ...] = field(default_factory=tuple)

    def matches_import(self, language: str, specifier: str) -> bool:
        return any(
            specifier == pattern
            or specifier.startswith(pattern + ".")
            or specifier.startswith(pattern + "/")
            or fnmatch.fnmatchcase(specifier, pattern)
            for pattern in self.imports.get(language, ())
        )

    def is_sink_call(self, method: str) -> bool:
        if method in self.setup_calls:
            return False
        return "*" in self.sink_calls or method in self.sink_calls

    def hooks_for(self, language: str) -> tuple[MitigationHook, ...]:
        return tuple(h for h in self.mitigation_hooks if language in h.languages)

    def summary(self) -> dict[str, Any]:
        """Compact form attached to graph nodes and decision states."""
        return {
            "id": self.id,
            "vendor": self.vendor,
            "destination_class": self.destination_class,
            "processor_claims": {
                "gdpr_processor": self.processor_claims.gdpr_processor,
                "cpra_service_provider": self.processor_claims.cpra_service_provider,
            },
            "auto_collected": list(self.auto_collected),
            "endpoints": [{"host": e.host, "region": e.region} for e in self.endpoints],
            "dpa_url": self.dpa_url,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> RegistryEntry:
        try:
            claims = data.get("processor_claims", {})
            reviewed = data["last_reviewed"]
            return cls(
                id=str(data["id"]),
                vendor=str(data["vendor"]),
                packages={k: tuple(v) for k, v in data["packages"].items()},
                imports={k: tuple(v) for k, v in data["imports"].items()},
                destination_class=str(data["destination_class"]),
                sink_calls=tuple(data.get("sink_calls", ["*"])),
                setup_calls=tuple(data.get("setup_calls", [])),
                event_paths=dict(data.get("event_paths", {})),
                auto_collected=tuple(data.get("auto_collected", [])),
                endpoints=tuple(Endpoint(**e) for e in data.get("endpoints", [])),
                processor_claims=ProcessorClaims(
                    gdpr_processor=claims.get("gdpr_processor"),
                    cpra_service_provider=claims.get("cpra_service_provider"),
                    notes=str(claims.get("notes", "")),
                ),
                dpa_url=str(data["dpa_url"]),
                mitigation_hooks=tuple(
                    MitigationHook(
                        name=str(h["name"]),
                        kind=str(h["kind"]),
                        languages=tuple(h.get("languages", LANGUAGES)),
                        config_call=str(h.get("config_call", "")),
                        argument=str(h.get("argument", "")),
                        description=str(h.get("description", "")),
                    )
                    for h in data.get("mitigation_hooks", [])
                ),
                last_reviewed=(
                    reviewed if isinstance(reviewed, date) else date.fromisoformat(str(reviewed))
                ),
                captures_unhandled_exceptions=bool(data.get("captures_unhandled_exceptions")),
                notes=str(data.get("notes", "")),
                references=tuple(data.get("references", [])),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise RegistryError(f"bad registry entry {data.get('id')!r}: {exc}") from exc


@dataclass(frozen=True)
class Registry:
    version: str
    entries: tuple[RegistryEntry, ...]

    def get(self, entry_id: str) -> RegistryEntry:
        for entry in self.entries:
            if entry.id == entry_id:
                return entry
        raise KeyError(entry_id)

    def match_import(self, language: str, specifier: str) -> RegistryEntry | None:
        for entry in self.entries:
            if entry.matches_import(language, specifier):
                return entry
        return None

    def match_package(self, ecosystem: str, name: str) -> RegistryEntry | None:
        for entry in self.entries:
            if any(
                name == p or fnmatch.fnmatchcase(name, p) for p in entry.packages.get(ecosystem, ())
            ):
                return entry
        return None

    @classmethod
    def from_yaml(cls, text: str) -> Registry:
        data = yaml.safe_load(text)
        entries = tuple(RegistryEntry.from_dict(e) for e in data["entries"])
        ids = [e.id for e in entries]
        if len(ids) != len(set(ids)):
            raise RegistryError("duplicate registry ids")
        return cls(version=str(data.get("version", "unversioned")), entries=entries)


@cache
def load_registry(path: str | None = None) -> Registry:
    if path is None:
        text = (resources.files("lantern_registry") / "registry.yaml").read_text(encoding="utf-8")
    else:
        text = Path(path).read_text(encoding="utf-8")
    return Registry.from_yaml(text)


def as_names(values: Sequence[str]) -> tuple[str, ...]:
    return tuple(values)
