"""Command-line entry point.

    python -m brd_agent.cli --project "Phoenix" --notes-dir ./meetings --emails-dir ./emails
"""

from __future__ import annotations

import argparse
import json
import logging
import re
import sys
from pathlib import Path

import anthropic

from . import audit, pipeline, redact
from .loaders import load_folder
from .models import SourceDoc
from .render_docx import RunInfo, render_brd, render_srs
from .validate import scrub, validate

log = logging.getLogger("brd_agent")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="brd_agent",
        description="Collect meeting notes and project emails, then write a BRD and an SRS (.docx).",
    )
    parser.add_argument("--project", required=True, help="Project name, used to find related emails")
    parser.add_argument("--notes-dir", type=Path, required=True, help="Folder with meeting notes (.txt .md .docx .pdf .vtt)")
    parser.add_argument("--emails-dir", type=Path, help="Folder with exported emails (.eml .msg)")
    parser.add_argument("--aliases", default="", help="Comma-separated other names/codes for the project")
    parser.add_argument("--out", type=Path, default=Path("output"), help="Output folder (default: ./output)")
    parser.add_argument("--since", help="Ignore sources dated before this day (YYYY-MM-DD)")
    parser.add_argument("--skip-email-filter", action="store_true",
                        help="Treat every email in --emails-dir as belonging to the project")
    parser.add_argument("--previous", type=Path,
                        help="requirements.json from an earlier run, to keep ids stable and build a "
                             "change log (default: <out>/requirements.json if it exists)")
    parser.add_argument("-v", "--verbose", action="store_true", help="Debug logging")
    return parser.parse_args(argv)


def _protect(docs: list[SourceDoc]) -> None:
    """Redact secrets and flag injection attempts before any source reaches a model or a log."""
    for doc in docs:
        doc.title, n_title = redact.redact(doc.title)
        doc.text, n_text = redact.redact(doc.text)
        doc.redactions = n_title + n_text
        doc.injection_flags = redact.injection_flags(f"{doc.title}\n{doc.text}")
        if doc.redactions:
            log.warning("%s: redacted %d secret value(s)", doc.id, doc.redactions)
        if doc.injection_flags:
            log.warning("%s: contains %d instruction-like passage(s); treated as data",
                        doc.id, len(doc.injection_flags))


def _write_json(path: Path, data) -> None:
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


