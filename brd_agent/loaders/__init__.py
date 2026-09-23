"""Load every supported file in a folder into SourceDoc objects."""

from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path

from ..config import EMAIL_EXTENSIONS, NOTE_EXTENSIONS
from ..models import SourceDoc
from . import emails, notes

log = logging.getLogger(__name__)


def load_folder(folder: Path, start_index: dict[str, int] | None = None) -> list[SourceDoc]:
    """Parse all supported files under `folder` (recursively).

    Files are classified by extension, not by which folder they are in, so an
    .eml dropped into the notes folder is still treated as an email.
    Unreadable files are logged and skipped. `start_index` carries the id
    counters across several calls so ids stay unique ("N1", "E1", ...).
    """
    counters = start_index if start_index is not None else {"note": 0, "email": 0}
    docs: list[SourceDoc] = []
    for path in sorted(p for p in folder.rglob("*") if p.is_file()):
        ext = path.suffix.lower()
        if path.name.startswith("~$"):  # Office lock files
            continue
        try:
            if ext in NOTE_EXTENSIONS:
                text = notes.READERS[ext](path)
                if not text.strip():
                    log.warning("Skipping %s: no text found", path)
                    continue
                counters["note"] += 1
                docs.append(SourceDoc(
                    id=f"N{counters['note']}",
                    kind="note",
                    path=str(path),
                    title=path.stem,
                    text=text,
                    date=datetime.fromtimestamp(path.stat().st_mtime).date().isoformat(),
                ))
            elif ext in EMAIL_EXTENSIONS:
                parsed = emails.READERS[ext](path)
                body = emails.strip_quotes_and_signature(parsed.body)
                if not body.strip():
                    log.warning("Skipping %s: empty email body", path)
                    continue
                counters["email"] += 1
                docs.append(SourceDoc(
                    id=f"E{counters['email']}",
                    kind="email",
                    path=str(path),
                    title=parsed.subject,
                    text=body,
                    date=parsed.date,
                    sender=parsed.sender,
                    recipients=parsed.recipients,
                ))
            else:
                log.debug("Ignoring unsupported file %s", path)
        except Exception as exc:  # one bad file must not stop the run
            log.warning("Skipping %s: %s", path, exc)
    return docs
