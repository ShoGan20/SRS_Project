"""Audit trail, versioning and change log (guardrail G10, spec sections 25-27, 31).

The audit log stores ids, hashes and metadata only, never source bodies.
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path

from .models import ExtractedFacts, SourceDoc

REGISTRY_FILE = "requirements.json"
UNKNOWN_STATUSES = {"TBD", "NEEDS_CLARIFICATION", "ASSUMPTION", "CONFLICT"}


# ---------------------------------------------------------------- summary

def summary(facts: ExtractedFacts, source_count: int) -> dict[str, int]:
    """The generation summary a reviewer sees first (spec section 31)."""
    statuses = [r.status for r in facts.requirements]
    return {
        "Sources processed": source_count,
        "Confirmed requirements": statuses.count("CONFIRMED"),
        "Assumptions": statuses.count("ASSUMPTION") + len(facts.assumptions),
        "TBD items": statuses.count("TBD"),
        "Needs clarification": statuses.count("NEEDS_CLARIFICATION"),
        "Conflicts": len(facts.conflicts),
        "Open questions": len(facts.open_questions),
    }


# ---------------------------------------------------------------- versioning

def load_previous(path: Path | None) -> dict | None:
    """The registry saved by an earlier run, or None."""
    if not path or not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def next_version(previous: dict | None) -> str:
    """v0.1 on the first run, then +0.1. Never reaches 1.0 on its own: that needs human approval."""
    if not previous:
        return "v0.1"
    match = re.match(r"v?(\d+)\.(\d+)", str(previous.get("version", "")))
    if not match:
        return "v0.1"
    major, minor = int(match.group(1)), int(match.group(2))
    return f"v{major}.{minor + 1}"


def change_log(previous: dict | None, facts: ExtractedFacts) -> dict[str, list[str]]:
    """Deterministic diff by id between the previous registry and this one."""
    log = {"added": [], "modified": [], "removed": [], "newly_answered": [],
           "new_conflicts": [], "resolved_conflicts": []}
    if not previous:
        log["added"] = [r.id for r in facts.requirements]
        log["new_conflicts"] = [c.id for c in facts.conflicts]
        return log

    old = {r["id"]: r for r in previous.get("requirements", [])}
    new = {r.id: r for r in facts.requirements}
    for rid, req in new.items():
        before = old.get(rid)
        if before is None:
            log["added"].append(rid)
            continue
        changes = []
        if before.get("requirement") != req.requirement:
            changes.append("text changed")
        if before.get("status") != req.status:
            changes.append(f"status {before.get('status')} -> {req.status}")
            if before.get("status") in UNKNOWN_STATUSES and req.status == "CONFIRMED":
                log["newly_answered"].append(rid)
        if changes:
            log["modified"].append(f"{rid}: {'; '.join(changes)}")
    log["removed"] = sorted(set(old) - set(new))

    old_conflicts = {c["id"] for c in previous.get("conflicts", [])}
    new_conflicts = {c.id for c in facts.conflicts}
    log["new_conflicts"] = sorted(new_conflicts - old_conflicts)
    log["resolved_conflicts"] = sorted(old_conflicts - new_conflicts)
    return log


def registry_payload(facts: ExtractedFacts, version: str) -> dict:
    """What gets saved to requirements.json (and read back by the next run)."""
    return {"version": version, "status": "Draft for Review", **facts.model_dump()}


# ---------------------------------------------------------------- audit log

def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def audit_record(
    *,
    project: str,
    version: str,
    models_used: list[str],
    loaded: list[SourceDoc],
    used: list[SourceDoc],
    email_report: list[dict],
    facts: ExtractedFacts,
    issues: list,
    changes: dict,
) -> dict:
    used_ids = {d.id for d in used}
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "project": project,
        "document_version": version,
        "document_status": "Draft for Review",
        "models_used": sorted(set(models_used)),
        "sources": [
            {
                "id": d.id, "kind": d.kind, "file": Path(d.path).name, "date": d.date,
                "sha256": _sha(d.text), "chars": len(d.text), "used": d.id in used_ids,
                "redactions": d.redactions, "injection_flags": len(d.injection_flags),
            }
            for d in loaded
        ],
        "email_filter": [{"id": r["id"], "relevant": r["relevant"], "reason": r["reason"]} for r in email_report],
        "requirements": [
            {"id": r.id, "type": r.type, "status": r.status, "confidence": r.confidence,
             "source_ids": r.source_ids}
            for r in facts.requirements
        ],
        "conflicts": [
            {"id": c.id, "topic": c.topic, "related": c.related_requirement_ids,
             "source_ids": sorted({s for o in c.options for s in o.source_ids})}
            for c in facts.conflicts
        ],
        "assumptions": [r.id for r in facts.requirements if r.status == "ASSUMPTION"]
                       + [f"fact: {a.statement[:80]}" for a in facts.assumptions],
        "open_questions": [q.id for q in facts.open_questions],
        "validation_issues": [i.to_dict() for i in issues],
        "change_log": changes,
        "summary": summary(facts, len(used)),
    }
