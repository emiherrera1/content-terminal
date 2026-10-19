"""Rule-based tagging that needs no API calls: packaging formats (2.11) and named entities (6.2).

Both run over video titles, descriptions and tags already stored by the daily pull, so they cost nothing and run
every day. The rules are drafts: packaging codes come from config/ctncs/packaging.csv (a proposed CTNCS v3 layer)
and entities from config/crosswalk.csv plus config/entities.csv. A model-based tagger can replace either later
without changing the tables.
"""
import csv
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PACKAGING = ROOT / "config" / "ctncs" / "packaging.csv"
CROSSWALK = ROOT / "config" / "crosswalk.csv"
ENTITIES = ROOT / "config" / "entities.csv"


def packaging_rules(path=PACKAGING) -> list[tuple[str, str, re.Pattern | None]]:
    with open(path, newline="", encoding="utf-8") as f:
        return [(r["id"], r["label"], re.compile(r["pattern"], re.I) if r["pattern"] else None) for r in csv.DictReader(f)]


def packaging_of(title: str, rules) -> tuple[str, str]:
    for pid, label, rx in rules:
        if rx is not None and rx.search(title or ""):
            return pid, label
    return "P99", "Other"


def tag_packaging(con, redo: bool = False) -> int:
    rules = packaging_rules()
    where = "" if redo else "WHERE video_id NOT IN (SELECT video_id FROM video_packaging)"
    rows = con.execute(f"SELECT video_id, title FROM videos {where}").fetchall()
    if rows:
        con.executemany("INSERT OR REPLACE INTO video_packaging VALUES (?, ?, ?, 'rules')",
                        [(vid, *packaging_of(title, rules)) for vid, title in rows])
    return len(rows)


def entity_list() -> list[tuple[str, str]]:
    """(name, type): companies and brands from the crosswalk, plus any extra names in config/entities.csv."""
    out = {}
    if CROSSWALK.exists():
        with open(CROSSWALK, newline="", encoding="utf-8") as f:
            for r in csv.DictReader(f):
                for part in (r.get("public_companies") or "").split(";"):
                    name = re.sub(r"\s*\(.*?\)", "", part).strip()
                    if name:
                        out[name] = "company"
                for part in (r.get("private_brands") or "").split(";"):
                    name = re.sub(r"\s*\(.*?\)", "", part).strip()
                    if name:
                        out[name] = "brand"
    if ENTITIES.exists():
        with open(ENTITIES, newline="", encoding="utf-8") as f:
            for r in csv.DictReader(f):
                if r.get("name"):
                    out[r["name"].strip()] = (r.get("type") or "brand").strip()
    return sorted(out.items())


def _rx(name: str) -> re.Pattern:
    return re.compile(r"(?<![\w])" + re.escape(name) + r"(?![\w])", re.I)


def tag_entities(con, redo: bool = False) -> int:
    ents = [(n, t, _rx(n)) for n, t in entity_list()]
    where = "" if redo else "WHERE video_id NOT IN (SELECT DISTINCT video_id FROM video_entities)"
    rows = con.execute(f"SELECT video_id, coalesce(title, ''), coalesce(description, ''), coalesce(tags, '') FROM videos {where}").fetchall()
    found = []
    for vid, title, desc, tags in rows:
        for name, kind, rx in ents:
            where_hit = next((w for w, text in (("title", title), ("tags", tags), ("description", desc)) if rx.search(text)), None)
            if where_hit:
                found.append((vid, name, kind, where_hit))
    if found:
        con.executemany("INSERT OR REPLACE INTO video_entities VALUES (?, ?, ?, ?)", found)
    return len(found)


INTENT = re.compile(r"where (can i|do i|did you|to) (buy|get|find)|link\??\b|how much|price|cost\b|discount|code\b|"
                    r"available in|ship(s|ping)? to|sold out|in stock|where('s| is) (it|this) from|need this", re.I)


def purchase_intent(text: str) -> bool:
    """6.3 first-pass rule: asks where to buy, price, link or availability."""
    return bool(INTENT.search(text or ""))
