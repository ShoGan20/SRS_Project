"""Central settings. Override the models with environment variables if needed."""

import os

# Main reasoning model (extraction, BRD, SRS).
MAIN_MODEL = os.environ.get("BRD_AGENT_MODEL", "claude-opus-5")
# Cheap model used only to decide whether an email belongs to the project.
FILTER_MODEL = os.environ.get("BRD_AGENT_FILTER_MODEL", "claude-haiku-4-5")

# Effort for the main model: low | medium | high | xhigh | max
EFFORT = os.environ.get("BRD_AGENT_EFFORT", "high")

# Server-side refusal fallback (re-runs a declined request on another model).
FALLBACK_BETA = "server-side-fallback-2026-07-01"

# Output ceiling for each streamed generation call.
MAX_OUTPUT_TOKENS = 64000

# If all sources together exceed this many input tokens, extract in chunks and merge.
CHUNK_THRESHOLD_TOKENS = 600_000
# Target size of one chunk when chunking.
CHUNK_TARGET_TOKENS = 300_000

# How many emails the filter model classifies per request, and how much of each it sees.
FILTER_BATCH_SIZE = 25
FILTER_PREVIEW_CHARS = 1500

NOTE_EXTENSIONS = {".txt", ".md", ".docx", ".pdf", ".vtt"}
EMAIL_EXTENSIONS = {".eml", ".msg"}
