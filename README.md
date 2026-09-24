# BRD / SRS Agent

Point it at a folder of **meeting notes** and a folder of **emails**, and it writes a draft
**Business Requirements Document (BRD)** and **Software Requirements Specification (SRS)**
as Word files.

Every requirement shows where it came from. Anything missing is marked `TBD`, and anything
contradictory is marked `CONFLICT`. Nothing is made up, and every document is labelled
**Draft for Review**.

---

## Quick start (Windows, PowerShell)

You need **Python 3.10 or newer** and an **Anthropic API key** (`sk-ant-...`).

**1. Install.** Run this once, in the project folder:

```powershell
python -m venv .venv
.\.venv\Scripts\pip install -r requirements.txt
```

**2. Set your API key.** Do this in each new terminal window:

```powershell
$env:ANTHROPIC_API_KEY = "sk-ant-..."
```

**3. Try the sample project:**

```powershell
.\.venv\Scripts\python samples\make_samples.py
.\.venv\Scripts\python -m brd_agent --project Phoenix --notes-dir samples\meetings --emails-dir samples\emails
```

**4. Open the results** in the `output` folder:
- `Phoenix_BRD_v0.1.docx`
- `Phoenix_SRS_v0.1.docx`

That's it.

---

## Run it on your own project

1. Put your meeting notes in one folder, for example `C:\work\meetings`.
2. Put your emails in another folder, for example `C:\work\emails`. To export them from
   Outlook, select the emails and drag them into the folder.
3. Run:

```powershell
.\.venv\Scripts\python -m brd_agent --project "My Project" --notes-dir "C:\work\meetings" --emails-dir "C:\work\emails"
```

**Supported files**

| Folder | File types |
|---|---|
| Notes | `.txt` `.md` `.docx` `.pdf` `.vtt` (Teams/Zoom transcripts) |
| Emails | `.eml` `.msg` |

Subfolders are included, and unreadable files are skipped with a warning.

**Optional extras**

Add any of these to the end of the command:

| Add this | What it does |
|---|---|
| `--aliases "PHX,Customer Portal"` | Other names for the project, so more of its emails are found |
| `--since 2026-01-01` | Ignore anything older than this date |
| `--out results` | Save the results to a different folder (default: `output`) |
| `--skip-email-filter` | Use every email in the folder, without filtering |
| `-v` | Show detailed logging, which helps when something goes wrong |

**Running it again:** run the same command after adding new notes or emails. You get
`v0.2`, `v0.3` and so on, requirement IDs stay the same, and a change log shows what's new.
Older versions are kept.

---

## What you get

In the `output` folder:

| File | What it is |
|---|---|
| `<Project>_BRD_v0.1.docx` | The Business Requirements Document (draft) |
| `<Project>_SRS_v0.1.docx` | The Software Requirements Specification (draft) |
| `requirements.json` | Every requirement with its status, sources and evidence |
| `validation_report.json` | Problems found by the automatic checks |
| `change_log.json` | What changed since the last run |
| `audit_log.json` | Which sources were used, and how (no email or note text is stored) |
| `email_filter_report.json` | Which emails were kept or skipped, and why |
| `brd.json`, `srs.json` | The written sections in raw form |

**Reading the documents**
- Each requirement has an ID: `BR-001` (business), `FR-001` (functional) or `NFR-001`
  (non-functional).
- Each requirement cites its sources: `N3` is a note and `E5` is an email. The appendix
  lists which file each ID refers to.
- Each requirement has a status:

  | Status | Meaning |
  |---|---|
  | `CONFIRMED` | Clearly stated in a source |
  | `ASSUMPTION` | Reasonable, but not stated |
  | `TBD` | Information is missing |
  | `NEEDS_CLARIFICATION` | Too vague to test, for example "should be fast" |
  | `CONFLICT` | Sources disagree. Both positions are shown, and a person decides. |

- Rows that aren't `CONFIRMED` are highlighted.
- The **Open Questions** section lists what stakeholders still need to answer.

---

## How it keeps the output trustworthy

- **No guessing.** Missing details become `TBD`, never invented numbers or technologies.
- **Checked in code.** A requirement marked CONFIRMED with no real source is automatically
  downgraded to NEEDS_CLARIFICATION.
- **Conflicts stay visible.** A newer email doesn't silently override an older meeting.
- **Secrets are removed.** Passwords, API keys and tokens are replaced with `[REDACTED]`
  before anything is sent to the AI.