def run(args: argparse.Namespace) -> int:
    aliases = [a.strip() for a in args.aliases.split(",") if a.strip()]
    for folder in [args.notes_dir, args.emails_dir]:
        if folder is not None and not folder.is_dir():
            log.error("Folder not found: %s", folder)
            return 2

    # 1. Load only the folders the user provided, then redact (G7) and flag injections (G5)
    counters = {"note": 0, "email": 0}
    docs = load_folder(args.notes_dir, counters)
    if args.emails_dir:
        docs += load_folder(args.emails_dir, counters)
    if args.since:
        docs = [d for d in docs if not d.date or d.date[:10] >= args.since]
    _protect(docs)
    notes = [d for d in docs if d.kind == "note"]
    emails = [d for d in docs if d.kind == "email"]
    log.info("Loaded %d notes and %d emails", len(notes), len(emails))
    if not docs:
        log.error("No readable sources found.")
        return 2

    client = anthropic.Anthropic()
    args.out.mkdir(parents=True, exist_ok=True)
    stem = re.sub(r"[^\w\-]+", "_", args.project).strip("_") or "Project"
    previous = audit.load_previous(args.previous or args.out / audit.REGISTRY_FILE)
    version = audit.next_version(previous)
    if previous:
        log.info("Updating from previous registry %s -> %s", previous.get("version"), version)

    # 2. Filter emails
    report: list[dict] = []
    if emails and not args.skip_email_filter:
        emails, report = pipeline.filter_emails(client, args.project, aliases, emails, notes)
        _write_json(args.out / "email_filter_report.json", report)
        log.info("Kept %d of %d emails as project-related", len(emails), len(report))
    sources = notes + emails
    source_ids = {d.id for d in sources}

    # 3. Extract the structured requirement registry
    facts, sources_block = pipeline.extract_facts(client, args.project, sources, previous)

    # 4. Validate in code before any document is written (G9)
    facts, issues = validate(facts, source_ids)
    for issue in issues:
        (log.warning if issue.severity != "info" else log.info)("Check: %s", issue)
    changes = audit.change_log(previous, facts)
    _write_json(args.out / audit.REGISTRY_FILE, audit.registry_payload(facts, version))
    _write_json(args.out / "validation_report.json", [i.to_dict() for i in issues])
    _write_json(args.out / "change_log.json", {"version": version, **changes})

    # 5. Narrative sections, scrubbed for secrets once more
    brd = pipeline.write_brd(client, args.project, facts, sources_block)
    brd, brd_issues = scrub(brd, "BRD")
    srs = pipeline.write_srs(client, args.project, facts, brd, sources_block)
    srs, srs_issues = scrub(srs, "SRS")
    issues += brd_issues + srs_issues
    _write_json(args.out / "brd.json", brd.model_dump())
    _write_json(args.out / "srs.json", srs.model_dump())

    # 6. Render drafts
    notes_for_review = [str(i) for i in issues if i.severity != "info"]
    notes_for_review += [f"[SECURITY] {d.id}: instruction-like text was treated as data, not "
                         f"followed: \"{flag}\"" for d in sources for flag in d.injection_flags]
    notes_for_review += [f"[SECURITY] {d.id}: {d.redactions} secret value(s) redacted before processing"
                         for d in sources if d.redactions]
    notes_for_review += [str(i) for i in issues if i.severity == "info"]
    run_info = RunInfo(version=version, review_notes=notes_for_review, change_log=changes)
    brd_path = args.out / f"{stem}_BRD_{version}.docx"
    srs_path = args.out / f"{stem}_SRS_{version}.docx"
    render_brd(brd, facts, args.project, sources, brd_path, run_info)
    render_srs(srs, facts, args.project, sources, srs_path, run_info)

    # 7. Audit trail (ids and metadata only) (G10)
    _write_json(args.out / "audit_log.json", audit.audit_record(
        project=args.project, version=version, models_used=pipeline.MODELS_USED,
        loaded=docs, used=sources, email_report=report, facts=facts, issues=issues, changes=changes,
    ))

    print(f"\nStatus: Draft for Review ({version})")
    print(f"BRD: {brd_path.resolve()}\nSRS: {srs_path.resolve()}")
    for label, count in audit.summary(facts, len(sources)).items():
        print(f"  {label}: {count}")
    errors = sum(i.severity == "error" for i in issues)
    print(f"  Validation issues: {len(issues)} ({errors} errors) - see validation_report.json")
    return 0


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(message)s",
        datefmt="%H:%M:%S",
    )
    if not args.verbose:
        for noisy in ("httpx", "anthropic", "extract_msg"):
            logging.getLogger(noisy).setLevel(logging.WARNING)
    try:
        return run(args)
    except anthropic.AuthenticationError:
        log.error("Anthropic rejected the credentials. Check ANTHROPIC_API_KEY (or run `ant auth login`).")
    except TypeError as exc:
        if "authentication" not in str(exc).lower() and "api_key" not in str(exc).lower():
            raise
        log.error("No Anthropic credentials found. Set ANTHROPIC_API_KEY or run `ant auth login`.")
    except anthropic.RateLimitError:
        log.error("Rate limited by the API. Wait a minute and run again.")
    except anthropic.APIStatusError as exc:
        log.error("API error %s: %s (request id %s)", exc.status_code, exc.message,
                  getattr(exc, "request_id", "?"))
    except anthropic.APIConnectionError:
        log.error("Could not reach the Anthropic API. Check your network connection.")
    except pipeline.GenerationError as exc:
        log.error("%s", exc)
    return 1


if __name__ == "__main__":
    sys.exit(main())
