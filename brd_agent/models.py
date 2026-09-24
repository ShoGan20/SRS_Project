"""Data models.

`SourceDoc` is what the loaders produce. Everything else is a Pydantic model that
Claude fills in through structured outputs; `strict_schema()` turns a model into the
JSON schema the API expects (every object closed, every property required).

The requirement registry (`ExtractedFacts.requirements`, `.conflicts`,
`.open_questions`) is the single source of truth: it is validated in code and the
BRD/SRS requirement tables are rendered straight from it. The BRD and SRS models
only carry narrative sections.
"""

from __future__ import annotations

import copy
import re
from dataclasses import dataclass, field
from typing import Literal

from pydantic import BaseModel, Field

Priority = Literal["Must", "Should", "Could", "Won't", "TBD"]
Status = Literal["CONFIRMED", "ASSUMPTION", "TBD", "NEEDS_CLARIFICATION", "CONFLICT"]
Confidence = Literal["HIGH", "MEDIUM", "LOW"]
RequirementType = Literal["business", "functional", "non_functional"]
QuestionPriority = Literal["HIGH", "MEDIUM", "LOW"]

STATUSES: tuple[str, ...] = Status.__args__


# ---------------------------------------------------------------- sources

# A source must not be able to close its own <source> tag (or open a new one).
_TAG_BREAKOUT = re.compile(r"<(/?)(sources?)\b", re.IGNORECASE)


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
    redactions: int = 0          # secrets replaced with [REDACTED] before any LLM call
    injection_flags: list[str] = field(default_factory=list)

    def render(self) -> str:
        """Text block handed to Claude, with a header Claude can cite by id."""
        head = [f'<source id="{self.id}" type="{self.kind}">', f"Title: {_neutralise(self.title)}"]
        if self.date:
            head.append(f"Date: {self.date}")
        if self.sender:
            head.append(f"From: {self.sender}")
        if self.recipients:
            head.append(f"To: {', '.join(self.recipients)}")
        return "\n".join(head) + "\n\n" + _neutralise(self.text.strip()) + "\n</source>"


def _neutralise(text: str) -> str:
    return _TAG_BREAKOUT.sub(lambda m: f"&lt;{m.group(1)}{m.group(2)}", text)


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
    impact: Literal["High", "Medium", "Low", "TBD"]
    mitigation: str = Field(description="Mitigation if mentioned, else 'Not discussed'")
    source_ids: list[str]


class Term(BaseModel):
    term: str
    definition: str


class Fact(BaseModel):
    statement: str
    status: Status = Field(description="CONFIRMED only when a source states it explicitly")
    source_ids: list[str]


# ---------------------------------------------------------------- requirement registry

class Requirement(BaseModel):
    id: str = Field(description="BR-001 for business, FR-001 for functional, NFR-001 for non-functional")
    type: RequirementType
    category: str = Field(description="NFR only: Performance, Security, Availability, Scalability, "
                                      "Reliability, Accessibility, Compliance, Maintainability, "
                                      "Logging, Monitoring, Usability, ... Empty string for BR/FR")
    title: str
    requirement: str = Field(description="The requirement statement. Unknown details are written as TBD")
    status: Status
    confidence: Confidence = Field(description="How strongly the sources support this statement")
    priority: Priority = Field(description="Only what the sources indicate, else TBD")
    source_ids: list[str]
    source_reference: str = Field(description="Where in the sources: short quote or location, "
                                              "e.g. 'N2: payment options discussion'. Empty if none")
    acceptance_criteria: list[str] = Field(description="Only criteria stated or directly implied by the "
                                                       "sources; empty list otherwise")
    br_refs: list[str] = Field(description="FR/NFR: BR ids this implements (only when the link is clear)")
    dependencies: list[str] = Field(description="Ids of requirements this depends on")
    open_question_ids: list[str] = Field(description="OPEN-### ids about this requirement")
    history: list[str] = Field(description="Explicit supersessions, e.g. 'E3 changed 10 MB to 25 MB'")


class ConflictOption(BaseModel):
    statement: str
    source_ids: list[str]


class Conflict(BaseModel):
    id: str = Field(description="CONFLICT-001, CONFLICT-002, ...")
    topic: str
    options: list[ConflictOption] = Field(description="Every contradicting position, each with its sources")
    related_requirement_ids: list[str]
    resolution: str = Field(description="'Needs stakeholder confirmation' unless a source explicitly resolves it")


class OpenQuestion(BaseModel):
    id: str = Field(description="OPEN-001, OPEN-002, ...")
    question: str = Field(description="Short question a stakeholder can answer directly")
    reason: str = Field(description="Why it is open")
    related_requirement_ids: list[str]
    priority: QuestionPriority
    source_ids: list[str]


# ---------------------------------------------------------------- extraction

# Extraction is split over two schemas: one combined schema compiles to a grammar the
# API rejects as too large. Both calls read the same cached sources block.

class Registry(BaseModel):
    requirements: list[Requirement]
    conflicts: list[Conflict]
    open_questions: list[OpenQuestion]


class ProjectContext(BaseModel):
    project_summary: str
    problem_statement: str
    business_objectives: list[Fact]
    stakeholders: list[Stakeholder]
    user_roles: list[Fact]
    scope_in: list[Fact]
    scope_out: list[Fact]
    current_process: list[Fact]
    proposed_process: list[Fact]
    business_rules: list[Fact]
    constraints: list[Fact]
    dependencies: list[Fact]
    assumptions: list[Fact]
    risks: list[Risk]
    decisions: list[Fact]
    glossary: list[Term]


class ExtractedFacts(ProjectContext, Registry):
    """Both halves combined in code. Never sent to the API as a schema."""

    @classmethod
    def combine(cls, context: ProjectContext, registry: Registry) -> "ExtractedFacts":
        return cls(**context.model_dump(), **registry.model_dump())


# ---------------------------------------------------------------- BRD (narrative only)

class Objective(BaseModel):
    id: str = Field(description="OBJ-001, OBJ-002, ...")
    statement: str
    success_metric: str = Field(description="Only if the sources state one, else 'TBD'")
    source_ids: list[str]


class BRD(BaseModel):
    executive_summary: str
    background: str
    problem_statement: str
    business_objectives: list[Objective]
    scope_in: list[str]
    scope_out: list[str]
    stakeholders: list[Stakeholder]
    current_process: str
    proposed_process: str
    business_rules: list[str]
    success_criteria: list[str]
    assumptions: list[str]
    constraints: list[str]
    dependencies: list[str]
    risks: list[Risk]


# ---------------------------------------------------------------- SRS (narrative only)

class UserClass(BaseModel):
    name: str
    description: str


class Interface(BaseModel):
    name: str
    type: Literal["User", "Software", "Hardware", "Communication"]
    description: str
    source_ids: list[str]


class DataEntity(BaseModel):
    name: str
    description: str
    key_attributes: list[str]
    source_ids: list[str]


class Workflow(BaseModel):
    name: str
    steps: list[str]
    requirement_ids: list[str]


class SRS(BaseModel):
    purpose: str
    system_overview: str
    scope: str
    definitions: list[Term]
    user_classes: list[UserClass]
    operating_environment: str
    system_workflows: list[Workflow]
    external_interfaces: list[Interface]
    data_requirements: list[DataEntity]
    error_handling: list[str]
    design_constraints: list[str]
    dependencies: list[str]
    assumptions: list[str]


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
