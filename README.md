# Scout AI — Project Overview

*Last updated: 2026-07-21*

## 1. What this project does

Scout AI is a personal job-search copilot focused on **freshly funded startups** — companies that just raised money and are therefore likely to be hiring. Every run, it searches funding news for new startups, researches each one in depth (product, tech stack, founders, open roles), scores how well the company fits *your* resume and preferences, and writes personalized outreach drafts (email, LinkedIn note, cover letter). The core principle is **"AI prepares, human approves"**: the system never contacts anyone on its own. Every draft stops in a review queue, and only after you click Approve does anything get exported — and even then, *you* do the actual sending.

## 2. Tech stack

| Technology | Role / why it's used |
|---|---|
| **Python 3.13** | The whole backend. Single language keeps a one-person project simple. |
| **anthropic SDK** | Official client library for talking to the Claude AI model that powers all four agents. |
| **pydantic** | Defines the exact "shape" of data each agent must return (e.g. a match score must be a number 0–100). Catches malformed AI output before it reaches the database. |
| **tavily-python** | Client for the Tavily web-search service — how agents search the web and read pages. |
| **fastapi** | The web framework serving the dashboard and its JSON API. |
| **uvicorn** | The server program that actually runs the FastAPI app on localhost. |
| **python-dotenv** | Loads API keys from the `.env` file at startup, so no manual `export` commands. |
| **sqlite3** (built into Python) | The database driver. No extra install needed. |
| **jinja2** | Installed as a dependency; the dashboard currently serves one inline HTML page and doesn't use templates. |

**Database — SQLite** at `data/scout.db`. One file, zero setup, no server process. Chosen because this is a single-user local tool: a "real" database (Postgres etc.) would add installation and maintenance for no benefit at this scale.

**Frontend — plain HTML/CSS/JavaScript**, served as one inline page by FastAPI. No React, no build step, no `npm install`. The UI is a single screen with a handful of interactions; a framework would multiply the moving parts without adding capability. Open `http://localhost:8000` and it just works.

**`.env` file** (project root) holds the secrets: `ANTHROPIC_API_KEY`, `ANTHROPIC_BASE_URL`, and `TAVILY_API_KEY`. It's listed in `.gitignore` so keys can never be accidentally committed to version control.

## 3. APIs / external services

**Anthropic API (via the aerolink proxy).** All AI reasoning — research summaries, match scoring, outreach writing — runs on the Claude model `claude-sonnet-5` (with "thinking" disabled to control cost). Requests don't go to Anthropic directly: they're routed through a third-party proxy at `capi.aerolink.lat` (set via `ANTHROPIC_BASE_URL`). That routing has a real architectural consequence: Anthropic's built-in *server-side* web-search tools don't execute through this proxy, so the project implements its own **client-side tool loop** — the model asks for a search, our code performs it with Tavily, and the results are fed back to the model. That loop lives in `llm.py`.

**Tavily API.** A web-search-for-AI service used for two things: `search()` (returns top results with title, URL, and snippet) and `extract()` (fetches a page and returns its text, truncated to ~3,000 characters to control cost). The key lives in `.env` as `TAVILY_API_KEY`; the dev-tier free plan provides a monthly credit allowance (about 1,000 credits — one search or extract each consumes credits), which comfortably covers a few dozen company research runs per month.

## 4. The agents

All four agents live in `agents.py`. Each is one job with a strict, pydantic-validated output. "Web tools" means the Tavily-backed search/fetch loop described above.

### 4.1 Discovery
- **Purpose:** find startups funded in the last 90 days.
- **Input:** today's date and the list of companies already in the database (so it skips known ones).
- **Web use:** searches funding-news sources (TechCrunch, Crunchbase news, Entrackr, etc.) for recent seed–Series B rounds.
- **Output (per startup):** company name, website, funding stage, amount raised, funding date, the source URL it was found at, and a one-line description.
- **Cost:** runs **once per pipeline run** (not per company) — one model conversation with a handful of search rounds; typically finds 10–25 startups in a single pass.

### 4.2 Company + Founder Research
- **Purpose:** deep-dive a single startup: what they build, who runs it, are they hiring.
- **Input:** one discovered startup (name, website, funding details).
- **Web use:** heavy — visits the company site, careers page, job boards, LinkedIn, founder pages; two-phase design: first a research pass that produces free-form notes, then a second cheap call converts the notes into structured fields.
- **Output:** summary of the business, tech stack list, open roles, founders (name, role, background, LinkedIn, recent public activity), concrete hiring signals, a 0–100 hiring probability, and every source URL consulted.
- **Cost:** the most expensive agent — observed ~5–7 model calls and ~15–20 Tavily operations (e.g. 8 searches + 12 fetches for one company), capped at 6 tool rounds.

### 4.3 Resume Matching
- **Purpose:** honestly score how well *you* fit one researched company.
- **Input:** your profile (`profile/resume.md` + `preferences.md`) plus that company's research.
- **Web use:** none — pure reasoning over text it already has.
- **Output:** match score (0–100), strong points, weak points/gaps, best-fitting open role, a single strongest "pitch angle" to lead with, and the reasoning.
- **Cost:** 1 model call per company, no search credits.