- **Instructions inside emails are ignored.** Text such as "ignore previous instructions" is
  treated as data, and it is flagged for review.
- **Read-only.** It only reads the folders you give it and writes to the output folder. It
  never sends, deletes or approves anything.
- **Humans decide.** Every document is a *Draft for Review*.

For the full rules, see
[`BRD & SRS Generation Agent — Instructions and Guardrails.md`](BRD%20%26%20SRS%20Generation%20Agent%20—%20Instructions%20and%20Guardrails.md).
For a presentation-style overview, see [`docs/BRD_SRS_Agent_Overview.docx`](docs/BRD_SRS_Agent_Overview.docx).

---

## Mac, Linux or GitHub Codespaces (bash)

The steps are the same, but bash uses different commands:

```bash
python -m pip install -r requirements.txt
export ANTHROPIC_API_KEY="sk-ant-..."          # no spaces around "="
python samples/make_samples.py
python -m brd_agent --project Phoenix --notes-dir samples/meetings --emails-dir samples/emails
```

**In GitHub Codespaces**
1. Push this folder to a GitHub repository.
2. Add your key as a secret, so you don't have to type it each time:
   1. Go to **github.com → Settings → Codespaces → Secrets → New secret**.
   2. Name it `ANTHROPIC_API_KEY`, and give it access to this repository.
3. Open the codespace: **Code → Codespaces → Create codespace on main**.
4. Run the bash commands above in the terminal. Skip the `export` line if you added the
   secret.
5. Right-click the files in `output/` and choose **Download**.
6. When you're done, stop the codespace (**Code → Codespaces → ⋯ → Stop codespace**) so it
   doesn't use up your free hours.

`output/` is excluded from git. If you upload confidential notes into the repository folder,
keep them out of git too, for example with `echo "input/" >> .gitignore`.

---

## Troubleshooting

| Problem | Fix |
|---|---|
| `No Anthropic credentials found` or an authentication error | Set the key (step 2). You need to set it again in every new terminal. |
| `ModuleNotFoundError` | Run the install step (step 1). |
| `python` isn't recognised | Install Python 3.10+ from python.org, and tick **Add to PATH** during setup. |
| `bash: env:ANTHROPIC_API_KEY: command not found` | That's the PowerShell command. In bash, use `export ANTHROPIC_API_KEY="sk-ant-..."`. |
| `Folder not found` | Check the path, and put it in quotes if it contains spaces. |
| `No readable sources found` | The folder has no supported files, or `--since` filtered them all out. |
| `Rate limited` | Wait a minute and run again. |
| Anything else | Run again with `-v` and read the last lines of the log. |

---

## For developers

**Run the tests** (offline, no API key needed):

```powershell
.\.venv\Scripts\python -m pytest
```

**Change the model settings** with environment variables:

| Variable | Default | What it controls |
|---|---|---|
| `BRD_AGENT_MODEL` | `claude-opus-5` | Main model |
| `BRD_AGENT_FILTER_MODEL` | `claude-haiku-4-5` | Email-filter model |
| `BRD_AGENT_EFFORT` | `high` | Effort level: `low` to `max` |

**How it works**
1. **Load:** read the files, redact secrets and flag suspicious instructions.
2. **Filter emails:** keep emails that name the project, and ask a small model about the rest.
3. **Extract:** Claude pulls out the requirements, conflicts and open questions in one pass,
   and the project context (scope, stakeholders, risks and so on) in a second pass.
4. **Validate:** code checks the IDs, sources, confidence, conflicts, duplicates and secrets,
   and downgrades anything unsafe.
5. **Write:** Claude writes the prose. The requirement tables come straight from the checked
   data.
6. **Save:** write the Word files, the change log and the audit log.

**Code map**

| Path | What it does |
|---|---|
| `brd_agent/loaders/` | Reads notes and emails |
| `brd_agent/redact.py` | Secret redaction and injection flagging |
| `brd_agent/pipeline.py` | The AI calls |
| `brd_agent/prompts.py` | The prompts |
| `brd_agent/validate.py` | The checks |
| `brd_agent/audit.py` | Versions, the change log and the audit log |
| `brd_agent/render_docx.py` | Builds the Word documents |

**The sample project** (`samples/`) contains planted test cases:
- two contradictions, which should appear as `CONFLICT`
- a project email that never names "Phoenix", which should be kept
- two unrelated emails, which should be skipped
- an email with a fake API key, a password and an injection line, which should be redacted
  and flagged
