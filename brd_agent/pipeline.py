"""Pipeline: filter emails -> extract requirement registry -> (validate) -> BRD -> SRS.

Every Claude call returns JSON constrained to a Pydantic model's schema, which is
then validated into that model. The combined source text is the first, cached
block of each main-model request, so the BRD and SRS calls reuse it cheaply.
Validation of the registry happens in code (see validate.py) between extraction
and document writing; the BRD/SRS calls only write narrative sections.
"""

from __future__ import annotations

import json
import logging
from typing import TypeVar

import anthropic
from pydantic import BaseModel, ValidationError

from . import config, prompts
from .models import (
    BRD, SRS, EmailVerdicts, ExtractedFacts, ProjectContext, Registry, SourceDoc, strict_schema,
)

log = logging.getLogger(__name__)
M = TypeVar("M", bound=BaseModel)

# Model ids actually used by the API (after any fallback), for the audit log.
MODELS_USED: list[str] = []


class GenerationError(RuntimeError):
    pass


# ---------------------------------------------------------------- Claude calls

def _response_text(message) -> str:
    return "".join(b.text for b in message.content if b.type == "text")


def _check_stop(message, label: str) -> None:
    if message.stop_reason == "refusal":
        details = getattr(message, "stop_details", None)
        raise GenerationError(f"{label}: the model declined the request ({details})")
    if message.stop_reason == "max_tokens":
        raise GenerationError(
            f"{label}: output hit the {config.MAX_OUTPUT_TOKENS}-token limit. "
            "Try splitting the project or narrowing --since."
        )


def generate(client: anthropic.Anthropic, schema: type[M], content: list[dict], label: str) -> M:
    """One streamed main-model call whose output is validated into `schema`."""
    log.info("%s: calling %s ...", label, config.MAIN_MODEL)
    with client.beta.messages.stream(
        model=config.MAIN_MODEL,
        max_tokens=config.MAX_OUTPUT_TOKENS,
        system=prompts.SYSTEM,
        thinking={"type": "adaptive"},
        output_config={
            "effort": config.EFFORT,
            "format": {"type": "json_schema", "schema": strict_schema(schema)},
        },
        messages=[{"role": "user", "content": content}],
        betas=[config.FALLBACK_BETA],
        fallbacks="default",
    ) as stream:
        message = stream.get_final_message()

    _check_stop(message, label)
    MODELS_USED.append(message.model)
    usage = message.usage
    log.info(
        "%s: done (model=%s, input=%s, cache_read=%s, cache_write=%s, output=%s)",
        label, message.model, usage.input_tokens,
        getattr(usage, "cache_read_input_tokens", 0),
        getattr(usage, "cache_creation_input_tokens", 0), usage.output_tokens,
    )
    try:
        return schema.model_validate_json(_response_text(message))
    except ValidationError as exc:
        raise GenerationError(f"{label}: response did not match the expected structure: {exc}") from exc


# ---------------------------------------------------------------- email filter

def filter_emails(
    client: anthropic.Anthropic,
    project: str,
    aliases: list[str],
    emails: list[SourceDoc],
    notes: list[SourceDoc],
) -> tuple[list[SourceDoc], list[dict]]:
    """Keep emails that mention the project by name; ask the cheap model about the rest.

    Returns (kept emails, report rows for every email).
    """
    terms = [t.lower() for t in [project, *aliases] if t.strip()]
    kept: list[SourceDoc] = []
    report: list[dict] = []
    unsure: list[SourceDoc] = []
    for doc in emails:
        haystack = f"{doc.title}\n{doc.text}".lower()
        if any(term in haystack for term in terms):
            kept.append(doc)
            report.append({"id": doc.id, "subject": doc.title, "relevant": True, "reason": "mentions project name"})
        else:
            unsure.append(doc)

    if not unsure:
        return kept, report

    context = "\n".join(f"- {n.title}: {n.text[:300].replace(chr(10), ' ')}" for n in notes)[:4000] or "(no notes)"
    alias_text = f" (also called: {', '.join(aliases)})" if aliases else ""
    for start in range(0, len(unsure), config.FILTER_BATCH_SIZE):
        batch = unsure[start:start + config.FILTER_BATCH_SIZE]
        listing = "\n\n".join(
            f'<email id="{d.id}">\nSubject: {d.title}\nFrom: {d.sender or "?"}\n\n'
            f"{d.text[:config.FILTER_PREVIEW_CHARS]}\n</email>"
            for d in batch
        )
        response = client.messages.create(
            model=config.FILTER_MODEL,
            max_tokens=4096,
            messages=[{"role": "user", "content": prompts.FILTER.format(
                project=project, aliases=alias_text, context=context, emails=listing)}],
            output_config={"format": {"type": "json_schema", "schema": strict_schema(EmailVerdicts)}},
        )
        MODELS_USED.append(response.model)
        verdicts = {}
        if response.stop_reason == "end_turn":
            try:
                verdicts = {v.id: v for v in EmailVerdicts.model_validate_json(_response_text(response)).verdicts}
            except ValidationError as exc:
                log.warning("Email filter returned malformed output; keeping this batch: %s", exc)
        else:
            log.warning("Email filter stopped with %s; keeping this batch", response.stop_reason)

        for doc in batch:
            verdict = verdicts.get(doc.id)
            # When in doubt keep the email: a missed requirement costs more than extra reading.
            relevant = verdict.relevant if verdict else True
            reason = verdict.reason if verdict else "no verdict returned; kept to be safe"
            report.append({"id": doc.id, "subject": doc.title, "relevant": relevant, "reason": reason})
            if relevant:
                kept.append(doc)

    kept.sort(key=lambda d: int(d.id[1:]))
    return kept, report


