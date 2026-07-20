# Pydantic models — these double as structured-output schemas for the API
# (client.messages.parse validates responses against them).
from typing import Optional
from pydantic import BaseModel, Field


# ── Workflow state machine ────────────────────────────────────────────────
# DISCOVERED → RESEARCHED → MATCHED → OUTREACH_READY → WAITING_APPROVAL
#   → APPROVED → SENT   (or REJECTED / SKIPPED at any review point)
STATES = [
    "DISCOVERED",
    "RESEARCHED",
    "MATCHED",
    "OUTREACH_READY",
    "WAITING_APPROVAL",
    "APPROVED",
    "SENT",
    "REJECTED",
    "SKIPPED",
]


# ── Discovery ─────────────────────────────────────────────────────────────
class Startup(BaseModel):
    company: str
    website: Optional[str] = None
    funding_stage: Optional[str] = Field(None, description="e.g. Seed, Series A")
    funding_amount: Optional[str] = Field(None, description="e.g. $2M")
    funding_date: Optional[str] = Field(None, description="ISO date if known")
    source_url: Optional[str] = Field(None, description="Where this was found")
    one_liner: Optional[str] = Field(None, description="What the company does")


class DiscoveryResult(BaseModel):
    startups: list[Startup]


# ── Research ──────────────────────────────────────────────────────────────
class FounderInfo(BaseModel):
    name: str
    role: Optional[str] = None
    background: Optional[str] = None
    linkedin_url: Optional[str] = None
    recent_activity: Optional[str] = Field(
        None, description="Recent posts, interviews, or public statements"
    )


class CompanyResearch(BaseModel):
    summary: str = Field(description="What the company builds and for whom")
    tech_stack: list[str] = Field(default_factory=list)
    open_roles: list[str] = Field(default_factory=list)
    founders: list[FounderInfo] = Field(default_factory=list)
    hiring_signals: list[str] = Field(
        default_factory=list,
        description="Evidence of hiring: careers page, job posts, funding, team growth",
    )
    hiring_probability: int = Field(
        ge=0, le=100, description="0-100 estimate that they're hiring engineers now"
    )
    sources: list[str] = Field(default_factory=list, description="URLs consulted")


# ── Matching ──────────────────────────────────────────────────────────────
class MatchResult(BaseModel):
    match_score: int = Field(ge=0, le=100)
    strong_points: list[str] = Field(description="Where the candidate fits well")
    weak_points: list[str] = Field(description="Gaps vs. what the company needs")
    best_role: Optional[str] = Field(None, description="Most fitting open role, if any")
    pitch_angle: str = Field(
        description="The single strongest angle to lead with in outreach"
    )
    reasoning: str


# ── Outreach ──────────────────────────────────────────────────────────────
class OutreachDrafts(BaseModel):
    email_subject: str
    email_body: str
    linkedin_message: str = Field(description="Short connection-request note, <300 chars")
    cover_letter: str
    resume_ordering: list[str] = Field(
        description="Recommended order of resume projects/sections for this company"
    )
    recipient_name: Optional[str] = Field(None, description="Who to address")
    recipient_email: Optional[str] = Field(None, description="If found publicly")
