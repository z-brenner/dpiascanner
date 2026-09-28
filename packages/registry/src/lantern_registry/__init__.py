"""Curated third-party SDK registry. Entries are evidence, not verdicts."""

from lantern_registry.model import (
    DESTINATION_CLASSES,
    Endpoint,
    MitigationHook,
    ProcessorClaims,
    Registry,
    RegistryEntry,
    RegistryError,
    load_registry,
)

__version__ = "0.1.0"

__all__ = [
    "DESTINATION_CLASSES",
    "Endpoint",
    "MitigationHook",
    "ProcessorClaims",
    "Registry",
    "RegistryEntry",
    "RegistryError",
    "load_registry",
]
