"""News feed: official platform blogs and creator-economy trade press, pulled from RSS,
tagged by Claude, ranked by impact and recency.

Every item keeps its source link. Nothing is written about a person unless a source
article says it: the tags describe the article, they don't add to it.
"""
import datetime as dt
import email.utils
import json
import os
import xml.etree.ElementTree as ET
from pathlib import Path

import requests
import yaml

ROOT = Path(__file__).resolve().parent.parent

SCHEMA = """
CREATE TABLE IF NOT EXISTS news (
    url          VARCHAR PRIMARY KEY,
    source       VARCHAR,
    title        VARCHAR,
    summary      VARCHAR,
    published_at TIMESTAMP,
    category     VARCHAR,   -- POLICY, PLATFORM, MONETIZATION, CREATOR, DEAL, RESEARCH, TREND
    platform     VARCHAR,   -- YOUTUBE, TIKTOK, INSTAGRAM, META, TWITCH, X, ALL, OTHER
    impact       VARCHAR,   -- HIGH, MEDIUM, LOW: how much it moves creator economics
    sentiment    VARCHAR,   -- POSITIVE, NEUTRAL, NEGATIVE: for creators, as the article reports it
    creators     VARCHAR,   -- creators named in the headline or summary, comma-separated
    fetched_at   TIMESTAMP DEFAULT now()
);
"""

RANKED = """
CREATE OR REPLACE VIEW news_ranked AS
SELECT n.*,
       -- impact weight, halving every 7 days
       (CASE impact WHEN 'HIGH' THEN 3 WHEN 'MEDIUM' THEN 2 ELSE 1 END)
         * pow(0.5, date_diff('hour', published_at, now()) / 168.0) AS score,
       (SELECT string_agg(c.channel_id, ',') FROM channels c
         WHERE n.creators IS NOT NULL AND c.title IS NOT NULL
           AND list_contains(list_transform(string_split(lower(n.creators), ','), x -> trim(x)), lower(c.title))) AS tracked_channel_ids
FROM news n
WHERE category IS NOT NULL;
"""

CATEGORIES = ["POLICY", "PLATFORM", "MONETIZATION", "CREATOR", "DEAL", "RESEARCH", "TREND"]
PLATFORMS = ["YOUTUBE", "TIKTOK", "INSTAGRAM", "META", "TWITCH", "X", "ALL", "OTHER"]

PROMPT = """Tag this news item for a terminal that tracks the creator economy.

Source: {source}
Headline: {title}
Summary: {summary}

Reply with JSON only:
{{"category": one of {categories},
  "platform": one of {platforms} (ALL if it spans platforms, OTHER if none of these),
  "impact": "HIGH" if it changes how creators earn or reach audiences, "MEDIUM" if it matters to parts of the market, "LOW" otherwise,
  "sentiment": "POSITIVE", "NEUTRAL" or "NEGATIVE" for creators, as the article itself reports it,
  "creators": [creators or creator companies named in the headline or summary, exactly as written; [] if none]}}"""


def connect(con):
    con.execute(SCHEMA)
    con.execute(RANKED)


def _text(el, *names):
    for n in names:
        x = el.find(n)
        if x is not None and (x.text or x.get("href")):
            return (x.text or x.get("href")).strip()
    return None


def _date(s):
    if not s:
        return None
    try:
        return email.utils.parsedate_to_datetime(s).astimezone(dt.timezone.utc).replace(tzinfo=None)
    except (TypeError, ValueError):
        try:
            return dt.datetime.fromisoformat(s.replace("Z", "+00:00")).astimezone(dt.timezone.utc).replace(tzinfo=None)
        except ValueError:
            return None


def parse_feed(xml_text: str, source: str) -> list[dict]:
    """RSS 2.0 or Atom -> items."""
    root = ET.fromstring(xml_text)
    atom = "{http://www.w3.org/2005/Atom}"
    items = root.findall("./channel/item") or root.findall(f"{atom}entry")
    out = []
    for it in items:
        url = _text(it, "link", f"{atom}link")
        title = _text(it, "title", f"{atom}title")
        if not url or not title:
            continue
        out.append({"url": url, "source": source, "title": title,
                    "summary": (_text(it, "description", f"{atom}summary") or "")[:600],
                    "published_at": _date(_text(it, "pubDate", f"{atom}published", f"{atom}updated"))})
    return out


def pull(con) -> int:
    feeds = yaml.safe_load((ROOT / "config" / "feeds.yaml").read_text())["feeds"]
    n = 0
    for f in feeds:
        try:
            r = requests.get(f["url"], timeout=20, headers={"User-Agent": "content-terminal/0.2"})
            r.raise_for_status()
            items = parse_feed(r.text, f["name"])
        except (requests.RequestException, ET.ParseError) as e:
            print(f"feed failed: {f['name']}: {e}")
            continue
        for i in items:
            # keep existing tags if we've seen this article before
            con.execute("""INSERT INTO news (url, source, title, summary, published_at) VALUES (?, ?, ?, ?, ?)
                           ON CONFLICT (url) DO UPDATE SET title = excluded.title, summary = excluded.summary""",
                        [i["url"], i["source"], i["title"], i["summary"], i["published_at"]])
            n += 1
    return n


def tag(con, limit: int | None = None) -> int:
    import anthropic
    client = anthropic.Anthropic()
    model = os.environ.get("CLASSIFIER_MODEL", "claude-sonnet-5-5")
    rows = con.execute("SELECT url, source, title, summary FROM news WHERE category IS NULL ORDER BY published_at DESC").fetchall()[:limit]
    n = 0
    for url, source, title, summary in rows:
        msg = client.messages.create(model=model, max_tokens=200, messages=[{"role": "user", "content": PROMPT.format(
            source=source, title=title, summary=summary or "", categories=CATEGORIES, platforms=PLATFORMS)}])
        text = msg.content[0].text
        try:
            t = json.loads(text[text.index("{"): text.rindex("}") + 1])
        except ValueError:
            continue
        con.execute("UPDATE news SET category = ?, platform = ?, impact = ?, sentiment = ?, creators = ? WHERE url = ?",
                    [t.get("category") if t.get("category") in CATEGORIES else "TREND",
                     t.get("platform") if t.get("platform") in PLATFORMS else "OTHER",
                     t.get("impact") if t.get("impact") in ("HIGH", "MEDIUM", "LOW") else "LOW",
                     t.get("sentiment") if t.get("sentiment") in ("POSITIVE", "NEUTRAL", "NEGATIVE") else "NEUTRAL",
                     ", ".join(t.get("creators") or []) or None, url])
        n += 1
    return n