### 4.4 Outreach Drafting
- **Purpose:** write personalized application material for one matched company.
- **Input:** your profile, the company research, and the match result (especially the pitch angle).
- **Web use:** none.
- **Output:** email subject + body (under 150 words, referencing something real about the company), a LinkedIn connection note (≤300 characters), a cover letter, a recommended ordering of your resume projects for this company, and — when found — the recipient's name/email.
- **Cost:** 1 model call per company, no search credits.

## 5. The pipeline / state machine

Every company is a row in the database with a `state` column. The states move strictly forward:

```text
DISCOVERED ──► RESEARCHED ──► MATCHED ──► WAITING_APPROVAL ──► APPROVED ──► SENT
     │              │             │                │
 (dedup blocks      │             │                └──► REJECTED   (you click Reject)
  re-insertion)     │             └──► SKIPPED   (match score below threshold, default 60)
                    └────────────────► SKIPPED   (research refused by AI safety filters)
```

| Transition | Triggered by |
|---|---|
| *(new)* → DISCOVERED | Discovery agent (`pipeline.py --stage discover` or the Run Scout button). Duplicate names are normalized (e.g. "XYZ AI Inc" = "xyz-ai") and silently dropped. |
| DISCOVERED → RESEARCHED | Research agent, capped at N companies per run (default 10). |
| RESEARCHED → MATCHED / SKIPPED | Matching agent; scores below the threshold go to SKIPPED with a note. |
| MATCHED → WAITING_APPROVAL | Outreach agent finishes the drafts. **The pipeline stops here — always.** |
| WAITING_APPROVAL → APPROVED / REJECTED | **You**, via `review.py` or the dashboard's Approve/Reject buttons. Approving exports the drafts to `outbox/<company>/`. |
| APPROVED → SENT | You, after actually sending the material yourself (`python review.py --mark-sent <id>`). |

## 6. How it's run

**CLI path**
- `python pipeline.py` — full run (discover → research → match → outreach), or `--stage discover|research|match|outreach` for one stage. Cost knobs via env vars: `SCOUT_MAX_RESEARCH_PER_RUN`, `SCOUT_MIN_MATCH_SCORE`.
- `python review.py` — terminal review queue: view each draft, approve / edit / reject; `--mark-sent <id>` after you've sent.

**Web UI path** — `python web.py`, then open `http://localhost:8000`:
- **State tabs** (sidebar): browse companies by pipeline state; click a card for full research, founders, hiring signals, and the editable "Dispatch Tray" (email / LinkedIn / cover letter) with Approve & Dispatch / Reject.
- **Profile & Prefs tab:** edit `profile/resume.md` and `preferences.md` directly in the browser.
- **Run Control ("Expedition" bar):** set max-research and min-match for this run, click **Run Scout** to launch the full pipeline in the background, and watch its live progress in the Dispatch Log panel. Nothing runs automatically on page load.

Both paths call the **same** underlying code (`db.py`, `agents.py`, `pipeline.py` stage functions) — the dashboard is a skin over the CLI, not a fork of it.

## 7. Files map

| File | Purpose |
|---|---|
| `config.py` | Settings: model name, DB path, cost limits, profile dir; loads `.env` first. |
| `models.py` | Pydantic data shapes for all agent outputs + the list of pipeline states. |
| `db.py` | SQLite persistence: schema, state transitions, name-dedup, activity log. |
| `llm.py` | Anthropic client wrapper: client-side Tavily tool loop, schema-validated output, transient-error retry (5s/15s/30s backoff), detailed error reporting. |
| `agents.py` | The four agents (prompts + calls) and profile loading. |
| `pipeline.py` | Orchestrates the stages in order; stops at WAITING_APPROVAL; CLI entry point. |
| `review.py` | Terminal approval queue + `outbox/` export (the only path to APPROVED, shared with the dashboard). |
| `web.py` | FastAPI dashboard: field-log UI, JSON API, profile editor, background pipeline runs with live log. |

## 8. Known limitations / not yet built

- **No automatic sending.** Email and LinkedIn outreach are exported to `outbox/` for you to send manually — by design. LinkedIn automation violates their Terms of Service, and auto-emailing removes the human safety check this project is built around.
- **Matching is a blunt cutoff.** One 0–100 score against a fixed threshold decides skip-or-continue; there's no weighting by role type, location, or sector. Discovery likewise searches funding news broadly and isn't yet biased toward your stated preferences (e.g. backend roles, India/remote), so some researched companies were never realistic fits.
- **The aerolink proxy is occasionally unstable** — intermittent 503 "no healthy upstream" errors and dropped connections. `llm.py` retries these three times with increasing waits, which papers over brief blips but can't fix a longer outage; a failed company simply stays in its current state for the next run.
- **Single-user by design.** One local SQLite file, no accounts, no authentication on the dashboard — don't expose port 8000 beyond your own machine.
- **Other gaps:** no follow-up scheduling (the planned FOLLOW_UP state), no per-company resume PDF generation, and `profile/README.md` (the template instructions file) is currently concatenated into your profile alongside your real resume when agents read it.
