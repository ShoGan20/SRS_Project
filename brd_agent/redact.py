"""Secret redaction and prompt-injection flagging, done in code before any LLM call.

Redaction keeps the key name and replaces only the value, e.g.
`API_KEY=abc123secret` -> `API_KEY=[REDACTED]`. Nothing here logs secret values.
"""

from __future__ import annotations

import re

MARK = "[REDACTED]"

# Key names whose value is a secret: API_KEY, db_password, clientSecret, access_token, ...
_KEY = (r"(\b[\w.-]*(?:password|passwd|pwd|secret|token|api[_-]?key|apikey|access[_-]?key|"
        r"private[_-]?key)[\w.-]*\b")

# (name, pattern, replacement). Replacements keep any captured key name.
_PATTERNS: list[tuple[str, re.Pattern, str]] = [
    ("private_key",
     re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?(?:-----END [A-Z ]*PRIVATE KEY-----|\Z)", re.S),
     MARK),
    ("url_credentials",
     re.compile(r"(\b[a-z][a-z0-9+.-]*://[^\s:/@]+:)[^\s@/]+(@)", re.I),
     r"\1" + MARK + r"\2"),
    # KEY=value: any value. "Key: value" is also used in prose ("Password: must be 12
    # characters"), so there the value must look like a secret (6+ chars with a digit or symbol).
    ("key_value",
     re.compile(_KEY + r"[\"']?\s*=\s*[\"']?)(?!\[REDACTED\])([^\s\"',;]{3,})", re.I),
     r"\1" + MARK),
    ("key_colon_value",
     re.compile(_KEY + r"[\"']?\s*:\s*[\"']?)(?!\[REDACTED\])"
                r"(?=[^\s\"',;]*[0-9!@#$%^&*_\-+/])([^\s\"',;]{6,})", re.I),
     r"\1" + MARK),
    ("bearer", re.compile(r"(\bBearer\s+)[A-Za-z0-9\-._~+/]{8,}=*", re.I), r"\1" + MARK),
    ("jwt", re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\b"), MARK),
    ("anthropic_key", re.compile(r"\bsk-ant-[A-Za-z0-9_-]{8,}"), MARK),
    ("generic_sk_key", re.compile(r"\bsk-[A-Za-z0-9]{16,}\b"), MARK),
    ("aws_access_key", re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b"), MARK),
    ("github_token", re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}\b"), MARK),
    ("slack_token", re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{10,}\b"), MARK),
    ("secret_query_param",
     re.compile(r"([?&](?:sig|signature|token|key|access_token|code)=)[^&\s]{6,}", re.I),
     r"\1" + MARK),
]


def redact(text: str) -> tuple[str, int]:
    """Return (redacted text, number of redactions)."""
    total = 0
    for _name, pattern, repl in _PATTERNS:
        text, n = pattern.subn(repl, text)
        total += n
    return text, total


def scan(text: str) -> list[str]:
    """Names of secret patterns still present in `text` (for validating output)."""
    found = []
    for name, pattern, _repl in _PATTERNS:
        for match in pattern.finditer(text):
            if MARK not in match.group(0):
                found.append(name)
                break
    return found


_INJECTION = [
    re.compile(p, re.I) for p in (
        r"\b(?:ignore|disregard|forget|override)\b.{0,40}\b(?:previous|prior|above|earlier|all|system)\b"
        r".{0,20}\b(?:instructions?|prompts?|rules?|guidelines?)",
        r"\byou are now\b",
        r"\b(?:new|updated) (?:system )?instructions?\b\s*:",
        r"\bsystem prompt\b",
        r"\bmark (?:every|all|each)\b.{0,40}\b(?:approved|confirmed)\b",
        r"\b(?:act|behave) as\b.{0,30}\b(?:assistant|ai|model|agent)\b",
        r"\bdo not (?:flag|report|mention)\b.{0,40}\b(?:conflicts?|questions?|issues?)\b",
    )
]


def injection_flags(text: str) -> list[str]:
    """Short excerpts that look like instructions aimed at the agent.

    They are only surfaced for reviewers; the model is told to treat them as data."""
    hits = []
    for pattern in _INJECTION:
        for match in pattern.finditer(text):
            start = max(0, match.start() - 20)
            excerpt = " ".join(text[start:match.end() + 20].split())
            hits.append(excerpt[:120])
    return hits
