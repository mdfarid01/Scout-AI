#!/usr/bin/env python3
# Pipeline orchestrator: discover → research → match → draft outreach.
# Stops at WAITING_APPROVAL — nothing external happens without `review.py`.
import argparse
import json
import sys
from datetime import date

import anthropic

import agents
import db
from llm import describe_error
from config import MAX_RESEARCH_PER_RUN, MIN_MATCH_SCORE
from llm import ModelRefusal
from models import CompanyResearch, MatchResult, Startup


class UsageLimit(Exception):
    pass


def _classify(e: Exception) -> None:
    """Re-raise usage-limit errors so stage loops stop instead of churning."""
    if isinstance(e, anthropic.APIStatusError) and e.status_code in (402, 429):
        raise UsageLimit(str(e)) from e


def run_discovery(conn):
    print("→ Discovery: searching for recently funded startups...")
    found = agents.discover(db.known_names(conn), date.today())
    added = 0
    for s in found:
        if db.upsert_discovered(conn, s.model_dump()) is not None:
            added += 1
            print(f"  + {s.company} ({s.funding_stage or '?'} {s.funding_amount or ''})")
    conn.commit()
    print(f"  {added} new startups (of {len(found)} found)")


def run_research(conn):
    rows = db.get_by_state(conn, "DISCOVERED")[:MAX_RESEARCH_PER_RUN]
    if not rows:
        print("→ Research: nothing to research")
        return
    for row in rows:
        startup = Startup.model_validate(json.loads(row["discovery_json"]))
        print(f"→ Researching {startup.company}...")
        try:
            res = agents.research(startup)
        except ModelRefusal:
            # Safety classifiers decline some domains (e.g. biotech) — skip
            # permanently instead of retrying every run.
            db.set_stage(conn, row["id"], "SKIPPED", note="research refused by model")
            conn.commit()
            print("  refused by safety classifiers — skipped")
            continue
        except Exception as e:
            _classify(e)
            print(f"  ! research failed:\n{describe_error(e)}", file=sys.stderr)
            continue
        db.set_stage(conn, row["id"], "RESEARCHED", "research_json", res.model_dump())
        conn.commit()
        print(f"  hiring probability: {res.hiring_probability}%, "
              f"roles: {', '.join(res.open_roles) or 'none listed'}")


def run_matching(conn, profile: str):
    rows = db.get_by_state(conn, "RESEARCHED")
    for row in rows:
        startup = Startup.model_validate(json.loads(row["discovery_json"]))
        res = CompanyResearch.model_validate(json.loads(row["research_json"]))
        print(f"→ Matching {startup.company}...")
        try:
            m = agents.match(startup, res, profile)
        except Exception as e:
            _classify(e)
            print(f"  ! matching failed:\n{describe_error(e)}", file=sys.stderr)
            continue
        if m.match_score < MIN_MATCH_SCORE:
            db.set_stage(conn, row["id"], "SKIPPED", "match_json", m.model_dump(),
                         note=f"score {m.match_score} < {MIN_MATCH_SCORE}")
            print(f"  score {m.match_score} — below threshold, skipped")
        else:
            db.set_stage(conn, row["id"], "MATCHED", "match_json", m.model_dump())
            print(f"  score {m.match_score} — {m.pitch_angle}")
        conn.commit()


def run_outreach(conn, profile: str):
    rows = db.get_by_state(conn, "MATCHED")
    for row in rows:
        startup = Startup.model_validate(json.loads(row["discovery_json"]))
        res = CompanyResearch.model_validate(json.loads(row["research_json"]))
        m = MatchResult.model_validate(json.loads(row["match_json"]))
        print(f"→ Drafting outreach for {startup.company}...")
        try:
            drafts = agents.draft_outreach(startup, res, m, profile)
        except Exception as e:
            _classify(e)
            print(f"  ! drafting failed:\n{describe_error(e)}", file=sys.stderr)
            continue
        db.set_stage(conn, row["id"], "WAITING_APPROVAL", "outreach_json",
                     drafts.model_dump())
        conn.commit()
        print(f"  drafts ready → waiting for your approval")


def report(conn):
    counts = {r["state"]: r["n"] for r in conn.execute(
        "SELECT state, COUNT(*) n FROM companies GROUP BY state")}
    print("\n=== Today's report ===")
    for state in ("DISCOVERED", "RESEARCHED", "MATCHED", "WAITING_APPROVAL",
                  "APPROVED", "SENT", "SKIPPED", "REJECTED"):
        if counts.get(state):
            print(f"  {state:<18} {counts[state]}")
    waiting = counts.get("WAITING_APPROVAL", 0)
    if waiting:
        print(f"\n{waiting} draft(s) waiting — run: python review.py")


def main():
    p = argparse.ArgumentParser(description="Scout AI pipeline")
    p.add_argument("--stage", choices=["discover", "research", "match", "outreach", "all"],
                   default="all")
    args = p.parse_args()

    # Profile is only needed for matching/outreach — load lazily so
    # discovery/research can run before the user has written a resume.
    with db.get_db() as conn:
        try:
            if args.stage in ("discover", "all"):
                run_discovery(conn)
            if args.stage in ("research", "all"):
                run_research(conn)
            if args.stage in ("match", "outreach", "all"):
                profile = agents.load_profile()
            if args.stage in ("match", "all"):
                run_matching(conn, profile)
            if args.stage in ("outreach", "all"):
                run_outreach(conn, profile)
        except UsageLimit as e:
            print(f"\n! API usage limit reached — stopping. Re-run later.\n  {e}",
                  file=sys.stderr)
        report(conn)


if __name__ == "__main__":
    main()
