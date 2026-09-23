"""Tests that run without calling the Claude API."""

from pathlib import Path

import pytest
from docx import Document

from brd_agent.loaders import load_folder
from brd_agent.loaders.emails import strip_quotes_and_signature
from brd_agent.models import (
    BRD, SRS, BusinessRequirement, ExtractedFacts, FunctionalRequirement, strict_schema,
)
from brd_agent.pipeline import consistency_warnings, filter_emails
from brd_agent.render_docx import render_brd, render_srs

SAMPLES = Path(__file__).parent.parent / "samples"


@pytest.fixture(scope="session", autouse=True)
def binary_samples():
    from samples.make_samples import MEETINGS, make_docx, make_pdf
    make_docx(MEETINGS / "2026-06-30_steering_group.docx")
    make_pdf(MEETINGS / "2026-07-07_ux_review.pdf", ["Phoenix - UX Review", "Support English and Welsh."])


def by_title(docs):
    return {d.title: d for d in docs}


def test_notes_all_formats():
    docs = by_title(load_folder(SAMPLES / "meetings"))
    assert {d.kind for d in docs.values()} == {"note"}
    assert "5,000 concurrent users" in docs["2026-06-02_kickoff"].text
    assert "Welsh" in docs["2026-07-07_ux_review"].text                      # pdf
    assert "GBP 180k" in docs["2026-06-30_steering_group"].text              # docx table
    vtt = docs["2026-06-16_design_workshop"].text
    assert "Marco Rossi: Okay, so I checked" in vtt                          # speaker kept
    assert "-->" not in vtt                                                   # timings gone
    assert "security" in docs["2026-06-23_security_review"].text.lower()


def test_emails_metadata_and_quote_stripping():
    docs = by_title(load_folder(SAMPLES / "emails"))
    pay = docs["RE: Phoenix - payment options"]
    assert pay.kind == "email" and pay.id.startswith("E")
    assert "direct debit set-up" in pay.text
    assert "wrote:" not in pay.text and "card payments only" not in pay.text  # quote removed
    assert "Billing Operations Lead" not in pay.text                           # signature removed
    assert "tom.becker@example-energy.co.uk" in pay.recipients
    assert pay.date.startswith("2026-06-25")
    golive = docs["Customer self-service portal - go-live date"]
    assert "30 November 2026" in golive.text and "<b>" not in golive.text      # html converted


def test_strip_keeps_body_when_no_quote():
    assert strip_quotes_and_signature("Line one\nLine two") == "Line one\nLine two"


def test_corrupt_file_is_skipped(tmp_path, caplog):
    (tmp_path / "broken.docx").write_bytes(b"not a real docx")
    (tmp_path / "ok.txt").write_text("Phoenix notes")
    docs = load_folder(tmp_path)
    assert [d.title for d in docs] == ["ok"]
    assert "broken.docx" in caplog.text


def test_ids_unique_across_folders():
    counters = {"note": 0, "email": 0}
    docs = load_folder(SAMPLES / "meetings", counters) + load_folder(SAMPLES / "emails", counters)
    ids = [d.id for d in docs]
    assert len(ids) == len(set(ids))


def test_keyword_filter_needs_no_api_call():
    emails = [d for d in load_folder(SAMPLES / "emails") if "Phoenix" in d.title]
    kept, report = filter_emails(client=None, project="Phoenix", aliases=[], emails=emails, notes=[])
    assert len(kept) == len(emails) == 2
    assert all(r["relevant"] for r in report)


def test_strict_schema_closes_all_objects():
    def walk(node):
        if isinstance(node, dict):
            if node.get("type") == "object" and "properties" in node:
                assert node["additionalProperties"] is False
                assert set(node["required"]) == set(node["properties"])
            assert "default" not in node
            for v in node.values():
                walk(v)
        elif isinstance(node, list):
            for v in node:
                walk(v)
    for model in (ExtractedFacts, BRD, SRS):
        walk(strict_schema(model))


def _docs():
    brd = BRD(
        executive_summary="Summary.", background="Bg.", problem_statement="Problem.",
        business_objectives=[], scope_in=["Portal"], scope_out=["Native app"], stakeholders=[],
        business_requirements=[
            BusinessRequirement(id="BR-001", title="Pay online", description="d", priority="Must",
                                rationale="r", objective_refs=[], source_ids=["N1"]),
            BusinessRequirement(id="BR-002", title="Readings", description="d", priority="Should",
                                rationale="r", objective_refs=[], source_ids=[]),
        ],
        success_metrics=[], assumptions=[], constraints=[], risks=[], open_questions=[],
    )
    srs = SRS(
        purpose="p", scope="s", definitions=[], product_perspective="pp", user_classes=[],
        operating_environment="Azure", design_constraints=[], assumptions_dependencies=[],
        functional_requirements=[
            FunctionalRequirement(id="FR-001", title="Card payment", description="The system shall ...",
                                  priority="Must", acceptance_criteria=["ok"], br_refs=["BR-001", "BR-009"],
                                  source_ids=["N1", "X7"]),
        ],
        non_functional_requirements=[], external_interfaces=[], data_requirements=[], open_questions=[],
    )
    return brd, srs


def test_consistency_warnings():
    brd, srs = _docs()
    warnings = consistency_warnings(brd, srs, {"N1"})
    assert "FR-001 references unknown BR-009" in warnings
    assert "BR-002 is not implemented by any functional requirement" in warnings
    assert "BR-002 cites no source" in warnings
    assert "FR-001 cites unknown source X7" in warnings


def test_render_docx(tmp_path):
    brd, srs = _docs()
    sources = load_folder(SAMPLES / "meetings")
    render_brd(brd, "Phoenix", sources, tmp_path / "brd.docx")
    render_srs(srs, brd, "Phoenix", sources, ["a warning"], tmp_path / "srs.docx")
    brd_text = "\n".join(p.text for p in Document(str(tmp_path / "brd.docx")).paragraphs)
    srs_doc = Document(str(tmp_path / "srs.docx"))
    srs_text = "\n".join(p.text for p in srs_doc.paragraphs)
    assert "Business Requirements Document" in brd_text and "Open Questions" in brd_text
    assert "FR-001 Card payment" in srs_text and "a warning" in srs_text
    cells = [c.text for t in srs_doc.tables for r in t.rows for c in r.cells]
    assert "NOT COVERED" in cells  # BR-002 in traceability matrix
