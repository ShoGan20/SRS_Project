"""Deterministic validation of the requirement registry (guardrails G1-G4, G7, G9).

Runs in code, before the BRD/SRS are generated. Problems are never ignored: each
one becomes an `Issue`, and where a guardrail would otherwise be breached the item
is made safe (e.g. a CONFIRMED requirement without evidence is downgraded).
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass

from pydantic import BaseModel

from . import redact
from .models import ExtractedFacts, Fact, Requirement

ID_FORMATS = {
    "business": re.compile(r"^BR-\d{3,}$"),
    "functional": re.compile(r"^FR-\d{3,}$"),
    "non_functional": re.compile(r"^NFR-\d{3,}$"),
}
CONFLICT_ID = re.compile(r"^CONFLICT-\d{3,}$")
OPEN_ID = re.compile(r"^OPEN-\d{3,}$")

VAGUE_TERMS = re.compile(
    r"\b(fast|quick(?:ly)?|slow|responsive|user[- ]friendly|intuitive|easy|simple|robust|"
    r"scalable|flexible|seamless(?:ly)?|efficient(?:ly)?|modern|secure(?:ly)?|reliable|"
    r"high[- ]performance|as soon as possible|asap|etc\.?|and/or|appropriate(?:ly)?|"
    r"adequate(?:ly)?|minimal|optimal|state[- ]of[- ]the[- ]art)\b",
    re.I,
)
UNRESOLVED = re.compile(r"needs|pending|tbd|unresolved|open|awaiting|to be (?:agreed|confirmed|decided)", re.I)


@dataclass
class Issue:
    severity: str        # "error" (guardrail breach, fixed or blocking), "warning", "info"
    code: str
    item_id: str
    message: str

    def __str__(self) -> str:
        return f"[{self.severity.upper()}] {self.item_id}: {self.message}"

    def to_dict(self) -> dict:
        return asdict(self)


def _norm(text: str) -> str:
    return " ".join(re.sub(r"[^a-z0-9 ]+", " ", text.lower()).split())


def validate(facts: ExtractedFacts, source_ids: set[str]) -> tuple[ExtractedFacts, list[Issue]]:
    """Return a corrected copy of `facts` and every issue found."""
    facts = facts.model_copy(deep=True)
    issues: list[Issue] = []

    def add(severity, code, item_id, message):
        issues.append(Issue(severity, code, item_id or "(no id)", message))

    reqs = facts.requirements
    req_ids = {r.id for r in reqs if r.id}
    br_ids = {r.id for r in reqs if r.type == "business" and r.id}
    open_ids = {q.id for q in facts.open_questions if q.id}
    conflict_ids = {c.id for c in facts.conflicts if c.id}

    # ---- ids: present, well-formed, unique
    all_ids = ([r.id for r in reqs] + [c.id for c in facts.conflicts] + [q.id for q in facts.open_questions])
    seen: set[str] = set()
    for item_id in all_ids:
        if item_id and item_id in seen:
            add("error", "duplicate_id", item_id, "id is used more than once")
        seen.add(item_id)
    for r in reqs:
        if not r.id.strip():
            add("error", "missing_id", r.title, "requirement has no id")
        elif not ID_FORMATS[r.type].match(r.id):
            add("warning", "bad_id_format", r.id, f"id does not match the {r.type} id scheme")
    for c in facts.conflicts:
        if not CONFLICT_ID.match(c.id):
            add("warning", "bad_id_format", c.id, "conflict id should look like CONFLICT-001")
    for q in facts.open_questions:
        if not OPEN_ID.match(q.id):
            add("warning", "bad_id_format", q.id, "open question id should look like OPEN-001")

    # ---- requirement content, evidence, status
    for r in reqs:
        if not r.requirement.strip():
            add("error", "empty_requirement", r.id, "requirement text is empty; marked NEEDS_CLARIFICATION")
            r.requirement = "TBD"
            r.status = "NEEDS_CLARIFICATION"

        unknown = [s for s in r.source_ids if s not in source_ids]
        for sid in unknown:
            add("error", "unknown_source", r.id, f"cites unknown source {sid}")
        valid_sources = [s for s in r.source_ids if s in source_ids]

        if r.status == "CONFIRMED" and not valid_sources:
            add("error", "confirmed_without_source", r.id,
                "CONFIRMED but has no valid supporting source; downgraded to NEEDS_CLARIFICATION")
            r.status = "NEEDS_CLARIFICATION"
        elif r.status == "CONFIRMED" and not r.source_reference.strip():
            add("warning", "missing_source_reference", r.id, "CONFIRMED but no source reference/quote given")
        if r.status == "CONFIRMED" and r.confidence == "LOW":
            add("error", "low_confidence_confirmed", r.id,
                "LOW confidence cannot be CONFIRMED; downgraded to ASSUMPTION")
            r.status = "ASSUMPTION"
        if not r.source_ids:
            add("info", "no_source", r.id, "no source cited")

        if r.status == "CONFIRMED":
            vague = sorted({m.group(0).lower() for m in VAGUE_TERMS.finditer(r.requirement)})
            if vague:
                add("warning", "vague_requirement", r.id,
                    f"not measurable as written ({', '.join(vague)}); needs clarification")

        # type separation
        if r.type == "non_functional" and not r.category.strip():
            add("warning", "missing_category", r.id, "non-functional requirement has no category")
        if r.type != "non_functional" and r.category.strip():
            add("warning", "category_on_non_nfr", r.id, f"category '{r.category}' only applies to NFRs; cleared")
            r.category = ""
        if r.type == "business" and r.br_refs:
            add("warning", "br_refs_on_br", r.id, "business requirements do not implement other BRs; cleared")
            r.br_refs = []

        # references
        for ref in r.br_refs:
            if ref not in br_ids:
                add("warning", "unknown_br_ref", r.id, f"references unknown {ref}")
        for ref in r.dependencies:
            if ref not in req_ids:
                add("warning", "unknown_dependency", r.id, f"depends on unknown {ref}")
        for ref in r.open_question_ids:
            if ref not in open_ids:
                add("warning", "unknown_open_question", r.id, f"references unknown {ref}")

    # ---- BRD -> SRS traceability (review notes, never forced)
    covered = {ref for r in reqs if r.type == "functional" for ref in r.br_refs}
    for r in reqs:
        if r.type == "functional" and not r.br_refs:
            add("info", "fr_not_linked", r.id, "not linked to any business requirement")
    for br in sorted(br_ids - covered):
        add("info", "br_not_implemented", br, "not implemented by any functional requirement")

    # ---- duplicates by text
    by_text: dict[str, str] = {}
    for r in reqs:
        key = _norm(r.requirement)
        if key and key != "tbd":
            if key in by_text:
                add("warning", "duplicate_requirement", r.id, f"same text as {by_text[key]}; merge into one")
            else:
                by_text[key] = r.id

    # ---- conflicts must stay visible and unresolved unless a source resolves them
    in_conflict: set[str] = set()
    for c in facts.conflicts:
        if len(c.options) < 2:
            add("error", "conflict_single_option", c.id, "a conflict needs at least two positions")
        for opt in c.options:
            if not opt.source_ids:
                add("warning", "conflict_option_unsourced", c.id, f"option '{opt.statement[:60]}' has no source")
            for sid in opt.source_ids:
                if sid not in source_ids:
                    add("error", "unknown_source", c.id, f"cites unknown source {sid}")
        if not c.resolution.strip():
            c.resolution = "Needs stakeholder confirmation."
        unresolved = bool(UNRESOLVED.search(c.resolution))
        for ref in c.related_requirement_ids:
            if ref not in req_ids:
                add("warning", "unknown_requirement_ref", c.id, f"references unknown {ref}")
            elif unresolved:
                in_conflict.add(ref)
    for r in reqs:
        if r.id in in_conflict and r.status != "CONFLICT":
            add("error", "conflict_silently_resolved", r.id,
                f"has an unresolved conflict but was marked {r.status}; set to CONFLICT")
            r.status = "CONFLICT"
        elif r.status == "CONFLICT" and r.id not in {x for c in facts.conflicts for x in c.related_requirement_ids}:
            add("warning", "conflict_without_record", r.id, "status is CONFLICT but no CONFLICT-### record lists it")

    # ---- open questions
    for q in facts.open_questions:
        for ref in q.related_requirement_ids:
            if ref not in req_ids and ref not in conflict_ids:
                add("warning", "unknown_requirement_ref", q.id, f"references unknown {ref}")
        for sid in q.source_ids:
            if sid not in source_ids:
                add("warning", "unknown_source", q.id, f"cites unknown source {sid}")

    # ---- facts: CONFIRMED needs a source too
    for name in ("business_objectives", "user_roles", "scope_in", "scope_out", "current_process",
                 "proposed_process", "business_rules", "constraints", "dependencies", "assumptions",
                 "decisions"):
        for fact in getattr(facts, name):
            _check_fact(fact, name, source_ids, add)

    # ---- secrets (G7)
    facts, secret_issues = scrub(facts, "extracted requirements")
    issues.extend(secret_issues)
    return facts, issues


def _check_fact(fact: Fact, section: str, source_ids: set[str], add) -> None:
    valid = [s for s in fact.source_ids if s in source_ids]
    if fact.status == "CONFIRMED" and not valid:
        add("error", "confirmed_without_source", section,
            f"'{fact.statement[:60]}' is CONFIRMED without a valid source; downgraded to NEEDS_CLARIFICATION")
        fact.status = "NEEDS_CLARIFICATION"
    if section == "assumptions" and fact.status == "CONFIRMED":
        fact.status = "ASSUMPTION"


def scrub(model: BaseModel, label: str) -> tuple[BaseModel, list[Issue]]:
    """Redact any secret left in any string of `model` (generated output included)."""
    issues: list[Issue] = []

    def walk(node, path):
        if isinstance(node, str):
            found = redact.scan(node)
            if found:
                issues.append(Issue("error", "secret_in_output", label,
                                    f"{', '.join(found)} found at {path}; redacted"))
                return redact.redact(node)[0]
            return node
        if isinstance(node, list):
            return [walk(v, f"{path}[{i}]") for i, v in enumerate(node)]
        if isinstance(node, dict):
            return {k: walk(v, f"{path}.{k}") for k, v in node.items()}
        return node

    cleaned = walk(model.model_dump(), label)
    return (type(model).model_validate(cleaned) if issues else model), issues


def by_type(requirements: list[Requirement], kind: str) -> list[Requirement]:
    return [r for r in requirements if r.type == kind]
