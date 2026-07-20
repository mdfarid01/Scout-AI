#!/usr/bin/env python3
# Scout AI review dashboard — FastAPI, single inline page, no build step.
# Reuses db.py for all persistence and review.py's export for approvals.
import json

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel

import db
import review
from models import OutreachDrafts

app = FastAPI(title="Scout AI")

STATES = ["WAITING_APPROVAL", "MATCHED", "RESEARCHED", "DISCOVERED", "SKIPPED", "SENT",
          "APPROVED", "REJECTED"]

SIDEBAR_STATES = [
    ("WAITING_APPROVAL", "Waiting Approval"),
    ("MATCHED", "Matched"),
    ("RESEARCHED", "Researched"),
    ("DISCOVERED", "Discovered"),
    ("SKIPPED", "Skipped"),
    ("SENT", "Sent"),
]


def _row_summary(row) -> dict:
    disc = json.loads(row["discovery_json"] or "{}")
    res = json.loads(row["research_json"] or "{}")
    match = json.loads(row["match_json"] or "{}")
    return {
        "id": row["id"],
        "name": row["name"],
        "state": row["state"],
        "website": row["website"],
        "funding_stage": disc.get("funding_stage"),
        "funding_amount": disc.get("funding_amount"),
        "one_liner": disc.get("one_liner") or (res.get("summary") or "")[:140],
        "match_score": match.get("match_score"),
        "hiring_probability": res.get("hiring_probability"),
        "updated_at": row["updated_at"],
    }


@app.get("/api/companies")
def list_companies(state: str = "WAITING_APPROVAL"):
    if state not in STATES:
        raise HTTPException(400, f"unknown state {state}")
    with db.get_db() as conn:
        rows = db.get_by_state(conn, state)
        counts = {r["state"]: r["n"] for r in conn.execute(
            "SELECT state, COUNT(*) n FROM companies GROUP BY state")}
    return {"companies": [_row_summary(r) for r in rows],
            "counts": counts}


@app.get("/api/companies/{company_id}")
def company_detail(company_id: int):
    with db.get_db() as conn:
        row = db.get_company(conn, company_id)
    if row is None:
        raise HTTPException(404, "not found")
    return {
        **_row_summary(row),
        "research": json.loads(row["research_json"] or "null"),
        "match": json.loads(row["match_json"] or "null"),
        "outreach": json.loads(row["outreach_json"] or "null"),
        "review_note": row["review_note"],
    }


@app.post("/api/companies/{company_id}/approve")
def approve(company_id: int):
    with db.get_db() as conn:
        row = db.get_company(conn, company_id)
        if row is None:
            raise HTTPException(404, "not found")
        if row["state"] != "WAITING_APPROVAL":
            raise HTTPException(409, f"cannot approve from state {row['state']}")
        folder = review.export(row)
        db.set_stage(conn, company_id, "APPROVED", note="approved via dashboard")
    return {"ok": True, "outbox": str(folder)}


@app.post("/api/companies/{company_id}/reject")
def reject(company_id: int):
    with db.get_db() as conn:
        row = db.get_company(conn, company_id)
        if row is None:
            raise HTTPException(404, "not found")
        if row["state"] != "WAITING_APPROVAL":
            raise HTTPException(409, f"cannot reject from state {row['state']}")
        db.set_stage(conn, company_id, "REJECTED", note="rejected via dashboard")
    return {"ok": True}


class EditPayload(BaseModel):
    email_subject: str | None = None
    email_body: str | None = None
    linkedin_message: str | None = None
    cover_letter: str | None = None


@app.post("/api/companies/{company_id}/edit")
def edit(company_id: int, payload: EditPayload):
    with db.get_db() as conn:
        row = db.get_company(conn, company_id)
        if row is None or not row["outreach_json"]:
            raise HTTPException(404, "no drafts to edit")
        drafts = OutreachDrafts.model_validate(json.loads(row["outreach_json"]))
        for field, value in payload.model_dump(exclude_none=True).items():
            setattr(drafts, field, value)
        db.set_stage(conn, company_id, row["state"], "outreach_json",
                     drafts.model_dump(), note="edited via dashboard")
    return {"ok": True}


