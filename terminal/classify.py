"""Assign each channel a CTNCS code with Claude, then audit a sample by hand.

Only codes inside the configured sectors are allowed; anything else comes back as
"unclassified" so it can be reviewed rather than forced into a niche.
"""
import json
import os

import anthropic

from . import ctncs, llm

MODEL = os.environ.get("CLASSIFIER_MODEL", "claude-sonnet-5-5")

PROMPT = """You classify YouTube channels using the CTNCS taxonomy (Content Terminal Niche Classification Standard).

Allowed codes (code | label | definition):
{codes}

Channel:
Title: {title}
Description: {description}
Recent video titles:
{videos}

Pick the single most specific code that fits the channel's main content. If none fits, use "unclassified".
Reply with JSON only: {{"code": "...", "confidence": 0.0-1.0, "rationale": "one sentence"}}"""


def _codes_block(rows):
    return "\n".join(f'{r["code"]} | {r["label"]} | {r["definition"]}' for r in rows)


def classify_channels(con, sectors: list[str], limit: int | None = None, redo: bool = False) -> int:
    book = ctncs.load()
    allowed = ctncs.subtree(book, sectors)
    allowed_codes = {r["code"] for r in allowed}
    codes = _codes_block(allowed)
    client = anthropic.Anthropic()

    q = """SELECT c.channel_id, c.title, c.description FROM channels c
           LEFT JOIN classifications k USING (channel_id)
           WHERE k.channel_id IS NULL OR (? AND NOT coalesce(k.reviewed, false))"""
    rows = con.execute(q, [redo]).fetchall()[:limit]

    n = 0
    for channel_id, title, description in rows:
        titles = [t for (t,) in con.execute(
            "SELECT title FROM videos WHERE channel_id = ? ORDER BY published_at DESC LIMIT 15", [channel_id]).fetchall()]
        msg = client.messages.create(
            model=MODEL, max_tokens=2000,
            messages=[{"role": "user", "content": PROMPT.format(
                codes=codes, title=title, description=(description or "")[:1500],
                videos="\n".join(f"- {t}" for t in titles) or "- (none)")}],
        )
        text = llm.reply_text(msg).strip()
        try:
            out = json.loads(text[text.index("{"): text.rindex("}") + 1])
        except ValueError:
            out = {"code": "unclassified", "confidence": 0, "rationale": "unparseable reply"}
        code = out.get("code") if out.get("code") in allowed_codes else "unclassified"
        parts = code.split(".") if code != "unclassified" else []
        con.execute(
            "INSERT OR REPLACE INTO classifications "
            "(channel_id, ctncs_code, sector, category, niche, confidence, rationale, model, reviewed) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, false)",
            [channel_id, code,
             parts[0] if len(parts) > 0 else None,
             ".".join(parts[:2]) if len(parts) > 1 else None,
             ".".join(parts[:3]) if len(parts) > 2 else None,
             float(out.get("confidence") or 0), out.get("rationale"), MODEL],
        )
        n += 1
    return n


def audit_sample(con, n: int = 25):
    """Random sample of unreviewed classifications for a person to check."""
    return con.execute(
        "SELECT k.channel_id, c.title, k.ctncs_code, k.confidence, k.rationale "
        "FROM classifications k JOIN channels c USING (channel_id) "
        "WHERE NOT k.reviewed ORDER BY random() LIMIT ?", [n]).fetchall()


def audit_sheet(con, kind: str = "video", n: int = 25, out_dir: str = "data/audits") -> str:
    """10.3: write a random sample of unreviewed classifications to a CSV for a person to check.

    Fill in correct_code only where the assigned code is wrong (leave it blank when it is right), then load it
    with `python -m terminal audit --record <file>`."""
    import csv
    import datetime as dt
    import os
    if kind == "video":
        rows = con.execute(
            "SELECT k.video_id, v.title, k.topic_code, k.format_label, k.confidence "
            "FROM video_classifications k JOIN videos v USING (video_id) "
            "WHERE NOT k.reviewed AND k.topic_code <> 'unclassified' ORDER BY random() LIMIT ?", [n]).fetchall()
    else:
        rows = [(r[0], r[1], r[2], "", r[3]) for r in audit_sample(con, n)]
    os.makedirs(out_dir, exist_ok=True)
    path = f"{out_dir}/{kind}-audit-{dt.date.today().isoformat()}.csv"
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["kind", "item_id", "title", "assigned_code", "format", "confidence", "correct_code"])
        for item, title, code, fmt, conf in rows:
            w.writerow([kind, item, title, code, fmt or "", conf, ""])
    return path


def record_audit(con, path: str, auditor: str | None = None, audited_on=None) -> dict:
    """Load a filled audit sheet: store each verdict, fix wrong codes, and mark every checked item reviewed."""
    import csv
    import datetime as dt
    audited_on = audited_on or dt.date.today()
    n = wrong = 0
    with open(path, newline="", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            kind, item, assigned = r["kind"].strip(), r["item_id"].strip(), r["assigned_code"].strip()
            fixed = (r.get("correct_code") or "").strip()
            ok = not fixed or fixed == assigned
            con.execute("INSERT OR REPLACE INTO classification_audits VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                        [audited_on, kind, item, assigned.split(".")[0], assigned, fixed or assigned, ok, auditor])
            parts = (fixed or assigned).split(".")
            lv = lambda i: ".".join(parts[:i]) if len(parts) >= i else None
            if kind == "video":
                con.execute("UPDATE video_classifications SET reviewed = true, topic_code = ?, sector = ?, category = ?, "
                            "niche = ?, subniche = ? WHERE video_id = ?",
                            [fixed or assigned, lv(1), lv(2), lv(3), lv(4), item])
            else:
                con.execute("UPDATE classifications SET reviewed = true, ctncs_code = ?, sector = ?, category = ?, "
                            "niche = ? WHERE channel_id = ?", [fixed or assigned, lv(1), lv(2), lv(3), item])
            n += 1
            wrong += not ok
    return {"checked": n, "wrong": wrong, "error_rate": wrong / n if n else None}
