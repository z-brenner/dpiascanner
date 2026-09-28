"""Narrative prose for the report, every sentence cited to a finding.

A section's narrative is drafted from ``SectionBrief``: the section's purpose and the
code-free briefs of the findings it covers (``ReportContext.brief``). Two drafters:

- ``TemplateDrafter`` turns each finding into plain sentences with fixed templates. It needs
  no model and is always available.
- ``LLMDrafter`` asks a language model for the same section. It sees only the section brief,
  never source code. Its output is validated: every sentence must cite at least one finding
  as ``[F-0042]``, and only findings given to it. A section whose draft fails validation is
  regenerated with the rejections as feedback; after two failed attempts the section falls
  back to the template drafter.
"""

from __future__ import annotations

import json
import os
import re
from collections import defaultdict
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol

import httpx

from lantern_analysis.secrets import payload_guard

CITATION = re.compile(r"\[(F-\d{4,})\]")
ANY_BRACKET_F = re.compile(r"\[F-[^\]]*\]")
MAX_SENTENCE = 700
MAX_CITES = 24
_ABBREVIATIONS = (
    "Art",
    "Arts",
    "Cal",
    "Civ",
    "Regs",
    "tit",
    "No",
    "Inc",
    "Co",
    "Ltd",
    "St",
    "Sec",
    "vs",
    "etc",
    "e.g",
    "i.e",
    "U.S",
    "C.F.R",
    "Pub",
    "L",
    "Stat",
)

CATEGORY_LABELS = {
    "identifier": "identifier",
    "contact": "contact",
    "financial": "financial",
    "health": "health",
    "biometric": "biometric",
    "precise_location": "precise location",
    "coarse_location": "coarse location",
    "behavioral": "behavioral",
    "credentials": "credential",
    "government_id": "government ID",
    "demographic": "demographic",
}
DESTINATION_LABELS = {
    "first_party_store": "the organization's own storage",
    "log": "logging or error reporting",
    "queue": "a message queue",
    "analytics": "an analytics service",
    "ad_tech": "an advertising service",
    "cloud_provider": "a cloud infrastructure provider",
    "payment_processor": "a payment processor",
    "communications": "a communications service",
    "ai_model_provider": "an AI model provider",
    "unknown_third_party": "a third party that is not in Lantern's SDK registry",
    "cross_border": "a destination in another jurisdiction",
}
PURPOSE_LABELS = {
    "service_delivery": "delivering the service",
    "security": "security",
    "analytics": "analytics",
    "marketing": "marketing",
    "legal_obligation": "meeting a legal obligation",
}
SENSITIVE_PI = {
    "government_id": "government identifiers such as a Social Security number",
    "precise_location": "precise geolocation",
    "health": "health information",
    "biometric": "biometric information processed to identify a consumer",
    "credentials": "account log-in credentials",
}


# --------------------------------------------------------------------------- data types


@dataclass(frozen=True)
class SectionBrief:
    section_id: str
    title: str
    instruction: str
    findings: list[dict[str, Any]]

    @property
    def finding_ids(self) -> set[str]:
        return {f["id"] for f in self.findings}

    def payload(self) -> dict[str, Any]:
        data = {
            "section": self.section_id,
            "title": self.title,
            "instruction": self.instruction,
            "findings": self.findings,
        }
        payload_guard(data)
        return data


@dataclass(frozen=True)
class Rejection:
    sentence: str
    reason: str


@dataclass
class Narrative:
    section_id: str
    sentences: list[str]
    drafter: str  # template | llm
    attempts: int = 0
    rejections: list[Rejection] = field(default_factory=list)
    note: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "section_id": self.section_id,
            "sentences": self.sentences,
            "drafter": self.drafter,
            "attempts": self.attempts,
            "rejections": [{"sentence": r.sentence, "reason": r.reason} for r in self.rejections],
            "note": self.note,
        }


# --------------------------------------------------------------------------- validation


def split_sentences(text: str) -> list[str]:
    """Split prose into sentences without breaking legal abbreviations (Art., Cal. Civ.)."""
    parts: list[str] = []
    start = 0
    for m in re.finditer(r"[.!?](?:\s*\[F-\d{4,}\])*\s+(?=[A-Z\[(\"'])", text):
        before = text[start : m.start()]
        word = re.split(r"[\s(]", before)[-1] if before else ""
        if word in _ABBREVIATIONS or re.fullmatch(r"[A-Z]", word):
            continue
        parts.append(text[start : m.end()].strip())
        start = m.end()
    tail = text[start:].strip()
    if tail:
        parts.append(tail)
    return [p for p in parts if p]