@app.get("/", response_class=HTMLResponse)
def index():
    return PAGE


PAGE = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Scout AI — Field Log</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Fraunces:opsz,wght@9..144,600;9..144,700&family=IBM+Plex+Mono:wght@400;500&family=IBM+Plex+Sans:wght@400;500;600&display=swap" rel="stylesheet">
<style>
:root {
  --bg-pine: #0F231C;
  --panel-moss: #1B3328;
  --paper: #F3EEDD;
  --ink: #24301F;
  --signal-amber: #D9932F;
  --trail-moss: #7A9B6E;
  --passed-red: #A65D4B;
}
* { margin: 0; padding: 0; box-sizing: border-box; }
html, body { height: 100%; }
body {
  background: var(--bg-pine);
  color: var(--paper);
  font-family: "IBM Plex Sans", sans-serif;
  font-size: 14px;
  display: flex;
}

/* ── Sidebar ─────────────────────────────────────────── */
nav {
  width: 220px; min-width: 220px;
  background: var(--panel-moss);
  padding: 20px 0;
  display: flex; flex-direction: column;
  border-right: 1px solid rgba(122,155,110,.25);
}
nav h1 {
  font-family: "Fraunces", serif;
  font-size: 20px; font-weight: 700;
  padding: 0 18px 4px;
  color: var(--paper);
}
nav .sub {
  font-family: "IBM Plex Mono", monospace;
  font-size: 10px; letter-spacing: .14em; text-transform: uppercase;
  color: var(--trail-moss);
  padding: 0 18px 18px;
  border-bottom: 1px solid rgba(122,155,110,.25);
  margin-bottom: 10px;
}
nav button.tab {
  display: flex; justify-content: space-between; align-items: center;
  width: 100%;
  background: none; border: none; cursor: pointer;
  color: var(--paper);
  font-family: inherit; font-size: 13px; text-align: left;
  padding: 9px 18px 9px 15px;
  border-left: 3px solid transparent;
  opacity: .75;
}
nav button.tab:hover { opacity: 1; background: rgba(122,155,110,.08); }
nav button.tab.active {
  opacity: 1;
  border-left-color: var(--paper);
  background: rgba(243,238,221,.05);
}
nav .count {
  font-family: "IBM Plex Mono", monospace;
  font-size: 11px; font-weight: 500;
  color: var(--signal-amber);
  min-width: 20px; text-align: right;
}
nav .foot {
  margin-top: auto;
  padding: 14px 18px 0;
  font-family: "IBM Plex Mono", monospace;
  font-size: 10px; color: var(--trail-moss);
  border-top: 1px solid rgba(122,155,110,.25);
}

/* ── Main area ───────────────────────────────────────── */
main { flex: 1; overflow-y: auto; padding: 26px 30px; }
.log-head {
  display: flex; align-items: baseline; gap: 14px;
  margin-bottom: 18px;
}
.log-head h2 { font-family: "Fraunces", serif; font-size: 28px; font-weight: 600; }
.log-head .meta { font-family: "IBM Plex Mono", monospace; font-size: 11px; color: var(--trail-moss); }

.cards { display: grid; grid-template-columns: repeat(auto-fill, minmax(320px, 1fr)); gap: 14px; }

.card {
  position: relative;
  background: var(--paper);
  color: var(--ink);
  padding: 14px 16px 12px;
  cursor: pointer;
  border-left: 4px solid var(--trail-moss);
  box-shadow: 0 1px 0 rgba(0,0,0,.35);
  overflow: hidden;
}
.card:hover { border-left-color: var(--signal-amber); }
.card h3 { font-family: "Fraunces", serif; font-size: 19px; font-weight: 600; margin-bottom: 2px; }
.card .fund {
  font-family: "IBM Plex Mono", monospace;
  font-size: 10.5px; color: #5c6b52; margin-bottom: 6px;
}
.card p.line {
  font-size: 12.5px; line-height: 1.45; color: #3a4732;
  display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical;
  overflow: hidden; min-height: 2.9em; margin-bottom: 9px;
}

