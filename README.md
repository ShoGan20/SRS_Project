# BRD / SRS Agent

Collects a project's meeting notes and emails, reads them with Claude, and writes a
**Business Requirements Document** and a **Software Requirements Specification** as Word files.

## What it reads

| Folder | Formats |
|---|---|
| `--notes-dir` | `.txt`, `.md`, `.docx`, `.pdf`, `.vtt` (Teams/Zoom transcripts) |
| `--emails-dir` | `.eml`, `.msg` (export from Outlook: select mails → drag into a folder, or *Save As*) |

- Subfolders are included.
- Quoted replies and signatures are removed from emails, so the same text isn't read twice.
- Unreadable files are skipped with a warning.

## Setup

```powershell
python -m venv .venv
.\.venv\Scripts\pip install -r requirements.txt
$env:ANTHROPIC_API_KEY = "sk-ant-..."     # or: ant auth login
```

## Run

```powershell
.\.venv\Scripts\python -m brd_agent --project "Phoenix" `
    --notes-dir "C:\path\to\meetings" --emails-dir "C:\path\to\emails" `
    --aliases "PHX,Customer Portal" --out output
```

| Option | Meaning |
|---|---|
| `--aliases` | Other names for the project. They help pick out its emails. |
| `--since 2026-01-01` | Ignore sources dated before this day. |
| `--skip-email-filter` | Treat every email in the folder as belonging to the project. |
| `-v` | Debug logging. |

## Output (`--out` folder)

| File | Contents |
|---|---|
| `<Project>_BRD.docx` | Executive summary, objectives, scope, stakeholders, business requirements (BR-###), KPIs, assumptions, constraints, risks, open questions, source list |
| `<Project>_SRS.docx` | IEEE 830-style: introduction, overall description, functional requirements (FR-### with acceptance criteria), non-functional requirements (NFR-###), interfaces, data, BR → FR traceability matrix, open questions, review notes |
| `extracted_facts.json`, `brd.json`, `srs.json` | The raw structured output, for review |
| `email_filter_report.json` | Which emails were kept or dropped, and why |

Every requirement cites the sources it came from (`N3` = note, `E5` = email). The appendix
maps each id to its file. When sources contradict each other, the conflict is listed under
**Open Questions** instead of being resolved silently.

## How it works

1. **Load:** parse every file into text plus metadata (`brd_agent/loaders/`).
2. **Filter emails:** emails that mention the project name or an alias are kept. The other
   emails are sent to Claude Haiku 4.5, which classifies each one against the meeting-note
   context. If Haiku gives no clear answer for an email, it is kept.
3. **Extract:** Claude Opus 5 reads all sources and returns structured facts: requirements,
   decisions, risks, conflicts. Very large projects (over about 600K tokens) are processed
   in chunks and merged.
4. **Write the BRD, then the SRS:** both reuse the cached source text, so they cost less.
5. **Check and render:** the program checks that every FR links to an existing BR, every BR
   is covered by an FR, and every cited source id exists. It then writes the .docx files.

The model and effort level can be changed with the `BRD_AGENT_MODEL`,
`BRD_AGENT_FILTER_MODEL` and `BRD_AGENT_EFFORT` environment variables. The generated
documents are drafts: review them with stakeholders before sign-off.

## Try it on the sample project

```powershell
.\.venv\Scripts\python samples\make_samples.py    # creates the .docx / .pdf sample notes
.\.venv\Scripts\python -m brd_agent --project Phoenix --notes-dir samples\meetings --emails-dir samples\emails
.\.venv\Scripts\python -m pytest                  # offline tests (no API calls)
```

The samples contain these planted cases:
- Two contradictions:
  - card-only vs. direct-debit payments at launch
  - 5,000 vs. 2,000 concurrent users
- One project email that never names "Phoenix"
- Two unrelated emails that should be dropped

## Demo in GitHub Codespaces

You can run the demo from any computer with a browser.

1. Push this folder to a GitHub repository. A private repository is fine.
2. On github.com, go to **Settings → Codespaces → Secrets** and add `ANTHROPIC_API_KEY`.
   Give the secret access to the repository. You can also enter the key when the
   codespace is created.
3. In the repository, click **Code → Codespaces → Create codespace on main**. Setup
   installs the requirements and creates the sample .docx and .pdf notes.
4. In the terminal (bash):
   ```bash
   python -m pytest     # offline check, no API calls
   python -m brd_agent --project Phoenix --notes-dir samples/meetings --emails-dir samples/emails
   ```
5. Open `output/Phoenix_BRD.docx` and `output/Phoenix_SRS.docx`. They preview in the editor,
   or you can right-click and choose **Download**. Things to show:
   - `output/email_filter_report.json`: the parking and scanner emails are dropped.
   - **Open Questions**: the planted contradictions are listed there.

Stop the codespace when you finish, so it doesn't use up your free hours.
