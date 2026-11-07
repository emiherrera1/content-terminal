"""Creator companies: who has a company around them, how far it has gone (the stage ladder) and who financed it.

The research lives in the Google Sheet "Creator Companies" (CVA Work / Fund). The terminal reads its three Feed tabs,
which hold Public rows only; Private rows never leave the sheet. Read from, in order:
  COMPANIES_SHEET_CSV_URL   the sheet's "Publish to web" CSV link (any tab); the Feed tabs are fetched by gid
  data/inputs/companies_{creators,companies,rounds}.csv   local exports
Stages: 1 no company found, 2 payout LLC, 3 operating company, 4 holding company, 5 outside capital, 6 exited.
A blank stage means not researched yet, which is different from 1.
"""
import csv
import datetime as dt
import io
import os
import re
import urllib.parse
import urllib.request
from pathlib import Path

from .sources import NotConfigured

ROOT = Path(__file__).resolve().parent.parent
LOCAL = ROOT / "data" / "inputs"
GIDS = {"creators": 10, "companies": 11, "rounds": 12}   # the Feed tabs' fixed sheet ids
STAGES = {1: "No company found", 2: "Payout LLC", 3: "Operating company", 4: "Holding company", 5: "Outside capital", 6: "Exited"}

DDL = [
    """CREATE TABLE IF NOT EXISTS creator_companies (creator VARCHAR, channel_id VARCHAR, in_terminal VARCHAR, stage INTEGER,
         stage_name VARCHAR, main_company VARCHAR, outside_money VARCHAR, raised_usd_m DOUBLE, research_status VARCHAR,
         confidence VARCHAR, source VARCHAR, notes VARCHAR, last_checked DATE)""",
    """CREATE TABLE IF NOT EXISTS creator_company_list (company VARCHAR, creator VARCHAR, relationship VARCHAR, type VARCHAR,
         parent VARCHAR, what VARCHAR, status VARCHAR, source VARCHAR, confidence VARCHAR, last_checked DATE)""",
    """CREATE TABLE IF NOT EXISTS creator_rounds (round_date DATE, company VARCHAR, creator VARCHAR, round VARCHAR,
         amount_m DOUBLE, valuation_m DOUBLE, currency VARCHAR, lead_investors VARCHAR, other_investors VARCHAR,
         source VARCHAR, confidence VARCHAR, last_checked DATE)""",
]


def connect(con):
    for s in DDL:
        con.execute(s)


def _feed_url(base: str, gid: int) -> str:
    u = urllib.parse.urlsplit(base)
    q = [(k, v) for k, v in urllib.parse.parse_qsl(u.query) if k not in ("gid", "single", "output")]
    q += [("gid", str(gid)), ("single", "true"), ("output", "csv")]
    return urllib.parse.urlunsplit((u.scheme, u.netloc, u.path, urllib.parse.urlencode(q), ""))


def _text(tab: str) -> str:
    base = os.environ.get("COMPANIES_SHEET_CSV_URL")
    if base:
        with urllib.request.urlopen(_feed_url(base, GIDS[tab]), timeout=30) as r:
            return r.read().decode("utf-8")
    p = LOCAL / f"companies_{tab}.csv"
    if p.exists():
        return p.read_text(encoding="utf-8")
    raise NotConfigured("creator companies: set COMPANIES_SHEET_CSV_URL or put exports at data/inputs/companies_*.csv")


def _num(s):
    s = re.sub(r"[,$\s]", "", s or "")
    try:
        return float(s) if s else None
    except ValueError:
        return None


def _date(s):
    s = (s or "").strip()
    for f in ("%Y-%m-%d", "%m/%d/%Y", "%Y/%m/%d"):
        try:
            return dt.datetime.strptime(s[:10], f).date()
        except ValueError:
            pass
    return None


def _rows(text: str) -> list[dict]:
    return [{(k or "").strip(): (v or "").strip() for k, v in r.items()} for r in csv.DictReader(io.StringIO(text))]


def parse_creators(text: str) -> list[dict]:
    out = []
    for r in _rows(text):
        if not r.get("Creator"):
            continue
        st = _num(r.get("Stage"))
        st = int(st) if st and int(st) in STAGES else None
        done = r.get("Research status", "").lower() == "done"
        out.append({"creator": r["Creator"], "channel_id": r.get("Channel ID") or None, "in_terminal": r.get("In terminal") or None,
                    "stage": st, "stage_name": STAGES.get(st), "main_company": r.get("Main company") or None,
                    "outside_money": r.get("Outside money") or None, "raised_usd_m": _num(r.get("Disclosed raised ($M, USD)")),
                    "research_status": "Done" if done else "To research", "confidence": r.get("Confidence") or None,
                    "source": r.get("Source") or None, "notes": r.get("Notes") or None, "last_checked": _date(r.get("Last checked"))})
    return out


def parse_companies(text: str) -> list[dict]:
    return [{"company": r["Company"], "creator": r.get("Creator") or None, "relationship": r.get("Relationship") or None,
             "type": r.get("Type") or None, "parent": r.get("Parent company") or None, "what": r.get("What it does") or None,
             "status": r.get("Status") or None, "source": r.get("Source") or None, "confidence": r.get("Confidence") or None,
             "last_checked": _date(r.get("Last checked"))}
            for r in _rows(text) if r.get("Company")]


def parse_rounds(text: str) -> list[dict]:
    return [{"round_date": _date(r.get("Date")), "company": r["Company"], "creator": r.get("Creator") or None,
             "round": r.get("Round") or None, "amount_m": _num(r.get("Amount ($M)")), "valuation_m": _num(r.get("Valuation ($M)")),
             "currency": (r.get("Currency") or "USD").upper(), "lead_investors": r.get("Lead investors") or None,
             "other_investors": r.get("Other investors") or None, "source": r.get("Source") or None,
             "confidence": r.get("Confidence") or None, "last_checked": _date(r.get("Last checked"))}
            for r in _rows(text) if r.get("Company")]


def _replace(con, table: str, rows: list[dict]):
    con.execute(f"DELETE FROM {table}")
    if rows:
        cols = list(rows[0])
        con.executemany(f"INSERT INTO {table} ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})",
                        [[r[c] for c in cols] for r in rows])


def load(con) -> str:
    """The sheet is the record: each run replaces all three tables with what the Feed tabs hold now."""
    connect(con)
    cr, co, ro = parse_creators(_text("creators")), parse_companies(_text("companies")), parse_rounds(_text("rounds"))
    # fill the channel id from the tracked channels when the sheet has only a name
    if cr and con.execute("SELECT count(*) FROM information_schema.tables WHERE table_name = 'channels'").fetchone()[0]:
        by_title = {t.strip().lower(): c for c, t in con.execute("SELECT channel_id, title FROM channels").fetchall() if t}
        for r in cr:
            if not r["channel_id"]:
                r["channel_id"] = by_title.get(r["creator"].strip().lower())
    _replace(con, "creator_companies", cr)
    _replace(con, "creator_company_list", co)
    _replace(con, "creator_rounds", ro)
    done = sum(r["research_status"] == "Done" for r in cr)
    return f"{len(cr)} creators ({done} researched), {len(co)} companies, {len(ro)} rounds"
