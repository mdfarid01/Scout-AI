# The four LLM agents. Each is one structured call; the pipeline chains them.
from datetime import date, timedelta

from config import FUNDING_WINDOW_DAYS, PROFILE_DIR
from llm import research_call, structured_call
from models import CompanyResearch, DiscoveryResult, MatchResult, OutreachDrafts, Startup


def load_profile() -> str:
    """Concatenate all profile files (resume.md, preferences.md, ...)."""
    parts = []
    for f in sorted(PROFILE_DIR.glob("*.md")):
        parts.append(f"## {f.name}\n\n{f.read_text()}")
    if not parts:
        raise SystemExit(
            f"No profile found. Add your resume/preferences as .md files in {PROFILE_DIR}/"
        )
    return "\n\n".join(parts)


# ── 1. Discovery ──────────────────────────────────────────────────────────
def discover(known: set[str], today: date) -> list[Startup]:
    since = today - timedelta(days=FUNDING_WINDOW_DAYS)
    prompt = f"""Today is {today.isoformat()}. Find startups that raised funding between \
{since.isoformat()} and today.

Search funding-news sources such as TechCrunch, Entrackr, Inc42, YourStory, \
Crunchbase news, and Product Hunt launches with funding mentions. Focus on \
early-stage rounds (Pre-seed through Series B) at companies likely to be hiring \
engineers.

For each startup found, capture: company name, website, funding stage, amount, \
date, source URL, and a one-line description of what they build. Only include \
companies where you found a real funding announcement — do not invent entries. \
Aim for 10-25 startups."""
    result = structured_call(
        prompt,
        DiscoveryResult,
        system="You are a startup-funding research agent. Be factual; every entry must trace to a source you actually read.",
        use_web=True,
        max_tokens=8000,
    )
    # Drop anything we've already seen (agent-side dedup happens in db too).
    from db import normalize_name
    return [s for s in result.startups if normalize_name(s.company) not in known]


# ── 2. Company + founder research ─────────────────────────────────────────
def research(startup: Startup) -> CompanyResearch:
    prompt = f"""Research this recently funded startup in depth:

Company: {startup.company}
Website: {startup.website or "unknown — find it"}
Funding: {startup.funding_stage or "?"} {startup.funding_amount or ""} ({startup.funding_date or "recent"})
What they do: {startup.one_liner or "unknown"}

Visit their website (about, careers, blog, docs) and search for:
1. What they build and for whom.
2. Their tech stack (from job posts, docs, engineering blog, GitHub).
3. Open engineering roles.
4. Founders and CTO: names, backgrounds, LinkedIn URLs, recent public posts or interviews.
5. Hiring signals: careers page activity, job posts, "we're hiring" mentions, team growth.

Write up your findings as concise markdown notes covering every numbered point, \
with the concrete facts and URLs you found. Include an estimated hiring \
probability (0-100) that they are actively hiring engineers right now, based \
only on evidence you found."""
    # Phase 1: web research → free-form notes. Phase 2: notes → schema.
    notes = research_call(
        prompt,
        system="You are a company research agent. Report only what you verified from sources; say 'unknown' rather than guessing.",
    )
    return structured_call(
        f"Convert these research notes into the structured schema. Use only "
        f"facts present in the notes; leave fields empty if not covered.\n\n"
        f"# Research notes on {startup.company}\n\n{notes}",
        CompanyResearch,
        max_tokens=8000,  # notes are dense; 4000 truncates the JSON
    )


# ── 3. Resume matching ────────────────────────────────────────────────────
def match(startup: Startup, res: CompanyResearch, profile: str) -> MatchResult:
    prompt = f"""Score how well this candidate fits this startup.

# Candidate profile
{profile}

# Company
{startup.company} — {res.summary}
Tech stack: {", ".join(res.tech_stack) or "unknown"}
Open roles: {", ".join(res.open_roles) or "none listed"}
Hiring probability: {res.hiring_probability}%

Compare required skills vs. the candidate's skills. Be honest about gaps — an \
inflated score wastes everyone's time. Identify the single strongest pitch angle: \
the most compelling, specific reason this candidate is valuable to THIS company."""
    return structured_call(prompt, MatchResult, max_tokens=8000)


# ── 4. Outreach drafting ──────────────────────────────────────────────────
def draft_outreach(
    startup: Startup, res: CompanyResearch, m: MatchResult, profile: str
) -> OutreachDrafts:
    founders = "\n".join(
        f"- {f.name} ({f.role or '?'}): {f.background or ''} {f.recent_activity or ''}"
        for f in res.founders
    ) or "unknown"
    prompt = f"""Draft personalized outreach for this candidate to this startup.

# Candidate profile
{profile}

# Company
{startup.company} — {res.summary}
Funding: {startup.funding_stage or ""} {startup.funding_amount or ""}
Founders:
{founders}
Best role: {m.best_role or "general engineering"}
Pitch angle: {m.pitch_angle}
Strong points: {", ".join(m.strong_points)}

Requirements:
- Email: short (under 150 words), specific to this company, references something \
real (their product, a founder's recent post, the funding round). No generic flattery.
- LinkedIn message: under 300 characters, personal, no pitch-slap.
- Cover letter: concise, tailored, leads with the pitch angle.
- resume_ordering: which of the candidate's projects to list first for this company.
- Address a specific founder if their name is known.

These are DRAFTS for human review — never claim they were sent."""
    return structured_call(prompt, OutreachDrafts, max_tokens=8000)
