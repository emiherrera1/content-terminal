"""Screen discovered channels before they are seeded.

Discovery finds channels by search, and search pulls in channels the fund can't use: non-US creators
(YouTube's region filter only nudges results), brands, news outlets, clip/re-upload channels, kids
channels and channels that only touched the niche once. This step checks each new channel's country
and recent uploads, then asks a small model for a verdict:

  keep    an independent, US-based English-language creator whose content fits the niche
  drop    clearly not one (reason given)
  review  borderline; a person decides

Results go to config/channels_screened.csv. Rows already in that file are never re-screened, so a
person can change a verdict by editing the file and it sticks. `seed` loads only discovered channels
whose verdict is keep.

Cost: channels.list is 1 unit per 50 channels and recent uploads are 1 unit per channel, so ~1,200
units for 1,200 channels; the model call is ~$0.001 per channel.
"""
import csv
import datetime as dt
import json
import os
from pathlib import Path

from . import discover, youtube

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "config" / "channels_screened.csv"
FIELDS = ["channel", "niche", "title", "subscribers", "country", "verdict", "reason", "note", "screened_at"]
MODEL = os.environ.get("SCREEN_MODEL", os.environ.get("VIDEO_CLASSIFIER_MODEL", "claude-haiku-5-5"))
BATCH = 20

HOME = {"US"}
NEAR = {"CA", "GB", "AU", "IE", "NZ"}          # English-speaking: a person decides
REASONS = {"ok", "non_us", "non_english", "brand", "news_media", "off_niche", "kids", "reupload", "other"}

NICHES = {"03.07": "Fitness", "03.08": "Food & Cooking", "03.09": "Fashion & Beauty", "04.10": "Consumer Tech",
          "08.23": "Entrepreneurship", "08.25": "Personal Finance", "09.26": "Physical Health & Nutrition",
          "09.27": "Mental Health"}

SYSTEM = """You screen YouTube channels for a venture fund that backs independent creators in the United States.

For each channel decide:
- keep: an individual creator or small creator-led team, making English-language content for a US audience, whose recent uploads fit the stated niche.
- drop: clearly not that. Give one reason:
  non_us (creator or audience is clearly outside the US: language, place names, currency, exams or institutions of another country),
  non_english, brand (a company, retailer, app or product marketing its own goods), news_media (news outlet, magazine, TV network, publisher),
  off_niche (the niche match was incidental; most uploads are about something else), kids (made for children),
  reupload (clips, compilations, AI voiceover or re-uploaded content), other.
- review: you can't tell, or it is a close call (for example a creator who has built a product brand around themself, or a near-niche fit).

Prefer review over drop when unsure. A creator who sells their own products is still a creator, not a brand."""

USER = """Screen each channel. Reply with a JSON list only, one object per channel, in the same order:
[{{"id": "...", "verdict": "keep|drop|review", "reason": "ok|non_us|non_english|brand|news_media|off_niche|kids|reupload|other", "note": "<10 words max>"}}]

{channels}"""


def load(path=OUT) -> dict:
    if not Path(path).exists():
        return {}
    with open(path, newline="", encoding="utf-8") as f:
        return {r["channel"]: r for r in csv.DictReader(f)}


def write(rows: dict, path=OUT) -> None:
    order = {"review": 0, "keep": 1, "drop": 2}
    out = sorted(rows.values(), key=lambda r: (r["niche"], order.get(r["verdict"], 3), r["title"] or ""))
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        w.writeheader()
        w.writerows({k: r.get(k, "") for k in FIELDS} for r in out)


def country_rule(country: str | None):
    """A verdict from country alone, or None to leave it to the model."""
    c = (country or "").upper()
    if not c or c in HOME:
        return None
    if c in NEAR:
        return ("review", "non_us", f"based in {c}")
    return ("drop", "non_us", f"based in {c}")


def _block(rows, info) -> str:
    out = []
    for r in rows:
        i = info.get(r["channel"], {})
        titles = "; ".join(i.get("recent_titles", [])[:10]) or "-"
        out.append(f'id: {r["channel"]}\nname: {r["title"]}\nniche: {NICHES.get(r["niche"], r["niche"])}\n'
                   f'subscribers: {r["subscribers"]}\ncountry: {i.get("country") or "not set"}\n'
                   f'language: {i.get("language") or "not set"}\n'
                   f'description: {(i.get("description") or "")[:300]}\nrecent uploads: {titles}')
    return "\n---\n".join(out)


def parse_reply(text: str, ids: list[str]) -> dict:
    try:
        items = json.loads(text[text.index("["): text.rindex("]") + 1])
    except ValueError:
        items = []
    got = {str(i.get("id")): i for i in items if isinstance(i, dict)}
    out = {}
    for cid in ids:
        i = got.get(cid, {})
        verdict = i.get("verdict") if i.get("verdict") in ("keep", "drop", "review") else "review"
        reason = i.get("reason") if i.get("reason") in REASONS else ("other" if i else "no answer")
        out[cid] = (verdict, reason, str(i.get("note") or "")[:120])
    return out


def run(info_fn=None, client=None, discovered=None, path=OUT, limit: int | None = None) -> dict:
    discovered = discovered if discovered is not None else discover.load_existing()
    done = load(path)
    todo = [r for cid, r in discovered.items() if cid not in done][: limit or None]
    if todo:
        info = (info_fn or youtube.channel_profiles)([r["channel"] for r in todo])
        today = dt.date.today().isoformat()
        ask = []
        for r in todo:
            i = info.get(r["channel"])
            base = {"channel": r["channel"], "niche": r["niche"], "title": r["title"],
                    "subscribers": r["subscribers"], "country": (i or {}).get("country") or "", "screened_at": today}
            if i is None:
                done[r["channel"]] = {**base, "verdict": "drop", "reason": "other", "note": "channel not found"}
            elif rule := country_rule(i.get("country")):
                done[r["channel"]] = {**base, "verdict": rule[0], "reason": rule[1], "note": rule[2]}
            else:
                done[r["channel"]] = {**base, "verdict": "review", "reason": "no answer", "note": ""}
                ask.append(r)
        if ask:
            if client is None:
                import anthropic
                client = anthropic.Anthropic()
            system = [{"type": "text", "text": SYSTEM, "cache_control": {"type": "ephemeral"}}]
            for k in range(0, len(ask), BATCH):
                batch = ask[k:k + BATCH]
                msg = client.messages.create(model=MODEL, max_tokens=60 * len(batch), system=system, messages=[
                    {"role": "user", "content": USER.format(channels=_block(batch, info))}])
                for cid, (verdict, reason, note) in parse_reply(msg.content[0].text, [r["channel"] for r in batch]).items():
                    done[cid].update(verdict=verdict, reason=reason, note=note)
        write(done, path)
    counts = {}
    for r in done.values():
        counts[r["verdict"]] = counts.get(r["verdict"], 0) + 1
    return {"screened": len(todo), "total": len(done), "counts": counts}


def kept(path=OUT) -> set[str]:
    return {cid for cid, r in load(path).items() if r["verdict"] == "keep"}