def validate_sentences(sentences: Sequence[str], allowed: set[str]) -> list[Rejection]:
    """Every sentence cites at least one finding, well-formed, and only allowed ids."""
    rejections: list[Rejection] = []
    for raw in sentences:
        for sentence in split_sentences(raw) or [raw]:
            cited = CITATION.findall(sentence)
            malformed = [b for b in ANY_BRACKET_F.findall(sentence) if not CITATION.fullmatch(b)]
            unknown = sorted(set(cited) - allowed)
            if not sentence.strip():
                rejections.append(Rejection(sentence, "empty sentence"))
            elif not cited:
                rejections.append(Rejection(sentence, "no citation"))
            elif malformed:
                rejections.append(Rejection(sentence, f"malformed citation {malformed[0]}"))
            elif unknown:
                rejections.append(Rejection(sentence, f"cites unknown finding {unknown[0]}"))
            elif len(sentence) > MAX_SENTENCE:
                rejections.append(Rejection(sentence, "sentence too long"))
    return rejections


# --------------------------------------------------------------------------- template drafter


def _cite(ids: Iterable[str]) -> str:
    unique = list(dict.fromkeys(ids))[:MAX_CITES]
    return " ".join(f"[{i}]" for i in unique)


def _sentence(text: str, ids: Iterable[str]) -> str:
    return f"{text.rstrip('.')} {_cite(ids)}."


def _join(items: Sequence[str]) -> str:
    items = [i for i in items if i]
    if len(items) <= 1:
        return "".join(items)
    return ", ".join(items[:-1]) + (", and " if len(items) > 2 else " and ") + items[-1]


def _cats(f: dict[str, Any], capitalize: bool = True) -> str:
    labels = [CATEGORY_LABELS.get(c, c.replace("_", " ")) for c in f["data_categories"]]
    text = f"{_join(labels)} data" if labels else "Personal data"
    if not labels:
        return text if capitalize else "personal data"
    return text[:1].upper() + text[1:] if capitalize else text


def _fields(f: dict[str, Any]) -> str:
    fields = [x.replace("_", " ") for x in f.get("fields", [])][:5]
    return f" ({_join(fields)})" if fields else ""


def _entries(f: dict[str, Any]) -> str:
    entries = f.get("entry_points") or []
    return f" received at {_join(entries[:3])}" if entries else ""


def _dest(f: dict[str, Any]) -> str:
    return str(f.get("destination") or "an unnamed destination")


def _dest_class(f: dict[str, Any]) -> str:
    return DESTINATION_LABELS.get(str(f.get("destination_class") or ""), "")


def _verified(f: dict[str, Any]) -> str:
    v = f.get("verification") or {}
    if v.get("status") == "verified" and v.get("reason") == "canary-observed":
        return ", which dynamic verification observed"
    if v.get("status") == "verified":
        return ", which dynamic verification observed in the application's output"
    return ""


def _purpose(f: dict[str, Any]) -> str:
    p = f.get("purpose") or {}
    label = PURPOSE_LABELS.get(str(p.get("answer")))
    return label or ""


def _destination_phrase(f: dict[str, Any]) -> str:
    """'is sent to Mixpanel, Inc. (an analytics service)', 'is written to application logs'."""
    dest, cls = _dest(f), f.get("destination_class")
    if not f.get("third_party"):
        verb = (
            "is written to"
            if cls == "log"
            else "is stored in"
            if cls == "first_party_store"
            else "reaches"
        )
        return f"{verb} {dest}"
    label = "an error-reporting or logging service" if cls == "log" else _dest_class(f)
    return f"is sent to {dest}" + (f", {label}" if label else "")


def _nature(f: dict[str, Any]) -> str | None:
    cat, fid = f["category"], [f["id"]]
    if cat in (
        "personal_data_to_third_party",
        "personal_data_processing",
        "logging_of_personal_data",
    ):
        return _sentence(
            f"{_cats(f)}{_fields(f)}{_entries(f)} {_destination_phrase(f)}{_verified(f)}", fid
        )
    if cat == "unresolved_flow":
        return _sentence(
            f"{_cats(f)}{_fields(f)}{_entries(f)} may reach {_dest(f)}, but static analysis could "
            f"not resolve every step of the path{_verified(f)}",
            fid,
        )
    if cat == "observed_unexpected_destination":
        canaries = (f.get("evidence") or {}).get("canaries") or []
        carrying = f", carrying synthetic {_join(canaries)} values" if canaries else ""
        return _sentence(
            f"During dynamic verification the application sent requests to {_dest(f)}, a "
            f"destination static analysis did not find{carrying}",
            fid,
        )
    if cat == "unreachable_flow":
        return _sentence(
            f"Code that would send {_cats(f, False)} to {_dest(f)} exists but no entry point "
            f"reaches it",
            fid,
        )
    return None