/* signal-strength meter: thin track, amber fill, mono readout */
.signal { display: flex; align-items: center; gap: 8px; }
.signal .track {
  flex: 1; height: 6px;
  background: rgba(36,48,31,.14);
}
.signal .fill { height: 100%; background: var(--signal-amber); }
.signal .num {
  font-family: "IBM Plex Mono", monospace;
  font-size: 12px; font-weight: 500; color: var(--ink);
  min-width: 42px; text-align: right;
}
.signal .lbl {
  font-family: "IBM Plex Mono", monospace;
  font-size: 9.5px; text-transform: uppercase; letter-spacing: .1em;
  color: #5c6b52; min-width: 38px;
}

.empty {
  font-family: "IBM Plex Mono", monospace;
  color: var(--trail-moss); font-size: 12px;
  padding: 40px 0; text-align: center;
}

/* ── Stamps ──────────────────────────────────────────── */
.stamp {
  position: absolute; top: 50%; left: 50%;
  font-family: "Fraunces", serif; font-weight: 700;
  font-size: 30px; letter-spacing: .08em;
  padding: 2px 14px;
  border: 3px double currentColor;
  pointer-events: none;
  mix-blend-mode: multiply;
  /* distressed edge */
  -webkit-mask-image: url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg'%3E%3Cfilter id='n'%3E%3CfeTurbulence baseFrequency='0.6' numOctaves='2'/%3E%3CfeColorMatrix values='0 0 0 0 1 0 0 0 0 1 0 0 0 0 1 0 0 0 .6 .3'/%3E%3C/filter%3E%3Crect width='100%25' height='100%25' filter='url(%23n)'/%3E%3C/svg%3E");
  mask-image: url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg'%3E%3Cfilter id='n'%3E%3CfeTurbulence baseFrequency='0.6' numOctaves='2'/%3E%3CfeColorMatrix values='0 0 0 0 1 0 0 0 0 1 0 0 0 0 1 0 0 0 .6 .3'/%3E%3C/filter%3E%3Crect width='100%25' height='100%25' filter='url(%23n)'/%3E%3C/svg%3E");
}
.stamp.dispatched { color: var(--signal-amber); transform: translate(-50%,-50%) rotate(-8deg); }
.stamp.passed     { color: var(--passed-red);   transform: translate(-50%,-50%) rotate(6deg); }

@keyframes stamp-in {
  0%   { opacity: 0; scale: 1.9; }
  70%  { opacity: 1; scale: .96; }
  100% { opacity: 1; scale: 1; }
}
@keyframes thump { 0% { scale: 1; } 40% { scale: .98; } 100% { scale: 1; } }
.stamp.animate { animation: stamp-in 150ms ease-out both; }
.card.thump, .detail.thump { animation: thump 200ms ease-out; }
@media (prefers-reduced-motion: reduce) {
  .stamp.animate { animation: none; }
  .card.thump, .detail.thump { animation: none; }
}

/* ── Detail view ─────────────────────────────────────── */
.detail { max-width: 860px; position: relative; }
.back {
  background: none; border: none; cursor: pointer;
  color: var(--trail-moss); font-family: "IBM Plex Mono", monospace;
  font-size: 12px; padding: 0; margin-bottom: 16px;
}
.back:hover { color: var(--paper); }
.detail-head h2 { font-family: "Fraunces", serif; font-size: 36px; font-weight: 700; }
.detail-head .fund {
  font-family: "IBM Plex Mono", monospace; font-size: 12px;
  color: var(--trail-moss); margin: 4px 0 14px;
}
.detail-head .signal { max-width: 420px; margin-bottom: 20px; }
.detail-head .signal .num, .detail-head .signal .lbl { color: var(--paper); }
.detail-head .signal .lbl { color: var(--trail-moss); }
.detail-head .signal .track { background: rgba(243,238,221,.15); }

