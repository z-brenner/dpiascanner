from datetime import date

import pytest
import yaml

from lantern_registry import DESTINATION_CLASSES, Registry, RegistryError, load_registry
from lantern_registry.validate import check_structure, validate

REQUIRED_VENDORS = {
    "sentry",
    "segment",
    "mixpanel",
    "amplitude",
    "posthog",
    "firebase",
    "firebase-analytics",
    "google-analytics",
    "meta-pixel",
    "stripe",
    "twilio",
    "sendgrid",
    "mailchimp",
    "openai",
    "anthropic",
    "google-gemini",
    "aws-python",
    "aws-js",
    "google-cloud",
    "azure",
    "datadog",
    "newrelic",
    "intercom",
    "hubspot",
    "braze",
    "onesignal",
}


def test_registry_covers_the_required_packages() -> None:
    registry = load_registry()
    assert {e.id for e in registry.entries} >= REQUIRED_VENDORS
    for ecosystem, name in [
        ("pypi", "sentry-sdk"),
        ("npm", "@sentry/node"),
        ("npm", "@segment/analytics-node"),
        ("npm", "analytics-node"),
        ("npm", "mixpanel-browser"),
        ("pypi", "posthog"),
        ("npm", "firebase-admin"),
        ("npm", "react-facebook-pixel"),
        ("npm", "react-ga4"),
        ("pypi", "stripe"),
        ("npm", "twilio"),
        ("npm", "@sendgrid/mail"),
        ("pypi", "mailchimp-marketing"),
        ("pypi", "openai"),
        ("npm", "@anthropic-ai/sdk"),
        ("pypi", "google-generativeai"),
        ("pypi", "boto3"),
        ("npm", "@aws-sdk/client-s3"),
        ("pypi", "google-cloud-storage"),
        ("npm", "@azure/storage-blob"),
        ("npm", "dd-trace"),
        ("pypi", "newrelic"),
        ("npm", "intercom-client"),
        ("npm", "@hubspot/api-client"),
        ("npm", "@braze/web-sdk"),
        ("npm", "@onesignal/node-onesignal"),
    ]:
        assert registry.match_package(ecosystem, name) is not None, (ecosystem, name)


def test_every_entry_is_well_formed() -> None:
    report = validate()
    assert report.ok, report.errors


def test_entries_carry_the_required_fields() -> None:
    for entry in load_registry().entries:
        assert entry.vendor and entry.dpa_url.startswith("https://")
        assert entry.destination_class in DESTINATION_CLASSES
        assert entry.auto_collected, entry.id
        assert entry.endpoints, entry.id
        assert entry.last_reviewed <= date.today()
        assert entry.reviewed_by, "say who reviewed it, even if it was only a draft"


def test_import_matching_uses_full_paths() -> None:
    registry = load_registry()
    assert registry.match_import("python", "google.cloud.storage").id == "google-cloud"  # type: ignore[union-attr]
    assert registry.match_import("python", "google.generativeai").id == "google-gemini"  # type: ignore[union-attr]
    assert registry.match_import("typescript", "firebase/analytics").id == "firebase-analytics"  # type: ignore[union-attr]
    assert registry.match_import("typescript", "firebase/firestore").id == "firebase"  # type: ignore[union-attr]
    assert registry.match_import("typescript", "@sentry/node").id == "sentry"  # type: ignore[union-attr]
    assert registry.match_import("python", "sentry_sdk_extra") is None
    assert registry.match_import("python", "googleapis_common") is None


def test_contested_processor_claims_are_not_papered_over() -> None:
    registry = load_registry()
    assert registry.get("meta-pixel").processor_claims.cpra_service_provider is False
    assert "Fashion ID" in registry.get("meta-pixel").processor_claims.notes
    assert registry.get("google-analytics").processor_claims.cpra_service_provider is None
    assert registry.get("google-gemini").processor_claims.gdpr_processor is None


def test_validator_catches_malformed_entries() -> None:
    raw = yaml.safe_load(
        """
schema_version: 1
entries:
  - id: bad
    vendor: Bad Co
    packages: {pypi: [bad]}
    imports: {python: [bad]}
    destination_class: telemetry
    sink_calls: [send]
    setup_calls: [send]
    auto_collected: []
    endpoints: []
    dpa_url: http://bad.example/dpa
    mitigation_hooks:
      - {name: hook, kind: event_scrubber, languages: [python]}
    last_reviewed: 2099-01-01
"""
    )
    registry = Registry.from_yaml(yaml.safe_dump(raw))
    errors = check_structure(registry, raw).errors
    joined = "\n".join(errors)
    for expected in (
        "destination_class",
        "both sink and setup",
        "auto_collected",
        "no endpoints",
        "https",
        "event_scrubber",
        "future",
    ):
        assert expected in joined, expected


def test_malformed_yaml_is_rejected() -> None:
    with pytest.raises(RegistryError):
        Registry.from_yaml("entries:\n  - id: x\n")