def _scope(findings: list[dict[str, Any]]) -> list[str]:
    by_cat: dict[str, list[str]] = defaultdict(list)
    fields: dict[str, set[str]] = defaultdict(set)
    for f in findings:
        if not f["reachable"]:
            continue
        for c in f["data_categories"]:
            by_cat[c].append(f["id"])
            fields[c].update((f.get("fields_by_category") or {}).get(c, []))
    out = []
    for c in sorted(by_cat, key=lambda c: (-len(by_cat[c]), c)):
        label = CATEGORY_LABELS.get(c, c)
        names = sorted(x.replace("_", " ") for x in fields[c])[:6]
        suffix = f", including {_join(names)}" if names else ""
        count = len(set(by_cat[c]))
        out.append(
            _sentence(
                f"{label[:1].upper() + label[1:]} data is involved in {count} "
                f"finding{'s' if count != 1 else ''}{suffix}",
                by_cat[c],
            )
        )
    special = [f for f in findings if f.get("special_category")]
    if special:
        cats = sorted({CATEGORY_LABELS.get(c, c) for f in special for c in f["data_categories"]})
        out.append(
            _sentence(
                f"This includes special category data under GDPR Art. 9 ({_join(cats)})",
                [f["id"] for f in special],
            )
        )
    minors = [f for f in findings if f.get("relates_to_minor")]
    if minors:
        out.append(
            _sentence("Some of the data appears to relate to children", [f["id"] for f in minors])
        )
    return out


def _context(findings: list[dict[str, Any]]) -> list[str]:
    by_entry: dict[str, list[str]] = defaultdict(list)
    for f in findings:
        for e in f.get("entry_points") or []:
            by_entry[e].append(f["id"])
    out = [
        _sentence(f"Personal data enters the application at {e}", ids)
        for e, ids in sorted(by_entry.items())
    ]
    vendors: dict[str, list[str]] = defaultdict(list)
    regions: dict[str, set[str]] = defaultdict(set)
    for f in findings:
        if f.get("vendor_regions"):
            vendors[_dest(f)].append(f["id"])
            regions[_dest(f)].update(f["vendor_regions"])
    for vendor, ids in sorted(vendors.items()):
        out.append(
            _sentence(
                f"{vendor} lists these processing locations: {'; '.join(sorted(regions[vendor]))}",
                ids,
            )
        )
    return out


def _purposes(findings: list[dict[str, Any]]) -> list[str]:
    out = []
    seen: set[str] = set()
    for f in findings:
        p = f.get("purpose")
        dest = _dest(f)
        if not p or f["category"] in ("unreachable_flow",) or dest in seen:
            continue
        seen.add(dest)
        label = PURPOSE_LABELS.get(str(p["answer"]))
        if label:
            out.append(
                _sentence(
                    f"The processing involving {dest} appears to serve {label} (provider answer, "
                    f"probability {float(p['probability']):.2f})",
                    [f["id"]],
                )
            )
        else:
            out.append(
                _sentence(
                    f"The purpose of the processing involving {dest} could not be determined from "
                    f"the code, so the controller must state it",
                    [f["id"]],
                )
            )
    return out


