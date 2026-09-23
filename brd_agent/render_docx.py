"""Render BRD and SRS models into formatted Word documents."""

from __future__ import annotations

from datetime import date
from pathlib import Path

from docx import Document
from docx.enum.table import WD_TABLE_ALIGNMENT
from docx.enum.text import WD_ALIGN_PARAGRAPH, WD_BREAK
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Pt, RGBColor

from .models import BRD, SRS, OpenQuestion, Risk, SourceDoc, Stakeholder

HEADER_FILL = "1F3864"


# ---------------------------------------------------------------- helpers

def _new_document() -> Document:
    doc = Document()
    normal = doc.styles["Normal"]
    normal.font.name = "Calibri"
    normal.font.size = Pt(11)
    return doc


def _title_page(doc: Document, title: str, project: str, source_count: int) -> None:
    for _ in range(6):
        doc.add_paragraph()
    heading = doc.add_paragraph()
    heading.alignment = WD_ALIGN_PARAGRAPH.CENTER
    run = heading.add_run(title)
    run.bold = True
    run.font.size = Pt(28)
    run.font.color.rgb = RGBColor(0x1F, 0x38, 0x64)

    sub = doc.add_paragraph()
    sub.alignment = WD_ALIGN_PARAGRAPH.CENTER
    sub_run = sub.add_run(project)
    sub_run.font.size = Pt(18)

    meta = doc.add_paragraph()
    meta.alignment = WD_ALIGN_PARAGRAPH.CENTER
    meta.add_run(
        f"Draft generated {date.today().isoformat()} from {source_count} source documents\n"
        "Review all content with stakeholders before sign-off."
    ).italic = True
    doc.add_paragraph().add_run().add_break(WD_BREAK.PAGE)


def _shade(cell, fill: str) -> None:
    props = cell._tc.get_or_add_tcPr()
    shading = OxmlElement("w:shd")
    shading.set(qn("w:val"), "clear")
    shading.set(qn("w:color"), "auto")
    shading.set(qn("w:fill"), fill)
    props.append(shading)


def _table(doc: Document, headers: list[str], rows: list[list[str]]) -> None:
    if not rows:
        doc.add_paragraph().add_run("None identified.").italic = True
        return
    table = doc.add_table(rows=1, cols=len(headers))
    table.style = "Table Grid"
    table.alignment = WD_TABLE_ALIGNMENT.CENTER
    for cell, text in zip(table.rows[0].cells, headers):
        cell.text = ""
        run = cell.paragraphs[0].add_run(text)
        run.bold = True
        run.font.color.rgb = RGBColor(0xFF, 0xFF, 0xFF)
        _shade(cell, HEADER_FILL)
    for row in rows:
        cells = table.add_row().cells
        for cell, text in zip(cells, row):
            cell.text = text
    doc.add_paragraph()


def _bullets(doc: Document, items: list[str]) -> None:
    if not items:
        doc.add_paragraph("None identified.")
        return
    for item in items:
        doc.add_paragraph(item, style="List Bullet")


def _para(doc: Document, text: str) -> None:
    for block in (text or "Not specified.").split("\n\n"):
        doc.add_paragraph(block.strip())


def _ids(ids: list[str]) -> str:
    return ", ".join(ids) if ids else "-"


def _stakeholders(doc: Document, items: list[Stakeholder]) -> None:
    _table(doc, ["Stakeholder", "Role", "Interest / Need", "Sources"],
           [[s.name, s.role, s.interest, _ids(s.source_ids)] for s in items])


def _risks(doc: Document, items: list[Risk]) -> None:
    _table(doc, ["#", "Risk", "Impact", "Mitigation", "Sources"],
           [[f"R-{i:02d}", r.description, r.impact, r.mitigation, _ids(r.source_ids)]
            for i, r in enumerate(items, start=1)])


def _open_questions(doc: Document, items: list[OpenQuestion]) -> None:
    doc.add_paragraph(
        "These questions and conflicts in the sources need a stakeholder decision "
        "before this document can be signed off."
    )
    _table(doc, ["#", "Question", "Context / Conflict", "Sources"],
           [[f"Q-{i:02d}", q.question, q.context, _ids(q.source_ids)] for i, q in enumerate(items, start=1)])


def _sources_appendix(doc: Document, sources: list[SourceDoc]) -> None:
    doc.add_paragraph("Source ids used throughout this document refer to:")
    _table(doc, ["Id", "Type", "Title / Subject", "Date", "File"],
           [[s.id, s.kind, s.title, (s.date or "")[:10], Path(s.path).name] for s in sources])


# ---------------------------------------------------------------- BRD

