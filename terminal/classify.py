"""Assign each channel a CTNCS code with Claude, then audit a sample by hand.

Only codes inside the configured sectors are allowed; anything else comes back as
"unclassified" so it can be reviewed rather than forced into a niche.
"""
import json
import os

import anthropic

from . import ctncs

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
            model=MODEL, max_tokens=300,
            messages=[{"role": "user", "content": PROMPT.format(
                codes=codes, title=title, description=(description or "")[:1500],
                videos="\n".join(f"- {t}" for t in titles) or "- (none)")}],
        )
        text = msg.content[0].text.strip()
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