def _necessity(f: dict[str, Any]) -> str | None:
    cat, fid = f["category"], [f["id"]]
    if cat == "indefinite_retention":
        store = (f.get("retention") or {}).get("store") or _dest(f)
        return _sentence(
            f"{_cats(f)} stored in {store} has no evident retention limit in the code, which "
            f"bears on storage limitation (GDPR Art. 5(1)(e)) and on the retention notice "
            f"required by Cal. Civ. Code § 1798.100(a)(3)",
            fid,
        )
    if cat == "logging_of_personal_data":
        return _sentence(
            f"{_cats(f)}{_fields(f)} is written to {_dest(f)}, and the controller should confirm "
            f"that this is necessary (GDPR Art. 5(1)(c))",
            fid,
        )
    if cat == "reversible_obfuscation":
        return _sentence(
            f"{_cats(f)} is protected only by a reversible encoding, which is neither encryption "
            f"nor pseudonymisation within GDPR Art. 32(1)(a)",
            fid,
        )
    if cat == "mitigated_partially":
        hooks = [h for h in (f.get("evidence") or {}).get("vendor_hooks", []) if h]
        via = f" ({_join(hooks)})" if hooks else ""
        return _sentence(
            f"The scrubbing configured for {_dest(f)}{via} covers some paths but not all, so "
            f"{_cats(f, False)} still reaches it",
            fid,
        )
    if cat == "cross_border_ambiguity":
        values = (f.get("evidence") or {}).get("conflicting_values") or []
        conflict = f" ({_join(values)})" if values else ""
        return _sentence(
            f"The storage region for {_dest(f)} is configured inconsistently{conflict}, so "
            f"whether the data leaves its jurisdiction cannot be determined from the code "
            f"(GDPR Arts. 44-46)",
            fid,
        )
    if cat == "personal_data_to_third_party":
        purpose = _purpose(f)
        for_purpose = f" for {purpose}" if purpose else ""
        return _sentence(
            f"{_cats(f)}{_fields(f)} is disclosed to {_dest(f)}, and the controller should "
            f"confirm that each field is needed{for_purpose} (GDPR Art. 5(1)(c))",
            fid,
        )
    if cat == "special_category_processing":
        return _sentence(
            f"Processing {_cats(f, False)} needs a condition under GDPR Art. 9(2) in addition to "
            f"a lawful basis under Art. 6",
            fid,
        )
    if cat == "unreachable_flow":
        return _sentence(
            f"Unused code that would disclose {_cats(f, False)} to {_dest(f)} remains in the "
            f"repository and could be re-enabled by a later change",
            fid,
        )
    if cat == "observed_unexpected_destination":
        return _sentence(
            f"Requests to {_dest(f)} are not accounted for in the code's known destinations, so "
            f"the recipient list may be incomplete (GDPR Art. 30(1)(d))",
            fid,
        )
    return None


LIKELIHOOD_WORDS = {"remote": "remote", "possible": "possible", "probable": "probable"}


def _risk(f: dict[str, Any]) -> str:
    return _sentence(
        f"{f['title']}: {_cats(f, False)}{' to ' + _dest(f) if f.get('destination') else ''} "
        f"(severity {f['severity']}, likelihood {f.get('likelihood', 'possible')})",
        [f["id"]],
    )


def _risks(findings: list[dict[str, Any]]) -> list[str]:
    high = [f for f in findings if f["severity"] in ("critical", "high")]
    rest = [f for f in findings if f["severity"] not in ("critical", "high")]
    out = [_risk(f) for f in high]
    if rest:
        out.append(
            _sentence(
                f"{len(rest)} further finding{'s are' if len(rest) != 1 else ' is'} rated medium "
                "or "
                f"lower and listed in the risk register",
                [f["id"] for f in rest],
            )
        )
    return out


MEASURES: dict[str, Callable[[dict[str, Any]], str]] = {
    "personal_data_to_third_party": lambda f: (
        f"confirm that a processor agreement (GDPR Art. 28) or service-provider contract "
        f"(Cal. Civ. Code § 1798.140(ag)) covers {_dest(f)}, send only the fields it needs, and "
        f"name it in the privacy notice"
    ),
    "special_category_processing": lambda f: (
        "record the Art. 9(2) condition, restrict access to the data, and confirm each field is "
        "necessary"
    ),
    "reversible_obfuscation": lambda f: (
        "replace the reversible encoding with encryption under managed keys, or with a keyed "
        "hash or token if the value is only compared"
    ),
    "indefinite_retention": lambda f: (
        "set a retention period, enforce it with an expiry field or a deletion job, and state it "
        "in the privacy notice"
    ),
    "logging_of_personal_data": lambda f: (
        f"remove or mask personal data before it reaches {_dest(f)} and limit how long those "
        f"logs are kept"
    ),
    "cross_border_ambiguity": lambda f: (
        "set the storage region in one place and, if it is outside the data subjects' "
        "jurisdiction, document the transfer mechanism (GDPR Art. 46)"
    ),
    "sale_or_share_candidate": lambda f: (
        f"determine whether {_dest(f)} is a service provider or contractor under a written "
        f"contract; if not, honor opt-outs of sale and sharing (Cal. Civ. Code §§ 1798.120, "
        f"1798.135)"
    ),
    "unresolved_flow": lambda f: (
        "have a reviewer resolve the flow as described in the Unresolved section"
    ),
    "unreachable_flow": lambda f: "delete the unused code or document why it is kept",
    "mitigated_partially": lambda f: (
        f"extend the scrubbing configured for {_dest(f)} to every path that reaches it, "
        f"including exception messages, request bodies, and stack-frame variables"
    ),
    "personal_data_processing": lambda f: (
        "record the processing and its lawful basis in the Art. 30 register"
    ),
    "observed_unexpected_destination": lambda f: (
        f"find the code that contacts {_dest(f)} and either add it to the vendor inventory or "
        f"remove it"
    ),
}


