"""Classify each video by topic and format, independent of the channel that posted it.

A woodworking video from a home-renovation channel counts toward Woodworking. Topic goes as
deep as CTNCS v2.0 level 4 (sector > category > niche > sub-niche); format is the CTNCS v2.0
format tag (Tutorials, Reviews, DIY...), which is independent of topic.

Videos are sent in batches with the codebook as a cached system prompt, so the per-video cost
is mostly the video's own title and description. Each video is classified once.
"""
import datetime as dt
import json
import os

from . import ctncs, llm

MODEL = os.environ.get("VIDEO_CLASSIFIER_MODEL", "claude-haiku-5-5")
BATCH = 25

SYSTEM = """You classify individual YouTube videos using the CTNCS taxonomy (Content Terminal Niche Classification Standard).

Classify the video's own content, not its channel: a cooking channel's video about building a cutting board is Woodworking.

Topic codes (code | label | definition). Pick the most specific code that fits, down to level 4:
{topics}

Formats (pick the one that best describes how the video is made):
{formats}

Follow each topic's "rule" when deciding between similar codes (for example yoga as exercise vs as a spiritual practice).
If the video fits a niche but none of its sub-niches, use the niche code. If no topic fits, use "unclassified". If no format fits, use null."""

USER = """Classify each video. Reply with a JSON list only, one object per video, in the same order:
[{{"id": "...", "topic": "<code or unclassified>", "format": "<format label or null>", "confidence": 0.0-1.0}}]

{videos}"""


def _system(book: dict) -> str:
    fmt = ctncs.formats()

    def line(r):
        common = [fmt[c] for c in (r.get("common_formats") or "").split(",") if c in fmt]
        return (f'{r["code"]} | {r["label"]} | {r["definition"]}'
                + (f' | rule: {r["scope_note"]}' if r.get("scope_note") else "")
                + (f' | common formats: {", ".join(common)}' if common else ""))
    topics = "\n".join(line(r) for r in ctncs.topics(book))
    formats = ", ".join(sorted(set(ctncs.formats(book).values())))
    return SYSTEM.format(topics=topics, formats=formats)


def _video_block(rows) -> str:
    out = []
    for vid, title, desc, tags, dur, channel, channel_code in rows:
        out.append(f"id: {vid}\ntitle: {title}\nchannel: {channel} (channel niche: {channel_code or 'unknown'})\n"
                   f"length: {'short (under 3 min)' if (dur or 0) <= 180 else f'{(dur or 0) // 60} min'}\n"
                   f"tags: {tags or '-'}\ndescription: {(desc or '')[:300]}")
    return "\n---\n".join(out)


def parse_reply(text: str, ids: list[str], book: dict) -> list[dict]:
    """Validate the model's JSON against the codebook. Bad codes become unclassified; unknown formats become null."""
    allowed = {r["code"] for r in ctncs.topics(book)}
    by_label = {label.lower(): fid for fid, label in ctncs.formats(book).items()}
    try:
        items = json.loads(text[text.index("["): text.rindex("]") + 1])
    except ValueError:
        items = []
    got = {str(i.get("id")): i for i in items if isinstance(i, dict)}
    out = []
    for vid in ids:
        i = got.get(vid, {})
        code = i.get("topic") if i.get("topic") in allowed else "unclassified"
        parts = code.split(".") if code != "unclassified" else []
        fid = by_label.get(str(i.get("format") or "").lower())
        out.append({"video_id": vid, "topic_code": code,
                    "sector": parts[0] if len(parts) > 0 else None,
                    "category": ".".join(parts[:2]) if len(parts) > 1 else None,
                    "niche": ".".join(parts[:3]) if len(parts) > 2 else None,
                    "subniche": ".".join(parts[:4]) if len(parts) > 3 else None,
                    "format_id": fid, "format_label": ctncs.formats(book).get(fid) if fid else None,
                    "confidence": float(i.get("confidence") or 0) if i else 0.0})
    return out


def save(con, rows: list[dict], model: str = MODEL) -> None:
    for r in rows:
        con.execute(
            "INSERT OR REPLACE INTO video_classifications (video_id, topic_code, sector, category, niche, subniche, "
            "format_id, format_label, confidence, model, reviewed) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, false)",
            [r["video_id"], r["topic_code"], r["sector"], r["category"], r["niche"], r["subniche"],
             r["format_id"], r["format_label"], r["confidence"], model])


def pending(con, days: int = 60, limit: int | None = None):
    """Unclassified videos published in the last `days` days, newest first."""
    rows = con.execute(
        """SELECT v.video_id, v.title, v.description, v.tags, v.duration_s, c.title, k.ctncs_code
           FROM videos v
           LEFT JOIN channels c USING (channel_id)
           LEFT JOIN classifications k USING (channel_id)
           LEFT JOIN video_classifications vc USING (video_id)
           WHERE vc.video_id IS NULL AND v.published_at >= ?
           ORDER BY v.published_at DESC""",
        [dt.datetime.now() - dt.timedelta(days=days)]).fetchall()
    return rows[:limit] if limit else rows


def classify_videos(con, limit: int | None = None, days: int = 60, client=None) -> int:
    book = ctncs.load()
    rows = pending(con, days, limit)
    if not rows:
        return 0
    if client is None:
        import anthropic
        client = anthropic.Anthropic()
    system = [{"type": "text", "text": _system(book), "cache_control": {"type": "ephemeral"}}]
    n = 0
    for i in range(0, len(rows), BATCH):
        batch = rows[i:i + BATCH]
        msg = client.messages.create(model=MODEL, max_tokens=4000 + 150 * len(batch), system=system,
                                     messages=[{"role": "user", "content": USER.format(videos=_video_block(batch))}])
        llm.warn_if_cut(msg, "classify-videos")
        out = parse_reply(llm.reply_text(msg), [r[0] for r in batch], book)
        save(con, out)
        n += len(out)
    return n
