"""Coverage: which channels sit behind the numbers, per niche.

One row per niche: channels tracked (in the database), added in the last 7 days, and what
screening did with every discovered channel (kept, waiting for review, dropped and why).
The terminal's Coverage view reads this as data/exports/coverage.csv.
"""
import csv
import datetime as dt
from collections import Counter, defaultdict
from pathlib import Path

from . import screen

ROOT = Path(__file__).resolve().parent.parent
ANCHORS = ROOT / "config" / "channels.csv"
DROP_REASONS = ["non_us", "off_niche", "non_english", "brand", "reupload", "news_media", "kids", "other"]
FIELDS = (["niche_code", "niche", "tracked", "anchors", "added_7d", "screened", "kept", "review", "dropped"]
          + [f"drop_{r}" for r in DROP_REASONS])


def _anchors(path=ANCHORS) -> dict:
    if not Path(path).exists():
        return {}
    with open(path, newline="", encoding="utf-8") as f:
        return {r["channel"].strip(): r["niche"].strip() for r in csv.DictReader(f)
                if (r.get("channel") or "").strip() and not r["channel"].startswith("#")}


def _label(code: str) -> str:
    if code in screen.NICHES:
        return screen.NICHES[code]
    try:
        from . import ctncs
        return ctncs.label_path(ctncs.load(), code)[-1]
    except Exception:
        return code


def rows(con=None, screened: dict | None = None, anchors: dict | None = None, today: dt.date | None = None) -> list[dict]:
    screened = screen.load() if screened is None else screened
    anchors = _anchors() if anchors is None else anchors
    today = today or dt.date.today()
    niche_of = {cid: r["niche"] for cid, r in screened.items()}
    niche_of.update({cid: n for cid, n in anchors.items() if cid.startswith("UC")})

    tracked, added = Counter(), Counter()
    if con is not None:
        # anchors listed by @handle get their niche from the channel's CTNCS classification
        for cid, added_at, code in con.execute(
                "SELECT c.channel_id, c.added_at, k.ctncs_code FROM channels c "
                "LEFT JOIN classifications k USING (channel_id)").fetchall():
            n = niche_of.get(cid) or (".".join(code.split(".")[:2]) if code else "unassigned")
            tracked[n] += 1
            if added_at and (today - (added_at.date() if hasattr(added_at, "date") else added_at)).days < 7:
                added[n] += 1

    verdicts, reasons = defaultdict(Counter), defaultdict(Counter)
    for r in screened.values():
        verdicts[r["niche"]][r["verdict"]] += 1
        if r["verdict"] == "drop":
            reasons[r["niche"]][r["reason"] if r["reason"] in DROP_REASONS else "other"] += 1
    anchor_count = Counter(anchors.values())

    out = []
    for code in sorted(set(verdicts) | set(tracked) | set(anchor_count)):
        v = verdicts[code]
        row = {"niche_code": code, "niche": _label(code), "tracked": tracked[code],
               "anchors": anchor_count[code], "added_7d": added[code], "screened": sum(v.values()),
               "kept": v["keep"], "review": v["review"], "dropped": v["drop"]}
        row.update({f"drop_{k}": reasons[code][k] for k in DROP_REASONS})
        out.append(row)
    return out


def write(con=None, path: str = "data/exports/coverage.csv") -> str:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        w.writeheader()
        w.writerows(rows(con))
    return path