def render_brd(brd: BRD, project: str, sources: list[SourceDoc], path: Path) -> None:
    doc = _new_document()
    _title_page(doc, "Business Requirements Document", project, len(sources))

    doc.add_heading("1. Executive Summary", level=1)
    _para(doc, brd.executive_summary)
    doc.add_heading("2. Background", level=1)
    _para(doc, brd.background)
    doc.add_heading("3. Problem Statement", level=1)
    _para(doc, brd.problem_statement)

    doc.add_heading("4. Business Objectives", level=1)
    _table(doc, ["Id", "Objective", "Success Metric", "Sources"],
           [[o.id, o.statement, o.success_metric, _ids(o.source_ids)] for o in brd.business_objectives])

    doc.add_heading("5. Scope", level=1)
    doc.add_heading("5.1 In Scope", level=2)
    _bullets(doc, brd.scope_in)
    doc.add_heading("5.2 Out of Scope", level=2)
    _bullets(doc, brd.scope_out)

    doc.add_heading("6. Stakeholders", level=1)
    _stakeholders(doc, brd.stakeholders)

    doc.add_heading("7. Business Requirements", level=1)
    _table(doc, ["Id", "Requirement", "Priority", "Rationale", "Objectives", "Sources"],
           [[br.id, f"{br.title}: {br.description}", br.priority, br.rationale,
             _ids(br.objective_refs), _ids(br.source_ids)] for br in brd.business_requirements])

    doc.add_heading("8. Success Metrics / KPIs", level=1)
    _table(doc, ["Metric", "Target", "How Measured"],
           [[m.name, m.target, m.measurement] for m in brd.success_metrics])

    doc.add_heading("9. Assumptions", level=1)
    _bullets(doc, brd.assumptions)
    doc.add_heading("10. Constraints", level=1)
    _bullets(doc, brd.constraints)
    doc.add_heading("11. Risks", level=1)
    _risks(doc, brd.risks)
    doc.add_heading("12. Open Questions / Conflicts", level=1)
    _open_questions(doc, brd.open_questions)

    doc.add_heading("Appendix A. Source Traceability", level=1)
    _sources_appendix(doc, sources)
    doc.save(str(path))


# ---------------------------------------------------------------- SRS

def render_srs(srs: SRS, brd: BRD, project: str, sources: list[SourceDoc],
               warnings: list[str], path: Path) -> None:
    doc = _new_document()
    _title_page(doc, "Software Requirements Specification", project, len(sources))

    doc.add_heading("1. Introduction", level=1)
    doc.add_heading("1.1 Purpose", level=2)
    _para(doc, srs.purpose)
    doc.add_heading("1.2 Scope", level=2)
    _para(doc, srs.scope)
    doc.add_heading("1.3 Definitions, Acronyms and Abbreviations", level=2)
    _table(doc, ["Term", "Definition"], [[t.term, t.definition] for t in srs.definitions])

    doc.add_heading("2. Overall Description", level=1)
    doc.add_heading("2.1 Product Perspective", level=2)
    _para(doc, srs.product_perspective)
    doc.add_heading("2.2 User Classes and Characteristics", level=2)
    _table(doc, ["User Class", "Description"], [[u.name, u.description] for u in srs.user_classes])
    doc.add_heading("2.3 Operating Environment", level=2)
    _para(doc, srs.operating_environment)
    doc.add_heading("2.4 Design and Implementation Constraints", level=2)
    _bullets(doc, srs.design_constraints)
    doc.add_heading("2.5 Assumptions and Dependencies", level=2)
    _bullets(doc, srs.assumptions_dependencies)

    doc.add_heading("3. Functional Requirements", level=1)
    for fr in srs.functional_requirements:
        doc.add_heading(f"{fr.id} {fr.title}", level=2)
        doc.add_paragraph(fr.description)
        meta = doc.add_paragraph()
        meta.add_run("Priority: ").bold = True
        meta.add_run(f"{fr.priority}    ")
        meta.add_run("Implements: ").bold = True
        meta.add_run(f"{_ids(fr.br_refs)}    ")
        meta.add_run("Sources: ").bold = True
        meta.add_run(_ids(fr.source_ids))
        doc.add_paragraph().add_run("Acceptance criteria:").bold = True
        _bullets(doc, fr.acceptance_criteria)
    if not srs.functional_requirements:
        doc.add_paragraph("None identified.")

    doc.add_heading("4. Non-Functional Requirements", level=1)
    _table(doc, ["Id", "Category", "Requirement", "Metric", "Sources"],
           [[n.id, n.category, n.description, n.metric, _ids(n.source_ids)]
            for n in srs.non_functional_requirements])

    doc.add_heading("5. External Interface Requirements", level=1)
    _table(doc, ["Interface", "Type", "Description"],
           [[i.name, i.type, i.description] for i in srs.external_interfaces])

    doc.add_heading("6. Data Requirements", level=1)
    _table(doc, ["Entity", "Description", "Key Attributes"],
           [[d.name, d.description, ", ".join(d.key_attributes)] for d in srs.data_requirements])

    doc.add_heading("7. Traceability Matrix", level=1)
    doc.add_paragraph("Business requirements (BRD) mapped to the functional requirements that implement them.")
    by_br: dict[str, list[str]] = {br.id: [] for br in brd.business_requirements}
    for fr in srs.functional_requirements:
        for ref in fr.br_refs:
            by_br.setdefault(ref, []).append(fr.id)
    titles = {br.id: br.title for br in brd.business_requirements}
    _table(doc, ["BR", "Business Requirement", "Implemented by"],
           [[br_id, titles.get(br_id, "(not in BRD)"), ", ".join(frs) or "NOT COVERED"]
            for br_id, frs in by_br.items()])

    doc.add_heading("8. Open Questions / Conflicts", level=1)
    _open_questions(doc, srs.open_questions)

    if warnings:
        doc.add_heading("9. Review Notes", level=1)
        doc.add_paragraph("Automated consistency checks found the following items to review:")
        _bullets(doc, warnings)

    doc.add_heading("Appendix A. Sources", level=1)
    _sources_appendix(doc, sources)
    doc.save(str(path))
