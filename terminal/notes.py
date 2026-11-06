"""Creator Capital daily notes -> the terminal's News view (and the Platform view's platform-news strip).

The notes live in the Google Sheet "Creator Capital — Daily Notes", tab Notes. Read from, in order:
  NOTES_SHEET_CSV_URL   a CSV link to a tab holding the note columns (a GitHub secret; see docs/RUNBOOK.md)
  data/inputs/daily_notes.csv   a local export
Only these columns are read: Date, Category, Label, Headline, Note, Sources, POSTED. Flags, Gmail Thread ID and the
workflow checkboxes are internal and are never read, stored or shown.
"""
import csv
import datetime as dt
import io
import os
import re
import urllib.request
from pathlib import Path

import yaml

from .sources import NotConfigured

ROOT = Path(__file__).resolve().parent.parent
LOCAL = ROOT / "data" / "inputs" / "daily_notes.csv"
SOURCE = "Creator Capital"

# the sheet's categories -> the terminal's news categories
CATEGORY = {"Platform Change": "PLATFORM", "Creator Policy": "POLICY", "Creator Research": "RESEARCH",
            "Fundraise Announcement": "DEAL", "Acquisition / M&A": "DEAL", "New Funder": "DEAL",
            "New Creator Company (Subsidiary)": "DEAL", "Monetization": "MONETIZATION"}
URL = re.compile(r"https?://[^\s)>\]]+")


def _settings() -> dict:
    s = yaml.safe_load((ROOT / "config" / "settings.yaml").read_text()) or {}
    return s.get("notes", {}) or {}


def _text() -> str:
    url = os.environ.get("NOTES_SHEET_CSV_URL")
    if url:
        with urllib.request.urlopen(url, timeout=30) as r:
            return r.read().decode("utf-8")
    if LOCAL.exists():
        return LOCAL.read_text(encoding="utf-8")
    raise NotConfigured("daily notes: set NOTES_SHEET_CSV_URL or put an export at data/inputs/daily_notes.csv")


def parse(text: str, include_drafts: bool = True) -> list[dict]:
    out = []
    for r in csv.DictReader(io.StringIO(text)):
        r = {(k or "").strip(): (v or "").strip() for k, v in r.items()}
        head, date = r.get("Headline"), r.get("Date")
        if not head or not date:
            continue
        posted = r.get("POSTED", "").upper() == "TRUE"
        if not posted and not include_drafts:
            continue
        try:
            when = dt.datetime.fromisoformat(date[:10]).replace(hour=12)
        except ValueError:
            continue
        urls = URL.findall(r.get("Sources", ""))
        # the card links to the note's first source; a note without one gets a stable key that links nowhere
        # several notes can come from one newsletter issue, so each note gets its own key (the #fragment still opens the page)
        slug = f"cc-{date[:10]}-{re.sub(r'[^a-z0-9]+', '-', head.lower()).strip('-')[:50]}"
        key = f"{urls[0].rstrip('.,;')}#{slug}" if urls else f"creator-capital:{slug}"
        note = r.get("Note", "")
        first = note.split("\n", 1)[0]
        out.append({"url": key, "source": SOURCE, "title": head, "summary": first[:600], "published_at": when,
                    "category": CATEGORY.get(r.get("Category", "")), "note": note, "label": r.get("Label") or None,
                    "status": "posted" if posted else "draft"})
    return out


def load(con) -> int:
    rows = parse(_text(), bool(_settings().get("include_drafts", True)))
    keep = {r["url"] for r in rows}
    for r in rows:
        # a re-edited note updates in place; tags already given by the tagger are kept
        con.execute("""INSERT INTO news (url, source, title, summary, published_at, category, note, label, status)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                       ON CONFLICT (url) DO UPDATE SET source = excluded.source, title = excluded.title, summary = excluded.summary,
                         published_at = excluded.published_at, note = excluded.note, label = excluded.label, status = excluded.status,
                         category = coalesce(excluded.category, news.category)""",
                    [r["url"], r["source"], r["title"], r["summary"], r["published_at"], r["category"], r["note"], r["label"], r["status"]])
    # a note deleted from the sheet (or turned back to draft when drafts are off) leaves the terminal too
    gone = [u for (u,) in con.execute("SELECT url FROM news WHERE source = ?", [SOURCE]).fetchall() if u not in keep]
    for u in gone:
        con.execute("DELETE FROM news WHERE url = ?", [u])
    return len(rows)
