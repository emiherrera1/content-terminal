"""Classify each video by topic and format, independent of the channel that posted it.

A woodworking video from a home-renovation channel counts toward Woodworking. Topic goes as
deep as CTNCS v2.0 level 4 (sector > category > niche > sub-niche); format is the CTNCS v2.0
format tag (Tutorials, Reviews, DIY...), which is independent of topic.

Videos are sent in batches with the codebook as a cached system prompt, so the per-video cost
is mostly the video's own title and description. Each video is classified once.

The daily job uses Anthropic's Message Batches API (half the price of direct calls): submit() sends every pending
video as one batch early in the run, and collect() picks up finished batches later in the same run, or on the next
run if one is still processing. Videos in an open batch are not sent again. classify_videos() is the direct path,
kept for the command line and for CLASSIFY_MODE=direct.
"""
import datetime as dt
import json
import os

from . import store, ctncs, llm, usage

MODEL = os.environ.get("VIDEO_CLASSIFIER_MODEL", "claude-haiku-5-5")
BATCH = 25
WORKERS = int(os.environ.get("CLASSIFY_WORKERS", "6"))   # Claude calls in flight at once
SAVE_EVERY = 20                                          # batches per database write

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


SAVE_COLS = ["video_id", "topic_code", "sector", "category", "niche", "subniche", "format_id", "format_label", "confidence",
             "model", "reviewed"]


def save(con, rows: list[dict], model: str = MODEL) -> None:
    store.upsert(con, "video_classifications", SAVE_COLS,
                 [[r["video_id"], r["topic_code"], r["sector"], r["category"], r["niche"], r["subniche"],
                   r["format_id"], r["format_label"], r["confidence"], model, False] for r in rows])


BATCHES_DDL = """CREATE TABLE IF NOT EXISTS classify_batches (batch_id VARCHAR PRIMARY KEY, submitted_at TIMESTAMP,
                  videos INTEGER, request_map JSON, collected_at TIMESTAMP, classified INTEGER)"""


def _in_open_batches(con) -> set[str]:
    con.execute(BATCHES_DDL)
    out = set()
    for (m,) in con.execute("SELECT request_map FROM classify_batches WHERE collected_at IS NULL").fetchall():
        for ids in json.loads(m).values():
            out.update(ids)
    return out


def pending(con, days: int = 60, limit: int | None = None):
    """Unclassified videos published in the last `days` days, newest first, leaving out any already in an open batch."""
    rows = con.execute(
        """SELECT v.video_id, v.title, v.description, v.tags, v.duration_s, c.title, k.ctncs_code
           FROM videos v
           LEFT JOIN channels c USING (channel_id)
           LEFT JOIN classifications k USING (channel_id)
           LEFT JOIN video_classifications vc USING (video_id)
           WHERE vc.video_id IS NULL AND v.published_at >= ?
           ORDER BY v.published_at DESC""",
        [dt.datetime.now() - dt.timedelta(days=days)]).fetchall()
    busy = _in_open_batches(con)
    rows = [r for r in rows if r[0] not in busy]
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

    def one(batch):
        msg = client.messages.create(model=MODEL, max_tokens=4000 + 150 * len(batch), system=system,
                                     messages=[{"role": "user", "content": USER.format(videos=_video_block(batch))}])
        llm.warn_if_cut(msg, "classify-videos")
        usage.anthropic(msg, MODEL, "classify videos")
        return parse_reply(llm.reply_text(msg), [r[0] for r in batch], book)

    # several batches in flight at once; results are saved every SAVE_EVERY batches so a failure late in a long run
    # keeps what was already done
    from concurrent.futures import ThreadPoolExecutor
    batches = [rows[i:i + BATCH] for i in range(0, len(rows), BATCH)]
    n, pending_rows = 0, []
    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        for k, out in enumerate(pool.map(one, batches), 1):
            pending_rows += out
            if k % SAVE_EVERY == 0 or k == len(batches):
                save(con, pending_rows)
                n += len(pending_rows)
                pending_rows = []
    return n


# ------------------------------------------------------------------ Message Batches: half price, results within hours
def _params(system, batch) -> dict:
    return {"model": MODEL, "max_tokens": 4000 + 150 * len(batch), "system": system,
            "messages": [{"role": "user", "content": USER.format(videos=_video_block(batch))}]}


def submit(con, limit: int | None = None, days: int = 60, client=None) -> str:
    """Send every pending video as one batch request set (BATCH videos per request). Returns a status line."""
    book = ctncs.load()
    rows = pending(con, days, limit)
    if not rows:
        return "nothing to send"
    if client is None:
        import anthropic
        client = anthropic.Anthropic()
    system = [{"type": "text", "text": _system(book), "cache_control": {"type": "ephemeral"}}]
    groups = [rows[i:i + BATCH] for i in range(0, len(rows), BATCH)]
    reqs = [{"custom_id": f"g{k}", "params": _params(system, g)} for k, g in enumerate(groups)]
    b = client.messages.batches.create(requests=reqs)
    con.execute("INSERT INTO classify_batches VALUES (?, ?, ?, ?, NULL, NULL)",
                [b.id, dt.datetime.now(), len(rows), json.dumps({f"g{k}": [r[0] for r in g] for k, g in enumerate(groups)})])
    return f"{len(rows)} videos sent in batch {b.id} ({len(reqs)} requests)"


def collect(con, client=None, wait_s: int = 0, poll_s: int = 30, sleep=None) -> str:
    """Save results from every finished open batch. Waits up to wait_s for open batches to finish."""
    import time
    sleep = sleep or time.sleep
    con.execute(BATCHES_DDL)
    open_ = con.execute("SELECT batch_id, request_map FROM classify_batches WHERE collected_at IS NULL ORDER BY submitted_at").fetchall()
    if not open_:
        return "no open batches"
    if client is None:
        import anthropic
        client = anthropic.Anthropic()
    book, done, waiting, deadline = ctncs.load(), 0, 0, time.monotonic() + wait_s
    for batch_id, m in open_:
        ids_of = json.loads(m)
        while True:
            b = client.messages.batches.retrieve(batch_id)
            if b.processing_status == "ended" or time.monotonic() >= deadline:
                break
            sleep(poll_s)
        if b.processing_status != "ended":
            waiting += 1
            continue
        out = []
        for r in client.messages.batches.results(batch_id):
            if r.result.type == "succeeded" and r.custom_id in ids_of:
                llm.warn_if_cut(r.result.message, "classify-videos (batch)")
                usage.anthropic(r.result.message, MODEL, "classify videos", batch=True)
                out += parse_reply(llm.reply_text(r.result.message), ids_of[r.custom_id], book)
        if out:
            save(con, out)
        # requests that errored or expired are simply left unclassified; the next submit() sends those videos again
        con.execute("UPDATE classify_batches SET collected_at = ?, classified = ? WHERE batch_id = ?",
                    [dt.datetime.now(), len(out), batch_id])
        done += len(out)
    return f"{done} classified" + (f"; {waiting} batch(es) still processing, collected next run" if waiting else "")
