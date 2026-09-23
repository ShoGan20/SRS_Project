"""Data models.

`SourceDoc` is what the loaders produce. Everything else is a Pydantic model that
Claude fills in through structured outputs; `strict_schema()` turns a model into the
JSON schema the API expects (every object closed, every property required).
"""

from __future__ import annotations

import copy
from dataclasses import dataclass, field
from typing import Literal

from pydantic import BaseModel, Field

Priority = Literal["Must", "Should", "Could", "Won't"]


# ---------------------------------------------------------------- sources

@dataclass
class SourceDoc:
    id: str                      # "N1" for notes, "E1" for emails
    kind: Literal["note", "email"]
    path: str
    title: str                   # file name for notes, subject for emails
    text: str
    date: str | None = None      # ISO 8601 when known
    sender: str | None = None
    recipients: list[str] = field(default_factory=list)

    def render(self) -> str:
        """Text block handed to Claude, with a header Claude can cite by id."""
        head = [f'<source id="{self.id}" type="{self.kind}">', f"Title: {self.title}"]
        if self.date:
            head.append(f"Date: {self.date}")
        if self.sender:
            head.append(f"From: {self.sender}")
        if self.recipients:
            head.append(f"To: {', '.join(self.recipients)}")
        return "\n".join(head) + "\n\n" + self.text.strip() + "\n</source>"


# ---------------------------------------------------------------- email filter

class EmailVerdict(BaseModel):
    id: str
    relevant: bool
    reason: str


class EmailVerdicts(BaseModel):
    verdicts: list[EmailVerdict]


# ---------------------------------------------------------------- shared pieces

class Stakeholder(BaseModel):
    name: str = Field(description="Person, team or organisation")
    role: str
    interest: str = Field(description="What they need from the project")
    source_ids: list[str]


class Risk(BaseModel):
    description: str
    impact: Literal["High", "Medium", "Low"]
    mitigation: str = Field(description="Mitigation if mentioned, else 'Not discussed'")
    source_ids: list[str]


class OpenQuestion(BaseModel):
    question: str
    context: str = Field(description="Why it is open, including any conflicting statements and who made them")
    source_ids: list[str]


class Term(BaseModel):
    term: str
    definition: str


# ---------------------------------------------------------------- extraction

class Fact(BaseModel):
    statement: str
    source_ids: list[str]


class RawRequirement(BaseModel):
    title: str
    description: str
    priority: Priority
    rationale: str
    source_ids: list[str]


class RawNFR(BaseModel):
    category: str = Field(description="e.g. Performance, Security, Availability, Usability, Compliance")
    description: str
    metric: str = Field(description="Measurable target if stated, else 'Not specified'")
    source_ids: list[str]


class ExtractedFacts(BaseModel):
    project_summary: str
    problem_statement: str
    business_objectives: list[Fact]
    stakeholders: list[Stakeholder]
    scope_in: list[Fact]
    scope_out: list[Fact]
    functional_requirements: list[RawRequirement]
    non_functional_requirements: list[RawNFR]
    constraints: list[Fact]
    assumptions: list[Fact]
    risks: list[Risk]
    decisions: list[Fact]
    open_questions: list[OpenQuestion]
    glossary: list[Term]


# ---------------------------------------------------------------- BRD

class Objective(BaseModel):
    id: str = Field(description="OBJ-001, OBJ-002, ...")
    statement: str
    success_metric: str
    source_ids: list[str]


class BusinessRequirement(BaseModel):
    id: str = Field(description="BR-001, BR-002, ...")
    title: str
    description: str
    priority: Priority
    rationale: str
    objective_refs: list[str] = Field(description="OBJ ids this requirement serves")
    source_ids: list[str]


class Metric(BaseModel):
    name: str
    target: str
    measurement: str


class BRD(BaseModel):
    executive_summary: str
    background: str
    problem_statement: str
    business_objectives: list[Objective]
    scope_in: list[str]
    scope_out: list[str]
    stakeholders: list[Stakeholder]
    business_requirements: list[BusinessRequirement]
    success_metrics: list[Metric]
    assumptions: list[str]
    constraints: list[str]
    risks: list[Risk]
    open_questions: list[OpenQuestion]


# ---------------------------------------------------------------- SRS

class UserClass(BaseModel):
    name: str
    description: str


class FunctionalRequirement(BaseModel):
    id: str = Field(description="FR-001, FR-002, ...")
    title: str
    description: str = Field(description="'The system shall ...' statement")
    priority: Priority
    acceptance_criteria: list[str]
    br_refs: list[str] = Field(description="BR ids from the BRD that this FR implements")
    source_ids: list[str]


class NonFunctionalRequirement(BaseModel):
    id: str = Field(description="NFR-001, NFR-002, ...")
    category: str
    description: str
    metric: str
    source_ids: list[str]


class Interface(BaseModel):
    name: str
    type: Literal["User", "Software", "Hardware", "Communication"]
    description: str


class DataEntity(BaseModel):
    name: str
    description: str
    key_attributes: list[str]


class SRS(BaseModel):
    purpose: str
    scope: str
    definitions: list[Term]
    product_perspective: str
    user_classes: list[UserClass]
    operating_environment: str
    design_constraints: list[str]
    assumptions_dependencies: list[str]
    functional_requirements: list[FunctionalRequirement]
    non_functional_requirements: list[NonFunctionalRequirement]
    external_interfaces: list[Interface]
    data_requirements: list[DataEntity]
    open_questions: list[OpenQuestion]


# ---------------------------------------------------------------- schema helper

def strict_schema(model: type[BaseModel]) -> dict:
    """Pydantic JSON schema adapted for structured outputs:
    every object gets additionalProperties=false and lists all its properties as required."""
    schema = copy.deepcopy(model.model_json_schema())

    def fix(node):
        if isinstance(node, dict):
            node.pop("default", None)
            if node.get("type") == "object" and "properties" in node:
                node["additionalProperties"] = False
                node["required"] = list(node["properties"].keys())
            for value in node.values():
                fix(value)
        elif isinstance(node, list):
            for item in node:
                fix(item)

    fix(schema)
    return schema
