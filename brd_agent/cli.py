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

from . import pipeline
from .loaders import load_folder
from .render_docx import render_brd, render_srs

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
    parser.add_argument("-v", "--verbose", action="store_true", help="Debug logging")
    return parser.parse_args(argv)


def run(args: argparse.Namespace) -> int:
    aliases = [a.strip() for a in args.aliases.split(",") if a.strip()]
    for folder in [args.notes_dir, args.emails_dir]:
        if folder is not None and not folder.is_dir():
            log.error("Folder not found: %s", folder)
            return 2

    # 1. Load
    counters = {"note": 0, "email": 0}
    docs = load_folder(args.notes_dir, counters)
    if args.emails_dir:
        docs += load_folder(args.emails_dir, counters)
    if args.since:
        docs = [d for d in docs if not d.date or d.date[:10] >= args.since]
    notes = [d for d in docs if d.kind == "note"]
    emails = [d for d in docs if d.kind == "email"]
    log.info("Loaded %d notes and %d emails", len(notes), len(emails))
    if not docs:
        log.error("No readable sources found.")
        return 2

    client = anthropic.Anthropic()
    args.out.mkdir(parents=True, exist_ok=True)
    stem = re.sub(r"[^\w\-]+", "_", args.project).strip("_") or "Project"

    # 2. Filter emails
    if emails and not args.skip_email_filter:
        emails, report = pipeline.filter_emails(client, args.project, aliases, emails, notes)
        (args.out / "email_filter_report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
        log.info("Kept %d of %d emails as project-related", len(emails), len(report))
    sources = notes + emails

    # 3. Extract
    facts, sources_block = pipeline.extract_facts(client, args.project, sources)
    (args.out / "extracted_facts.json").write_text(facts.model_dump_json(indent=2), encoding="utf-8")

    # 4-5. Write documents
    brd = pipeline.write_brd(client, args.project, facts, sources_block)
    (args.out / "brd.json").write_text(brd.model_dump_json(indent=2), encoding="utf-8")
    srs = pipeline.write_srs(client, args.project, facts, brd, sources_block)
    (args.out / "srs.json").write_text(srs.model_dump_json(indent=2), encoding="utf-8")

    warnings = pipeline.consistency_warnings(brd, srs, {d.id for d in sources})
    for warning in warnings:
        log.warning("Check: %s", warning)

    # 6. Render
    brd_path = args.out / f"{stem}_BRD.docx"
    srs_path = args.out / f"{stem}_SRS.docx"
    render_brd(brd, args.project, sources, brd_path)
    render_srs(srs, brd, args.project, sources, warnings, srs_path)
    print(f"\nBRD: {brd_path.resolve()}\nSRS: {srs_path.resolve()}")
    print(f"{len(brd.business_requirements)} business requirements, "
          f"{len(srs.functional_requirements)} functional, "
          f"{len(srs.non_functional_requirements)} non-functional, "
          f"{len(srs.open_questions) + len(brd.open_questions)} open questions, "
          f"{len(warnings)} consistency notes.")
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
