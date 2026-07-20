#!/usr/bin/env python3
# Human review queue. The only place drafts can be approved.
# Approving does NOT send anything — it exports the drafts to files you
# send yourself (Gmail API / browser automation are deliberate follow-ups).
import json
import subprocess
import sys
from pathlib import Path

import db
from config import ROOT
from models import CompanyResearch, MatchResult, OutreachDrafts, Startup

OUTBOX = ROOT / "outbox"


def show(row) -> None:
    startup = Startup.model_validate(json.loads(row["discovery_json"]))
    res = CompanyResearch.model_validate(json.loads(row["research_json"]))
    m = MatchResult.model_validate(json.loads(row["match_json"]))
    d = OutreachDrafts.model_validate(json.loads(row["outreach_json"]))

    print("=" * 70)
    print(f"[{row['id']}] {startup.company}  —  match {m.match_score}%, "
          f"hiring {res.hiring_probability}%")
    print(f"    {res.summary}")
    print(f"    Funding: {startup.funding_stage or '?'} {startup.funding_amount or ''}")
    print(f"    Best role: {m.best_role or '-'}")
    print(f"    Pitch: {m.pitch_angle}")
    if d.recipient_name:
        print(f"    To: {d.recipient_name} <{d.recipient_email or 'email unknown'}>")
    print("-" * 70)
    print(f"EMAIL — {d.email_subject}\n\n{d.email_body}")
    print("-" * 70)
    print(f"LINKEDIN\n\n{d.linkedin_message}")
    print("-" * 70)
    print(f"COVER LETTER\n\n{d.cover_letter}")
    print("-" * 70)
    print(f"Resume ordering: {', '.join(d.resume_ordering)}")
    print("=" * 70)


def export(row) -> Path:
    """Write approved drafts to outbox/<company>/ for manual sending."""
    startup = Startup.model_validate(json.loads(row["discovery_json"]))
    d = OutreachDrafts.model_validate(json.loads(row["outreach_json"]))
    safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in startup.company)
    folder = OUTBOX / safe
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "email.txt").write_text(
        f"To: {d.recipient_name or ''} <{d.recipient_email or ''}>\n"
        f"Subject: {d.email_subject}\n\n{d.email_body}\n")
    (folder / "linkedin.txt").write_text(d.linkedin_message + "\n")
    (folder / "cover_letter.txt").write_text(d.cover_letter + "\n")
    (folder / "resume_ordering.txt").write_text("\n".join(d.resume_ordering) + "\n")
    return folder


def edit_drafts(row) -> None:
    """Open the outreach JSON in $EDITOR, save back if valid."""
    import os
    import tempfile
    editor = os.environ.get("EDITOR", "nano")
    with tempfile.NamedTemporaryFile("w+", suffix=".json", delete=False) as f:
        json.dump(json.loads(row["outreach_json"]), f, indent=2)
        path = f.name
    subprocess.call([editor, path])
    try:
        edited = OutreachDrafts.model_validate(json.load(open(path)))
    except Exception as e:
        print(f"  ! invalid edit, keeping original: {e}")
        return
    with db.get_db() as conn:
        db.set_stage(conn, row["id"], "WAITING_APPROVAL", "outreach_json",
                     edited.model_dump(), note="edited by human")
    print("  saved.")


def main():
    with db.get_db() as conn:
        rows = db.get_by_state(conn, "WAITING_APPROVAL")
    if not rows:
        print("Nothing waiting for approval.")
        return

    print(f"{len(rows)} draft(s) waiting for review.\n")
    for row in rows:
        show(row)
        while True:
            choice = input("[a]pprove  [e]dit  [r]eject  [s]kip  [q]uit > ").strip().lower()
            if choice == "a":
                folder = export(row)
                with db.get_db() as conn:
                    db.set_stage(conn, row["id"], "APPROVED", note="approved by human")
                print(f"  approved → drafts exported to {folder}/")
                print("  (send them yourself, then mark sent with: "
                      f"python review.py --mark-sent {row['id']})")
                break
            elif choice == "e":
                edit_drafts(row)
                with db.get_db() as conn:
                    row = db.get_company(conn, row["id"])
                show(row)
            elif choice == "r":
                note = input("  reason (optional): ").strip() or None
                with db.get_db() as conn:
                    db.set_stage(conn, row["id"], "REJECTED", note=note)
                print("  rejected.")
                break
            elif choice == "s":
                break
            elif choice == "q":
                return


if __name__ == "__main__":
    if len(sys.argv) == 3 and sys.argv[1] == "--mark-sent":
        with db.get_db() as conn:
            db.set_stage(conn, int(sys.argv[2]), "SENT", note="sent manually")
        print("marked as sent.")
    else:
        main()
