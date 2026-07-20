# SQLite persistence — companies table drives the workflow state machine.
# JSON columns hold the full structured agent outputs so nothing is lost.
import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone

from config import DB_PATH

SCHEMA = """
CREATE TABLE IF NOT EXISTS companies (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    name_key TEXT NOT NULL UNIQUE,      -- normalized name for dedup
    website TEXT,
    state TEXT NOT NULL DEFAULT 'DISCOVERED',
    discovery_json TEXT,                -- Startup
    research_json TEXT,                 -- CompanyResearch
    match_json TEXT,                    -- MatchResult
    outreach_json TEXT,                 -- OutreachDrafts
    review_note TEXT,                   -- human note on approve/reject/edit
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS activities (
    id INTEGER PRIMARY KEY,
    company_id INTEGER NOT NULL REFERENCES companies(id),
    event TEXT NOT NULL,                -- e.g. state:RESEARCHED, approved, sent:email
    detail TEXT,
    created_at TEXT NOT NULL
);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def normalize_name(name: str) -> str:
    """Dedup key: lowercase, treat punctuation as spaces, strip common suffixes."""
    key = "".join(c if c.isalnum() else " " for c in name.lower()).strip()
    words = key.split()
    while words and words[-1] in ("inc", "labs", "ai", "technologies", "tech", "hq", "io"):
        words.pop()
    # If stripping suffixes removed everything (e.g. company named "AI Labs"), keep original words.
    return "".join(words) or key.replace(" ", "")


@contextmanager
def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def upsert_discovered(conn, startup_dict: dict) -> int | None:
    """Insert a discovered startup. Returns row id, or None if duplicate."""
    key = normalize_name(startup_dict["company"])
    if not key:
        return None
    existing = conn.execute(
        "SELECT id FROM companies WHERE name_key = ?", (key,)
    ).fetchone()
    if existing:
        return None
    now = _now()
    cur = conn.execute(
        "INSERT INTO companies (name, name_key, website, state, discovery_json, created_at, updated_at)"
        " VALUES (?, ?, ?, 'DISCOVERED', ?, ?, ?)",
        (startup_dict["company"], key, startup_dict.get("website"),
         json.dumps(startup_dict), now, now),
    )
    log_activity(conn, cur.lastrowid, "state:DISCOVERED", startup_dict.get("source_url"))
    return cur.lastrowid


def set_stage(conn, company_id: int, state: str, column: str | None = None,
              payload: dict | None = None, note: str | None = None):
    """Advance a company to `state`, optionally storing a JSON payload."""
    sets = ["state = ?", "updated_at = ?"]
    args: list = [state, _now()]
    if column and payload is not None:
        sets.append(f"{column} = ?")
        args.append(json.dumps(payload))
    if note is not None:
        sets.append("review_note = ?")
        args.append(note)
    args.append(company_id)
    conn.execute(f"UPDATE companies SET {', '.join(sets)} WHERE id = ?", args)
    log_activity(conn, company_id, f"state:{state}", note)


def log_activity(conn, company_id: int, event: str, detail: str | None = None):
    conn.execute(
        "INSERT INTO activities (company_id, event, detail, created_at) VALUES (?, ?, ?, ?)",
        (company_id, event, detail, _now()),
    )


def get_by_state(conn, *states: str) -> list[sqlite3.Row]:
    q = ",".join("?" for _ in states)
    return conn.execute(
        f"SELECT * FROM companies WHERE state IN ({q}) ORDER BY updated_at", states
    ).fetchall()


def get_company(conn, company_id: int) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT * FROM companies WHERE id = ?", (company_id,)
    ).fetchone()


def known_names(conn) -> set[str]:
    return {r["name_key"] for r in conn.execute("SELECT name_key FROM companies")}