def measure_for(f: dict[str, Any]) -> str:
    measure = MEASURES.get(f["category"])
    return measure(f) if measure is not None else "review the finding"


def _measures(findings: list[dict[str, Any]]) -> list[str]:
    grouped: dict[tuple[str, str], list[str]] = {}
    for f in findings:
        if f["severity"] != "informational" or f["category"] == "unreachable_flow":
            grouped.setdefault((f["title"], measure_for(f)), []).append(f["id"])
    return [_sentence(f"{title}: {measure}", ids) for (title, measure), ids in grouped.items()]


def _sale_share(findings: list[dict[str, Any]]) -> list[str]:
    candidates: dict[str, list[str]] = defaultdict(list)
    cats: dict[str, set[str]] = defaultdict(set)
    service: dict[str, list[str]] = defaultdict(list)
    pending: dict[str, list[str]] = defaultdict(list)
    for f in findings:
        if not f.get("third_party") or not f["reachable"]:
            continue
        dest = _dest(f)
        answer = (f.get("sale_or_share") or {}).get("answer")
        if f["category"] == "sale_or_share_candidate":
            candidates[dest].append(f["id"])
            cats[dest].update(f["data_categories"])
        elif answer == "no":
            service[dest].append(f["id"])
        elif answer == "yes":
            pending[dest].append(f["id"])
    out = []
    for dest, ids in sorted(candidates.items()):
        labels = _join([CATEGORY_LABELS.get(c, c) for c in sorted(cats[dest])]) or "personal"
        out.append(
            _sentence(
                f"Disclosing {labels} data to {dest} could be a sale or a sharing under Cal. Civ. "
                f"Code § 1798.140(ad) or (ah) unless {dest} is a service provider or contractor "
                "bound "
                f"by a written contract",
                ids,
            )
        )
    for dest, ids in sorted(pending.items()):
        if dest not in candidates:
            out.append(
                _sentence(
                    f"The decision provider indicated that disclosures to {dest} could be a sale "
                    "or "
                    f"sharing, but the flow itself is unresolved, so no determination is made",
                    ids,
                )
            )
    for dest, ids in sorted(service.items()):
        if dest not in candidates:
            out.append(
                _sentence(
                    f"Disclosures to {dest} were classified as disclosures to a service provider "
                    "for "
                    f"a business purpose, which holds only if the contract meets 11 CCR § 7051",
                    ids,
                )
            )
    return out


def _sensitive(findings: list[dict[str, Any]]) -> list[str]:
    by_cat: dict[str, list[str]] = defaultdict(list)
    for f in findings:
        if f["reachable"]:
            for c in f["data_categories"]:
                if c in SENSITIVE_PI:
                    by_cat[c].append(f["id"])
    return [
        _sentence(
            f"The code processes {SENSITIVE_PI[c]}, a category of sensitive personal information "
            f"under Cal. Civ. Code § 1798.140(ae), so consumers may limit its use under § 1798.121 "
            f"unless the use stays within the purposes the CPPA regulations permit (11 CCR § 7027)",
            ids,
        )
        for c, ids in sorted(by_cat.items())
    ]


TEMPLATES: dict[str, Callable[[list[dict[str, Any]]], list[str]]] = {
    "description.nature": lambda fs: [s for f in fs if (s := _nature(f))],
    "description.scope": _scope,
    "description.context": _context,
    "description.purposes": _purposes,
    "necessity": lambda fs: [s for f in fs if (s := _necessity(f))],
    "risks": _risks,
    "measures": _measures,
    "cpra.sale_share": _sale_share,
    "cpra.sensitive": _sensitive,
}


class TemplateDrafter:
    name = "template"

    def draft(self, section: SectionBrief) -> list[str]:
        template = TEMPLATES.get(section.section_id)
        return template(section.findings) if template else []


# --------------------------------------------------------------------------- LLM drafter


class LLMClient(Protocol):
    def complete(self, system: str, user: str) -> str: ...