# ---------------------------------------------------------------- extraction

def _sources_content(project: str, docs: list[SourceDoc]) -> dict:
    text = prompts.sources_block(project, "\n\n".join(d.render() for d in docs))
    return {"type": "text", "text": text, "cache_control": {"type": "ephemeral", "ttl": "1h"}}


def _count_tokens(client: anthropic.Anthropic, block: dict) -> int:
    try:
        result = client.messages.count_tokens(
            model=config.MAIN_MODEL,
            system=prompts.SYSTEM,
            messages=[{"role": "user", "content": block["text"]}],
        )
        return result.input_tokens
    except anthropic.APIStatusError as exc:
        estimate = len(block["text"]) // 3
        log.warning("Token count failed (%s); estimating %d tokens", exc.status_code, estimate)
        return estimate


def _chunk(docs: list[SourceDoc], total_tokens: int) -> list[list[SourceDoc]]:
    """Group sources into chunks of roughly CHUNK_TARGET_TOKENS, keeping each source whole."""
    total_chars = sum(len(d.render()) for d in docs) or 1
    tokens_per_char = total_tokens / total_chars
    chunks: list[list[SourceDoc]] = [[]]
    size = 0.0
    for doc in docs:
        doc_tokens = len(doc.render()) * tokens_per_char
        if chunks[-1] and size + doc_tokens > config.CHUNK_TARGET_TOKENS:
            chunks.append([])
            size = 0.0
        chunks[-1].append(doc)
        size += doc_tokens
    return chunks


def _previous_section(previous: dict | None) -> str:
    if not previous:
        return ""
    registry = {k: previous.get(k, []) for k in ("requirements", "conflicts", "open_questions")}
    return prompts.PREVIOUS.format(registry=json.dumps(registry, indent=1))


def _extract_both(client, block: dict, previous: dict | None, label: str) -> tuple[Registry, ProjectContext]:
    """Two calls over the same (cached) sources block: the registry, then the context."""
    registry_prompt = prompts.EXTRACT_REGISTRY.format(previous=_previous_section(previous))
    registry = generate(client, Registry, [block, {"type": "text", "text": registry_prompt}],
                        f"{label} requirements")
    context = generate(client, ProjectContext, [block, {"type": "text", "text": prompts.EXTRACT_CONTEXT}],
                       f"{label} context")
    return registry, context


def _merge(client, project: str, schema: type[M], partials: list[dict], extra: str, label: str) -> M:
    text = prompts.MERGE.format(partials=json.dumps(partials, indent=1)) + extra
    return generate(client, schema, [{"type": "text", "text": f"Project: {project}\n\n" + text}], label)


def extract_facts(
    client: anthropic.Anthropic, project: str, docs: list[SourceDoc], previous: dict | None = None
) -> tuple[ExtractedFacts, dict | None]:
    """Returns the registry and, when all sources fit in one request, the cached sources
    block to reuse in the BRD/SRS calls (None when chunking was needed).

    `previous` is the registry of an earlier run; its ids are preserved."""
    block = _sources_content(project, docs)
    tokens = _count_tokens(client, block)
    log.info("Sources total about %d tokens", tokens)

    if tokens <= config.CHUNK_THRESHOLD_TOKENS:
        registry, context = _extract_both(client, block, previous, "Extract")
        return ExtractedFacts.combine(context, registry), block

    chunks = _chunk(docs, tokens)
    log.info("Sources too large for one pass; extracting in %d chunks", len(chunks))
    registries, contexts = [], []
    for i, chunk in enumerate(chunks, start=1):
        registry, context = _extract_both(client, _sources_content(project, chunk), previous,
                                          f"Extract chunk {i}/{len(chunks)}")
        registries.append(registry.model_dump())
        contexts.append(context.model_dump())
    registry = _merge(client, project, Registry, registries, _previous_section(previous), "Merge requirements")
    context = _merge(client, project, ProjectContext, contexts, "", "Merge context")
    return ExtractedFacts.combine(context, registry), None


# ---------------------------------------------------------------- documents

def write_brd(client, project: str, facts: ExtractedFacts, sources_block: dict | None) -> BRD:
    text = prompts.BRD.format(
        facts=facts.model_dump_json(indent=1),
        with_sources=" and the original sources above" if sources_block else "",
    )
    content = ([sources_block] if sources_block else [{"type": "text", "text": f"Project: {project}"}])
    return generate(client, BRD, content + [{"type": "text", "text": text}], "BRD")


def write_srs(client, project: str, facts: ExtractedFacts, brd: BRD, sources_block: dict | None) -> SRS:
    text = prompts.SRS.format(
        facts=facts.model_dump_json(indent=1),
        brd=brd.model_dump_json(indent=1),
        with_sources=" and the original sources above" if sources_block else "",
    )
    content = ([sources_block] if sources_block else [{"type": "text", "text": f"Project: {project}"}])
    return generate(client, SRS, content + [{"type": "text", "text": text}], "SRS")
