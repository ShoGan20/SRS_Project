"""Email parsing for exported .eml and Outlook .msg files."""

from __future__ import annotations

import email
import html
import re
from dataclasses import dataclass, field
from email import policy
from email.utils import getaddresses, parsedate_to_datetime
from pathlib import Path


@dataclass
class ParsedEmail:
    subject: str
    sender: str | None
    recipients: list[str] = field(default_factory=list)
    date: str | None = None
    body: str = ""


# Lines that start the quoted previous message in a reply/forward.
_REPLY_MARKERS = [
    re.compile(r"^On .{0,200}wrote:\s*$", re.IGNORECASE),
    re.compile(r"^-{2,}\s*Original Message\s*-{2,}", re.IGNORECASE),
    re.compile(r"^-{2,}\s*Forwarded message\s*-{2,}", re.IGNORECASE),
    re.compile(r"^_{10,}\s*$"),  # Outlook separator line
    re.compile(r"^From:\s.*(@|\[mailto:)", re.IGNORECASE),  # Outlook header block of the quoted mail
]
_SIGNATURE_MARKERS = [
    re.compile(r"^--\s*$"),
    re.compile(r"^Sent from my ", re.IGNORECASE),
]


def strip_quotes_and_signature(body: str) -> str:
    """Keep only the newest message: drop quoted replies, '>' lines and the signature."""
    kept: list[str] = []
    for line in body.splitlines():
        stripped = line.strip()
        # A "From:" line only counts as a quote marker once there is real content above it.
        if any(m.match(stripped) for m in _REPLY_MARKERS) and any(k.strip() for k in kept):
            break
        if any(m.match(stripped) for m in _SIGNATURE_MARKERS):
            break
        if stripped.startswith(">"):
            continue
        kept.append(line.rstrip())
    text = "\n".join(kept).strip()
    return re.sub(r"\n{3,}", "\n\n", text)


def html_to_text(markup: str) -> str:
    markup = re.sub(r"(?is)<(script|style).*?</\1>", "", markup)
    markup = re.sub(r"(?i)<br\s*/?>|</p>|</div>|</li>|</tr>", "\n", markup)
    markup = re.sub(r"<[^>]+>", "", markup)
    return html.unescape(markup)


def _iso(date_value) -> str | None:
    if not date_value:
        return None
    try:
        if isinstance(date_value, str):
            return parsedate_to_datetime(date_value).isoformat()
        return date_value.isoformat()
    except (TypeError, ValueError):
        return str(date_value)


def read_eml(path: Path) -> ParsedEmail:
    with open(path, "rb") as fh:
        msg = email.message_from_binary_file(fh, policy=policy.default)
    part = msg.get_body(preferencelist=("plain", "html"))
    body = ""
    if part is not None:
        body = part.get_content()
        if part.get_content_type() == "text/html":
            body = html_to_text(body)
    recipients = [addr for _, addr in getaddresses(msg.get_all("to", []) + msg.get_all("cc", []))]
    return ParsedEmail(
        subject=str(msg.get("subject", "")).strip() or path.stem,
        sender=str(msg.get("from", "")).strip() or None,
        recipients=[r for r in recipients if r],
        date=_iso(msg.get("date")),
        body=body,
    )


def read_msg(path: Path) -> ParsedEmail:
    try:
        import extract_msg
    except ImportError as exc:  # optional dependency
        raise RuntimeError("reading .msg files needs the 'extract-msg' package") from exc

    msg = extract_msg.Message(str(path))
    try:
        body = msg.body or ""
        if not body.strip() and msg.htmlBody:
            raw = msg.htmlBody
            body = html_to_text(raw.decode("utf-8", "replace") if isinstance(raw, bytes) else raw)
        recipients = [r.strip() for r in ((msg.to or "") + ";" + (msg.cc or "")).split(";") if r.strip()]
        return ParsedEmail(
            subject=(msg.subject or path.stem).strip(),
            sender=msg.sender,
            recipients=recipients,
            date=_iso(msg.date),
            body=body,
        )
    finally:
        msg.close()


READERS = {".eml": read_eml, ".msg": read_msg}
