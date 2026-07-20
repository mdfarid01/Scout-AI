# Scout AI

Automated job-search pipeline: discovers newly funded startups, researches them,
scores your fit, and drafts personalized outreach — **but never sends anything
without your approval**.

```
discover → research → match → draft outreach → WAITING_APPROVAL
                                                    │
                                       you: approve / edit / reject
                                                    │
                                      approved drafts → outbox/ (you send)
```

## Setup

```bash
cd scout-ai
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
export ANTHROPIC_API_KEY=sk-ant-...   # or `ant auth login`
```

Add your profile (required):

```bash
# profile/resume.md       — your resume in markdown
# profile/preferences.md  — role/stack/location preferences
```

## Run

```bash
python pipeline.py            # full run: discover → research → match → draft
python pipeline.py --stage discover   # or run one stage at a time
python review.py              # review queue: approve / edit / reject drafts
python review.py --mark-sent 3        # after you sent company #3's email
```

Approved drafts land in `outbox/<company>/` as `email.txt`, `linkedin.txt`,
`cover_letter.txt`, and `resume_ordering.txt` — you send them yourself
(Gmail, LinkedIn). LinkedIn sending is deliberately manual: automating it
violates their Terms of Service.

## Daily schedule (optional)

```bash
crontab -e
# 8:00-ish AM daily (offset a few minutes):
7 8 * * * cd /path/to/scout-ai && .venv/bin/python pipeline.py >> logs/daily.log 2>&1
```

## Configuration

Environment variables (see `config.py`):

| Var | Default | Meaning |
|---|---|---|
| `SCOUT_MODEL` | `claude-opus-4-8` | Model for all agents |
| `SCOUT_FUNDING_WINDOW_DAYS` | `90` | How recent "recently funded" is |
| `SCOUT_MIN_MATCH_SCORE` | `60` | Below this → skipped |
| `SCOUT_MAX_RESEARCH_PER_RUN` | `10` | Caps token spend per run |

## Architecture

| File | Role |
|---|---|
| `agents.py` | 4 LLM agents: discovery, research (company+founder+hiring signals), matching, outreach drafting |
| `llm.py` | Anthropic API wrapper: structured outputs + web search/fetch tools, `pause_turn` handling |
| `models.py` | Pydantic schemas — also the structured-output contracts |
| `db.py` | SQLite state machine + dedup + activity log |
| `pipeline.py` | Orchestrator; stops at `WAITING_APPROVAL` |
| `review.py` | Human review CLI — the **only** path to `APPROVED` |

State machine: `DISCOVERED → RESEARCHED → MATCHED → WAITING_APPROVAL →
APPROVED → SENT` (with `SKIPPED` / `REJECTED` exits).

## Roadmap (not built yet, by design)

- Gmail API send after approval (currently manual from `outbox/`)
- Playwright form-fill that pauses before submit
- Follow-up scheduling (`FOLLOW_UP` state + reminder)
- Web dashboard replacing the CLI review queue