.sheet {
  background: var(--paper); color: var(--ink);
  padding: 18px 20px; margin-bottom: 18px;
  border-left: 4px solid var(--trail-moss);
}
.sheet h4 {
  font-family: "IBM Plex Mono", monospace; font-size: 10.5px;
  letter-spacing: .14em; text-transform: uppercase;
  color: #5c6b52; margin-bottom: 8px;
}
.sheet p { font-size: 13.5px; line-height: 1.55; }
.sheet ul { padding-left: 18px; font-size: 13px; line-height: 1.6; }
.pills { display: flex; flex-wrap: wrap; gap: 6px; margin-top: 4px; }
.pill {
  border: 1px solid var(--trail-moss);
  color: var(--ink); font-size: 12px;
  padding: 3px 10px;
}
.pill b { font-weight: 600; }
.pill span { color: #5c6b52; font-size: 11px; }

/* ── Dispatch tray ───────────────────────────────────── */
.tray h4 {
  font-family: "IBM Plex Mono", monospace; font-size: 10.5px;
  letter-spacing: .14em; text-transform: uppercase;
  color: var(--trail-moss); margin: 24px 0 10px;
}
.tray-tabs { display: flex; gap: 2px; margin-bottom: -1px; }
.tray-tabs button {
  background: rgba(243,238,221,.12); border: none; cursor: pointer;
  color: var(--paper); font-family: "IBM Plex Mono", monospace; font-size: 11px;
  letter-spacing: .06em; padding: 7px 14px;
}
.tray-tabs button.active { background: var(--paper); color: var(--ink); }
.slip {
  background: var(--paper); padding: 16px;
  box-shadow: 3px 4px 0 rgba(0,0,0,.3);
}
.slip.r1 { transform: rotate(-0.5deg); }
.slip.r2 { transform: rotate(0.4deg); }
.slip label {
  display: block; font-family: "IBM Plex Mono", monospace;
  font-size: 10px; letter-spacing: .1em; text-transform: uppercase;
  color: #5c6b52; margin: 0 0 4px;
}
.slip input[type=text], .slip textarea {
  width: 100%; border: none; background: transparent;
  color: var(--ink); font-family: "IBM Plex Sans", sans-serif; font-size: 13.5px;
  line-height: 1.5; resize: vertical;
  border-bottom: 1px dashed rgba(36,48,31,.25);
  padding: 2px 0 6px; margin-bottom: 10px;
}
.slip textarea { min-height: 180px; border-bottom: none; }
.slip :focus { outline: none; background: rgba(217,147,47,.07); }

.actions { display: flex; gap: 10px; margin: 18px 0 60px; align-items: center; }
.actions button {
  font-family: "IBM Plex Mono", monospace; font-size: 12px;
  letter-spacing: .08em; text-transform: uppercase;
  padding: 10px 22px; cursor: pointer; border: none;
}
.btn-approve { background: var(--signal-amber); color: var(--bg-pine); font-weight: 500; }
.btn-approve:hover { filter: brightness(1.08); }
.btn-save { background: transparent; color: var(--paper); border: 1px solid var(--trail-moss) !important; }
.btn-save:hover { background: rgba(122,155,110,.15); }
.btn-reject { background: transparent; color: var(--passed-red); border: 1px solid var(--passed-red) !important; }
.btn-reject:hover { background: rgba(166,93,75,.12); }
.actions .note { font-family: "IBM Plex Mono", monospace; font-size: 11px; color: var(--trail-moss); }
</style>
</head>
<body>
<nav>
  <h1>Scout AI</h1>
  <div class="sub">Field Log</div>
  <div id="tabs"></div>
  <div class="foot" id="foot">—</div>
</nav>
<main id="main"></main>

<script>
const SIDEBAR = [
  ["WAITING_APPROVAL","Waiting Approval"], ["MATCHED","Matched"],
  ["RESEARCHED","Researched"], ["DISCOVERED","Discovered"],
  ["SKIPPED","Skipped"], ["SENT","Sent"],
];
let state = "WAITING_APPROVAL";
let counts = {};

const $ = (sel, el=document) => el.querySelector(sel);
const esc = s => (s ?? "").toString()
  .replaceAll("&","&amp;").replaceAll("<","&lt;").replaceAll(">","&gt;")
  .replaceAll('"',"&quot;");

function signalBar(label, value, cls="") {
  const v = value == null ? null : Math.max(0, Math.min(100, value));
  return `<div class="signal ${cls}">
    <span class="lbl">${label}</span>
    <div class="track"><div class="fill" style="width:${v ?? 0}%"></div></div>
    <span class="num">${v == null ? "—" : v + "%"}</span>
  </div>`;
}

function renderTabs() {
  $("#tabs").innerHTML = SIDEBAR.map(([key, label]) => `
    <button class="tab ${key===state?"active":""}" data-state="${key}">
      <span>${label}</span><span class="count">${counts[key] ?? 0}</span>
    </button>`).join("");
  document.querySelectorAll("#tabs .tab").forEach(b =>
    b.addEventListener("click", () => { state = b.dataset.state; loadList(); }));
}

async function loadList() {
  const res = await fetch(`/api/companies?state=${state}`);
  const data = await res.json();
  counts = data.counts;
  renderTabs();
  $("#foot").textContent = new Date().toISOString().slice(0,16).replace("T"," ") + " UTC";
  const label = SIDEBAR.find(([k]) => k===state)[1];
  const cards = data.companies.map(c => `
    <div class="card" data-id="${c.id}">
      <h3>${esc(c.name)}</h3>
      <div class="fund">${esc(c.funding_stage ?? "?")} ${esc(c.funding_amount ?? "")} · #${c.id}</div>
      <p class="line">${esc(c.one_liner)}</p>
      ${signalBar("match", c.match_score)}
    </div>`).join("");
  $("#main").innerHTML = `
    <div class="log-head">
      <h2>${label}</h2>
      <span class="meta">${data.companies.length} entries</span>
    </div>
    ${cards ? `<div class="cards">${cards}</div>` : `<div class="empty">no entries in this state</div>`}`;
  document.querySelectorAll(".card").forEach(el =>
    el.addEventListener("click", () => loadDetail(el.dataset.id)));
}

async function loadDetail(id) {
  const c = await (await fetch(`/api/companies/${id}`)).json();
  const r = c.research ?? {};
  const m = c.match ?? {};
  const o = c.outreach;
  const founders = (r.founders ?? []).map(f =>
    `<span class="pill"><b>${esc(f.name)}</b>${f.role ? ` <span>· ${esc(f.role)}</span>` : ""}</span>`).join("");
  const signals = (r.hiring_signals ?? []).map(s => `<li>${esc(s)}</li>`).join("");

  const tray = o ? `
    <div class="tray">
      <h4>Dispatch Tray</h4>
      <div class="tray-tabs">
        <button class="active" data-slip="email">Email</button>
        <button data-slip="linkedin">LinkedIn</button>
        <button data-slip="cover">Cover Letter</button>
      </div>
      <div class="slip r1" id="slip-email">
        <label>Subject</label>
        <input type="text" id="f-subject" value="${esc(o.email_subject)}">
        <label>Body</label>
        <textarea id="f-body">${esc(o.email_body)}</textarea>
      </div>
      <div class="slip r2" id="slip-linkedin" hidden>
        <label>Connection note (&le;300 chars)</label>
        <textarea id="f-linkedin">${esc(o.linkedin_message)}</textarea>
      </div>
      <div class="slip r1" id="slip-cover" hidden>
        <label>Cover letter</label>
        <textarea id="f-cover">${esc(o.cover_letter)}</textarea>
      </div>
      <div class="actions">
        <button class="btn-approve" id="btn-approve">Approve &amp; Dispatch</button>
        <button class="btn-save" id="btn-save">Save Edits</button>
        <button class="btn-reject" id="btn-reject">Reject</button>
        <span class="note" id="action-note"></span>
      </div>
    </div>` : "";

  $("#main").innerHTML = `
    <div class="detail" id="detail">
      <button class="back" id="back">&larr; back to log</button>
      <div class="detail-head">
        <h2>${esc(c.name)}</h2>
        <div class="fund">${esc(c.funding_stage ?? "?")} ${esc(c.funding_amount ?? "")}
          · ${esc(c.website ?? "")} · state ${esc(c.state)}</div>
        ${signalBar("match", c.match_score)}
        ${signalBar("hiring", c.hiring_probability)}
      </div>
      ${r.summary ? `<div class="sheet"><h4>Research Summary</h4><p>${esc(r.summary)}</p></div>` : ""}
      ${founders ? `<div class="sheet"><h4>Founders</h4><div class="pills">${founders}</div></div>` : ""}
      ${signals ? `<div class="sheet"><h4>Hiring Signals</h4><ul>${signals}</ul></div>` : ""}
      ${m.pitch_angle ? `<div class="sheet"><h4>Pitch Angle</h4><p>${esc(m.pitch_angle)}</p></div>` : ""}
      ${tray}
    </div>`;

  $("#back").addEventListener("click", loadList);
  if (!o) return;

  document.querySelectorAll(".tray-tabs button").forEach(b =>
    b.addEventListener("click", () => {
      document.querySelectorAll(".tray-tabs button").forEach(x => x.classList.remove("active"));
      b.classList.add("active");
      ["email","linkedin","cover"].forEach(k => $("#slip-"+k).hidden = k !== b.dataset.slip);
    }));

  const gatherEdits = () => ({
    email_subject: $("#f-subject").value,
    email_body: $("#f-body").value,
    linkedin_message: $("#f-linkedin").value,
    cover_letter: $("#f-cover").value,
  });

  $("#btn-save").addEventListener("click", async () => {
    const res = await fetch(`/api/companies/${id}/edit`, {
      method: "POST", headers: {"Content-Type": "application/json"},
      body: JSON.stringify(gatherEdits()),
    });
    $("#action-note").textContent = res.ok ? "saved " + new Date().toLocaleTimeString() : "save failed";
  });

  function stamp(text, cls) {
    const el = document.createElement("div");
    el.className = `stamp ${cls}`;
    el.textContent = text;
    if (!matchMedia("(prefers-reduced-motion: reduce)").matches) {
      el.classList.add("animate");
      $("#detail").classList.add("thump");
    }
    $("#detail").appendChild(el);
  }

  $("#btn-approve").addEventListener("click", async () => {
    // Save edits first so what's approved is what's on screen.
    await fetch(`/api/companies/${id}/edit`, {
      method: "POST", headers: {"Content-Type": "application/json"},
      body: JSON.stringify(gatherEdits()),
    });
    const res = await fetch(`/api/companies/${id}/approve`, {method: "POST"});
    if (!res.ok) { $("#action-note").textContent = "approve failed"; return; }
    const data = await res.json();
    stamp("DISPATCHED", "dispatched");
    $("#action-note").textContent = "exported to " + data.outbox;
    setTimeout(loadList, 1100);
  });

  $("#btn-reject").addEventListener("click", async () => {
    const res = await fetch(`/api/companies/${id}/reject`, {method: "POST"});
    if (!res.ok) { $("#action-note").textContent = "reject failed"; return; }
    stamp("PASSED", "passed");
    setTimeout(loadList, 1100);
  });
}

loadList();
</script>
</body>
</html>
"""


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8000)