SYSTEM_PROMPT = """\
You draft one section of a data protection impact assessment (DPIA) for a software
application. You receive the section's purpose and a list of findings as JSON. Each finding
was produced by static and dynamic analysis of the code; you do not see the code.

Rules:
- Every sentence must cite the findings it is based on, as [F-0042], using only ids from the
  input. A sentence may cite several findings: [F-0001] [F-0004].
- State only what the cited findings support. Do not invent vendors, purposes, legal bases,
  retention periods, or data subjects. Say "could not be determined" when a finding says so.
- Keep legal references to those present in the findings' "statutes" lists.
- Plain, factual prose. No headings, bullets, or recommendations unless the section asks.
- Return JSON only: {"sentences": ["...", "..."]}
"""


class AnthropicClient:
    """Messages API client for the narrative step. Reads ANTHROPIC_API_KEY."""

    def __init__(
        self,
        api_key: str,
        model: str = "claude-sonnet-5",
        base_url: str = "https://api.anthropic.com",
        timeout: float = 60.0,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self.model = model
        self.http = httpx.Client(
            base_url=base_url,
            timeout=timeout,
            transport=transport,
            headers={"x-api-key": api_key, "anthropic-version": "2023-06-01"},
        )

    @classmethod
    def from_env(cls) -> AnthropicClient | None:
        key = os.environ.get("ANTHROPIC_API_KEY")
        if not key:
            return None
        return cls(
            key,
            model=os.environ.get("LANTERN_NARRATIVE_MODEL", "claude-sonnet-5"),
            base_url=os.environ.get("ANTHROPIC_BASE_URL", "https://api.anthropic.com"),
        )

    def complete(self, system: str, user: str) -> str:
        response = self.http.post(
            "/v1/messages",
            json={
                "model": self.model,
                "max_tokens": 4096,
                "system": system,
                "messages": [{"role": "user", "content": user}],
            },
        )
        response.raise_for_status()
        blocks = response.json().get("content", [])
        return "".join(b.get("text", "") for b in blocks if b.get("type") == "text")


def parse_sentences(text: str) -> list[str]:
    """Sentences from the model's JSON reply; tolerates a fenced code block around it."""
    body = text.strip()
    fence = re.match(r"^```(?:json)?\s*(.*?)\s*```$", body, re.S)
    if fence:
        body = fence.group(1)
    data = json.loads(body)
    sentences = data.get("sentences") if isinstance(data, dict) else None
    if not isinstance(sentences, list) or not all(isinstance(s, str) for s in sentences):
        raise ValueError('reply is not {"sentences": [string, ...]}')
    return [s.strip() for s in sentences if s.strip()]


class LLMDrafter:
    name = "llm"

    def __init__(self, client: LLMClient, max_attempts: int = 2) -> None:
        self.client = client
        self.max_attempts = max_attempts

    def attempt(
        self, section: SectionBrief, feedback: list[Rejection]
    ) -> tuple[list[str], list[Rejection]]:
        user = json.dumps(section.payload(), indent=1, sort_keys=True)
        if feedback:
            lines = "\n".join(f"- {r.reason}: {r.sentence[:200]}" for r in feedback[:10])
            user += (
                "\n\nYour previous draft was rejected. Every sentence needs a citation of a "
                f"finding id from the input. Rejected sentences:\n{lines}"
            )
        try:
            sentences = parse_sentences(self.client.complete(SYSTEM_PROMPT, user))
        except (ValueError, httpx.HTTPError) as exc:
            return [], [Rejection("", f"unusable reply: {type(exc).__name__}")]
        if not sentences and section.findings:
            return [], [Rejection("", "empty draft")]
        return sentences, validate_sentences(sentences, section.finding_ids)


def draft_section(
    section: SectionBrief,
    template: TemplateDrafter,
    llm: LLMDrafter | None = None,
) -> Narrative:
    if llm is None or not section.findings:
        return Narrative(section.section_id, template.draft(section), "template")
    rejections: list[Rejection] = []
    feedback: list[Rejection] = []
    for attempt in range(1, llm.max_attempts + 1):
        sentences, problems = llm.attempt(section, feedback)
        if not problems:
            return Narrative(section.section_id, sentences, "llm", attempt, rejections)
        rejections.extend(problems)
        feedback = problems
    return Narrative(
        section.section_id,
        template.draft(section),
        "template",
        llm.max_attempts,
        rejections,
        note=f"LLM draft rejected {llm.max_attempts} times; template fallback used",
    )
